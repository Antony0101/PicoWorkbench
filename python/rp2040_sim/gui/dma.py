"""DMA inspector consumes immutable copies supplied by the engine worker."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit,
)


def request_name(number):
    if number < 16:
        return f"PIO{number // 8} {'RX' if number & 4 else 'TX'}{number & 3}"
    if number == 36:
        return "ADC"
    if 59 <= number <= 62:
        return f"Timer {number - 59}"
    if number == 63:
        return "Permanent"
    return f"DREQ {number}"


class DmaInspector(QWidget):
    def __init__(self):
        super().__init__()
        self.snapshot = None
        layout = QVBoxLayout(self)
        self.summary = QLabel("Waiting for DMA state…")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.channels = QTableWidget(12, 5)
        self.channels.setHorizontalHeaderLabels(["Ch", "State", "Remaining", "Reload", "Pacing"])
        self.channels.verticalHeader().hide()
        self.channels.verticalHeader().setDefaultSectionSize(24)
        self.channels.setFixedHeight(320)
        self.channels.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.channels.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.channels.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.channels.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row in range(12):
            for col in range(5):
                self.channels.setItem(row, col, QTableWidgetItem(""))
        layout.addWidget(self.channels)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.channels.currentCellChanged.connect(self.refresh_details)
        self.channels.setCurrentCell(0, 0)

    def display(self, state):
        self.snapshot = state
        self.summary.setText(f"Raw IRQ 0x{state.intr:03x}\n"
                             f"IRQ0 0x{state.ints[0]:03x} · IRQ1 0x{state.ints[1]:03x}")
        for row, channel in enumerate(state.channels):
            if channel.ctrl & (3 << 29):
                status = "Bus error"
            elif channel.busy:
                status = "Paused" if not channel.ctrl & 1 else "Waiting" if channel.waiting else "Running"
            else:
                status = "Idle"
            values = (row, status, channel.remaining, channel.reload, request_name((channel.ctrl >> 15) & 63))
            for col, value in enumerate(values):
                self.channels.item(row, col).setText(str(value))
        self.refresh_details()

    def refresh_details(self, *_):
        if self.snapshot is None:
            return
        row = max(0, self.channels.currentRow())
        c = self.snapshot.channels[row]
        width = ("byte", "halfword", "word", "reserved")[(c.ctrl >> 2) & 3]
        ring = (c.ctrl >> 6) & 15
        wrap = f"{1 << ring} bytes on {'write' if c.ctrl & (1 << 10) else 'read'}" if ring else "off"
        chain = (c.ctrl >> 11) & 15
        timers = '\n'.join(f"Timer {i}: {value >> 16}/{value & 65535}" for i, value in enumerate(self.snapshot.timers))
        self.details.setPlainText(
            f"Channel {row} · {width}\n"
            f"READ_ADDR  {c.read_addr:08x}  {'increment' if c.ctrl & 16 else 'fixed'}\n"
            f"WRITE_ADDR {c.write_addr:08x}  {'increment' if c.ctrl & 32 else 'fixed'}\n"
            f"CTRL       {c.ctrl:08x}\nRing: {wrap}\n"
            f"Chain: {'disabled' if chain == row else f'channel {chain}'}\n"
            f"Byte swap: {bool(c.ctrl & (1 << 22))} · Quiet IRQ: {bool(c.ctrl & (1 << 21))}\n\n{timers}")
