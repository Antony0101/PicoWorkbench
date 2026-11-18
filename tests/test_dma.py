"""DMA behavior specified from the RP2040 DMA register map and DREQ table."""
import struct
from contextlib import closing

import pytest

from conftest import make_elf
from rp2040_sim import demo
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import SimulationError, Stop
from rp2040_sim.project import Project

DMA = 0x50000000
PIO = 0x50200000
RAM = 0x20000000
DST = RAM + 256


def ctrl(channel=0, size=4, dreq=63, extra=0, chain=None):
    return 1 | ({1: 0, 2: 1, 4: 2}[size] << 2) | (3 << 4) | (dreq << 15) | ((channel if chain is None else chain) << 11) | extra


def configure(thumb, channel=0, source=RAM, dest=DST, count=8, control=None):
    base = DMA + channel * 64
    return thumb.write(base, source).write(base + 4, dest).write(base + 8, count).write(
        base + 16, ctrl(channel) if control is None else control)


def load(engine, thumb, ram=bytes(range(64))):
    engine.load(make_elf(thumb.elf()[264:], ram, bss=1024))


def finish(engine, deadline=1_000_000):
    for _ in range(100):
        stop = engine.run(deadline, budget=1000)
        if stop != Stop.BUDGET:
            return stop
    pytest.fail("DMA run exceeded bounded work")


def test_reset_and_copy_queries(engine):
    state = engine.dma()
    assert len(state.channels) == 12
    assert state.intr == 0 and state.ints == (0, 0) and state.timers == (0, 0, 0, 0)
    assert all(c.ctrl == c.remaining == c.reload == 0 and not c.waiting for c in state.channels)
    assert int.from_bytes(engine.peek(DMA + 0x448), 'little') == 12
    assert engine.dma() == state


@pytest.mark.parametrize('size', [1, 2, 4])
@pytest.mark.parametrize('channel', [0, 5, 11])
def test_copy_width_channels_and_completion_while_cpu_sleeps(engine, thumb, size, channel):
    configure(thumb, channel, control=ctrl(channel, size))
    thumb.write(DMA + 0x430, 1 << channel).op(0xbf30)
    load(engine, thumb)
    assert finish(engine) == Stop.DEADLINE
    assert engine.peek(DST, 8 * size) == bytes(range(8 * size))
    c = engine.dma().channels[channel]
    assert (c.read_addr, c.write_addr, c.remaining, c.reload) == (RAM + 8 * size, DST + 8 * size, 0, 8)
    assert not c.busy and engine.dma().intr == 1 << channel
    assert engine.snapshot().instructions < 30


@pytest.mark.parametrize('size', [1, 2, 4])
def test_byte_swap(engine, thumb, size):
    configure(thumb, count=4, control=ctrl(size=size, extra=1 << 22))
    thumb.write(DMA + 12, ctrl(size=size, extra=1 << 22)).op(0xbf30)
    load(engine, thumb)
    finish(engine)
    assert engine.peek(DST, 4 * size) == b''.join(bytes(range(i, i + size))[::-1] for i in range(0, size * 4, size))


@pytest.mark.parametrize('write_ring', [False, True])
def test_ring_address_wrap(engine, thumb, write_ring):
    control = ctrl(extra=(4 << 6) | (int(write_ring) << 10))
    configure(thumb, count=10, control=control)
    thumb.write(DMA + 12, control).op(0xbf30)
    load(engine, thumb)
    finish(engine)
    c = engine.dma().channels[0]
    if write_ring:
        assert engine.peek(DST, 16) == bytes(range(32, 40)) + bytes(range(24, 32))
        assert c.write_addr == DST + 8 and c.read_addr == RAM + 40
    else:
        assert engine.peek(DST, 40) == bytes(range(16)) * 2 + bytes(range(8))
        assert c.read_addr == RAM + 8 and c.write_addr == DST + 40


@pytest.mark.parametrize('increment_read', [False, True])
def test_fixed_source_or_destination(engine, thumb, increment_read):
    control = ctrl() & ~(1 << (5 if increment_read else 4))
    configure(thumb, count=4, control=control)
    thumb.write(DMA + 12, control).op(0xbf30)
    load(engine, thumb)
    finish(engine)
    assert engine.peek(DST, 16) == (bytes(range(12, 16)) + bytes(12) if increment_read else bytes(range(4)) * 4)


@pytest.mark.parametrize('trigger_offset', [12, 28, 44, 60])
def test_alias_triggers_and_reload(engine, thumb, trigger_offset):
    configure(thumb, count=3)
    thumb.op(0xbe00)
    value = {12: ctrl(), 28: 3, 44: DST, 60: RAM}[trigger_offset]
    thumb.write(DMA + trigger_offset, value).op(0xbf30)
    load(engine, thumb)
    assert engine.run(budget=100) == Stop.BREAKPOINT
    assert engine.dma().channels[0].reload == 3
    assert engine.dma().channels[0].remaining == 0
    assert int.from_bytes(engine.peek(DMA + 0x804), 'little') == 3
    finish(engine)
    assert engine.peek(DST, 12) == bytes(range(12))
    assert not engine.dma().channels[0].busy


def test_pause_resume_abort_and_busy_trigger_ignored(engine, thumb):
    configure(thumb, count=100)
    thumb.write(DMA + 12, ctrl()).write(DMA + 16, ctrl() & ~1).op(0xbe00)
    thumb.write(DMA + 16, ctrl()).op(0xbf00).op(0xbf00).op(0xbe00)
    thumb.write(DMA + 0x444, 1).op(0xbf30)
    load(engine, thumb)
    engine.run(budget=100)
    paused = engine.dma().channels[0]
    assert paused.busy and not paused.ctrl & 1 and 0 < paused.remaining < 100
    engine.run(budget=100)
    resumed = engine.dma().channels[0]
    assert resumed.busy and resumed.remaining < paused.remaining
    finish(engine)
    aborted = engine.dma().channels[0]
    assert not aborted.busy and 0 < aborted.remaining < resumed.remaining
    assert engine.dma().intr == 0
    assert int.from_bytes(engine.peek(DMA + 0x444), 'little') == 0


def test_chain_completion_and_quiet_null_trigger(engine, thumb):
    configure(thumb, 1, source=RAM + 16, dest=DST + 16, count=4)
    configure(thumb, 0, count=4, control=ctrl(chain=1, extra=1 << 21))
    thumb.write(DMA + 12, ctrl(chain=1, extra=1 << 21)).op(0xbe00)
    for _ in range(20):
        thumb.op(0xbf00)
    thumb.op(0xbe00).write(DMA + 28, 0).op(0xbf30)
    load(engine, thumb)
    engine.run(budget=100)
    engine.run(budget=100)
    assert engine.peek(DST, 32) == bytes(range(32))
    assert engine.dma().intr == 2
    finish(engine)
    assert engine.dma().intr == 3


def test_control_block_writes_another_channel_trigger(engine, thumb):
    # Channel 0 copies [CTRL, READ, WRITE, COUNT] into channel 1's AL1 block.
    descriptor = struct.pack('<4I', ctrl(1), RAM + 16, DST, 3)
    configure(thumb, 0, dest=DMA + 64 + 16, count=4)
    thumb.write(DMA + 12, ctrl()).op(0xbf30)
    load(engine, thumb, descriptor + bytes(range(12)))
    finish(engine)
    assert engine.peek(DST, 12) == bytes(range(12))
    assert engine.dma().intr == 3


@pytest.mark.parametrize('source,dest,flag', [(RAM + 1, DST, 30), (RAM, DST + 1, 29),
    (0x12345678, DST, 30), (RAM, 0x10000000, 29), (RAM, 0x50000300, 29),
    (0xd0000058, DST, 30), (RAM, 0xd0000054, 29), (0xe000e100, DST, 30)])
def test_bus_errors_stop_channel_and_interrupt_even_when_quiet(engine, thumb, source, dest, flag):
    control = ctrl(extra=1 << 21)
    configure(thumb, source=source, dest=dest, count=4, control=control)
    thumb.write(DMA + 12, control).op(0xbf30)
    load(engine, thumb)
    assert finish(engine) == Stop.DEADLINE
    c = engine.dma().channels[0]
    assert c.ctrl & (1 << flag) and c.ctrl & (1 << 31) and not c.busy
    assert c.remaining == 4 and engine.dma().intr == 1


@pytest.mark.parametrize('dreq', [16, 20, 24, 32, 37, 58])
def test_unsupported_requests_are_diagnostic(engine, thumb, dreq):
    configure(thumb, control=ctrl(dreq=dreq))
    thumb.write(DMA + 12, ctrl(dreq=dreq))
    load(engine, thumb)
    with pytest.raises(SimulationError, match='DREQ'):
        engine.run(budget=100)


@pytest.mark.parametrize('timer', range(4))
def test_fractional_timer_pacing_and_sleep(engine, thumb, timer):
    thumb.write(DMA + 0x420 + 4 * timer, (1 << 16) | 100)
    configure(thumb, count=8, control=ctrl(dreq=59 + timer))
    thumb.write(DMA + 12, ctrl(dreq=59 + timer)).op(0xbf30)
    load(engine, thumb)
    assert finish(engine, 450_000) == Stop.DEADLINE
    c = engine.dma().channels[0]
    assert c.remaining == 4 and c.busy
    assert engine.peek(DST, 16) == bytes(range(16))
    assert engine.peek(DST + 16, 16) == bytes(16)
    assert finish(engine, 850_000) == Stop.DEADLINE
    assert engine.dma().channels[0].remaining == 0 and engine.dma().intr == 1


def test_pio_tx_backpressure_and_narrow_write_replication(engine, thumb):
    control = ctrl(size=1, dreq=0) & ~(1 << 5)
    configure(thumb, dest=PIO + 0x10, count=10, control=control)
    thumb.write(DMA + 12, control).op(0xbe00)
    thumb.write(PIO + 0xd8, 0x80a0).op(0xbf30)  # forced PULL frees one slot
    load(engine, thumb)
    engine.run(budget=100)
    for _ in range(5):
        engine.step()
    # Execution has reached WFI; first PULL allows exactly one more byte.
    finish(engine)
    assert engine.dma().channels[0].remaining == 5
    assert engine.dma().channels[0].waiting
    assert engine.pio().sm[0].tx == tuple(i * 0x01010101 for i in range(1, 5))
    assert engine.pio().sm[0].osr == 0
    assert not engine.pio().fdebug & (1 << 16)


def test_pio_rx_pop_with_nondestructive_inspection(engine, thumb):
    thumb.write(PIO + 0xd8, 0xe027).write(PIO + 0xd8, 0xa0c1).write(PIO + 0xd8, 0x8020)
    configure(thumb, source=PIO + 0x20, count=2, control=ctrl(dreq=4) & ~(1 << 4))
    thumb.write(DMA + 12, ctrl(dreq=4) & ~(1 << 4)).op(0xbf30)
    load(engine, thumb)
    finish(engine)
    assert engine.peek(DST) == struct.pack('<I', 7)
    before = engine.dma()
    assert before.channels[0].remaining == 1 and before.channels[0].waiting
    assert engine.pio().sm[0].rx == ()
    assert engine.dma() == before


def test_adc_fifo_paces_dma(engine, thumb):
    thumb.write(0x4004c008, (1 << 24) | 9)  # FIFO enable, DREQ enable, threshold 1
    configure(thumb, source=0x4004c00c, count=3, control=ctrl(dreq=36) & ~(1 << 4))
    thumb.write(DMA + 12, ctrl(dreq=36) & ~(1 << 4))
    thumb.write(0x4004c000, 9).op(0xbf30)  # ADC enable, continuous conversions
    load(engine, thumb)
    engine.adc(0, 1234, 0)
    finish(engine, 150_000)
    assert engine.peek(DST, 12) == struct.pack('<3I', 1234, 1234, 1234)
    assert engine.dma().intr == 1


def test_integrated_waveform_irq_wake_and_reset_replay():
    with closing(Simulation(Project.dma_pio())) as sim:
        sim.load(demo.image('dma_pio'))
        traces = []
        for _ in range(2):
            for _ in range(100):
                stop = sim.run(3_100_000_000)
                if stop != Stop.BUDGET:
                    break
            assert stop == Stop.DEADLINE
            events = sim.engine.events()
            edges = [e for e in events if e.kind == 1 and e.source == 25 and e.value in (0, 1)]
            high = [e for e in edges if e.value == 1]
            assert len(high) == 6
            assert all(b.time_ns - a.time_ns == 250_000_000 for a, b in zip(edges[1:], edges[2:]))
            assert ''.join(chr(e.value) for e in events if e.kind == 2) == 'DMA complete\n'
            state = sim.engine.dma()
            assert not state.channels[0].busy and state.channels[0].remaining == 0
            assert state.intr == 0  # handler acknowledged DMA IRQ0
            assert sim.engine.snapshot().instructions < 200
            assert sim.engine.pio().sm[0].tx == ()
            traces.append(events)
            sim.reset()
            assert sim.engine.dma().channels[0].reload == 0
        assert traces[0] == traces[1]


@pytest.mark.parametrize('line', [0, 1])
def test_completion_interrupt_wakes_cpu_on_either_line(engine, tmp_path, line):
    from rp2040_sim.build import ROOT, build, compiler
    try:
        compiler()
    except RuntimeError:
        pytest.skip('ARM cross compiler not installed')
    irq = 11 + line
    source = tmp_path / 'dma_irq.c'
    source.write_text(f'''
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 count;
static const u32 words[3] = {{0x12345678, 0xabcdef, 0x98765432}};
void reset_handler(void); void handler(void);
__attribute__((section(".vectors"),used)) const u32 vectors[{17 + irq}] = {{
 [0]=0x20042000, [1]=(u32)reset_handler, [{16 + irq}]=(u32)handler }};
void handler(void) {{ REG(0x50000400)=1; count++; }}
void reset_handler(void) {{
 REG(0x50000420)=(1u<<16)|100;
 REG(0x50000000)=(u32)words; REG(0x50000004)=0x20000100;
 REG(0x50000008)=3; REG({DMA + 0x404 + 16 * line}u)=1;
 REG(0xe000e100)=1u<<{irq}; REG(0x5000000c)={ctrl(dreq=59)}u;
 while (!count) __asm volatile("wfi");
 __asm volatile("bkpt 0"); for (;;) {{}}
}}
''')
    output = tmp_path / 'dma_irq.elf'
    build(source, output, ROOT / 'firmware/fixtures/direct.ld')
    engine.load(output)
    assert finish(engine) == Stop.BREAKPOINT
    assert 300_000 <= engine.snapshot().time_ns < 400_000
    assert engine.peek(RAM) == struct.pack('<I', 1)
    assert engine.peek(DST, 12) == struct.pack('<3I', 0x12345678, 0xabcdef, 0x98765432)
    assert engine.dma().intr == 0


def test_interrupt_force_mask_clear_and_channel_errors_w1c(engine, thumb):
    configure(thumb, source=RAM + 1)
    thumb.write(DMA + 12, ctrl()).op(0xbf00).op(0xbe00)
    thumb.write(DMA + 16, ctrl() | (1 << 30))
    thumb.write(DMA + 0x404, 1).write(DMA + 0x418, 2).op(0xbe00)
    thumb.write(DMA + 0x40c, 1).write(DMA + 0x3418, 2).op(0xbe00)
    load(engine, thumb)
    engine.run(budget=100)
    assert engine.dma().channels[0].ctrl >> 30 == 3
    engine.run(budget=100)
    state = engine.dma()
    assert not state.channels[0].ctrl & (7 << 29)
    assert state.intr == 1 and state.ints == (1, 2)
    assert int.from_bytes(engine.peek(0xe000e200), 'little') & (3 << 11) == 3 << 11
    engine.run(budget=100)
    assert engine.dma().ints == (0, 0) and engine.dma().intr == 0


def test_busy_retrigger_keeps_remaining_but_updates_reload(engine, thumb):
    configure(thumb, count=100)
    thumb.write(DMA + 12, ctrl()).write(DMA + 28, 7).op(0xbe00)
    thumb.write(DMA + 0x444, 1).write(DMA, RAM).write(DMA + 4, DST)
    thumb.write(DMA + 0x430, 1).op(0xbf30)
    load(engine, thumb)
    engine.run(budget=100)
    c = engine.dma().channels[0]
    assert c.reload == 7 and c.remaining == 97 and c.busy
    finish(engine)
    assert engine.dma().channels[0].remaining == 0 and engine.peek(DST, 28) == bytes(range(28))


@pytest.mark.parametrize('dreq', list(range(16)))
def test_every_pio_request_routes_to_correct_block_and_fifo(engine, thumb, dreq):
    block, sm, rx = dreq // 8, dreq & 3, bool(dreq & 4)
    base = PIO + block * 0x100000
    if rx:
        thumb.write(base + 0xd8 + sm * 24, 0xe02b)  # SET X,11
        thumb.write(base + 0xd8 + sm * 24, 0xa0c1)  # MOV ISR,X
        thumb.write(base + 0xd8 + sm * 24, 0x8020)  # PUSH BLOCK
    port = base + (0x20 if rx else 0x10) + sm * 4
    control = ctrl(dreq=dreq) & ~(1 << (4 if rx else 5))
    configure(thumb, source=port if rx else RAM, dest=DST if rx else port,
              count=1 if rx else 5, control=control)
    thumb.write(DMA + 12, control).op(0xbf30)
    load(engine, thumb)
    finish(engine)
    if rx:
        assert engine.peek(DST) == struct.pack('<I', 11)
        assert not engine.pio(block).sm[sm].rx and not engine.dma().channels[0].busy
    else:
        assert engine.pio(block).sm[sm].tx == tuple(struct.unpack('<4I', bytes(range(16))))
        assert engine.dma().channels[0].remaining == 1 and engine.dma().channels[0].waiting


def test_multiple_trigger_and_priority_rounds_are_bounded(engine, thumb):
    configure(thumb, 0, count=100, control=ctrl(extra=2))
    configure(thumb, 1, source=RAM + 16, dest=DST + 128, count=100)
    thumb.write(DMA + 0x430, 3).op(0xbf30)
    load(engine, thumb)
    assert finish(engine, 60_000) == Stop.DEADLINE
    state = engine.dma()
    assert 0 < state.channels[0].remaining < 100
    assert 0 < state.channels[1].remaining < 100
    assert abs(state.channels[0].remaining - state.channels[1].remaining) <= 1


@pytest.mark.parametrize('extra', [4, 1 << 23, 12 << 11])
def test_reserved_or_unsupported_config_is_diagnostic(engine, thumb, extra):
    # DATA_SIZE=3, SNIFF_EN=1, or CHAIN_TO=12 respectively.
    configure(thumb, control=ctrl(extra=extra))
    thumb.write(DMA + 12, ctrl(extra=extra))
    load(engine, thumb)
    with pytest.raises(SimulationError, match='DMA'):
        engine.run(budget=100)


def test_dma_replaces_previously_executed_sram_instructions(engine, thumb):
    # Prime Unicorn's translated RAM code, replace it via DMA, then call again.
    ram = bytearray(260)
    struct.pack_into('<HH', ram, 0, 0x232a, 0x4770)  # MOVS R3,42; BX LR
    struct.pack_into('<HH', ram, 256, 0x2301, 0x4770)  # MOVS R3,1; BX LR
    thumb.ldr(2, DST | 1).op(0x4790).op(0xbe00)
    configure(thumb, count=1)
    thumb.write(DMA + 12, ctrl()).op(0xbf00).op(0xbf00)
    thumb.ldr(2, DST | 1).op(0x4790).op(0xbe00)
    image = bytearray(make_elf(thumb.elf()[264:], bytes(ram), bss=1024))
    struct.pack_into('<I', image, 108, 7)  # second PT_LOAD explicitly allows execution
    engine.load(bytes(image))
    assert engine.run(budget=100) == Stop.BREAKPOINT
    assert engine.snapshot().registers[3] == 1
    assert engine.run(budget=100) == Stop.BREAKPOINT
    assert engine.snapshot().registers[3] == 42
