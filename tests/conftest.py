import struct

import pytest

from rp2040_sim.engine import Engine


def make_elf(code: bytes, ram: bytes = b"", bss: int = 0) -> bytes:
    entry = 0x10000009
    program = struct.pack("<II", 0x20042000, entry) + code
    count = 2 if ram or bss else 1
    header = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01" + b"\0" * 9,
                         2, 40, 1, entry, 52, 0, 0x5000200, 52, 32, count, 0, 0, 0)
    ph = struct.pack("<8I", 1, 256, 0x10000000, 0x10000000, len(program), len(program), 5, 4)
    if count == 2:
        ph += struct.pack("<8I", 1, 256 + len(program), 0x20000000, 0x20000000,
                          len(ram), len(ram) + bss, 6, 1)
    return header + ph + bytes(256 - len(header) - len(ph)) + program + ram


class Thumb:
    """Small fixture assembler for independently specified ARM instruction tests."""
    def __init__(self):
        self.words = []
        self.literals = []

    def op(self, word):
        self.words.append(word)
        return self

    def ldr(self, reg, value):
        self.literals.append((len(self.words), reg, value))
        self.words.append(0)
        return self

    def write(self, address, value):
        return self.ldr(0, address).ldr(1, value).op(0x6001)

    def read(self, address, reg=2):
        return self.ldr(0, address).op(0x6800 | reg)

    def elf(self, loop=False):
        words = self.words.copy() + [0xe7fe if loop else 0xbe00]
        if len(words) % 2:
            words.append(0xbf00)
        pool = bytearray()
        for index, reg, value in self.literals:
            pc = (8 + index * 2 + 4) & ~3
            offset = (8 + len(words) * 2 + len(pool) - pc) // 4
            assert 0 <= offset <= 255
            words[index] = 0x4800 | (reg << 8) | offset
            pool.extend(struct.pack("<I", value))
        return make_elf(struct.pack("<" + "H" * len(words), *words) + pool)


@pytest.fixture
def engine():
    with Engine() as engine:
        yield engine


@pytest.fixture
def thumb():
    return Thumb()
