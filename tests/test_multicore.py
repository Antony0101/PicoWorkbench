"""Direct-load dual-core startup, synchronization, and deterministic scheduling."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import struct

import pytest

from rp2040_sim import demo
from rp2040_sim.build import ROOT, build, compiler
from rp2040_sim.engine import Engine, SimulationError, Stop

SIO = 0xd0000000
PSM = 0x40010000
RAM = 0x20000000


def run_to(engine, deadline=5_000_000, budget=1000):
    for _ in range(10000):
        stop = engine.run(deadline, budget)
        if stop != Stop.BUDGET:
            return stop
    pytest.fail('Multicore fixture exceeded bounded execution')


def words(engine, offset=0, count=8):
    return struct.unpack('<' + 'I' * count, engine.peek(RAM + offset, count * 4))


def program(tmp_path, core0, core1='', handlers='', vectors='', launch=True, vtor='0x10000000u', sp='0x20041000u', entry='(u32)core1_entry', core1_returns=False):
    try:
        compiler()
    except RuntimeError:
        pytest.skip('ARM cross compiler not installed')
    source = tmp_path / 'dual.c'
    source.write_text('''
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define R ((volatile u32 *)0x20000000u)
#define SHARED REG(0x20000100u)
#define SIO 0xd0000000u
void reset_handler(void); void core1_entry(void);
''' + handlers + '''
__attribute__((section(".vectors"), used)) const u32 vectors[48] = {
[0]=0x20042000u, [1]=(u32)reset_handler, ''' + vectors + '''};
void push(u32 word) {
    while (!(REG(SIO+0x50)&2)) {}
    REG(SIO+0x54)=word; __asm volatile("sev");
}
u32 pop(void) {
    while (!(REG(SIO+0x50)&1)) __asm volatile("wfe");
    return REG(SIO+0x58);
}
void launch(u32 vtor, u32 sp, u32 entry) {
    const u32 cmd[6]={0,0,1,vtor,sp,entry};
    for (unsigned i=0;i<6;i++) { push(cmd[i]); if (pop()!=cmd[i]) R[15]++; }
}
void core1_entry(void) {
''' + core1 + ('' if core1_returns else '\nfor (;;) __asm volatile("wfe");') + '''
}
void reset_handler(void) {
''' + (f'launch({vtor},{sp},{entry});\n' if launch else '') + core0 + '''
for (;;) __asm volatile("wfi");
}
''')
    output = tmp_path / 'dual.elf'
    build(source, output, ROOT / 'firmware/fixtures/direct.ld')
    return output


def test_reset_state_and_read_only_copied_inspection(engine):
    state = engine.cores()
    assert state.core[0].launched and not state.core[1].launched
    assert state.core[0].registers[13] == 0x20042000
    assert state.core[1].registers == (0,) * 16
    assert all(c.xpsr & (1 << 24) for c in state.core)
    assert state.core[0].instructions == state.core[1].instructions == 0
    assert all(c.fifo_status == 2 and c.rx == () for c in state.core)
    assert state.spinlocks == state.boot_stage == 0
    assert engine.peek(SIO + 0x50) == struct.pack('<I', 2)
    assert engine.peek(SIO + 0x100) == struct.pack('<I', 1)
    assert engine.cores() == state
    with pytest.raises(FrozenInstanceError):
        state.core[0].sleeping = 1


def test_register_banks_cpuid_shared_memory_and_solo_sleep(engine, tmp_path):
    engine.load(program(tmp_path, 'R[0]=REG(SIO);', 'R[1]=REG(SIO); SHARED=0x12345678u;'))
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2) == (0, 1)
    assert words(engine, 256, 1) == (0x12345678,)
    state = engine.cores()
    assert state.boot_stage == 6
    assert state.core[0].sleeping == 1 and state.core[1].sleeping == 2
    assert all(c.xpsr & (1 << 24) for c in state.core)
    assert state.core[0].msp > state.core[1].msp
    assert engine.snapshot().instructions == sum(c.instructions for c in state.core)
    assert engine.snapshot().registers == state.core[0].registers
    assert all(c.instructions > 0 for c in state.core)
    assert engine.peek(SIO) == struct.pack('<I', 0)  # debugger defaults to core 0
    assert engine.cores() == state  # debugger does not change the selected context


@pytest.mark.parametrize('lock', [0, 7, 31])
def test_spinlocks_protect_contended_shared_sram(engine, tmp_path, lock):
    address = SIO + 0x100 + 4 * lock
    body = f'for (unsigned i=0;i<60;i++) {{ while (!REG({address}u)) {{}} u32 v=SHARED; SHARED=v+1; REG({address}u)=0; }}'
    engine.load(program(tmp_path, body + 'while (!R[1]) {} R[0]=SHARED;', body + 'R[1]=1;'))
    assert run_to(engine, 50_000_000) == Stop.DEADLINE
    assert words(engine, count=2) == (120, 1)
    assert engine.cores().spinlocks == 0


def test_spinlock_peeks_do_not_claim_and_writes_release(engine, thumb):
    thumb.read(SIO + 0x17c, 2).read(SIO + 0x17c, 3).op(0xbe00)
    thumb.write(SIO + 0x17c, 999).op(0xbe00)
    engine.load(thumb.elf())
    engine.run(budget=100)
    assert engine.snapshot().registers[2:4] == (1 << 31, 0)
    state = engine.cores()
    assert state.spinlocks == 1 << 31 and state.spinlock_owner[31] == 1
    assert engine.peek(SIO + 0x5c) == struct.pack('<I', 1 << 31)
    assert engine.peek(SIO + 0x17c) == bytes(4)
    assert engine.cores() == state
    engine.run(budget=100)
    assert engine.cores().spinlocks == 0 and engine.cores().spinlock_owner[31] == 0


def test_fifo_order_capacity_errors_and_nondestructive_debug(engine, tmp_path):
    core1 = 'R[2]=REG(SIO+0x58); R[3]=REG(SIO+0x50); R[10]=1; for (;;) __asm volatile("wfi");'
    core0 = 'while (!R[10]) {} for (u32 i=0;i<10;i++) REG(SIO+0x54)=100+i; R[0]=REG(SIO+0x50); __asm volatile("bkpt 0"); REG(SIO+0x50)=4;'
    engine.load(program(tmp_path, core0, core1))
    assert run_to(engine) == Stop.BREAKPOINT
    state = engine.cores()
    assert state.core[0].fifo_status == 4  # full TX, WOF
    assert state.core[1].fifo_status == 11  # RX valid, TX ready, ROE
    assert state.core[1].rx == tuple(range(100, 108))
    assert engine.peek(SIO + 0x58) == bytes(4)
    assert engine.cores() == state
    assert run_to(engine) == Stop.DEADLINE
    assert engine.cores().core[0].fifo_status == 0


@pytest.mark.parametrize('receiver', [0, 1])
def test_each_fifo_interrupt_wakes_its_receiving_core(engine, tmp_path, receiver):
    irq = 15 + receiver
    handler = f'void fifo_handler(void) {{ while(REG(SIO+0x50)&1) R[{receiver}]=REG(SIO+0x58); }}'
    setup = f'REG(0xe000e280u)=1u<<{irq}; REG(0xe000e100u)=1u<<{irq}; R[10]=1; for (;;) __asm volatile("wfi");'
    sender = 'while (!R[10]) {} push(0x1234);'
    engine.load(program(tmp_path, setup if receiver == 0 else sender,
                        sender if receiver == 0 else setup,
                        handlers=handler, vectors=f'[{16+irq}]=(u32)fifo_handler,'))
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2)[receiver] == 0x1234
    state = engine.cores()
    assert state.core[receiver].nvic_enable == 1 << irq
    assert not state.core[1 - receiver].nvic_enable
    assert state.core[receiver].rx == ()


def test_sev_wakes_other_core_without_enabled_interrupts(engine, tmp_path):
    core1 = 'R[10]=1; while (!SHARED) __asm volatile("wfe"); R[1]=SHARED;'
    core0 = 'while (!R[10]) {} SHARED=42; __asm volatile("sev");'
    engine.load(program(tmp_path, core0, core1))
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2)[1] == 42
    assert not any(c.nvic_enable for c in engine.cores().core)


def test_core1_systick_vtor_and_primask_are_private(engine, tmp_path):
    handler = 'void tick(void) { R[1]++; if(R[1]==3) REG(0xe000e010u)=0; }'
    core0 = 'REG(0x20001000u+15*4)=(u32)tick; __asm volatile("cpsid i"); launch(0x20001000u,0x20041000u,(u32)core1_entry);'
    core1 = 'REG(0xe000e014u)=99; REG(0xe000e018u)=0; REG(0xe000e010u)=7; for (;;) __asm volatile("wfi");'
    engine.load(program(tmp_path, core0, core1, handlers=handler, launch=False))
    assert run_to(engine) == Stop.DEADLINE
    state = engine.cores()
    assert words(engine, count=2)[1] == 3
    assert state.core[0].vtor == 0x10000000 and state.core[1].vtor == 0x20001000
    assert state.core[0].primask == 1 and state.core[1].primask == 0
    assert state.core[0].systick_load == 0 and state.core[1].systick_load == 99


def test_core1_svc_uses_its_process_stack_and_restores_thread_mode(engine, tmp_path):
    handler = 'void svc_handler(void) { R[1]=0xabcdef; }'
    core1 = '__asm volatile("ldr r0,=0x20040000; msr psp,r0; movs r0,#2; msr control,r0; isb; svc 0" ::: "r0", "memory"); R[2]=123;'
    engine.load(program(tmp_path, '', core1, handlers=handler, vectors='[11]=(u32)svc_handler,'))
    assert run_to(engine) == Stop.DEADLINE
    state = engine.cores()
    assert words(engine, count=3)[1:] == (0xabcdef, 123)
    assert state.core[1].control & 2 and state.core[1].psp == 0x20040000
    assert state.core[1].xpsr & 0x1ff == 0
    assert state.core[0].psp == 0 and state.core[0].control == 0


def test_core1_gpio_interrupt_bank_is_independent(engine, tmp_path):
    handler = 'void gpio_handler(void) { REG(0x400140f0u)=8; R[1]++; }'
    core1 = 'REG(0x40014130u)=8; REG(0xe000e100u)=1u<<13; for (;;) __asm volatile("wfi");'
    engine.load(program(tmp_path, '', core1, handlers=handler, vectors='[29]=(u32)gpio_handler,'))
    from rp2040_sim.engine import Drive
    engine.gpio(0, Drive.LOW, 0)
    engine.gpio(0, Drive.HIGH, 2_000_000)
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2)[1] == 1
    assert engine.peek(0x40014100) == bytes(4)
    assert engine.peek(0x40014130) == struct.pack('<I', 8)
    assert not engine.cores().core[0].irq_levels & (1 << 13)


def test_psm_core1_reset_relaunch_preserves_shared_sio_and_sram(engine, tmp_path):
    core1 = 'R[1]++; R[3]=REG(0xe000e100u); R[4]=REG(0xe000e014u); REG(0xe000e100u)=1u<<31; REG(0xe000e014u)=123;'
    core0 = '''while (!R[1]) {} (void)REG(SIO+0x10c); SHARED=77;
REG(0x40012004u)=0x10000; REG(0x40013004u)=0x10000; R[0]=pop();
launch(0x10000000u,0x20041000u,(u32)core1_entry); while(R[1]<2) {}'''
    engine.load(program(tmp_path, core0, core1))
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=5)[:2] == (0, 2)
    assert words(engine, count=5)[3:] == (0, 0)
    state = engine.cores()
    assert state.core[1].launched and not state.proc1_off
    assert state.spinlocks == 8 and state.spinlock_owner[3] == 1
    assert words(engine, 256, 1) == (77,)
    assert engine.snapshot().instructions == sum(c.instructions for c in state.core)


@pytest.mark.parametrize('vtor,sp,entry', [('0x10000001u','0x20041000u','(u32)core1_entry'),
    ('0x12340000u','0x20041000u','(u32)core1_entry'), ('0x10000000u','0x20041001u','(u32)core1_entry'),
    ('0x10000000u','0x10041000u','(u32)core1_entry'), ('0x10000000u','0x20041000u','0x10000000u'),
    ('0x10000000u','0x20041000u','0x12345679u')])
def test_invalid_launch_is_diagnostic(engine, tmp_path, vtor, sp, entry):
    engine.load(program(tmp_path, '', vtor=vtor, sp=sp, entry=entry))
    with pytest.raises(SimulationError, match='Invalid core 1 launch'):
        run_to(engine)


def test_core1_breakpoint_stops_both_and_step_is_one_total_instruction(engine, tmp_path):
    engine.load(program(tmp_path, '', 'R[1]=1; __asm volatile("bkpt 0"); R[1]=2;'))
    assert run_to(engine) == Stop.BREAKPOINT
    assert engine.cores().active_core == 1 and words(engine, count=2)[1] == 1
    before = engine.snapshot().instructions
    engine.step()
    assert engine.snapshot().instructions == before + 1
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2)[1] == 2


def test_returning_core1_entry_parks_without_rom_execution(engine, tmp_path):
    engine.load(program(tmp_path, '', 'R[1]=9;', core1_returns=True))
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2)[1] == 9
    assert engine.cores().core[1].sleeping == 3


@pytest.mark.parametrize('budget', [1, 7, 1000])
def test_scheduling_is_independent_of_batch_budget_and_reset(engine, tmp_path, budget):
    core1 = 'for(u32 n=0;n<10;n++) { push(pop()+1); }'
    core0 = 'for(u32 n=0;n<10;n++) { push(n); R[n]=pop(); }'
    image = program(tmp_path, core0, core1)
    engine.load(image)
    assert run_to(engine, budget=1000) == Stop.DEADLINE
    expected = (engine.cores(), engine.snapshot(), engine.events(), engine.peek(RAM, 40))
    engine.reset()
    assert run_to(engine, budget=budget) == Stop.DEADLINE
    actual = (engine.cores(), engine.snapshot(), engine.events(), engine.peek(RAM, 40))
    assert actual == expected
    assert words(engine, count=10) == tuple(range(1, 11))


def test_global_instruction_budget_is_exact_and_runnable_cores_alternate(engine, tmp_path):
    engine.load(program(tmp_path, 'for (;;) R[0]++;', 'for (;;) R[1]++;'))
    assert engine.run(budget=1000) == Stop.BUDGET
    before = engine.cores()
    assert all(c.launched and not c.sleeping for c in before.core)
    assert engine.run(budget=37) == Stop.BUDGET
    after = engine.cores()
    deltas = [b.instructions - a.instructions for a, b in zip(before.core, after.core)]
    assert sum(deltas) == 37 and abs(deltas[0] - deltas[1]) == 1
    assert engine.snapshot().instructions == 1037


def test_core1_device_request_yields_both_cores_until_response(engine, tmp_path):
    core1 = 'REG(0x4003c000u)=7; REG(0x4003c010u)=2; REG(0x4003c004u)=2; REG(0x4003c008u)=0xa5; R[1]=REG(0x4003c008u);'
    engine.load(program(tmp_path, 'while (!R[1]) {}', core1))
    assert run_to(engine) == Stop.DEVICE_REQUEST
    request = engine.device_request()
    assert request.bus == 1 and request.controller == 0 and request.value == 0xa5
    state = engine.cores()
    assert engine.run(budget=100) == Stop.DEVICE_REQUEST
    assert engine.cores() == state
    engine.complete_device(request, 0x5a, True)
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2)[1] == 0x5a


def test_dma_started_by_core1_uses_shared_bus_while_both_cores_sleep(engine, tmp_path):
    core1 = '''REG(0x20000080u)=0x12345678;
REG(0x50000000u)=0x20000080; REG(0x50000004u)=0x20000084;
REG(0x50000008u)=1; REG(0x5000000cu)=1|(2u<<2)|(63u<<15);'''
    engine.load(program(tmp_path, '', core1))
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, 128, 2) == (0x12345678, 0x12345678)
    assert engine.dma().intr == 1


def test_unsupported_core0_psm_reset_is_explicit(engine, thumb):
    engine.load(thumb.write(PSM + 4, 1 << 15).elf())
    with pytest.raises(SimulationError, match='PSM PROC1'):
        engine.run(budget=100)


def test_bad_handshake_prefix_restarts_and_echoes_zero(engine, thumb):
    thumb.write(SIO + 0x54, 77).op(0xbe00)
    engine.load(thumb.elf())
    assert engine.run(budget=100) == Stop.BREAKPOINT
    state = engine.cores()
    assert state.boot_stage == 0 and not state.core[1].launched
    assert state.core[0].rx == (0,)
    assert engine.peek(SIO + 0x58) == bytes(4)
    assert engine.cores() == state


def test_bundled_two_core_demo_replay_and_project_persistence(tmp_path):
    from rp2040_sim.controller import Simulation
    from rp2040_sim.project import Project
    project = Project.multicore()
    path = tmp_path / 'dual.json'
    project.save(path)
    sim = Simulation(Project.load(path))
    try:
        sim.load(demo.image('multicore'))
        captures = []
        for _ in range(2):
            for _ in range(100):
                stop = sim.run(1_100_000_000, budget=1000)
                if stop != Stop.BUDGET:
                    break
            assert stop == Stop.DEADLINE
            events = sim.engine.events()
            text = ''.join(chr(e.value) for e in events if e.kind == 2)
            assert text == ''.join(f'Core 1 count: {i:08x}\n' for i in range(1, 5))
            edges = [e for e in events if e.kind == 1 and e.source == 25]
            assert len([e for e in edges if e.value == 1]) == 2
            assert all(249_000_000 <= b.time_ns - a.time_ns <= 251_000_000 for a, b in zip(edges[1:], edges[2:]))
            state = sim.engine.cores()
            assert state.core[1].launched and state.core[1].instructions > 100
            assert state.spinlocks == 0 and all(c.rx == () for c in state.core)
            assert sim.engine.snapshot().instructions < 2000
            captures.append((state, events))
            sim.reset()
            assert not sim.engine.cores().core[1].launched
        assert captures[0] == captures[1]
    finally:
        sim.close()


def test_pc_breakpoint_on_core1_entry_and_resume(engine, tmp_path):
    from rp2040_sim.symbols import Symbols
    image = program(tmp_path, '', 'R[1]=55;')
    address = Symbols(image.read_bytes()).names['core1_entry']
    engine.load(image)
    engine.breakpoint(address)
    assert run_to(engine) == Stop.BREAKPOINT
    assert engine.cores().core[1].registers[15] == address
    assert words(engine, count=2)[1] == 0
    before = engine.snapshot().instructions
    assert engine.step() == Stop.BUDGET
    assert engine.snapshot().instructions == before + 1
    engine.breakpoint(address, False)
    assert run_to(engine) == Stop.DEADLINE
    assert words(engine, count=2)[1] == 55
