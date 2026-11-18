import struct

import pytest

from rp2040_sim.build import ROOT, build, compiler
from rp2040_sim.engine import Drive, Engine, Stop, SimulationError


def test_pwm_frequency_duty_and_progress_while_cpu_sleeps(engine, thumb):
    thumb.write(0x40014004, 4).write(0x40050010, 9).write(0x4005000c, 4)
    thumb.write(0x40050000, 1).op(0xbf30)  # WFI, PWM keeps running
    engine.load(thumb.elf())
    assert engine.run(200_000, budget=1000) == Stop.DEADLINE
    events = [e for e in engine.events() if e.kind == 1 and e.source == 0]
    widths = [b.time_ns - a.time_ns for a, b in zip(events, events[1:])]
    assert 4000 in widths and 6000 in widths
    assert len(events) > 30
    assert engine.snapshot().instructions < 30


def test_adc_conversion_fifo_peek_and_input_schedule(engine, thumb):
    thumb.write(0x4004c000, 1).write(0x4004c008, 0x01000001)
    thumb.write(0x4004c000, 5).op(0xbf00).op(0xbf00).op(0xbf00)
    thumb.read(0x4004c004, 2)
    engine.load(thumb.elf())
    engine.adc(0, 1234, 0)
    assert engine.run(100_000, budget=1000) == Stop.BREAKPOINT
    assert engine.snapshot().registers[2] == 1234
    assert int.from_bytes(engine.peek(0x4004c00c), "little") == 1234
    assert int.from_bytes(engine.peek(0x4004c00c), "little") == 1234
    assert int.from_bytes(engine.peek(0x4004c008), "little") >> 16 & 15 == 1
    assert int.from_bytes(engine.peek(0x4004c000), "little") & 256


def test_adc_continuous_fifo_overflow_is_observable(engine, thumb):
    thumb.write(0x4004c008, 0x01000001).write(0x4004c000, 9)
    engine.load(thumb.elf(loop=True))
    engine.adc(0, 4095)
    engine.run(100_000, budget=1000)
    fcs = int.from_bytes(engine.peek(0x4004c008), "little")
    assert fcs >> 16 & 15 == 4
    assert fcs & (1 << 11)
    assert int.from_bytes(engine.peek(0x4004c014), "little") == 1


def test_pwm_wrap_interrupt_wakes_sleeping_cpu(tmp_path):
    try:
        compiler()
    except RuntimeError:
        pytest.skip("ARM cross compiler not installed")
    source = tmp_path / "pwm.c"
    source.write_text("""
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 count;
void reset_handler(void); void irq(void);
__attribute__((section(".vectors"),used)) const u32 vectors[21] = {
 [0]=0x20042000, [1]=(u32)reset_handler, [20]=(u32)irq };
void irq(void) { REG(0x400500a4)=1; if (++count==3) REG(0x40050000)=0; }
void reset_handler(void) {
 REG(0x40050010)=99; REG(0x4005000c)=40; REG(0x400500a8)=1;
 REG(0xe000e100)=16; REG(0x40050000)=1;
 while (count<3) __asm volatile("wfi");
 __asm volatile("bkpt 0"); for (;;) {}
}
""")
    output = tmp_path / "pwm.elf"
    build(source, output, ROOT / "firmware/fixtures/direct.ld")
    with Engine() as engine:
        engine.load(output)
        assert engine.run(1_000_000, budget=1000) == Stop.BREAKPOINT
        assert int.from_bytes(engine.peek(0x20000000), "little") == 3


@pytest.mark.parametrize("address,value", [(0x40050000, 3), (0x4004c000, 0x6001)])
def test_unsupported_peripheral_modes_stop(engine, thumb, address, value):
    engine.load(thumb.write(address, value).elf())
    with pytest.raises(SimulationError, match="Unsupported MMIO"):
        engine.run(budget=1000)
