"""Functional SSD1306 I2C model; no host UI or wall-clock dependencies."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DisplayFrame:
    pixels: bytes  # 128 x 64 luminance bytes, already transformed
    revision: int
    warning: str = ''


class SSD1306:
    # Number of following command bytes, including recognized unsupported scroll.
    PARAMETERS = {0x20: 1, 0x21: 2, 0x22: 2, 0x81: 1, 0x8d: 1, 0xa8: 1,
                  0xad: 1, 0xd3: 1, 0xd5: 1, 0xd9: 1, 0xda: 1, 0xdb: 1,
                  0x26: 6, 0x27: 6, 0x29: 5, 0x2a: 5, 0xa3: 2}

    def __init__(self):
        self.ram = bytearray(1024)
        self.mode = 2
        self.column = self.page = 0
        self.columns, self.pages = (0, 127), (0, 7)
        self.on = self.inverse = self.entire = False
        self.remap = self.scan_reverse = False
        self.start_line = self.offset = 0
        self.mux = 63
        self.contrast = 127
        self.config = {}
        self.command = None
        self.parameters = []
        self.control = None
        self.revision = self.transactions = 0
        self.warning = ''
        self._frame = None

    def exchange(self, value, flags):
        self.transactions += 1
        if flags & 4:
            self.control = None
        if not flags & 1:
            if self.control is None:
                self.control = value
                if value & 0x3f:
                    self.warning = 'Invalid SSD1306 control byte'
            else:
                control = self.control
                if control & 0x40:
                    self._data(value)
                else:
                    self._command(value)
                if control & 0x80:
                    self.control = None
        if flags & 2:
            self.control = None
        return 0

    def _data(self, value):
        address = self.page * 128 + self.column
        if self.ram[address] != value:
            self.ram[address] = value
            self.revision += 1
        if self.mode == 1:
            self.page += 1
            if self.page > self.pages[1]:
                self.page = self.pages[0]
                self.column = self.column + 1 if self.column < self.columns[1] else self.columns[0]
        elif self.mode == 0:
            self.column += 1
            if self.column > self.columns[1]:
                self.column = self.columns[0]
                self.page = self.page + 1 if self.page < self.pages[1] else self.pages[0]
        else:
            self.column = (self.column + 1) & 127

    def _command(self, value):
        if self.command is not None:
            self.parameters.append(value)
            if len(self.parameters) < self.PARAMETERS[self.command]:
                return
            cmd, p = self.command, self.parameters
            self.config[cmd] = tuple(p)
            self.command, self.parameters = None, []
            if cmd == 0x20:
                if p[0] <= 2:
                    self.mode = p[0]
                else:
                    self.warning = 'Unsupported SSD1306 address mode'
            elif cmd == 0x21:
                self.columns = (p[0] & 127, max(p[0] & 127, p[1] & 127))
                self.column = self.columns[0]
            elif cmd == 0x22:
                self.pages = (p[0] & 7, max(p[0] & 7, p[1] & 7))
                self.page = self.pages[0]
            elif cmd == 0x81:
                self.contrast = p[0]
            elif cmd == 0xa8:
                self.mux = p[0] & 63
            elif cmd == 0xd3:
                self.offset = p[0] & 63
            elif cmd in (0x26, 0x27, 0x29, 0x2a, 0xa3):
                self.warning = 'SSD1306 hardware scrolling is unsupported'
            self.revision += 1
            return
        if value in self.PARAMETERS:
            self.command = value
        elif value <= 0x0f:
            self.column = (self.column & 0x70) | value
        elif 0x10 <= value <= 0x1f:
            self.column = (self.column & 15) | ((value & 7) << 4)
        elif 0x40 <= value <= 0x7f:
            self.start_line = value & 63
        elif 0xb0 <= value <= 0xb7:
            self.page = value & 7
        elif value in (0xae, 0xaf):
            self.on = value == 0xaf
        elif value in (0xa0, 0xa1):
            self.remap = value == 0xa1
        elif value in (0xc0, 0xc8):
            self.scan_reverse = value == 0xc8
        elif value in (0xa4, 0xa5):
            self.entire = value == 0xa5
        elif value in (0xa6, 0xa7):
            self.inverse = value == 0xa7
        elif value in (0x2e, 0x2f):
            if value == 0x2f:
                self.warning = 'SSD1306 hardware scrolling is unsupported'
        elif value != 0xe3:
            self.warning = f'Unsupported SSD1306 command 0x{value:02X}'
        self.revision += 1

    def snapshot(self):
        if self._frame is not None and self._frame.revision == self.revision and self._frame.warning == self.warning:
            return self._frame
        pixels = bytearray(8192)
        if self.on:
            for y in range(64):
                # Board orientation: upstream driver's A1/C8 is upright.
                com = y if self.scan_reverse else 63 - y
                row = (com + self.start_line - self.offset) & 63
                for x in range(128):
                    column = x if self.remap else 127 - x
                    bit = self.entire or bool(self.ram[(row // 8) * 128 + column] & (1 << (row & 7)))
                    if com <= self.mux and (bit != self.inverse):
                        pixels[y * 128 + x] = 32 + self.contrast * 223 // 255
        self._frame = DisplayFrame(bytes(pixels), self.revision, self.warning)
        return self._frame

    def summary(self):
        return self.warning or f'{"ON" if self.on else "OFF"} · {self.transactions} bytes'
