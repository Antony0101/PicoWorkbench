"""Optional ELF symbols and DWARF line locations; no hardware dependencies."""
from bisect import bisect_right
from dataclasses import dataclass
from io import BytesIO

from elftools.elf.elffile import ELFFile


@dataclass(frozen=True)
class Location:
    address: int
    file: str
    line: int


class Symbols:
    def __init__(self, data: bytes):
        self.names: dict[str, int] = {}
        self.lines: list[Location] = []
        elf = ELFFile(BytesIO(data))
        for section in elf.iter_sections():
            if section.header.sh_type == "SHT_SYMTAB":
                for symbol in section.iter_symbols():
                    if symbol.name and symbol.entry.st_info.type == "STT_FUNC":
                        self.names[symbol.name] = symbol.entry.st_value & ~1
        if elf.has_dwarf_info():
            dwarf = elf.get_dwarf_info()
            for cu in dwarf.iter_CUs():
                table = dwarf.line_program_for_CU(cu)
                if table is None:
                    continue
                for entry in table.get_entries():
                    state = entry.state
                    if state is None or state.end_sequence:
                        continue
                    index = state.file - (0 if table.header.version >= 5 else 1)
                    entries = table.header.file_entry
                    if 0 <= index < len(entries):
                        name = entries[index].name.decode("utf-8", "replace")
                        self.lines.append(Location(state.address, name, state.line or 0))
        self.lines.sort(key=lambda e: e.address)
        self._addresses = [e.address for e in self.lines]

    def location(self, pc: int) -> Location | None:
        index = bisect_right(self._addresses, pc) - 1
        return self.lines[index] if index >= 0 else None
