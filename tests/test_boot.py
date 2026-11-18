"""UF2 parser robustness and real B2 ROM -> SSI -> boot2 -> SDK execution."""
from pathlib import Path
import struct

import pytest

from rp2040_sim import demo
from rp2040_sim.engine import Engine, SimulationError, Stop, ffi, lib
from rp2040_sim.images import bootrom, elf_flash, uf2
from rp2040_sim.project import Project
from rp2040_sim.controller import Simulation


@pytest.fixture
def rom():
    try:
        return bootrom()
    except RuntimeError:
        pytest.skip('Official B2 ROM not prepared; run tools/prepare_bootrom.py')


def fake_rom():
    rom = bytearray(16384)
    struct.pack_into('<II', rom, 0, 0x20041f00, 0x101)
    rom[16:20] = b'Mu\x01\x03'
    rom[0x100:0x104] = b'\x2a\x20\x00\xbe'  # movs r0,42; bkpt
    return bytes(rom)


def run_to(engine, time=1_100_000_000, budget=10000):
    for _ in range(100000):
        reason = engine.run(time, budget)
        if reason != Stop.BUDGET:
            return reason
    pytest.fail('Boot workload exceeded bounded execution')


def load_native(engine, image):
    rom = fake_rom()
    engine._check(lib.sim_load_uf2(engine.handle, image, len(image), rom, len(rom)))


def test_uf2_native_out_of_order_duplicates_and_erased_gaps(engine):
    image = uf2(b'\x12' * 256 + b'\x34' * 256)
    shuffled = image[512:] + image[:512] + image[512:]
    load_native(engine, shuffled)
    assert engine.peek(0x10000000, 512) == b'\x12' * 256 + b'\x34' * 256
    assert engine.peek(0x10000200, 256) == b'\xff' * 256
    assert engine.cores().core[0].vtor == 0
    assert engine.run(budget=10) == Stop.BREAKPOINT
    assert engine.snapshot().registers[0] == 42


@pytest.mark.parametrize('change', ['start', 'middle', 'end', 'family', 'flags', 'size', 'unaligned',
    'underflow', 'overflow', 'number', 'total_zero', 'total_large', 'total_mismatch', 'duplicate', 'overlap',
    'missing', 'partial', 'no_boot2'])
def test_uf2_rejects_malformed_input(engine, change):
    data = bytearray(uf2(b'\x12' * 512))
    changes = {'start': (0, 0), 'middle': (4, 0), 'end': (508, 0), 'family': (28, 0),
               'flags': (8, 0x2001), 'size': (16, 476), 'unaligned': (12, 0x10000001),
               'underflow': (12, 0xffffff00), 'overflow': (12, 0x10200000),
               'number': (20, 2), 'total_zero': (24, 0), 'total_large': (24, 8193),
               'total_mismatch': (512 + 24, 3), 'overlap': (512 + 12, 0x10000000)}
    if change in changes:
        offset, value = changes[change]
        struct.pack_into('<I', data, offset, value)
    elif change == 'duplicate':
        duplicate = bytearray(data[:512]); duplicate[32] ^= 1; data.extend(duplicate)
    elif change == 'missing':
        data = data[:512]
    elif change == 'partial':
        data = data[:-1]
    elif change == 'no_boot2':
        struct.pack_into('<I', data, 12, 0x10000200)
    with pytest.raises(SimulationError):
        load_native(engine, bytes(data))


@pytest.mark.parametrize('length', [0, 255, 2 * 1024 * 1024 + 1])
def test_flash_length_validation(engine, length):
    data, rom = bytes(length), fake_rom()
    assert lib.sim_load_flash(engine.handle, data, len(data), rom, len(rom)) < 0


def test_bootrom_image_validation_and_no_packaged_rom(engine, tmp_path):
    with pytest.raises(ValueError, match='validated official'):
        bootrom(fake_rom())
    rom = fake_rom()
    assert lib.sim_load_flash(engine.handle, bytes(256), 256, rom, 100) < 0
    assert lib.sim_load_flash(ffi.NULL, bytes(256), 256, rom, len(rom)) < 0
    with pytest.raises(RuntimeError, match='PICO_BOOTROM_PATH'):
        bootrom(tmp_path / 'absent.bin')


@pytest.mark.parametrize('budget', [1, 47, 10000])
def test_real_rom_boot_crc_boot2_ssi_and_sdk_reset(engine, rom, budget):
    binary = elf_flash(demo.image('sdk_blink'))
    image = uf2(binary)
    engine.load(image, startup='rom', bootrom=rom)
    assert engine.peek(0, 16384) == rom
    assert engine.cores().core[0].vtor == 0
    engine.breakpoint(0x20041f00)
    assert run_to(engine, budget=budget) == Stop.BREAKPOINT
    assert engine.peek(0x20041f00, 256) == binary[:256]
    engine.breakpoint(0x20041f00, False)
    assert run_to(engine, budget=budget) == Stop.DEADLINE
    events = engine.events()
    led = [e for e in events if e.kind == 1 and e.source == 25]
    assert [e.value for e in led] == [1, 0, 1, 0, 1]
    assert led[0].time_ns == 31_065_000
    assert engine.snapshot().instructions == 33606
    assert engine.cores().core[0].vtor == 0x20000000
    assert int.from_bytes(engine.peek(0x18000000), 'little') == 0x005f0300
    engine.reset()
    assert run_to(engine) == Stop.DEADLINE
    assert engine.events() == events


def test_bad_boot2_crc_does_not_execute_firmware(engine, rom):
    binary = bytearray(elf_flash(demo.image('sdk_blink')))
    binary[252] ^= 1
    engine.load(bytes(binary), startup='rom', bootrom=rom)
    # All ROM retry attempts fail their real CRC check. It then tries USB boot,
    # whose clock resuscitation mode is outside this profile and fails explicitly.
    with pytest.raises(SimulationError, match='resuscitation|0x40060000'):
        run_to(engine, 10_000_000_000)
    assert not [e for e in engine.events() if e.kind == 1 and e.source == 25]


def test_rom_elf_binary_uf2_equivalence_and_project_replay(tmp_path, rom):
    elf = demo.image('sdk_uart')
    binary = elf_flash(elf)
    traces = []
    for data in (elf, binary, uf2(binary)):
        with Engine() as engine:
            engine.load(data, startup='rom', bootrom=rom)
            assert run_to(engine, 50_000_000) == Stop.DEADLINE
            traces.append(engine.events())
    assert traces[0] == traces[1] == traces[2]
    assert bytes(e.value for e in traces[0] if e.kind == 2) == b'Pico SDK UART ready\r\n'
    rom_path = tmp_path / 'b2.bin'; rom_path.write_bytes(rom)
    path = tmp_path / 'boot.json'
    project = Project.rom_blink(); project.bootrom = str(rom_path); project.save(path)
    loaded = Project.load(path)
    assert loaded.startup == 'rom' and loaded.resolve(loaded.bootrom) == rom_path
    simulation = Simulation(loaded)
    try:
        simulation.load(demo.image('sdk_blink'))
        while simulation.engine.snapshot().time_ns < 300_000_000:
            simulation.run(300_000_000)
        first = simulation.engine.events()
        recording = tmp_path / 'recording.json'; simulation.export_recording(recording)
        assert simulation.replay(recording, 300_000_000) == first
    finally:
        simulation.close()


def test_elf_flash_rejects_direct_load_and_invalid_headers():
    with pytest.raises(ValueError):
        from conftest import Thumb
        elf_flash(Thumb().elf())
    with pytest.raises(ValueError):
        elf_flash(b'\x7fELF')


def test_sdk_flash_erase_program_and_reset_persistence(engine, rom):
    engine.load(demo.image('sdk_flash'), startup='rom', bootrom=rom)
    assert run_to(engine, 200_000_000) == Stop.DEADLINE
    assert bytes(e.value for e in engine.events() if e.kind == 2) == b'SDK flash erase/program verified\r\n'
    assert engine.peek(0x101f0000, 256) == bytes(i ^ 0x5a for i in range(256))
    assert engine.peek(0x101f0100, 256) == b'\xff' * 256
    engine.reset()
    assert engine.peek(0x101f0000, 256) == bytes(i ^ 0x5a for i in range(256))


def test_uf2_path_with_corrupt_magic_is_not_treated_as_raw_flash(engine, rom, tmp_path):
    image = bytearray(uf2(elf_flash(demo.image('sdk_blink'))))
    image[0] ^= 1
    path = tmp_path / 'bad.uf2'; path.write_bytes(image)
    with pytest.raises(SimulationError, match='magic'):
        engine.load(path, startup='rom', bootrom=rom)


def test_narrow_mmio_replication_and_ppb_priority_lanes(engine, thumb):
    thumb.ldr(0, 0xd0000010).ldr(1, 0x12).op(0x7001)  # byte store replicated over the I/O bus
    thumb.read(0xd0000010, 3)
    thumb.ldr(0, 0xd0000010).ldr(1, 0x1234).op(0x8001)
    thumb.read(0xd0000010, 4)
    thumb.ldr(0, 0xd0000011).op(0x7802)  # byte read selects its addressed lane
    thumb.write(0xe000e400, 0xc0c0c0c0).ldr(0, 0xe000e401).ldr(1, 0x80).op(0x7001)
    engine.load(thumb.elf())
    engine.run(budget=100)
    state = engine.snapshot()
    assert state.registers[3] == 0x12121212
    assert state.registers[4] == 0x12341234
    assert state.registers[2] == 0x12
    assert engine.peek(0xe000e400) == struct.pack('<I', 0xc0c080c0)


def test_uart_tx_interrupt_clear_and_full_mask_storage(engine, thumb):
    thumb.write(0x40034030, 0x301).write(0x40034038, 0x7ff)
    thumb.read(0x4003403c, 3).write(0x40034000, 65).read(0x40034040, 4)
    thumb.write(0x40034044, 0x7ff).read(0x40034040, 5)
    engine.load(thumb.elf())
    engine.run(budget=100)
    state = engine.snapshot()
    assert state.registers[3] == state.registers[5] == 0
    assert state.registers[4] == 0x20
    assert engine.peek(0x40034038) == struct.pack('<I', 0x7ff)
