"""External models run on the simulation worker and never access Qt objects."""
from dataclasses import dataclass, field


@dataclass
class RegisterSensor:
    """Deterministic test device: ID=0x42, temperature=25, writable register file."""
    registers: bytearray = field(default_factory=lambda: bytearray([0x42, 25]) + bytearray(254))
    pointer: int = 0
    first_write: bool = True
    transactions: int = 0

    def exchange(self, value: int, flags: int) -> int:
        read, stop, restart = flags & 1, flags & 2, flags & 4
        self.transactions += 1
        if read:
            result = self.registers[self.pointer]
            self.pointer = (self.pointer + 1) & 255
        elif self.first_write or restart:
            self.pointer = value
            self.first_write = False
            result = 0
        else:
            self.registers[self.pointer] = value
            self.pointer = (self.pointer + 1) & 255
            result = 0
        if stop:
            self.first_write = True
        return result

    def summary(self):
        return f"{self.transactions} bytes · reg {self.pointer:02x}"


@dataclass
class SpiLoopback:
    transactions: int = 0
    last: int = 0

    def exchange(self, value: int, flags: int) -> int:
        self.transactions += 1
        self.last = value
        return value

    def summary(self):
        return f"RX/TX {self.last:02X} · {self.transactions} bytes"
