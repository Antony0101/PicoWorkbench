import struct

import pytest

from rp2040_sim import demo
from rp2040_sim.engine import Drive, Engine, SimulationError, Stop
from conftest import make_elf


def run_to(engine, deadline):
    while engine.snapshot().time_ns < deadline:
        assert engine.run(deadline) in (Stop.BUDGET, Stop.DEADLINE)


def test_real_demo_gpio_uart_and_reset(engine):
    engine.load(demo.image())
    run_to(engine, 800_000_000)
    first = engine.events()
    assert bytes(e.value for e in first if e.kind == 2) == b"RP2040 simulator ready\r\n"
    led = [e for e in first if e.kind == 1 and e.source == 25]
    assert [e.value for e in led] == [1, 0, 1]
    assert all(250_000_000 <= b.time_ns - a.time_ns <= 250_050_000 for a, b in zip(led, led[1:]))
    engine.reset()
    run_to(engine, 800_000_000)
    assert engine.events() == first


def test_instruction_flags_and_instance_isolation(engine, thumb):
    image = thumb.op(0x2001).op(0x07c0).op(0x1800).elf()  # r0=1; lsl #31; add r0,r0,r0
    engine.load(image)
    assert engine.run(budget=20) == Stop.BREAKPOINT
    state = engine.snapshot()
    assert state.registers[0] == 0
    assert state.xpsr & 0xf0000000 == 0x70000000  # Z, C, V
    with Engine() as other:
        assert other.snapshot().instructions == 0
        assert other.snapshot().registers[15] != state.registers[15]


def test_budget_deadline_and_breakpoint_resume(engine, thumb):
    engine.load(thumb.op(0x3001).elf(loop=True))
    engine.breakpoint(0x10000008)
    assert engine.run(budget=100) == Stop.BREAKPOINT
    assert engine.snapshot().instructions == 0
    assert engine.step() == Stop.BUDGET
    assert engine.snapshot().registers[0] == 1
    engine.breakpoint(0x10000008, False)
    assert engine.run(100_000, budget=3) == Stop.BUDGET
    assert engine.snapshot().instructions == 4
    assert engine.run(10_000) == Stop.DEADLINE
    assert engine.snapshot().time_ns == 10_000


def test_sio_atomic_aliases_and_output_enable(engine, thumb):
    thumb.write(0x40014004, 5).write(0xd0000024, 1).write(0xd0000014, 1)
    thumb.read(0xd0000004).write(0xd000001c, 1).read(0xd0000004, 3)
    thumb.write(0xd0000010, 0x000000f0).write(0xd0000018, 0x30)
    engine.load(thumb.elf())
    engine.run(budget=200)
    state = engine.snapshot()
    assert state.registers[2] & 1 and not state.registers[3] & 1
    assert state.gpio_out == 0xc0
    assert state.gpio_oe == 1


def test_pad_and_ctrl_aliases(engine, thumb):
    thumb.write(0x40014004, 5).write(0x40016004, 1 << 9)  # OUTOVER force low
    thumb.write(0xd0000020, 1).write(0xd0000010, 1)
    thumb.read(0xd0000004)
    engine.load(thumb.elf())
    engine.run(budget=100)
    assert engine.snapshot().registers[2] & 1 == 0
    assert int.from_bytes(engine.peek(0x40014004), "little") == 5 | 1 << 9


def test_wires_share_output_contention_and_pulls(engine, thumb):
    thumb.write(0x40014004, 5).write(0xd0000020, 1).write(0xd0000010, 1)
    engine.load(thumb.elf(loop=True))
    engine.connect_gpio(1, 0)
    engine.run(budget=100)
    assert engine.snapshot().pin(0) == engine.snapshot().pin(1) == Drive.HIGH
    engine.gpio(1, Drive.LOW)
    engine.run(budget=0)
    assert engine.snapshot().gpio_conflict & 3 == 3
    engine.gpio(1, Drive.FLOATING)
    engine.run(budget=0)
    assert engine.snapshot().gpio_conflict == 0


def test_scheduled_input_order_and_past_rejection(engine, thumb):
    engine.load(thumb.elf(loop=True))
    engine.gpio(0, Drive.HIGH, 2000)
    engine.gpio(0, Drive.LOW, 2000)
    engine.gpio(0, Drive.HIGH, 1000)
    run_to(engine, 3000)
    inputs = [e for e in engine.events() if e.kind == 3]
    assert [(e.time_ns, e.value) for e in inputs] == [(1000, 1), (2000, 1), (2000, 0)]
    assert engine.snapshot().pin(0) == Drive.LOW
    with pytest.raises(SimulationError):
        engine.gpio(0, Drive.HIGH, 0)


def test_debug_peek_never_consumes_uart(engine, thumb):
    thumb.read(0x40034000, 2).read(0x40034000, 3)
    engine.load(thumb.elf())
    engine.uart(b"AB")
    engine.run(budget=0)
    assert engine.peek(0x40034000)[0] == ord("A")
    assert engine.peek(0x40034000)[0] == ord("A")
    assert engine.peek(0x40034018)[0] & 0x10 == 0
    engine.run(budget=100)
    assert engine.snapshot().registers[2:4] == (ord("A"), ord("B"))
    assert engine.peek(0x40034018)[0] & 0x10


def test_unavailable_peek_preserves_machine(engine, thumb):
    engine.load(thumb.elf(loop=True))
    before = engine.snapshot()
    with pytest.raises(SimulationError, match="unavailable"):
        engine.peek(0x40034004)
    assert engine.snapshot() == before
    assert engine.run(budget=5) == Stop.BUDGET


def test_timer_polling_alarm_and_write_clear(engine, thumb):
    thumb.write(0x40054010, 30).op(0xe7fe)
    engine.load(thumb.elf(loop=True))
    run_to(engine, 50_000)
    assert int.from_bytes(engine.peek(0x40054028), "little") == 50
    assert int.from_bytes(engine.peek(0x40054034), "little") == 1
    assert int.from_bytes(engine.peek(0x40054020), "little") == 0


def test_fractional_clock_does_not_drift(thumb):
    with Engine(133_000_000) as engine:
        engine.load(thumb.elf(loop=True))
        engine.run(budget=133_000)
        assert engine.snapshot().time_ns == 1_000_000


@pytest.mark.parametrize("address", [0x00000000, 0x4000000c, 0x40014160])
def test_unsupported_access_stops_with_address(engine, thumb, address):
    engine.load(thumb.read(address).elf())
    with pytest.raises(SimulationError, match=f"0x{address:08x}"):
        engine.run(budget=100)
    assert engine.snapshot().stop == Stop.FAULT
    with pytest.raises(SimulationError):
        engine.run(budget=100)


def test_bss_reset_and_mutable_ram(engine):
    engine.load(make_elf(b"\xfe\xe7", b"hello", 20))
    assert engine.peek(0x20000000, 25) == b"hello" + bytes(20)
    engine.reset()
    assert engine.peek(0x20000000, 25) == b"hello" + bytes(20)


@pytest.mark.parametrize("field,value", [(4, 2), (5, 2), (18, 3), (24, 0), (28, 0xfffffff0), (44, 0xff)])
def test_malformed_elf_rejected_without_replacing_image(engine, thumb, field, value):
    original = thumb.op(0x202a).elf()
    engine.load(original)
    malformed = bytearray(original)
    malformed[field] = value & 255
    with pytest.raises(SimulationError):
        engine.load(bytes(malformed))
    engine.reset()
    engine.step()
    assert engine.snapshot().registers[0] == 42


def test_trace_overflow_is_counted(engine, thumb):
    thumb.write(0x40014004, 5).write(0xd0000020, 1)
    # A loop around OUT_XOR avoids generating trace traffic from fixture loading.
    thumb.ldr(0, 0xd000001c).ldr(1, 1).op(0x6001).op(0xe7fd)
    engine.load(thumb.elf())
    engine.run(budget=30_000)
    assert engine.snapshot().dropped_events > 0
    assert len(engine.events()) == 8192


def test_image_and_closed_handle_errors():
    engine = Engine()
    assert engine.run() == Stop.NO_IMAGE
    engine.close()
    engine.close()
    with pytest.raises(SimulationError, match="closed"):
        engine.snapshot()
