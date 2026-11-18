from copy import deepcopy
from pathlib import Path
import os
import re
import sys

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget, QSlider, QSpinBox,
)

from .. import demo
from ..build import FirmwareBuild, SdkFirmwareBuild
from ..project import Component, Project
from ..symbols import Symbols
from ..engine import Stop
from .canvas import CircuitScene, CircuitView
from .waveform import Waveform
from .worker import Worker
from .pio import PioInspector
from .dma import DmaInspector
from .cores import CoresInspector
from .menus import install_table_menu, popup_menu, text_menu

STYLE = """
QMainWindow, QWidget { background: #111d26; color: #d6e4ec; font-size: 12px; }
QMenuBar, QMenu, QToolBar { background: #182a35; border: 0; padding: 5px; }
QMenu::item:selected { background: #2c5261; }
QMenu::item:disabled { color: #607685; }
QToolBar { spacing: 8px; border-bottom: 1px solid #2b414f; }
QToolButton, QPushButton { background: #223c49; border: 1px solid #355361; border-radius: 5px; padding: 7px 12px; }
QToolButton:hover, QPushButton:hover { background: #315867; }
QToolButton:disabled, QPushButton:disabled { color: #607685; background: #192c36; }
QLineEdit, QComboBox, QSpinBox { background: #142630; border: 1px solid #355361; border-radius: 4px; padding: 5px; }
QPlainTextEdit, QTableWidget { background: #101d25; border: 0; selection-background-color: #275061; }
QHeaderView::section { background: #1d3440; color: #93adbd; border: 0; padding: 5px; }
QTabWidget::pane { border: 1px solid #2a414e; }
QTabBar::tab { background: #182b36; color: #8ca8b9; padding: 9px 17px; }
QTabBar::tab:selected { color: #68e0b7; background: #203b45; border-bottom: 2px solid #68e0b7; }
QSplitter::handle { background: #29414e; height: 4px; width: 4px; }
QStatusBar { background: #182c36; color: #91acbb; }
QScrollBar:vertical { background: #182b35; width: 10px; }
QScrollBar::handle:vertical { background: #3c5968; min-height: 20px; }
QScrollBar:horizontal { background: #182b35; height: 10px; }
QScrollBar::handle:horizontal { background: #3c5968; min-width: 20px; }
"""


class CHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        for pattern, color in [
            (r"\b(?:void|int|unsigned|const|volatile|typedef|static|char|if|else|for|while|return)\b", "#82c5fc"),
            (r"\b(?:0x[0-9a-fA-F]+|\d+)u?\b", "#efc57e"),
            (r'"[^"\\]*(?:\\.[^"\\]*)*"', "#84d8ae"),
            (r"//.*$|/\*.*?\*/", "#688998"),
            (r"^\s*#.*$", "#bd9ee4"),
        ]:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class BuildTask(QObject):
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, text, source, output, linker, startup="direct"):
        super().__init__()
        self.text, self.source, self.output, self.linker = text, source, output, linker
        self.job = None
        self.startup = startup
        self.canceled = False

    def cancel(self):
        self.canceled = True
        if self.job:
            self.job.cancel()

    @Slot()
    def run(self):
        try:
            if self.canceled:
                return
            self.source.parent.mkdir(parents=True, exist_ok=True)
            self.source.write_text(self.text)
            builder = SdkFirmwareBuild if self.startup in ("sdk", "rom") else FirmwareBuild
            self.job = builder(self.source, self.output, self.linker)
            if self.canceled:
                self.job.cancel()
                return
            log = self.job.finish()
            if not self.canceled:
                self.done.emit((self.output, log, self.startup))
        except Exception as error:
            if not self.canceled:
                self.failed.emit(str(error))
        finally:
            self.finished.emit()


class Window(QMainWindow):
    dispatch = Signal(object)

    def __init__(self, project_path=None):
        super().__init__()
        self.setWindowTitle("RP2040 Lab")
        self.resize(1440, 1000)
        self.project = Project.load(project_path) if project_path else Project.demo()
        self.running = False
        self.playback_speed = 1.0
        self.state = None
        self.symbols = None
        self.breakpoints = set()
        self.build_thread = None
        self.build_task = None
        self.source_path = self.project.resolve(self.project.source) if self.project.source else None
        self._make_ui()
        if self.project.builtin == "pio_blink":
            self.inspector.setCurrentWidget(self.pio_inspector)
        elif self.project.builtin == "dma_pio":
            self.inspector.setCurrentWidget(self.dma_inspector)
        elif self.project.builtin == "multicore":
            self.inspector.setCurrentWidget(self.cores_inspector)
        self.editor.setPlainText(self.project.source_text if self.project.source_text is not None else
                                 self.source_path.read_text() if self.source_path else demo.source(self.project.builtin))
        self.thread = QThread(self)
        self.worker = Worker()
        self.worker.moveToThread(self.thread)
        self.dispatch.connect(self.worker.command, Qt.ConnectionType.QueuedConnection)
        self.thread.started.connect(self.worker.initialize)
        self.worker.updated.connect(self.update_state)
        self.worker.failed.connect(self.error)
        self.worker.memory.connect(self.show_memory)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()
        if project_path:
            self.send("project", project=deepcopy(self.project))
        self._read_symbols(self.project.resolve(self.project.firmware) if self.project.firmware else None)

    def action(self, title, callback, shortcut=None):
        action = QAction(title, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(callback)
        return action

    def _make_ui(self):
        file_menu = self.menuBar().addMenu("File")
        for title, callback, shortcut in [
            ("New demo project", self.new_project, "Ctrl+N"),
            ("New peripheral workbench", self.new_workbench, None),
            ("New PIO blink demo", self.new_pio_blink, None),
            ("New DMA to PIO demo", self.new_dma_pio, None),
            ("New two-core demo", self.new_multicore, None),
            ("New Pico SDK blink", lambda: self.new_sdk("sdk_blink"), None),
            ("New Pico SDK UART", lambda: self.new_sdk("sdk_uart"), None),
            ("New ROM boot blink", self.new_rom, None),
            ("New ROM flash test", lambda: self.new_rom(True), None),
            ("New MicroPython project", self.new_micropython, None),
            ("Select B2 boot ROM…", self.select_bootrom, None),
            ("Open project…", self.open_project, "Ctrl+O"),
            ("Save project…", self.save_project, "Ctrl+S"),
            ("Load firmware ELF / UF2…", self.open_firmware, "Ctrl+L"),
            ("Open source…", self.open_source, None),
            ("Save source as…", self.save_source, "Ctrl+Shift+S"),
            ("Export input recording…", self.export_recording, None),
            ("Export flash image…", self.export_flash, None),
        ]:
            file_menu.addAction(self.action(title, callback, shortcut))
        toolbar = QToolBar("Simulation", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        brand = QLabel("  RP2040 LAB   ")
        brand.setStyleSheet("font-size: 17px; font-weight: 700; color: #6ee6bd;")
        toolbar.addWidget(brand)
        self.run_action = self.action("▶ Run", lambda: self.send("run"), "F5")
        self.pause_action = self.action("Ⅱ Pause", lambda: self.send("pause"), "F6")
        self.step_action = self.action("Step next", lambda: self.send("step"), "F10")
        self.reset_action = self.action("Reset", lambda: self.send("reset"), "Ctrl+R")
        for action in [self.run_action, self.pause_action, self.step_action, self.reset_action]:
            toolbar.addAction(action)
        toolbar.addSeparator()
        self.build_action = self.action("Build + Load", self.build_source, "F7")
        toolbar.addAction(self.build_action)
        self.cancel_action = self.action("Cancel build", self.cancel_build)
        self.cancel_action.setEnabled(False)
        toolbar.addAction(self.cancel_action)
        spacer = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.startup_label = QLabel()
        toolbar.addWidget(self.startup_label)

        self.addToolBarBreak()
        execution = QToolBar("Execution controls", self)
        execution.setMovable(False)
        self.addToolBar(execution)
        execution.addWidget(QLabel("  Playback "))
        self.speed_combo = QComboBox()
        for speed in (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0):
            self.speed_combo.addItem(f"{speed:g}×", speed)
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(1.0))
        self.speed_combo.setToolTip("Target virtual seconds per real second. Preserves chip timing; limited by host performance.")
        self.speed_combo.currentIndexChanged.connect(
            lambda _: self.send("speed", value=self.speed_combo.currentData()))
        execution.addWidget(self.speed_combo)
        execution.addSeparator()
        execution.addWidget(QLabel("CPU clock "))
        self.clock_spin = QSpinBox()
        self.clock_spin.setRange(1_000, 133_000_000)
        self.clock_spin.setSingleStep(1_000)
        self.clock_spin.setGroupSeparatorShown(True)
        self.clock_spin.setSuffix(" Hz")
        self.clock_spin.setValue(self.project.clock_hz)
        self.clock_spin.setToolTip("Functional instruction clock. Applying reloads firmware and resets chip state and breakpoints.")
        execution.addWidget(self.clock_spin)
        self.clock_button = QPushButton("Apply + Reset")
        self.clock_button.clicked.connect(lambda: self.send("clock", hz=self.clock_spin.value()))
        execution.addWidget(self.clock_button)
        execution.addSeparator()
        self.instruction_spin = QSpinBox()
        self.instruction_spin.setRange(1, 1_000_000)
        self.instruction_spin.setGroupSeparatorShown(True)
        self.instruction_spin.setValue(1_000)
        self.instruction_spin.setSuffix(" instructions")
        execution.addWidget(self.instruction_spin)
        self.run_n_button = QPushButton("Run N")
        self.run_n_button.setToolTip("Run at the selected playback speed, then pause after N instructions or at a breakpoint.")
        self.run_n_button.clicked.connect(lambda: self.send("run_instructions", count=self.instruction_spin.value()))
        execution.addWidget(self.run_n_button)

        self.scene = CircuitScene(self)
        self.scene.set_project(self.project)
        self.view = CircuitView(self.scene)
        self.scene.connect_requested.connect(self.add_wire)
        self.scene.button.connect(lambda key, down: self.send("button", id=key, pressed=down))
        self.scene.moved.connect(self.move_part)
        self.scene.edit_requested.connect(self.edit_part)
        self.view.delete_requested.connect(self.delete_selected)
        self.view.add_requested.connect(lambda kind, x, y: self.add_part(kind=kind, x=x, y=y))
        self.view.disconnect_requested.connect(self.disconnect_port)
        self.view.simulation_actions = [self.run_action, self.pause_action, self.step_action, self.reset_action]
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("  COMPONENTS"))
        self.part_kind = QComboBox()
        self.part_kind.addItems(["led", "button", "probe", "pullup", "pulldown", "i2c_sensor", "spi_loopback"])
        controls.addWidget(self.part_kind)
        self.add_part_button = QPushButton("+ Add")
        self.add_part_button.clicked.connect(self.add_part)
        controls.addWidget(self.add_part_button)
        fit = QPushButton("Fit canvas")
        fit.clicked.connect(self.view.fit_canvas)
        controls.addWidget(fit)
        controls.addStretch()
        controls.addWidget(QLabel("Green: high   Slate: low   Amber: floating   Red: conflict  "))
        layout.addLayout(controls)
        layout.addWidget(self.view)

        self.inspector = inspector = QTabWidget()
        inspector.setMinimumWidth(300)
        cpu = QWidget()
        cpu_layout = QVBoxLayout(cpu)
        self.location_label = QLabel("PC · direct-load entry")
        self.location_label.setWordWrap(True)
        cpu_layout.addWidget(self.location_label)
        self.registers = QTableWidget(17, 2)
        self.registers.setHorizontalHeaderLabels(["Register", "Value"])
        self.registers.verticalHeader().hide()
        self.registers.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.registers.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, name in enumerate([f"R{i}" for i in range(13)] + ["SP", "LR", "PC", "xPSR"]):
            self.registers.setItem(i, 0, QTableWidgetItem(name))
            self.registers.setItem(i, 1, QTableWidgetItem("0x00000000"))
        cpu_layout.addWidget(self.registers)
        self.break_address = QLineEdit("0x10000008")
        self.break_address.setPlaceholderText("Breakpoint address or ELF function")
        cpu_layout.addWidget(self.break_address)
        bp = QPushButton("Toggle breakpoint")
        bp.clicked.connect(self.toggle_breakpoint)
        cpu_layout.addWidget(bp)
        self.break_list = QLabel("No breakpoints")
        self.break_list.setWordWrap(True)
        cpu_layout.addWidget(self.break_list)
        inspector.addTab(cpu, "CPU0")
        memory = QWidget()
        memory_layout = QVBoxLayout(memory)
        self.memory_address = QLineEdit("0x20000000")
        memory_layout.addWidget(self.memory_address)
        read = QPushButton("Inspect 128 bytes")
        read.clicked.connect(self.read_memory)
        memory_layout.addWidget(read)
        self.memory_text = QPlainTextEdit()
        self.memory_text.setReadOnly(True)
        self.memory_text.setFont(QFont("monospace", 10))
        memory_layout.addWidget(self.memory_text)
        inspector.addTab(memory, "Memory")
        inputs = QWidget()
        inputs_layout = QVBoxLayout(inputs)
        inputs_layout.addWidget(QLabel("ADC input · 12-bit sample (0–4095)"))
        self.adc_sliders = []
        for channel in range(4):
            label = QLabel(f"ADC{channel} / GP{26 + channel}: 0")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 4095)

            def change(value, channel=channel, label=label):
                label.setText(f"ADC{channel} / GP{26 + channel}: {value}")
                self.send("adc", channel=channel, value=value)

            slider.valueChanged.connect(change)
            inputs_layout.addWidget(label)
            inputs_layout.addWidget(slider)
            self.adc_sliders.append((slider, label, channel))
        inputs_layout.addWidget(QLabel("PWM · configured frequency and duty"))
        self.pwm_table = QTableWidget(8, 4)
        self.pwm_table.setHorizontalHeaderLabels(["Slice", "Hz", "A %", "B %"])
        self.pwm_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pwm_table.verticalHeader().hide()
        self.pwm_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row in range(8):
            for col in range(4):
                self.pwm_table.setItem(row, col, QTableWidgetItem(str(row) if col == 0 else "0"))
        inputs_layout.addWidget(self.pwm_table)
        inspector.addTab(inputs, "I/O")
        self.pio_inspector = PioInspector()
        inspector.addTab(self.pio_inspector, "PIO")
        self.dma_inspector = DmaInspector()
        inspector.addTab(self.dma_inspector, "DMA")
        self.cores_inspector = CoresInspector()
        inspector.addTab(self.cores_inspector, "Cores")
        limits = QPlainTextEdit()
        limits.setReadOnly(True)
        limits.setPlainText(
            "IMPLEMENTED\n\nReal Thumb firmware execution through Unicorn's Cortex-M0 backend.\n\n"
            "264 KiB SRAM, 2 MiB mapped flash.\n\nSIO GPIO, pin functions/overrides, pads, digital nets, "
            "pulls and contention.\n\nRead-only timer clock and alarms.\n\nNVIC, timer/GPIO/UART interrupts, SysTick, SVC/PendSV, "
            "PRIMASK, exception frames and WFI/WFE.\n\nUART register-level byte I/O.\n\n"
            "I2C master transfers to a Python register sensor; SPI master transfers to a Python loopback device.\n\n"
            "Free-running PWM and wrap interrupts; ADC conversions, FIFO and manually supplied inputs.\n\n"
            "PIO0/PIO1 state machines, instruction memory, FIFOs, side-set, clock dividers and IRQs.\n\n"
            "DMA: 12 channels, byte/halfword/word transfers, chaining, rings, PIO/ADC/timer pacing and IRQs.\n\n"
            "Two CPU contexts share SRAM and peripherals. Core-local NVIC/SysTick, inter-core FIFOs/IRQs, "
            "32 spinlocks, SEV/WFE and core 1 reset/launch handshake.\n\n"
            "B2 ROM boot, boot2 CRC/execution, RP2040 UF2 loading and serial flash erase/program. "
            "Calendar RTC and a pinned MicroPython 1.26.1 UART profile with script upload and persistent files.\n\n"
            "APPROXIMATIONS\n\nOne instruction advances one timing tick. The demo uses 1 MHz ticks. "
            "This is not cycle-accurate timing.\n\nUART transmission completes immediately; baud and pin waveforms are not modeled.\n\n"
            "I2C/SPI transfers complete at transaction boundaries. Bit-banging, arbitration, clock stretching and bus waveforms are not modeled.\n\n"
            "Direct and SDK ELF modes bypass boot ROM and boot2. SDK mode runs real SDK 2.2.0 crt0, "
            "RAM initialization, clock setup and timer IRQs with functional clock/reset registers and ROM ABI shims. "
            "SDK clock writes do not change the configured timing tick.\n\n"
            "PIO input synchronizer latency and bus contention are not modeled. OUT_STICKY and INLINE_OUT_EN stop with diagnostics.\n\n"
            "DMA uses one functional transfer beat per tick, level-gated FIFO pacing, and instant aborts. "
            "Other DREQ sources and checksum sniffing stop with diagnostics.\n\n"
            "Both runnable cores alternate single instructions on the functional clock; bus contention and simultaneous "
            "hardware cycles are not modeled. The core 1 launch protocol is a ROM shim.\n\n"
            "NOT YET IMPLEMENTED\n\nUSB transfers/BOOTSEL USB and watchdog countdown/reset. "
            "Other SDK/MicroPython modules require validation. Unsupported accesses stop with diagnostics.\n\n"
            "The board drawing shows logical chip GPIOs, including pins that have no Pico header connection."
        )
        inspector.addTab(limits, "Support")
        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(workspace)
        top.addWidget(inspector)
        top.setSizes([1040, 350])

        self.tabs = QTabWidget()
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("monospace", 11))
        self.editor.setTabStopDistance(32)
        self.highlighter = CHighlighter(self.editor.document())
        self.tabs.addTab(self.editor, "Firmware · C")
        serial = QWidget()
        serial_layout = QVBoxLayout(serial)
        self.serial = QPlainTextEdit()
        self.serial.setReadOnly(True)
        self.serial.setMaximumBlockCount(5000)
        self.serial.setFont(QFont("monospace", 11))
        serial_layout.addWidget(self.serial)
        send_row = QHBoxLayout()
        self.serial_input = QLineEdit()
        self.serial_input.setPlaceholderText("UART0 input (Enter appends newline)")
        self.serial_input.returnPressed.connect(self.send_serial)
        send_row.addWidget(self.serial_input)
        send_button = QPushButton("Send")
        send_button.clicked.connect(self.send_serial)
        send_row.addWidget(send_button)
        serial_layout.addLayout(send_row)
        self.interrupt_button = QPushButton('Interrupt Python · Ctrl+C')
        self.interrupt_action = self.action('Interrupt Python', lambda: self.send('interrupt_python'))
        self.interrupt_action.setEnabled(False)
        self.interrupt_button.setEnabled(False)
        self.interrupt_button.clicked.connect(self.interrupt_action.trigger)
        serial_layout.addWidget(self.interrupt_button)
        self.tabs.addTab(serial, "Serial · UART0")
        self.waveform = Waveform()
        self.tabs.addTab(self.waveform, "Logic analyzer")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(5000)
        self.logs.setFont(QFont("monospace", 10))
        self.tabs.addTab(self.logs, "Build + diagnostics")
        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.addWidget(top)
        vertical.addWidget(self.tabs)
        vertical.setSizes([650, 300])
        self.setCentralWidget(vertical)
        for table in self.findChildren(QTableWidget):
            install_table_menu(table)
        for editor in (self.editor, self.serial, self.logs):
            editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            editor.customContextMenuRequested.connect(
                lambda position, editor=editor: popup_menu(
                    editor, self.output_context_menu(editor, position),
                    editor.viewport().mapToGlobal(position)))
        self.status = QLabel("Initializing engine…")
        self.statusBar().addWidget(self.status, 1)
        self.project_label = QLabel(self.project.name)
        self.statusBar().addPermanentWidget(self.project_label)
        QTimer.singleShot(100, self.view.fit_canvas)

    def output_context_menu(self, editor, position):
        actions = ([self.build_action, self.cancel_action] if editor is self.editor else
                   [self.interrupt_action] if editor is self.serial else [])
        return text_menu(editor, position, actions, clear=editor in (self.serial, self.logs))

    def send(self, name, **fields):
        self.dispatch.emit({"name": name, **fields})

    @Slot(object)
    def update_state(self, packet):
        self.state, self.running = packet["state"], packet["running"]
        state = self.state
        self.project.startup = packet.get("startup", self.project.startup)
        mode = {"sdk": "SDK ELF · boot2 skipped", "rom": "B2 ROM + boot2", "direct": "direct load"}[self.project.startup]
        self.startup_label.setText(f"2 cores · functional timing · {mode}  ")
        self.playback_speed = packet["speed"]
        previous_python = getattr(self, 'python_status', '')
        self.python_status = packet.get('python_status', '')
        if self.python_status != previous_python and self.python_status in ('finished', 'error'):
            self.logs.appendPlainText('Python program finished' if self.python_status == 'finished' else 'Python program raised an exception; see UART output')
        python = self.project.builtin == 'micropython'
        self.interrupt_action.setEnabled(python and state.loaded)
        self.interrupt_button.setEnabled(self.interrupt_action.isEnabled())
        self.build_action.setText('Run Python' if python else 'Build + Load')
        self.tabs.setTabText(0, 'Python source' if python else 'Firmware · C')
        self.serial_input.setPlaceholderText('MicroPython UART REPL command' if python else 'UART0 input (Enter appends newline)')
        if packet.get("clock_changed"):
            self.project.clock_hz = state.clock_hz
            self.clock_spin.setValue(state.clock_hz)
            self.breakpoints.clear()
            self.break_list.setText("No breakpoints")
        if packet["reset"]:
            self.serial.clear()
            for slider, label, channel in self.adc_sliders:
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)
                label.setText(f"ADC{channel} / GP{26 + channel}: 0")
        self.run_action.setEnabled(not self.running and state.loaded)
        self.pause_action.setEnabled(self.running)
        self.step_action.setEnabled(not self.running and state.loaded)
        self.run_n_button.setEnabled(not self.running and state.loaded)
        self.instruction_spin.setEnabled(not self.running)
        self.clock_spin.setEnabled(not self.running)
        self.clock_button.setEnabled(not self.running and state.loaded)
        self.add_part_button.setEnabled(not self.running)
        self.part_kind.setEnabled(not self.running)
        self.scene.display(state, packet["pins"], self.running, packet.get("devices"), packet.get("endpoints"))
        self.pio_inspector.display(packet["pio"], state.clock_hz)
        self.dma_inspector.display(packet["dma"])
        self.cores_inspector.display(packet["cores"])
        for i, value in enumerate(state.registers + (state.xpsr,)):
            self.registers.item(i, 1).setText(f"0x{value:08x}")
        for i, (csr, div, cc, top) in enumerate(packet.get("pwm", [])):
            frequency = state.clock_hz * 16 / div / (top + 1) if csr & 1 else 0
            a, b = min(1, (cc & 65535) / (top + 1)), min(1, (cc >> 16) / (top + 1))
            if csr & 4:
                a = 1 - a
            if csr & 8:
                b = 1 - b
            for column, text in enumerate((f"{frequency:.1f}", f"{100 * a:.1f}", f"{100 * b:.1f}"), 1):
                self.pwm_table.item(i, column).setText(text)
        location = self.symbols.location(state.registers[15]) if self.symbols else None
        self.location_label.setText(f"{location.file}:{location.line}" if location else f"PC 0x{state.registers[15]:08x}")
        text = "".join(chr(e.value) for e in packet["events"] if e.kind == 2 and e.source == 0)
        if text:
            from PySide6.QtGui import QTextCursor
            self.serial.moveCursor(QTextCursor.MoveOperation.End)
            self.serial.insertPlainText(text)
        self.waveform.feed(state, packet["events"], packet["reset"])
        status = "RUNNING" if self.running else "PAUSED" if state.stop in (Stop.BUDGET, Stop.DEADLINE) else state.stop.name
        self.status.setText(f"{status}   ·   {state.time_ns / 1e6:,.2f} ms virtual   ·   "
                            f"{state.instructions:,} instructions   ·   "
                            f"{state.clock_hz / 1e6:g} MHz ticks   ·   {self.playback_speed:g}× playback   ·   "
                            f"{state.dropped_events} trace drops" +
                            (f"   ·   {packet['remaining_instructions']:,} instructions remaining"
                             if packet["remaining_instructions"] is not None else "") +
                            (f"   ·   GPIO contention 0x{state.gpio_conflict:08x}" if state.gpio_conflict else ""))

    @Slot(str)
    def error(self, message):
        self.logs.appendPlainText(message)
        self.tabs.setCurrentWidget(self.logs)
        self.status.setText("ERROR · " + message)

    def _read_symbols(self, path):
        self.symbols = None
        if path:
            try:
                self.symbols = Symbols(path.read_bytes())
            except Exception as error:
                self.logs.appendPlainText(f"Debug metadata unavailable: {error}")

    def _change_circuit(self):
        try:
            self.project.validate()
            self.clock_spin.setValue(self.project.clock_hz)
            self.scene.set_project(self.project)
            self.breakpoints.clear()
            self.break_list.setText("No breakpoints")
            self.send("project", project=deepcopy(self.project))
            self.project_label.setText(self.project.name)
        except (ValueError, OSError) as error:
            self.error(str(error))

    def add_part(self, checked=False, *, kind=None, x=None, y=None):
        if self.running:
            return
        kind = kind or self.part_kind.currentText()
        ids = {c.id for c in self.project.components}
        index = 1
        while kind + str(index) in ids:
            index += 1
        self.project.components.append(Component(kind + str(index), kind,
                                                 650 if x is None else x,
                                                 110 + 110 * (len(ids) % 5) if y is None else y))
        self._change_circuit()

    def disconnect_port(self, endpoint):
        if self.running:
            return
        wires = [wire for wire in self.project.wires if endpoint not in wire]
        if len(wires) != len(self.project.wires):
            self.project.wires = wires
            self._change_circuit()

    def add_wire(self, a, b):
        if self.running or tuple(sorted((a, b))) in {tuple(sorted(w)) for w in self.project.wires}:
            return
        self.project.wires.append((a, b))
        self._change_circuit()

    def move_part(self, key, x, y):
        if not self.running:
            for c in self.project.components:
                if c.id == key:
                    c.x, c.y = x, y

    def edit_part(self, key):
        if self.running:
            return
        component = next((c for c in self.project.components if c.id == key), None)
        if component is None:
            return
        if component.kind == "i2c_sensor":
            value, accepted = QInputDialog.getText(self, "I2C sensor", "Device address (hex or decimal)", text=f"0x{component.address:02x}")
            if accepted:
                try:
                    address = int(value, 0)
                    if not 8 <= address <= 119:
                        raise ValueError
                    component.address = address
                    self._change_circuit()
                except ValueError:
                    self.error("I2C address must be between 0x08 and 0x77")
        else:
            value, accepted = QInputDialog.getText(self, "Component label", "Label", text=component.label)
            if accepted:
                component.label = value[:256]
                self.scene.set_project(self.project)

    def delete_selected(self):
        if self.running:
            return
        selected = {item.data(0) for item in self.scene.selectedItems()}
        if not selected:
            return
        deleted = {key for key in selected if isinstance(key, str) and not key.startswith("wire:")}
        wires = {int(key.split(":")[1]) for key in selected if isinstance(key, str) and key.startswith("wire:")}
        self.project.components = [c for c in self.project.components if c.id not in deleted]
        self.project.wires = [w for i, w in enumerate(self.project.wires) if i not in wires and
                              not any(e.split(":")[0] in deleted for e in w)]
        self._change_circuit()

    def new_project(self):
        self.send("pause")
        self.project = Project.demo()
        self.source_path = None
        self.editor.setPlainText(demo.source())
        self.symbols = None
        self._change_circuit()

    def new_workbench(self):
        self.send("pause")
        self.project = Project.workbench()
        self.source_path = None
        self.editor.setPlainText(demo.source("workbench"))
        self.symbols = None
        self._change_circuit()

    def new_pio_blink(self):
        self.send("pause")
        self.project = Project.pio_blink()
        self.source_path = None
        self.editor.setPlainText(demo.source("pio_blink"))
        self.symbols = None
        self._change_circuit()
        self.inspector.setCurrentWidget(self.pio_inspector)

    def new_dma_pio(self):
        self.send("pause")
        self.project = Project.dma_pio()
        self.source_path = None
        self.editor.setPlainText(demo.source("dma_pio"))
        self.symbols = None
        self._change_circuit()
        self.inspector.setCurrentWidget(self.dma_inspector)

    def new_multicore(self):
        self.send("pause")
        self.project = Project.multicore()
        self.source_path = None
        self.editor.setPlainText(demo.source("multicore"))
        self.symbols = None
        self._change_circuit()
        self.inspector.setCurrentWidget(self.cores_inspector)

    def new_sdk(self, name="sdk_blink"):
        self.send("pause")
        self.project = Project.sdk_blink() if name == "sdk_blink" else Project.sdk_uart()
        self.source_path = None
        self.editor.setPlainText(demo.source(name))
        self.symbols = None
        self._change_circuit()

    def new_rom(self, flash_test=False):
        self.send("pause")
        rom = self.project.bootrom
        rom = str(self.project.resolve(rom)) if rom else ""
        self.project = Project.rom_flash() if flash_test else Project.rom_blink()
        self.project.bootrom = rom
        self.source_path = None
        self.editor.setPlainText(demo.source(self.project.builtin))
        self.symbols = None
        self._change_circuit()

    def select_bootrom(self):
        name, _ = QFileDialog.getOpenFileName(self, "Select official B2 boot ROM", "", "ROM binary (*.bin)")
        if name:
            from ..images import bootrom
            try:
                bootrom(Path(name))
                self.project.bootrom = str(Path(name).resolve())
                self._change_circuit()
            except (OSError, ValueError) as error:
                self.error(str(error))

    def new_micropython(self):
        self.send('pause')
        rom = self.project.bootrom
        rom = str(self.project.resolve(rom)) if rom else ''
        self.project = Project.micropython()
        self.project.bootrom = rom
        self.source_path = None
        self.editor.setPlainText(demo.source('micropython'))
        self.symbols = None
        self._change_circuit()

    def open_project(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Circuit projects (*.json)")
        if not name:
            return
        try:
            project = Project.load(Path(name))
            source = project.resolve(project.source) if project.source else None
            text = project.source_text if project.source_text is not None else source.read_text() if source else demo.source(project.builtin)
            self.project, self.source_path = project, source
            self.editor.setPlainText(text)
            self._read_symbols(project.resolve(project.firmware) if project.firmware else None)
            self._change_circuit()
        except (OSError, ValueError) as error:
            self.error(str(error))

    def save_project(self):
        name, _ = QFileDialog.getSaveFileName(self, "Save project", str(self.project.path or "circuit.json"), "Circuit projects (*.json)")
        if name:
            try:
                self.project.source_text = self.editor.toPlainText()
                self.project.save(Path(name))
            except (OSError, ValueError) as error:
                self.error(str(error))

    def open_firmware(self):
        filters = "Direct-load ARM ELF (*.elf *.axf);;Pico SDK ELF — skip boot2 (*.elf *.axf);;RP2040 ROM boot (*.uf2 *.bin *.elf)"
        selected = filters.split(";;")[("direct", "sdk", "rom").index(self.project.startup)]
        name, selected = QFileDialog.getOpenFileName(self, "Load ARM firmware", "", filters, selected)
        if name:
            self.project.firmware = str(Path(name).resolve())
            self._read_symbols(Path(name))
            startup = "rom" if selected.startswith("RP2040") else "sdk" if selected.startswith("Pico SDK") else "direct"
            self.send("load", path=Path(name), startup=startup)

    def open_source(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open source", "", "Source (*.c *.py)")
        if name:
            try:
                self.editor.setPlainText(Path(name).read_text())
                self.source_path = Path(name).resolve()
                self.project.source = str(self.source_path)
            except OSError as error:
                self.error(str(error))

    def save_source(self):
        python = self.project.builtin == 'micropython'
        name, _ = QFileDialog.getSaveFileName(self, "Save source", str(self.source_path or ('main.py' if python else 'firmware.c')), "Python source (*.py)" if python else "C source (*.c)")
        if name:
            try:
                self.source_path = Path(name).resolve()
                self.source_path.write_text(self.editor.toPlainText())
                self.project.source = str(self.source_path)
            except OSError as error:
                self.error(str(error))

    def build_source(self):
        if self.project.builtin == 'micropython':
            self.send('python', source=self.editor.toPlainText())
            self.tabs.setCurrentWidget(self.serial.parentWidget())
            return
        if self.build_thread is not None:
            return
        root = (self.project.path.parent if self.project.path else Path.cwd()) / "build"
        root.mkdir(parents=True, exist_ok=True)
        linker = self.project.resolve(self.project.linker) if self.project.linker else root / "direct.ld"
        if not self.project.linker:
            linker.write_text(demo.linker())
        # Build a scratch copy so editor changes do not overwrite the user's file.
        source = root / "editor.c"
        self.build_task = BuildTask(self.editor.toPlainText(), source, root / "editor.elf", linker, self.project.startup)
        self.build_thread = QThread(self)
        self.build_task.moveToThread(self.build_thread)
        self.build_thread.started.connect(self.build_task.run)
        self.build_task.done.connect(self.built)
        self.build_task.failed.connect(self.error)
        self.build_task.finished.connect(self.build_thread.quit, Qt.ConnectionType.DirectConnection)
        self.build_thread.finished.connect(self.build_task.deleteLater)
        self.build_thread.finished.connect(self.build_finished)
        self.build_action.setEnabled(False)
        self.cancel_action.setEnabled(True)
        self.logs.appendPlainText("Building Pico SDK 2.2.0 firmware…" if self.project.startup in ("sdk", "rom") else "Building freestanding Cortex-M0+ firmware…")
        self.build_thread.start()

    @Slot(object)
    def built(self, result):
        path, log, startup = result
        self.logs.appendPlainText(log + f"Built {path}")
        self.project.firmware = str(path.resolve())
        self.project.startup = startup
        self._read_symbols(path)
        self.send("load", path=path, startup=startup)

    @Slot()
    def build_finished(self):
        # finished() precedes deferred worker deletion; keep Python references
        # alive until that cleanup has completed in the owning thread.
        self.build_thread.wait()
        self.build_thread.deleteLater()
        self.build_thread = self.build_task = None
        self.build_action.setEnabled(True)
        self.cancel_action.setEnabled(False)

    def cancel_build(self):
        if self.build_task:
            self.build_task.cancel()

    def export_flash(self):
        name, _ = QFileDialog.getSaveFileName(self, 'Export flash image', 'flash.bin', 'Flash binary (*.bin)')
        if name:
            self.send('save_flash', path=Path(name))

    def send_serial(self):
        self.send("uart", data=(self.serial_input.text() + "\n").encode())
        self.serial_input.clear()

    def read_memory(self):
        try:
            address = int(self.memory_address.text(), 0)
            length = 128 if 0x10000000 <= address < 0x10200000 or 0x20000000 <= address < 0x20042000 else 4
            self.send("memory", address=address, length=length)
        except ValueError:
            self.error("Enter a hexadecimal or decimal memory address")

    @Slot(object)
    def show_memory(self, result):
        address, data = result
        self.memory_text.setPlainText("\n".join(
            f"{address + i:08x}  " + " ".join(f"{b:02x}" for b in data[i:i + 16])
            for i in range(0, len(data), 16)))

    def toggle_breakpoint(self):
        try:
            text = self.break_address.text().strip()
            address = self.symbols.names[text] if self.symbols and text in self.symbols.names else int(text, 0)
            if not 0 <= address <= 0xffffffff or address & 1:
                raise ValueError
            enabled = address not in self.breakpoints
            if enabled:
                self.breakpoints.add(address)
            else:
                self.breakpoints.remove(address)
            self.send("breakpoint", address=address, enabled=enabled)
            self.break_list.setText(", ".join(f"0x{a:08x}" for a in sorted(self.breakpoints)) or "No breakpoints")
        except ValueError:
            self.error("Enter an even instruction address or a known ELF function name")

    def export_recording(self):
        name, _ = QFileDialog.getSaveFileName(self, "Export input recording", "inputs.json", "JSON (*.json)")
        if name:
            self.send("recording", path=Path(name))

    def closeEvent(self, event):
        self.cancel_build()
        self.send("shutdown")
        if not self.thread.wait(5000):
            self.error("Waiting for the simulation worker to stop")
            event.ignore()
            return
        if self.build_thread is not None and not self.build_thread.wait(5000):
            event.ignore()
            return
        event.accept()


def _configure_wslg_display():
    """Find WSLg when systemd's runtime directory lacks its Wayland socket."""
    if (sys.platform != "linux" or not os.environ.get("WSL_DISTRO_NAME")
            or os.environ.get("QT_QPA_PLATFORM")):
        return
    display = os.environ.get("WAYLAND_DISPLAY")
    if not display or Path(display).is_absolute():
        return
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and (Path(runtime) / display).is_socket():
        return
    socket = Path("/mnt/wslg/runtime-dir") / display
    if socket.is_socket():
        # An absolute Wayland path avoids changing XDG_RUNTIME_DIR for other APIs.
        os.environ["WAYLAND_DISPLAY"] = str(socket)


def launch(project_path=None, screenshot=None):
    if QApplication.instance() is None:
        _configure_wslg_display()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("RP2040 Lab")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = Window(project_path)
    window.show()
    if screenshot:
        QTimer.singleShot(200, lambda: window.send("run"))

        def capture():
            window.tabs.setCurrentWidget(window.waveform)
            app.processEvents()
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(screenshot))
            window.close()

        QTimer.singleShot(1800, capture)
    return app.exec()
