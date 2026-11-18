"""Copied-state inspection of CPU banks and shared SIO synchronization."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QPlainTextEdit,
)


class CoresInspector(QWidget):
    def __init__(self):
        super().__init__()
        self.snapshot = None
        layout = QVBoxLayout(self)
        self.summary = QLabel('Waiting for core state…')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.cores = QTableWidget(2, 4)
        self.cores.setHorizontalHeaderLabels(['Core', 'State', 'Instructions', 'PC'])
        self.cores.verticalHeader().hide()
        self.cores.verticalHeader().setDefaultSectionSize(24)
        self.cores.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.cores.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.cores.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cores.setFixedHeight(84)
        for row in range(2):
            for col in range(4):
                self.cores.setItem(row, col, QTableWidgetItem(''))
        layout.addWidget(self.cores)
        self.selected = QComboBox()
        self.selected.addItems(['Core 0', 'Core 1'])
        self.selected.currentIndexChanged.connect(self.refresh)
        self.cores.cellClicked.connect(lambda row, _: self.selected.setCurrentIndex(row))
        layout.addWidget(self.selected)
        self.registers = QTableWidget(8, 4)
        self.registers.setHorizontalHeaderLabels(['Reg', 'Value', 'Reg', 'Value'])
        self.registers.verticalHeader().hide()
        self.registers.verticalHeader().setDefaultSectionSize(24)
        self.registers.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.registers.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.registers.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.registers.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.registers.setFixedHeight(224)
        names = [f'R{i}' for i in range(13)] + ['SP', 'LR', 'PC']
        for row in range(8):
            for half in range(2):
                self.registers.setItem(row, half * 2, QTableWidgetItem(names[row + 8 * half]))
                self.registers.setItem(row, half * 2 + 1, QTableWidgetItem('00000000'))
        layout.addWidget(self.registers)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)

    def display(self, state):
        self.snapshot = state
        self.refresh()

    def refresh(self, *_):
        if self.snapshot is None:
            return
        state = self.snapshot
        launch = 'Launch complete' if state.boot_stage == 6 else f'Launch {state.boot_stage}/6'
        self.summary.setText(f'{launch} · Next core {state.next_core}\n'
                             'Step advances one instruction on the next runnable core.')
        for row, c in enumerate(state.core):
            status = 'Reset' if row == 1 and state.proc1_off else 'Boot wait' if not c.launched else (
                'Ready', 'WFI', 'WFE', 'Returned')[c.sleeping]
            for col, value in enumerate((row, status, c.instructions, f'{c.registers[15]:08x}')):
                self.cores.item(row, col).setText(str(value))
        c = state.core[self.selected.currentIndex()]
        for row in range(8):
            for half in range(2):
                self.registers.item(row, 2 * half + 1).setText(f'{c.registers[row + 8 * half]:08x}')
        held = ', '.join(f'{lock}: core {owner - 1}' for lock, owner in enumerate(state.spinlock_owner) if owner)
        fifo = ' '.join(f'{word:08x}' for word in c.rx) or 'empty'
        self.details.setPlainText(
            f'xPSR={c.xpsr:08x}  PRIMASK={c.primask}  CONTROL={c.control:x}\n'
            f'MSP={c.msp:08x}  PSP={c.psp:08x}\nVTOR={c.vtor:08x}  SCR={c.scr:02x}\n'
            f'NVIC enable={c.nvic_enable:08x}\nPending={c.nvic_pending:08x}  IRQ={c.irq_levels:08x}\n'
            f'SysTick CTRL={c.systick_ctrl:x} LOAD={c.systick_load} VALUE={c.systick_value}\n'
            f'FIFO status={c.fifo_status:x} · RX: {fifo}\n'
            f'Spinlocks {state.spinlocks:08x}: {held or "none held"}')
