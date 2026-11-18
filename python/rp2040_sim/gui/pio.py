"""Read-only PIO inspection from copied snapshots; never pop a device FIFO."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QPlainTextEdit,
)


def instruction_text(word, pinctrl=0, execctrl=0):
    major, arg, data = word >> 13, (word >> 5) & 7, word & 31
    names = ("PINS", "X", "Y", "NULL", "reserved", "STATUS", "ISR", "OSR")
    if major == 0:
        condition = ("", "!X ", "X-- ", "!Y ", "Y-- ", "X!=Y ", "PIN ", "!OSRE ")[arg]
        text = f"JMP {condition}{data}"
    elif major == 1:
        source = ("GPIO", "PIN", "IRQ", "reserved")[arg & 3]
        index = data & 7 if (arg & 3) == 2 else data
        text = f"WAIT {arg >> 2} {source} {index}" + (" REL" if (arg & 3) == 2 and data & 16 else "")
    elif major in (2, 3):
        destination = names[arg] if major == 2 else ("PINS", "X", "Y", "NULL", "PINDIRS", "PC", "ISR", "EXEC")[arg]
        text = f"{'IN' if major == 2 else 'OUT'} {destination}, {data or 32}"
    elif major == 4:
        text = ("PULL" if arg & 4 else "PUSH") + ((" IFEMPTY" if arg & 4 else " IFFULL") if arg & 2 else "")
        text += " BLOCK" if arg & 1 else " NOBLOCK"
    elif major == 5:
        destination = ("PINS", "X", "Y", "reserved", "EXEC", "PC", "ISR", "OSR")[arg]
        text = f"MOV {destination}, {('', '~', '::', '?')[data >> 3]}{names[data & 7]}"
    elif major == 6:
        text = f"IRQ {('CLEAR' if arg & 2 else 'WAIT' if arg & 1 else 'SET')} {data & 7}" + (" REL" if data & 16 else "")
    else:
        text = f"SET {( 'PINS', 'X', 'Y', 'reserved', 'PINDIRS', 'reserved', 'reserved', 'reserved')[arg]}, {data}"
    sides = pinctrl >> 29
    if sides:
        side = (word >> (13 - sides)) & ((1 << sides) - 1)
        optional = bool(execctrl & (1 << 30))
        if not optional or side & (1 << (sides - 1)):
            text += f" SIDE {side & ((1 << (sides - optional)) - 1)}"
    delay = (word >> 8) & ((1 << (5 - sides)) - 1)
    return text + (f" [{delay}]" if delay else "")


class PioInspector(QWidget):
    def __init__(self):
        super().__init__()
        self.snapshots = ()
        self.clock_hz = 1_000_000
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.block = QComboBox()
        self.block.addItems(["PIO0", "PIO1"])
        self.machine = QComboBox()
        self.machine.addItems(["SM0", "SM1", "SM2", "SM3"])
        controls.addWidget(self.block)
        controls.addWidget(self.machine)
        layout.addLayout(controls)
        self.summary = QLabel("Waiting for PIO state…")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.machines = QTableWidget(4, 6)
        self.machines.setHorizontalHeaderLabels(["SM", "PC", "State", "TX", "RX", "Delay"])
        self.machines.verticalHeader().hide()
        self.machines.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.machines.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.machines.verticalHeader().setDefaultSectionSize(24)
        self.machines.setFixedHeight(132)
        for row in range(4):
            for col in range(6):
                self.machines.setItem(row, col, QTableWidgetItem(""))
        self.machines.cellClicked.connect(lambda row, _: self.machine.setCurrentIndex(row))
        layout.addWidget(self.machines)
        self.registers = QPlainTextEdit()
        self.registers.setReadOnly(True)
        self.registers.setMaximumHeight(190)
        layout.addWidget(self.registers)
        self.program = QTableWidget(32, 3)
        self.program.setHorizontalHeaderLabels(["PC", "Code", "Instruction"])
        self.program.verticalHeader().hide()
        self.program.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.program.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.program.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.program.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row in range(32):
            for col in range(3):
                self.program.setItem(row, col, QTableWidgetItem(""))
        layout.addWidget(self.program)
        self.block.currentIndexChanged.connect(self.refresh)
        self.machine.currentIndexChanged.connect(self.refresh)

    def display(self, snapshots, clock_hz):
        self.snapshots, self.clock_hz = snapshots, clock_hz
        self.refresh()

    def refresh(self, *_):
        if not self.snapshots:
            return
        state = self.snapshots[self.block.currentIndex()]
        sm = state.sm[self.machine.currentIndex()]
        self.summary.setText(f"IRQ flags 0x{state.irq:02x} · FDEBUG 0x{state.fdebug:08x}\n"
                             f"OUT 0x{state.out:08x} · OE 0x{state.oe:08x}")
        stalls = ("", "WAIT", "TX stall", "RX stall", "IRQ wait")
        for row, machine in enumerate(state.sm):
            status = stalls[machine.stalled] or ("Run" if machine.enabled else "Disabled")
            for col, text in enumerate((row, machine.pc, status, len(machine.tx), len(machine.rx), machine.delay)):
                self.machines.item(row, col).setText(str(text))
        div = (sm.clkdiv >> 8) / 256 or 65536
        fifo = lambda words: " ".join(f"{word:08x}" for word in words) or "empty"
        self.registers.setPlainText(
            f"X={sm.x:08x}  Y={sm.y:08x}\nISR={sm.isr:08x} ({sm.in_count} bits in)\n"
            f"OSR={sm.osr:08x} ({sm.out_count} bits out)\n"
            f"Clock ÷{div:g}: {self.clock_hz / div:g} Hz · injected={int(sm.exec_pending)}\n"
            f"EXECCTRL={sm.execctrl:08x}\nSHIFTCTRL={sm.shiftctrl:08x}  PINCTRL={sm.pinctrl:08x}\n"
            f"TX: {fifo(sm.tx)}\nRX: {fifo(sm.rx)}")
        for row, instruction in enumerate(state.instructions):
            for col, text in enumerate((str(row), f"{instruction:04x}", instruction_text(instruction, sm.pinctrl, sm.execctrl))):
                self.program.item(row, col).setText(text)
        self.program.setCurrentCell(sm.pc, 0)
