"""PIO encodings and expected results specified from RP2040 datasheet chapter 3."""
import pytest

from rp2040_sim import demo
from rp2040_sim.build import ROOT, build, compiler
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Drive, SimulationError, Stop
from rp2040_sim.project import Project

PIO = 0x50200000


def force(thumb, instruction, block=0, sm=0):
    return thumb.write(PIO + block * 0x100000 + 0xd8 + sm * 24, instruction)


def start(thumb, instructions, block=0, sm=0, div=0x10000, pinctrl=1 << 26, shift=0xc0000, extra_exec=0, directions=1):
    base = PIO + block * 0x100000
    for i, instruction in enumerate(instructions):
        thumb.write(base + 0x48 + i * 4, instruction)
    thumb.write(base + 0xc8 + sm * 24, div)
    thumb.write(base + 0xcc + sm * 24, ((len(instructions) - 1) << 12) | extra_exec)
    thumb.write(base + 0xd0 + sm * 24, shift)
    thumb.write(base + 0xdc + sm * 24, pinctrl)
    force(thumb, 0xe080 | directions, block, sm)  # SET PINDIRS while disabled
    thumb.write(base, (1 << sm) | (1 << (8 + sm)))
    return thumb


def run_to(engine, deadline):
    for _ in range(1000):
        stop = engine.run(deadline, budget=1000)
        if stop != Stop.BUDGET:
            return stop
    pytest.fail("PIO execution did not return within bounded batches")


def test_pio_reset_and_independent_blocks(engine):
    assert int.from_bytes(engine.peek(PIO + 4), "little") == 0x0f000f00
    assert int.from_bytes(engine.peek(PIO + 0x44), "little") == 0x200404
    for block in (0, 1):
        state = engine.pio(block)
        assert state.irq == state.out == state.oe == 0
        assert state.instructions == (0,) * 32
        for sm in state.sm:
            assert not sm.enabled and sm.pc == 0 and sm.out_count == 32
            assert sm.clkdiv == 0x10000 and sm.execctrl == 0x1f000
            assert sm.shiftctrl == 0xc0000 and sm.pinctrl == 0x14000000
    with pytest.raises(ValueError):
        engine.pio(2)


@pytest.mark.parametrize("block,sm", [(0, 0), (1, 3)])
def test_known_waveform_fractional_clock_delay_wrap_and_sleep(engine, thumb, block, sm):
    thumb.write(0x40014004, 6 + block)
    start(thumb, [0xe301, 0xe100], block, sm, div=0x28000).op(0xbf30)
    engine.load(thumb.elf())
    assert run_to(engine, 300_000) == Stop.DEADLINE
    edges = [e for e in engine.events() if e.kind == 1 and e.source == 0 and e.value in (0, 1)]
    widths = [b.time_ns - a.time_ns for a, b in zip(edges, edges[1:])]
    assert widths[-10:] == [10_000, 5_000] * 5 or widths[-10:] == [5_000, 10_000] * 5
    assert len(edges) > 20
    assert engine.snapshot().instructions < 100  # PIO continues during WFI
    assert engine.pio(block).sm[sm].enabled
    engine.reset()
    assert engine.pio(block).sm[sm].pc == 0
    assert not engine.pio(block).sm[sm].enabled


def test_wait_and_side_set_during_stall(engine, thumb):
    thumb.write(0x40014004 + 8, 6).write(0x40014004 + 16, 6)
    # GP1 is SET, GP2 is side-set. WAIT 1 GPIO0 side 1 [2].
    pins = (2 << 26) | (1 << 5) | (2 << 10) | (1 << 29)
    start(thumb, [0x3280, 0xe001], pinctrl=pins, directions=3).op(0xbf30)
    engine.load(thumb.elf())
    engine.gpio(0, Drive.LOW, 0)
    engine.gpio(0, Drive.HIGH, 200_000)
    assert run_to(engine, 150_000) == Stop.DEADLINE
    assert engine.pio().sm[0].stalled == 1
    assert engine.pio().out & 4  # side-set applied even though WAIT is blocked
    assert not engine.pio().out & 2
    assert run_to(engine, 210_000) == Stop.DEADLINE
    edges = [e for e in engine.events() if e.kind == 1 and e.source == 1 and e.value == 1]
    assert edges and 202_000 <= edges[0].time_ns <= 205_000


def test_forced_instructions_scratch_shifts_and_bit_reverse(engine, thumb):
    for instruction in (0xe03f, 0xa049, 0xa031):  # SET X,31; MOV Y,~X; MOV X,::X
        force(thumb, instruction)
    force(thumb, 0xa0e2)  # MOV OSR,Y
    force(thumb, 0x6023)  # OUT X,3
    force(thumb, 0x4044)  # IN Y,4
    engine.load(thumb.elf())
    assert engine.run(budget=200) == Stop.BREAKPOINT
    sm = engine.pio().sm[0]
    assert sm.x == 0 and sm.y == 0xffffffe0
    assert sm.osr == 0x1ffffffc and sm.out_count == 3
    assert sm.isr == 0 and sm.in_count == 4
    assert sm.pc == 0 and not sm.enabled
    assert engine.pio(1).sm[0].y == 0


@pytest.mark.parametrize("condition,x,y,taken,final_x,final_y", [
    (0, 3, 5, True, 3, 5), (1, 0, 5, True, 0, 5), (1, 3, 5, False, 3, 5),
    (2, 0, 5, False, 0xffffffff, 5), (2, 3, 5, True, 2, 5),
    (3, 3, 0, True, 3, 0), (4, 3, 0, False, 3, 0xffffffff),
    (4, 3, 5, True, 3, 4), (5, 3, 5, True, 3, 5), (5, 3, 3, False, 3, 3),
    (6, 3, 5, False, 3, 5), (7, 3, 5, False, 3, 5),
])
def test_jmp_conditions_include_unconditional_decrement(engine, thumb, condition, x, y, taken, final_x, final_y):
    force(thumb, 0xe020 | x)
    force(thumb, 0xe040 | y)
    force(thumb, (condition << 5) | 7)
    engine.load(thumb.elf())
    assert engine.run(budget=100) == Stop.BREAKPOINT
    sm = engine.pio().sm[0]
    assert sm.pc == (7 if taken else 0)
    assert (sm.x, sm.y) == (final_x, final_y)


def test_fifo_overflow_peek_pop_and_nonblocking_pull(engine, thumb):
    for value in range(5):
        thumb.write(PIO + 0x10, 100 + value)
    force(thumb, 0x80a0)  # PULL BLOCK
    force(thumb, 0xa0c7)  # MOV ISR,OSR
    force(thumb, 0x8020)  # PUSH BLOCK
    thumb.op(0xbe00).read(PIO + 0x20, 3)
    engine.load(thumb.elf())
    assert engine.run(budget=1000) == Stop.BREAKPOINT
    state = engine.pio()
    assert state.fdebug & (1 << 16)
    assert state.sm[0].tx == (101, 102, 103) and state.sm[0].rx == (100,)
    assert int.from_bytes(engine.peek(PIO + 0x20), "little") == 100
    assert engine.pio() == state
    assert engine.run(budget=20) == Stop.BREAKPOINT
    assert engine.snapshot().registers[3] == 100 and not engine.pio().sm[0].rx


def test_nonblocking_pull_falls_back_to_x_and_push_discards_on_full(engine, thumb):
    force(thumb, 0xe033)  # X=19
    force(thumb, 0x8080)  # PULL NOBLOCK
    force(thumb, 0xa0c7)  # ISR=OSR
    for _ in range(5):
        force(thumb, 0xa0c7)
        force(thumb, 0x8000)
    engine.load(thumb.elf())
    engine.run(budget=200)
    state = engine.pio()
    assert state.sm[0].osr == 19 and state.sm[0].rx == (19,) * 4
    assert state.sm[0].isr == 0 and state.fdebug & 1


@pytest.mark.parametrize("join,capacity,rx", [(1, 8, False), (2, 8, True), (3, 0, False)])
def test_fifo_join_capacity_and_disabled_fifo_flags(engine, thumb, join, capacity, rx):
    thumb.write(PIO + 0xd0, 0xc0000 | (join << 30))
    for i in range(9):
        if rx:
            force(thumb, 0x8000)
        else:
            thumb.write(PIO + 0x10, i)
    engine.load(thumb.elf())
    engine.run(budget=200)
    sm = engine.pio().sm[0]
    assert len(sm.rx if rx else sm.tx) == capacity
    fstat = int.from_bytes(engine.peek(PIO + 4), "little")
    assert fstat & (1 << (16 if rx else 0))  # disabled opposite FIFO full
    assert fstat & (1 << (24 if rx else 8))  # and empty


def test_autopull_autopush_stream_and_tx_stall(engine, thumb):
    thumb.write(PIO + 0x10, 0xab).write(PIO + 0x10, 0xcd)
    start(thumb, [0x6028, 0x4020], shift=0xf0000 | (8 << 25)).op(0xbf30)
    engine.load(thumb.elf())
    assert run_to(engine, 300_000) == Stop.DEADLINE
    sm = engine.pio().sm[0]
    assert sm.rx == (0xab, 0xcd) and not sm.tx
    assert sm.stalled == 2 and engine.pio().fdebug & (1 << 24)


def test_autopush_stall_does_not_shift_input_twice(engine, thumb):
    force(thumb, 0xe021)
    start(thumb, [0x4021], shift=0xd0000 | (1 << 20))
    for _ in range(20):
        thumb.op(0xbf00)
    thumb.read(PIO + 0x20, 3).op(0xbf00).op(0xbf00)
    engine.load(thumb.elf())
    engine.run(budget=1000)
    sm = engine.pio().sm[0]
    assert sm.rx == (0x80000000,) * 4
    assert sm.isr == 0x80000000 and sm.in_count == 1 and sm.stalled == 3


def test_forced_blocking_instruction_stalls_while_disabled_and_restart_cancels(engine, thumb):
    force(thumb, 0x80a0)
    thumb.op(0xbe00).write(PIO + 0x10, 0x12345678).op(0xbf00)
    thumb.op(0xbe00)
    force(thumb, 0x80a0)
    thumb.write(PIO, 1 << 4)
    engine.load(thumb.elf())
    engine.run(budget=200)
    assert int.from_bytes(engine.peek(PIO + 0xcc), "little") & (1 << 31)
    engine.run(budget=200)
    assert engine.pio().sm[0].osr == 0x12345678
    assert not engine.pio().sm[0].exec_pending
    engine.run(budget=200)
    assert not engine.pio().sm[0].exec_pending


def test_exec_from_fifo_runs_on_following_cycle_and_jumps(engine, thumb):
    thumb.write(PIO + 0x10, 0xe037)  # SET X,23 encoded as FIFO data
    start(thumb, [0x80a0, 0x60e0, 0x0002]).op(0xbf30)  # PULL; OUT EXEC,32; JMP 2
    engine.load(thumb.elf())
    run_to(engine, 200_000)
    sm = engine.pio().sm[0]
    assert sm.x == 23 and sm.pc == 2 and not sm.exec_pending


@pytest.mark.parametrize("instruction", [0x4081, 0xe060, 0xa03b, 0x2060, 0xc080])
def test_reserved_instructions_fail_with_pio_diagnostic(engine, thumb, instruction):
    force(thumb, instruction)
    engine.load(thumb.elf())
    with pytest.raises(SimulationError, match="Unsupported PIO0 SM0 instruction"):
        engine.run(budget=100)


def test_unsupported_sticky_output_is_explicit(engine, thumb):
    thumb.write(PIO + 0xcc, 1 << 17)
    engine.load(thumb.elf())
    with pytest.raises(SimulationError, match="OUT_STICKY"):
        engine.run(budget=100)


def test_irq_relative_wait_flags_and_cpu_irq_routing(engine, thumb):
    force(thumb, 0xc011, sm=2)  # IRQ 1 REL -> flag3
    thumb.write(PIO + 0x12c, 1 << 11)
    thumb.op(0xbe00)
    force(thumb, 0x20d1, sm=2)  # WAIT 1 IRQ 1 REL, clears flag3
    engine.load(thumb.elf())
    engine.run(budget=200)
    assert engine.pio().irq == 8
    assert int.from_bytes(engine.peek(PIO + 0x134), "little") == 1 << 11
    assert int.from_bytes(engine.peek(0xe000e200), "little") & (1 << 7)
    engine.run(budget=200)
    assert engine.pio().irq == 0 and engine.pio().sm[2].stalled == 0


@pytest.mark.parametrize("block,line", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_pio_irq_wait_wakes_cpu_and_resumes_after_handler(engine, tmp_path, block, line):
    try:
        compiler()
    except RuntimeError:
        pytest.skip("ARM cross compiler not installed")
    source = tmp_path / "pio_irq.c"
    irq = 7 + block * 2 + line
    base = PIO + block * 0x100000
    source.write_text(f"""
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define PIO {base}u
volatile u32 count;
void reset_handler(void); void handler(void);
__attribute__((section(".vectors"),used)) const u32 vectors[{17 + irq}] = {{
 [0]=0x20042000, [1]=(u32)reset_handler, [{16 + irq}]=(u32)handler }};
void handler(void) {{ REG(PIO+0x30)=1; count++; }}
void reset_handler(void) {{
 REG(PIO+0x48)=0xc020; REG(PIO+0x4c)=0xe029; REG(PIO+0x50)=2;
 REG(PIO+0xc8)=50u<<16; REG(PIO+0xcc)=2u<<12;
 REG(PIO+{0x12c + line * 12})=0x100;
 REG(0xe000e100)=1u<<{irq}; REG(PIO)=0x101;
 while (!count) __asm volatile("wfi");
 for (volatile u32 n=0;n<100;n++) {{}}
 __asm volatile("bkpt 0"); for (;;) {{}}
}}
""")
    output = tmp_path / "pio_irq.elf"
    build(source, output, ROOT / "firmware/fixtures/direct.ld")
    engine.load(output)
    assert engine.run(5_000_000, budget=2000) == Stop.BREAKPOINT
    assert int.from_bytes(engine.peek(0x20000000), "little") == 1
    state = engine.pio(block)
    assert state.sm[0].x == 9 and state.sm[0].pc == 2 and state.irq == 0


def test_restart_preserves_scratch_pc_and_osr_but_clears_pending_state(engine, thumb):
    force(thumb, 0xe031)  # SET X,17
    force(thumb, 0xa0e1)  # MOV OSR,X
    force(thumb, 0x0007)  # JMP 7
    force(thumb, 0x2080)  # WAIT 1 GPIO0, forced and stalled
    thumb.write(PIO, 1 << 4)
    engine.load(thumb.elf())
    engine.run(budget=200)
    sm = engine.pio().sm[0]
    assert (sm.x, sm.osr, sm.pc, sm.out_count) == (17, 17, 7, 32)
    assert not sm.exec_pending and not sm.stalled


def test_mov_reverse_in_left_out_left_and_out_isr_count(engine, thumb):
    force(thumb, 0xe03f)
    force(thumb, 0xa031)  # MOV X,::X -> f8000000
    thumb.op(0xbe00).write(PIO + 0xd0, 0)  # left shifts
    force(thumb, 0xa0e1)  # MOV OSR,X
    force(thumb, 0x60c5)  # OUT ISR,5 -> 31, in_count=5
    force(thumb, 0x40c1)  # IN ISR,1 -> 63
    engine.load(thumb.elf())
    engine.run(budget=200)
    assert engine.pio().sm[0].x == 0xf8000000
    engine.run(budget=200)
    sm = engine.pio().sm[0]
    assert sm.isr == 63 and sm.in_count == 6 and sm.osr == 0 and sm.out_count == 5


def test_set_and_out_pin_mapping_wrap_and_pindir_side_set(engine, thumb):
    thumb.write(0x40014004, 6).write(0x40014004 + 29 * 8, 6)
    # OUT spans 29,30,31,0; unbonded 30/31 ignored. Side-set writes GP0 direction.
    thumb.write(PIO + 0xdc, 29 | (4 << 20) | (1 << 29))
    thumb.write(PIO + 0xcc, 0x2001f000)
    force(thumb, 0xf020)  # SET X,0 side1 -> GP0 direction enabled
    force(thumb, 0xe03f)  # X=31 side0 -> GP0 direction disabled
    force(thumb, 0xa0e1)  # MOV OSR,X
    force(thumb, 0x6084)  # OUT PINDIRS,4 -> enables GP29 and GP0
    force(thumb, 0xa0e1)
    force(thumb, 0x6004)  # OUT PINS,4
    engine.load(thumb.elf())
    engine.run(budget=200)
    assert engine.pio().out == (1 << 29) | 1
    # The final OUT's side0 overrides GP0 direction, while GP29 remains enabled.
    assert engine.pio().oe == 1 << 29
    assert engine.snapshot().pin(29) == Drive.HIGH
    assert engine.snapshot().pin(0) == Drive.LOW  # pad pull-down, PIO OE=0


def test_snapshot_and_debug_peeks_never_pop_or_clear_flags(engine, thumb):
    force(thumb, 0x8000)  # RX gets a zero
    thumb.read(PIO + 0x20).read(PIO + 0x20)  # drain and underflow
    engine.load(thumb.elf())
    engine.run(budget=100)
    state = engine.pio()
    assert state.fdebug == 1 << 8
    assert engine.peek(PIO + 0x20) == b"\0" * 4
    assert engine.pio() == state
    with pytest.raises(SimulationError):
        engine.peek(PIO + 0x300)
    assert engine.pio() == state


def test_diagnostic_survives_invalid_pio_instruction_during_cpu_sleep(engine, thumb):
    start(thumb, [0xe060], div=0x20000).op(0xbf30)
    engine.load(thumb.elf())
    with pytest.raises(SimulationError, match="Unsupported PIO0 SM0 instruction"):
        run_to(engine, 100_000)


def test_bundled_pio_waveform_and_deterministic_reset():
    simulation = Simulation(Project.pio_blink())
    try:
        simulation.load(demo.image("pio_blink"))
        def capture():
            events = []
            while simulation.engine.snapshot().time_ns < 1_100_000_000:
                assert simulation.run(1_100_000_000, budget=1000) in (Stop.BUDGET, Stop.DEADLINE)
                events.extend(simulation.engine.events())
            return events
        first = capture()
        edges = [e for e in first if e.kind == 1 and e.source == 25]
        assert len(edges) >= 5
        assert [b.time_ns - a.time_ns for a, b in zip(edges[1:], edges[2:])] == [250_000_000] * (len(edges) - 2)
        assert simulation.engine.snapshot().instructions < 100
        simulation.reset()
        assert capture() == first
    finally:
        simulation.close()


@pytest.mark.parametrize("control,first_edge_ns", [(1, 8_000), (0x101, 10_000)])
def test_disabled_divider_free_runs_and_restart_sets_phase(engine, thumb, control, first_edge_ns):
    thumb.write(0x40014004, 6).write(PIO + 0x48, 0xe001)
    thumb.write(PIO + 0xcc, 0).write(PIO + 0xdc, 1 << 26)
    force(thumb, 0xe081)
    thumb.write(PIO + 0xc8, 4 << 16).write(PIO, 0x100).op(0xbe00)
    thumb.op(0xbf00).op(0xbf00).op(0xbf00).write(PIO, control).op(0xbf30)
    engine.load(thumb.elf())
    engine.run(budget=1000)
    anchor = engine.snapshot().time_ns
    engine.events()
    assert run_to(engine, anchor + 100_000) == Stop.DEADLINE
    high = [e for e in engine.events() if e.kind == 1 and e.source == 0 and e.value == 1]
    assert high[0].time_ns - anchor == first_edge_ns
