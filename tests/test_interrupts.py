import struct

import pytest

from rp2040_sim.build import ROOT, build, compiler
from rp2040_sim.engine import Drive, Engine, Stop


def compile_source(tmp_path, text):
    try:
        compiler()
    except RuntimeError:
        pytest.skip("ARM cross compiler not installed")
    source = tmp_path / "irq.c"
    source.write_text(text)
    output = tmp_path / "irq.elf"
    build(source, output, ROOT / "firmware/fixtures/direct.ld")
    return output


def test_timer_gpio_irqs_sleep_and_pop_exception_return(tmp_path):
    output = compile_source(tmp_path, (ROOT / "firmware/fixtures/interrupts.c").read_text())
    with Engine() as engine:
        engine.load(output)
        engine.gpio(14, Drive.LOW, time_ns=3_000_000)
        result = engine.run(20_000_000, budget=10000)
        assert result == Stop.BREAKPOINT
        assert struct.unpack("<II", engine.peek(0x20000000, 8)) == (3, 1)
        assert engine.snapshot().registers[13] == 0x20042000
        assert engine.snapshot().xpsr & 0x1ff == 0
        assert engine.snapshot().instructions < 1000


def test_systick_interrupts_and_debug_countflag_peek(tmp_path):
    output = compile_source(tmp_path, """
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 count;
void reset_handler(void); void tick(void);
__attribute__((section(".vectors"),used)) const u32 vectors[16] = {
 [0]=0x20042000, [1]=(u32)reset_handler, [15]=(u32)tick };
void tick(void) { if (++count == 3) REG(0xe000e010)=0; }
void reset_handler(void) {
 REG(0xe000e014)=999; REG(0xe000e018)=0; REG(0xe000e010)=7;
 while (count<3) __asm volatile("wfi");
 __asm volatile("bkpt 0"); for (;;) {}
}
""")
    with Engine() as engine:
        engine.load(output)
        assert engine.run(10_000_000, budget=10000) == Stop.BREAKPOINT
        assert int.from_bytes(engine.peek(0x20000000), "little") == 3
        assert 3_000_000 <= engine.snapshot().time_ns < 3_100_000
        first = engine.peek(0xe000e010)
        assert engine.peek(0xe000e010) == first
        assert int.from_bytes(first, "little") & (1 << 16)


def test_primask_defers_pending_irq(tmp_path):
    output = compile_source(tmp_path, """
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 count, masked_count;
void reset_handler(void); void irq(void);
__attribute__((section(".vectors"),used)) const u32 vectors[17] = {
 [0]=0x20042000, [1]=(u32)reset_handler, [16]=(u32)irq };
void irq(void) { count++; }
void reset_handler(void) {
 __asm volatile("cpsid i");
 REG(0xe000e100)=1; REG(0xe000e200)=1;
 masked_count=count;
 __asm volatile("cpsie i");
 __asm volatile("nop"); __asm volatile("nop");
 __asm volatile("bkpt 0"); for (;;) {}
}
""")
    with Engine() as engine:
        engine.load(output)
        assert engine.run(10_000_000, budget=1000) == Stop.BREAKPOINT
        assert struct.unpack("<II", engine.peek(0x20000000, 8)) == (1, 0)


def test_svc_and_return_to_thread(tmp_path):
    output = compile_source(tmp_path, """
typedef unsigned int u32;
volatile u32 count;
void reset_handler(void); void svc(void);
__attribute__((section(".vectors"),used)) const u32 vectors[12] = {
 [0]=0x20042000, [1]=(u32)reset_handler, [11]=(u32)svc };
void svc(void) { count++; }
void reset_handler(void) { __asm volatile("svc 0"); __asm volatile("bkpt 0"); for (;;) {} }
""")
    with Engine() as engine:
        engine.load(output)
        assert engine.run(10_000_000, budget=1000) == Stop.BREAKPOINT
        assert int.from_bytes(engine.peek(0x20000000), "little") == 1


def test_nested_priorities_restore_handler_and_thread_state(tmp_path):
    output = compile_source(tmp_path, """
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 state[3];
void reset_handler(void); void low(void); void high(void);
__attribute__((section(".vectors"),used)) const u32 vectors[18] = {
 [0]=0x20042000, [1]=(u32)reset_handler, [16]=(u32)low, [17]=(u32)high };
void high(void) { u32 v; __asm volatile("mrs %0, ipsr":"=r"(v)); state[1]=v; }
void low(void) {
 state[0]=1; REG(0xe000e200)=2;
 u32 v; __asm volatile("mrs %0, ipsr":"=r"(v)); state[2]=v;
}
void reset_handler(void) {
 REG(0xe000e400)=0xc0; REG(0xe000e100)=3; REG(0xe000e200)=1;
 __asm volatile("nop"); __asm volatile("bkpt 0"); for (;;) {}
}
""")
    with Engine() as engine:
        engine.load(output)
        assert engine.run(10_000_000, budget=1000) == Stop.BREAKPOINT
        assert struct.unpack("<III", engine.peek(0x20000000, 12)) == (1, 17, 16)
        assert engine.snapshot().xpsr & 511 == 0
        assert engine.snapshot().registers[13] == 0x20042000


def test_psp_exception_frame_returns_to_process_stack(tmp_path):
    output = compile_source(tmp_path, """
typedef unsigned int u32;
volatile u32 count;
void reset_handler(void); void svc(void);
__attribute__((section(".vectors"),used)) const u32 vectors[12] = {
 [0]=0x20042000, [1]=(u32)reset_handler, [11]=(u32)svc };
void svc(void) { count++; }
void reset_handler(void) {
 __asm volatile("msr psp,%0"::"r"(0x20041000):"memory");
 __asm volatile("msr control,%0"::"r"(2):"memory");
 __asm volatile("isb");
 __asm volatile("svc 0"); __asm volatile("bkpt 0"); for (;;) {}
}
""")
    with Engine() as engine:
        engine.load(output)
        assert engine.run(10_000_000, budget=1000) == Stop.BREAKPOINT
        assert int.from_bytes(engine.peek(0x20000000), "little") == 1
        assert engine.snapshot().registers[13] == 0x20041000


def test_sev_then_wfe_consumes_event_without_sleep(engine, thumb):
    engine.load(thumb.op(0xbf40).op(0xbf20).op(0x202a).elf())
    assert engine.run(100_000, budget=100) == Stop.BREAKPOINT
    assert engine.snapshot().registers[0] == 42
