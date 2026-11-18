"""Firmware container conversion and local, hash-checked B2 ROM discovery."""
from io import BytesIO
from pathlib import Path
import hashlib
import os
import struct

FLASH_BASE = 0x10000000
FLASH_SIZE = 2 * 1024 * 1024
B2_SHA256 = '9c19b46f068c21f90d200c514faad4a0d5cecfc978f155b8c9d25cb6bc2efd81'


def bootrom(image=None) -> bytes:
    if image is None:
        image = os.environ.get('PICO_BOOTROM_PATH')
        if image is None:
            image = Path(__file__).resolve().parents[2] / '.tools/bootrom-b2.bin'
    try:
        data = image if isinstance(image, bytes) else Path(image).read_bytes()
    except OSError as error:
        raise RuntimeError('Select the official B2 ROM binary or set PICO_BOOTROM_PATH; see docs/boot.md') from error
    if hashlib.sha256(data).hexdigest() != B2_SHA256:
        raise ValueError('Boot ROM does not match the validated official RP2040 B2 image')
    return data


def elf_flash(data: bytes) -> bytes:
    from elftools.elf.elffile import ELFFile
    from elftools.common.exceptions import ELFError
    try:
        elf = ELFFile(BytesIO(data))
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM' or elf['e_type'] != 'ET_EXEC':
            raise ValueError('Expected a little-endian ELF32 ARM executable')
        parts = []
        for segment in elf.iter_segments():
            if segment['p_type'] != 'PT_LOAD' or not segment['p_filesz']:
                continue
            address, size = segment['p_paddr'], segment['p_filesz']
            if size > segment['p_memsz'] or address < FLASH_BASE or address + size > FLASH_BASE + FLASH_SIZE:
                raise ValueError('ROM boot requires all initialized ELF segments to have flash load addresses')
            content = segment.data()
            if len(content) != size:
                raise ValueError('Truncated ELF load segment')
            parts.append((address - FLASH_BASE, content))
        parts.sort()
        if not parts or parts[0][0] != 0 or len(parts[0][1]) < 256:
            raise ValueError('ROM boot requires a boot2 page at the flash base')
        result = bytearray(b'\xff' * (parts[-1][0] + len(parts[-1][1])))
        end = 0
        for offset, content in parts:
            if offset < end:
                raise ValueError('Overlapping ELF flash load segments')
            result[offset:offset + len(content)] = content
            end = offset + len(content)
        return bytes(result)
    except (ELFError, struct.error, IndexError) as error:
        raise ValueError(f'Invalid ELF image: {error}') from error


def uf2(data: bytes) -> bytes:
    """Encode a flash binary as RP2040 UF2, with 256-byte payloads."""
    if not 256 <= len(data) <= FLASH_SIZE:
        raise ValueError('Flash image must contain 256 bytes to 2 MiB')
    count = (len(data) + 255) // 256
    result = bytearray()
    for number in range(count):
        block = bytearray(512)
        struct.pack_into('<8I', block, 0, 0x0a324655, 0x9e5d5157, 0x2000,
                         FLASH_BASE + 256 * number, 256, number, count, 0xe48bff56)
        block[32:288] = data[number * 256:(number + 1) * 256].ljust(256, b'\xff')
        struct.pack_into('<I', block, 508, 0x0ab16f30)
        result.extend(block)
    return bytes(result)
