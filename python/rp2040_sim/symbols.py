"""Optional ELF symbols and DWARF line locations; no hardware dependencies."""
from bisect import bisect_right
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

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
        self._ranges = []
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
                sequence_start = None
                for entry in table.get_entries():
                    state = entry.state
                    if state is None:
                        continue
                    if state.end_sequence:
                        if sequence_start is not None:
                            self._ranges.append((sequence_start, state.address))
                        sequence_start = None
                        continue
                    if sequence_start is None:
                        sequence_start = state.address
                    index = state.file - (0 if table.header.version >= 5 else 1)
                    entries = table.header.file_entry
                    if 0 <= index < len(entries):
                        file_entry = entries[index]
                        name = file_entry.name.decode("utf-8", "replace")
                        directories = table.header.include_directory
                        directory_index = file_entry.dir_index - (0 if table.header.version >= 5 else 1)
                        directory = directories[directory_index].decode('utf-8', 'replace') if 0 <= directory_index < len(directories) else ''
                        comp_dir = cu.get_top_DIE().attributes.get('DW_AT_comp_dir')
                        base = comp_dir.value.decode('utf-8', 'replace') if comp_dir else ''
                        filename = str((Path(base) / directory / name).resolve()) if base or directory or Path(name).is_absolute() else name
                        self.lines.append(Location(state.address, filename, state.line or 0))
        self.lines.sort(key=lambda e: e.address)
        self._addresses = [e.address for e in self.lines]

    def location(self, pc: int) -> Location | None:
        if not any(start <= pc < end for start, end in self._ranges):
            return None
        index = bisect_right(self._addresses, pc) - 1
        return self.lines[index] if index >= 0 else None
