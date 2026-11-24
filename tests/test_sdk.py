"""Pinned SDK workloads plus independent startup, control-plane and ROM checks."""
import json
import struct

import pytest

from conftest import Thumb
from rp2040_sim import demo
from rp2040_sim.cli import main
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Drive, SimulationError, Stop
from rp2040_sim.project import Project

RAM = 0x20000000
RESET = 0x4000c000
XOSC = 0x40024000
PLL = 0x40028000
CLOCKS = 0x40008000
SIO = 0xd0000000


def word(engine, address):
    return int.from_bytes(engine.peek(address), 'little')


def sdk_image(image):
    """Relocate a one-segment Thumb test behind a synthetic boot2 prefix."""
    image = bytearray(image)
    offset = struct.unpack_from('<I', image, 56)[0]
    size = struct.unpack_from('<I', image, 68)[0]
    entry = struct.unpack_from('<I', image, 24)[0] + 256
    struct.pack_into('<I', image, 24, entry)
    struct.pack_into('<II', image, 68, size + 256, size + 256)
    struct.pack_into('<I', image, offset + 4, entry)
    image[offset:offset] = bytes(256)
    return image


def run_to(engine, deadline=1_100_000_000, budget=10000):
    for _ in range(20000):
        result = engine.run(deadline, budget)
        if result != Stop.BUDGET:
            return result
    pytest.fail('SDK test exceeded bounded execution')


def test_sdk_mode_is_explicit_and_reset_vector_bypasses_elf_debug_entry(engine):
    image = sdk_image(Thumb().op(0x202a).elf())  # movs r0,42
    with pytest.raises(SimulationError):
        engine.load(bytes(image))
    # ELF entry points at BKPT; reset vector points at MOV. SDK crt0 has a
    # different debugger entry too; executing the ELF entry would miss MOV.
    struct.pack_into('<I', image, 24, 0x1000010b)
    engine.load(bytes(image), startup='sdk')
    assert engine.cores().core[0].vtor == 0x10000100
    assert engine.run(budget=20) == Stop.BREAKPOINT
    assert engine.snapshot().registers[0] == 42
    assert engine.peek(0x10, 4) == b'Mu\x01\x03'
    engine.load(demo.image())
    with pytest.raises(SimulationError):
        engine.peek(0)
    with pytest.raises(ValueError, match='startup|Startup'):
        engine.load(demo.image(), startup='unsupported')


@pytest.mark.parametrize('budget', [1, 37, 10000])
def test_real_sdk_crt0_data_bss_vectors_sleep_and_replay(engine, budget):
    image = demo.image('sdk_blink')
    engine.load(image, startup='sdk')
    # Locate the initialized RAM segment independently of fixture symbol layout.
    phoff = struct.unpack_from('<I', image, 28)[0]
    phcount = struct.unpack_from('<H', image, 44)[0]
    segments = [struct.unpack_from('<8I', image, phoff + i * 32) for i in range(phcount)]
    data = next(p for p in segments if p[2] >= RAM and p[4])
    assert engine.peek(data[2], data[4]) == bytes(data[4])
    assert engine.peek(data[3], data[4]) == image[data[1]:data[1] + data[4]]
    assert run_to(engine, budget=budget) == Stop.DEADLINE
    first = engine.events()
    edges = [e for e in first if e.kind == 1 and e.source == 25]
    assert [e.value for e in edges] == [1, 0, 1, 0, 1]
    assert edges[0].time_ns == 2_458_000
    assert all(250_000_000 <= b.time_ns - a.time_ns <= 250_250_000 for a, b in zip(edges, edges[1:]))
    initialized_offset = image[data[1]:data[1] + data[4]].index(struct.pack('<I', 0x12345678))
    assert word(engine, data[2] + initialized_offset) == 0x12345678
    assert engine.cores().core[0].vtor == RAM
    assert engine.snapshot().clock_hz == 1_000_000
    assert word(engine, PLL) & (1 << 31)
    assert word(engine, CLOCKS + 0x44) == 2  # SYS selects PLL_SYS
    engine.reset()
    assert engine.peek(data[2], data[4]) == bytes(data[4])
    assert run_to(engine, budget=10000) == Stop.DEADLINE
    assert engine.events() == first


def test_real_sdk_uart_stdio(engine):
    engine.load(demo.image('sdk_uart'), startup='sdk')
    assert run_to(engine, 10_000_000) == Stop.DEADLINE
    assert bytes(e.value for e in engine.events() if e.kind == 2) == b'Pico SDK UART ready\r\n'
    assert word(engine, 0x40034048) == 3


@pytest.mark.parametrize('code,address,first', [(0x4653, 0x800, 0x401), (0x4453, 0x900, 0x501)])
def test_sdk_float_descriptors_initialize_but_arithmetic_requires_real_rom(engine, code, address, first):
    from conftest import Thumb
    thumb = Thumb()
    rom_call(thumb, 0x201, 0x180, code).op(0xbe00)
    engine.load(bytes(sdk_image(thumb.elf())), startup='sdk')
    assert engine.run(budget=100) == Stop.BREAKPOINT
    assert engine.snapshot().registers[0] == address
    assert engine.peek(0x7fe, 1) == b'\x20'
    assert word(engine, address) == first
    assert word(engine, address + 31 * 4) == first + 31 * 4
    thumb = Thumb()
    rom_call(thumb, first, 0, 0).op(0xbe00)
    engine.load(bytes(sdk_image(thumb.elf())), startup='sdk')
    with pytest.raises(SimulationError, match='floating-point.*use ROM startup'):
        engine.run(budget=100)


@pytest.mark.parametrize('change', ['physical_overlap', 'physical_outside', 'ram_lma', 'reset_thumb', 'entry_thumb', 'stack', 'vectors'])
def test_sdk_elf_rejects_invalid_layout(engine, change):
    image = bytearray(demo.image('sdk_blink'))
    if change == 'physical_overlap':
        struct.pack_into('<I', image, 52 + 32 + 12, 0x10000000)
    elif change == 'physical_outside':
        struct.pack_into('<I', image, 52 + 32 + 12, 0x101fffff)
    elif change == 'ram_lma':
        struct.pack_into('<I', image, 52 + 32 + 12, RAM + 4)
    elif change == 'entry_thumb':
        struct.pack_into('<I', image, 24, 0x10000108)
    elif change == 'vectors':
        struct.pack_into('<I', image, 52 + 16, 128)
    else:
        offset = struct.unpack_from('<I', image, 56)[0] + 256
        struct.pack_into('<I', image, offset + (4 if change == 'reset_thumb' else 0),
                         0x10000108 if change == 'reset_thumb' else RAM + 3)
    with pytest.raises(SimulationError):
        engine.load(bytes(image), startup='sdk')


def test_clock_ready_depends_on_xosc_pll_power_and_reset(engine, thumb):
    thumb.write(CLOCKS + 0x3c, 1).read(CLOCKS + 0x44, 2)  # SYS aux=PLL_SYS, not ready
    thumb.write(XOSC, 0xfabaa0).write(PLL + 8, 125).write(PLL + 4, 0)
    thumb.read(PLL, 3).read(CLOCKS + 0x44, 4)
    thumb.write(RESET + 0x2000, 1 << 12).read(PLL, 5).read(CLOCKS + 0x44, 6)
    thumb.write(PLL + 8, 125)  # ignored while held in reset
    thumb.write(RESET + 0x3000, 1 << 12)
    engine.load(thumb.elf())
    assert engine.run(budget=300) == Stop.BREAKPOINT
    regs = engine.snapshot().registers
    assert regs[2] == regs[6] == 0
    assert regs[3] == 0x80000001 and regs[4] == 2
    assert regs[5] == 1 and word(engine, PLL + 8) == 0
    assert word(engine, RESET + 8) == 0x1ffffff


def test_clock_aliases_masks_tick_and_fixed_functional_rate(engine, thumb):
    thumb.write(CLOCKS + 0x34, 0xffffffff).write(CLOCKS + 0x2030, 2)
    thumb.write(0x4005802c, 0x20c).write(0x4005a00c, 0x1234)
    engine.load(thumb.elf())
    engine.run(budget=100)
    assert word(engine, CLOCKS + 0x34) == 0x300
    assert word(engine, CLOCKS + 0x30) == 2
    assert word(engine, 0x4005802c) == 0x60c
    assert word(engine, 0x4005800c) == 0x1234
    state = engine.snapshot()
    assert state.time_ns == state.instructions * 1000
    engine.reset()
    assert word(engine, CLOCKS + 0x34) == 0x100
    assert word(engine, 0x4005800c) == 0


def test_peripheral_reset_keeps_sio_and_external_connections(engine, thumb):
    thumb.write(0x40014004, 5).write(SIO + 0x20, 1).write(SIO + 0x10, 1)
    thumb.write(RESET, (1 << 5) | (1 << 8) | (1 << 22))
    thumb.write(0x40014004, 5).write(0x40034030, 0x301)  # held writes ignored
    thumb.op(0xbe00).write(RESET, 0).write(0x40014004, 5)
    engine.load(thumb.elf())
    engine.connect_gpio(1, 0)
    engine.gpio(1, Drive.PULL_HIGH)
    engine.run(budget=200)
    assert word(engine, 0x40014004) == 31
    assert word(engine, 0x4001c004) == 0x56
    assert word(engine, 0x40034030) == 0x300
    assert engine.snapshot().gpio_oe == engine.snapshot().gpio_out == 1
    engine.uart(b'x')
    engine.run(budget=0)
    assert word(engine, 0x40034018) & 0x10
    engine.run(budget=200)
    assert engine.snapshot().pin(0) == engine.snapshot().pin(1) == Drive.HIGH


@pytest.mark.parametrize('address,value,diagnostic', [
    (CLOCKS + 0x48, 0x400, 'Clock phase'), (CLOCKS + 0x78, 1, 'resuscitation'),
    (0x40058000, 1 << 30, 'Unsupported MMIO'), (0x5011004c, 1, 'USB transceiver'),
    (RESET + 4, 1, 'Watchdog reset'), (0x40058004, 1, 'Unsupported MMIO'),
])
def test_unsupported_system_modes_are_explicit(engine, thumb, address, value, diagnostic):
    engine.load(thumb.write(address, value).elf())
    with pytest.raises(SimulationError, match=diagnostic):
        engine.run(budget=100)


@pytest.mark.parametrize('signed,a,b,q,r', [
    (False, 27, 4, 6, 3), (True, -27, 4, -6, -3), (True, 27, -4, -6, 3),
    (True, -0x80000000, -1, 0x80000000, 0), (False, 7, 0, -1, 7),
    (True, -7, 0, 1, -7), (True, 7, 0, -1, 7),
])
def test_sio_divider_signed_overflow_zero_and_dirty(engine, thumb, signed, a, b, q, r):
    base = SIO + (0x68 if signed else 0x60)
    thumb.write(base, a & 0xffffffff).write(base + 4, b & 0xffffffff)
    thumb.op(0xbe00).read(SIO + 0x70)
    engine.load(thumb.elf())
    engine.run(budget=100)
    assert word(engine, SIO + 0x70) == q & 0xffffffff
    assert word(engine, SIO + 0x74) == r & 0xffffffff
    assert word(engine, SIO + 0x78) == 3  # debug peeks do not clear dirty
    engine.run(budget=100)
    assert word(engine, SIO + 0x78) == 1


def test_sio_divider_is_core_local_and_manual_restore(engine, tmp_path):
    from test_multicore import program, words
    image = program(tmp_path,
        'REG(SIO+0x60)=27; REG(SIO+0x64)=4; R[0]=REG(SIO+0x70); R[1]=REG(SIO+0x74);',
        'REG(SIO+0x68)=-27; REG(SIO+0x6c)=4; R[2]=REG(SIO+0x70); R[3]=REG(SIO+0x74); '
        'REG(SIO+0x70)=42; REG(SIO+0x74)=17; R[4]=REG(SIO+0x70); R[5]=REG(SIO+0x74);')
    engine.load(image)
    assert run_to(engine, 1_000_000) == Stop.DEADLINE
    assert words(engine, count=6) == (6, 3, 0xfffffffa, 0xfffffffd, 42, 17)
    assert word(engine, SIO + 0x70) == 6


def test_reset_pio_blocks_independently_and_dma(engine, thumb):
    thumb.write(0x50200048, 0xe025).write(0x50300048, 0xe026)
    thumb.write(0x50000008, 99).write(RESET, (1 << 10) | (1 << 2))
    thumb.write(0x50200048, 0xe027).write(0x50000008, 77)
    thumb.write(RESET, 0)
    engine.load(thumb.elf())
    engine.run(budget=200)
    assert engine.pio(0).instructions[0] == 0
    assert engine.pio(1).instructions[0] == 0xe026
    assert engine.dma().channels[0].remaining == 0


def test_rom_aligned_memory_and_zero_length_operations(engine, thumb):
    rom_call(thumb, 0x361, RAM, 0x35, 8)
    rom_call(thumb, 0x371, RAM + 16, RAM, 8)
    rom_call(thumb, 0x341, 0xffffffff, 0xffffffff, 0)
    engine.load(bytes(sdk_image(thumb.elf())), startup='sdk')
    engine.run(budget=200)
    assert engine.peek(RAM, 8) == engine.peek(RAM + 16, 8) == b'5' * 8


def rom_call(thumb, address, a, b=0, n=0):
    thumb.ldr(0, a).ldr(1, b).ldr(2, n).ldr(3, address).op(0x4798)  # blx r3
    return thumb


@pytest.mark.parametrize('address,value,expected', [
    (0x301, 0, 32), (0x301, 0x100, 23), (0x311, 0, 32), (0x311, 0x100, 8),
    (0x321, 0xf0f, 8), (0x331, 0x80000001, 0x80000001), (0x331, 1, 0x80000000),
])
def test_rom_bit_operations(engine, thumb, address, value, expected):
    engine.load(bytes(sdk_image(rom_call(thumb, address, value).elf())), startup='sdk')
    assert engine.run(budget=100) == Stop.BREAKPOINT
    assert engine.snapshot().registers[0] == expected


def test_rom_lookup_memcpy_memset_and_return_values(engine, thumb):
    rom_call(thumb, 0x201, 0x100, 0x434d).op(0x4604)  # save MC function in r4
    rom_call(thumb, 0x351, RAM, 0xab, 16)
    thumb.ldr(0, RAM + 32).ldr(1, RAM).ldr(2, 16).op(0x47a0)  # blx r4
    engine.load(bytes(sdk_image(thumb.elf())), startup='sdk')
    engine.run(budget=200)
    assert engine.peek(RAM, 16) == engine.peek(RAM + 32, 16) == bytes([0xab]) * 16
    assert engine.snapshot().registers[0] == RAM + 32


@pytest.mark.parametrize('address,a,b,n,match', [
    (0x201, 0x100, 0x4653, 0, 'Unsupported ROM lookup'),
    (0x341, RAM, 0xdeadbeef, 4, 'outside mapped memory'),
    (0x351, 0x10000000, 0, 4, 'outside mapped memory'),
    (0x351, RAM, 0, 0xffffffff, 'outside mapped memory'),
    (0x361, RAM + 1, 0, 4, 'word-aligned'),
    (0x371, RAM, RAM + 1, 4, 'word-aligned'),
])
def test_rom_unsupported_calls_and_memory_bounds(engine, thumb, address, a, b, n, match):
    engine.load(bytes(sdk_image(rom_call(thumb, address, a, b, n).elf())), startup='sdk')
    with pytest.raises(SimulationError, match=match):
        engine.run(budget=100)


def test_sdk_project_persistence_recording_and_cli(tmp_path, capsys):
    path = tmp_path / 'sdk.json'
    Project.sdk_blink().save(path)
    project = Project.load(path)
    assert project.startup == 'sdk' and project.builtin == 'sdk_blink'
    simulation = Simulation(project)
    try:
        simulation.load(demo.image(project.builtin))
        assert simulation.run(300_000_000) == Stop.DEADLINE
        first = simulation.engine.events()
        record = tmp_path / 'inputs.json'
        simulation.export_recording(record)
        assert json.loads(record.read_text())['startup'] == 'sdk-elf'
        assert simulation.replay(record, 300_000_000) == first
        simulation.project.startup = 'direct'
        with pytest.raises(ValueError, match='format'):
            simulation.replay(record, 300_000_000)
    finally:
        simulation.close()
    image = tmp_path / 'uart.elf'
    image.write_bytes(demo.image('sdk_uart'))
    assert main(['run', str(image), '--startup', 'sdk', '--duration-ms', '10']) == 0
    assert 'Pico SDK UART ready' in capsys.readouterr().out
