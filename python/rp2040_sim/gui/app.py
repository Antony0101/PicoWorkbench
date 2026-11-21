from copy import deepcopy
from pathlib import Path
import os
import re
import sys

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget, QSlider, QSpinBox,
    QScrollArea, QCheckBox, QListWidget, QTabBar, QSizePolicy,
)

from .. import demo
from ..build import FirmwareBuild, SdkFirmwareBuild
from ..project import Project
from ..symbols import Symbols
from ..engine import Stop
from .canvas import CircuitScene, CircuitView
from .waveform import Waveform
from .worker import Worker
from .pio import PioInspector
from .dma import DmaInspector
from .cores import CoresInspector
from .menus import install_table_menu, popup_menu, text_menu
from .editor import SourceEditor
from .editor_pane import EditorPane
from .document import CircuitHistory, fingerprint
from .workspace import Workspace
from .workflow import Workflow

STYLE = """
QMainWindow, QWidget { background: #111d26; color: #d6e4ec; font-size: 12px; }
QMenuBar, QMenu, QToolBar { background: #182a35; border: 0; padding: 5px; }
QMenu::item:selected { background: #2c5261; }
QMenu::item:disabled { color: #607685; }
QToolBar { spacing: 8px; border-bottom: 1px solid #2b414f; }
QToolButton, QPushButton { background: #223c49; border: 1px solid #355361; border-radius: 5px; padding: 7px 12px; }
QToolButton:hover, QPushButton:hover { background: #315867; }
QToolButton:disabled, QPushButton:disabled { color: #607685; background: #192c36; }
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus { border: 2px solid #84d8ae; }
QLineEdit, QComboBox, QSpinBox { background: #142630; border: 1px solid #355361; border-radius: 4px; padding: 5px; }
QPlainTextEdit, QTableWidget { background: #101d25; border: 0; selection-background-color: #275061; }
QHeaderView::section { background: #1d3440; color: #93adbd; border: 0; padding: 5px; }
QTabWidget::pane { border: 1px solid #2a414e; }
QTabBar::tab { background: #182b36; color: #8ca8b9; padding: 9px 17px; }
QTabBar::tab:selected { color: #68e0b7; background: #203b45; border-bottom: 2px solid #68e0b7; }
QTabBar#workspaceTabs { background: transparent; }
QTabBar#workspaceTabs::tab { padding: 7px 12px; border: 1px solid #355361; }
QTabBar#workspaceTabs::tab:selected { border-bottom: 2px solid #68e0b7; }
QTabBar#workspaceTabs:focus { border: 1px solid #84d8ae; }
QSplitter::handle { background: #29414e; height: 4px; width: 4px; }
QStatusBar { background: #182c36; color: #91acbb; }
QScrollBar:vertical { background: #182b35; width: 10px; }
QScrollBar::handle:vertical { background: #3c5968; min-height: 20px; }
QScrollBar:horizontal { background: #182b35; height: 10px; }
QScrollBar::handle:horizontal { background: #3c5968; min-width: 20px; }
"""


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


class Window(Workflow, Workspace, QMainWindow):
    dispatch = Signal(object)

    def __init__(self, project_path=None, *, settings=None):
        super().__init__()
        self.setWindowTitle("RP2040 Lab")
        self.resize(1280, 760)
        self.settings = settings
        self.project = Project.load(project_path) if project_path else Project.demo()
        self.running = False
        self.playback_speed = 1.0
        self.state = None
        self.symbols = None
        self.breakpoints = set()
        self.build_thread = None
        self.build_task = None
        self.history = CircuitHistory()
        self.callbacks = {}
        self.request_number = 0
        self.busy = False
        self.saved_document = None
        self.applied = fingerprint(self.project, circuit=True) if not project_path else None
        self.loaded_source = None
        self.loaded_name = ''
        self.flash_dirty = False
        self.discard_flash = False
        self._loading_document = True
        self._after_load_run = False
        self._closing = False
        self.source_path = self.project.resolve(self.project.source) if self.project.source else None
        self.device_source_path = None
        self.device_source_saved = None
        self.files_busy = False
        self._display_frames = {}
        self.session_actions = []
        self._make_ui()
        if self.project.builtin == "pio_blink":
            self.inspector.setCurrentWidget(self.pio_inspector)
        elif self.project.builtin == "dma_pio":
            self.inspector.setCurrentWidget(self.dma_inspector)
        elif self.project.builtin == "multicore":
            self.inspector.setCurrentWidget(self.cores_inspector)
        self.editor.setPlainText(self.project.source_text if self.project.source_text is not None else
                                 self.source_path.read_text() if self.source_path else demo.source(self.project.builtin))
        self.source_saved = self.editor.toPlainText()
        self.saved_document = fingerprint(self.project, self.editor.toPlainText())
        self._loading_document = False
        self.setup_workspace()
        self.thread = QThread(self)
        self.worker = Worker()
        self.worker.moveToThread(self.thread)
        self.dispatch.connect(self.worker.command, Qt.ConnectionType.QueuedConnection)
        self.thread.started.connect(self.worker.initialize)
        self.worker.updated.connect(self.update_state)
        self.worker.failed.connect(self.error)
        self.worker.acknowledged.connect(self.command_completed)
        self.worker.memory.connect(self.show_memory)
        self.worker.file_result.connect(self.file_completed)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()
        if self.plotter.uart.currentIndex():
            self.send('plot_port', port=self.plotter.uart.currentIndex())
        if project_path:
            self.request('project', lambda ok: None, project=deepcopy(self.project), source=self.editor.toPlainText())
        self._read_symbols(self.project.resolve(self.project.firmware) if self.project.firmware else None)
        self.document_changed()

    def action(self, title, callback, shortcut=None):
        action = QAction(title, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(callback)
        self.session_actions.append(action)
        return action

    def _make_ui(self):
        file_menu = self.menuBar().addMenu("File")
        examples_menu = None
        for title, callback, shortcut in [
            ("New project / examples…", self.choose_example, "Ctrl+N"),
            ("New demo project", self.new_project, None),
            ("New peripheral workbench", self.new_workbench, None),
            ("New PIO blink demo", self.new_pio_blink, None),
            ("New DMA to PIO demo", self.new_dma_pio, None),
            ("New two-core demo", self.new_multicore, None),
            ("New Pico SDK blink", lambda: self.new_sdk("sdk_blink"), None),
            ("New Pico SDK UART", lambda: self.new_sdk("sdk_uart"), None),
            ("New ROM boot blink", self.new_rom, None),
            ("New ROM flash test", lambda: self.new_rom(True), None),
            ("New MicroPython project", self.new_micropython, None),
            ("New C temperature dashboard", lambda: self.new_dashboard(), None),
            ("New MicroPython temperature dashboard", lambda: self.new_dashboard(True), None),
            ("Select B2 boot ROM…", self.select_bootrom, None),
            ("Open project…", self.open_project, "Ctrl+O"),
            ("Save project…", self.save_project, "Ctrl+S"),
            ("Load firmware ELF / UF2…", self.open_firmware, "Ctrl+L"),
            ("Open source…", self.open_source, None),
            ("Save source as…", self.save_source, "Ctrl+Shift+S"),
            ("Export input recording…", self.export_recording, None),
            ("Export flash image…", self.export_flash, None),
        ]:
            if title.startswith('New ') and title != 'New project / examples…':
                if examples_menu is None:
                    examples_menu = file_menu.addMenu('Examples')
                examples_menu.addAction(self.action(title, callback, shortcut))
            else:
                file_menu.addAction(self.action(title, callback, shortcut))
        edit = self.menuBar().addMenu('Edit')
        self.edit_menu = edit
        self.undo_action = self.action('Undo circuit', self.undo, 'Ctrl+Z')
        self.redo_action = self.action('Redo circuit', self.redo, 'Ctrl+Shift+Z')
        edit.addActions([self.undo_action, self.redo_action,
                         self.action('Duplicate selected', self.duplicate_selected, 'Ctrl+D')])
        view_menu = self.menuBar().addMenu('View')
        self.panels_menu = view_menu.addMenu('Panels')
        self.inspector_action = self.action('Inspector', lambda: None)
        self.output_action = self.action('Output panel', lambda: None, 'Ctrl+J')
        self.advanced_action = self.action('Advanced execution controls', lambda: None)
        for action in (self.inspector_action, self.output_action, self.advanced_action):
            action.setCheckable(True)
            view_menu.addAction(action)
        view_menu.addSeparator()
        for name in ('Circuit', 'Code', 'Debug'):
            view_menu.addAction(self.action(name + ' workspace', lambda checked=False, name=name: self.select_workspace(name)))
        view_menu.addAction(self.action('Reset layout', self.reset_layout))
        view_menu.addAction(self.action('Larger editor text', lambda: self.set_editor_font(self.editor.font().pointSize() + 1)))
        view_menu.addAction(self.action('Smaller editor text', lambda: self.set_editor_font(self.editor.font().pointSize() - 1)))
        help_menu = self.menuBar().addMenu('Help')
        help_menu.addAction(self.action('Getting started', lambda: self.guide.show()))
        help_menu.addAction(self.action('ROM and compiler setup…', self.show_setup))
        help_menu.addAction(self.action('Code assistance setup…', lambda: self.assistance.setup_dialog()))
        toolbar = QToolBar("Simulation", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        brand = QLabel("  RP2040 LAB   ")
        brand.setStyleSheet("font-size: 17px; font-weight: 700; color: #6ee6bd;")
        toolbar.addWidget(brand)
        self.run_action = self.action("▶ Run", lambda: self.send("run"), "F5")
        self.pause_action = self.action("Ⅱ Pause", lambda: self.send("pause"), "F6")
        self.step_action = self.action("Step instruction", lambda: self.send("step"), "F10")
        self.step_action.setToolTip('One instruction on the next runnable core; peripherals advance. This is not source-line Step Over.')
        self.reset_action = self.action("Reset", lambda: self.send("reset"), "Ctrl+R")
        for action in [self.run_action, self.pause_action, self.step_action, self.reset_action]:
            toolbar.addAction(action)
        toolbar.addSeparator()
        self.build_action = self.action("Build + Load", self.build_source, "F7")
        toolbar.addAction(self.build_action)
        self.cancel_action = self.action("Cancel build", self.cancel_build)
        self.cancel_action.setEnabled(False)
        toolbar.addAction(self.cancel_action)
        self.apply_action = self.action('Apply circuit + Reset', self.apply_circuit)
        toolbar.addAction(self.apply_action)
        self.workspace_tabs = QTabBar()
        self.workspace_tabs.setObjectName('workspaceTabs')
        self.workspace_tabs.setAccessibleName('Workspace layout')
        self.workspace_tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.workspace_tabs.setExpanding(True)
        self.workspace_tabs.setUsesScrollButtons(False)
        self.workspace_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.workspace_tabs.setDrawBase(False)
        self.workspace_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for name, description in [('Circuit', 'Wire components and inspect the circuit'),
                                  ('Code', 'Edit C or MicroPython source'),
                                  ('Debug', 'Inspect code, chip state and output together')]:
            index = self.workspace_tabs.addTab(name)
            self.workspace_tabs.setTabToolTip(index, description)
        toolbar.addWidget(self.workspace_tabs)
        self.startup_label = QLabel()

        self.addToolBarBreak()
        execution = QToolBar("Execution controls", self)
        execution.setMovable(False)
        self.addToolBar(execution)
        self.execution_toolbar = execution
        execution.addWidget(QLabel('  Startup '))
        self.startup_combo = QComboBox()
        self.startup_combo.setAccessibleName('Firmware startup mode')
        self.startup_combo.addItem('Direct load', 'direct')
        self.startup_combo.addItem('ROM boot', 'rom')
        self.startup_combo.addItem('SDK shortcut', 'sdk')
        self.startup_combo.setItemData(0, 'Build freestanding C with an explicit vector table and reset_handler. Skip boot ROM.', Qt.ItemDataRole.ToolTipRole)
        self.startup_combo.setItemData(1, 'Build Pico SDK C with main(); execute the real B2 ROM, boot2, then firmware.', Qt.ItemDataRole.ToolTipRole)
        self.startup_combo.setItemData(2, 'Build Pico SDK C with main(); start at firmware vectors, skipping ROM and boot2.', Qt.ItemDataRole.ToolTipRole)
        self.startup_combo.setCurrentIndex(self.startup_combo.findData(self.project.build_mode))
        self.startup_combo.currentIndexChanged.connect(self.change_build_mode)
        execution.addWidget(self.startup_combo)
        execution.addWidget(QLabel("  Playback speed "))
        self.speed_combo = QComboBox()
        for speed in (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0):
            self.speed_combo.addItem(f"{speed:g}×", speed)
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(1.0))
        self.speed_combo.setToolTip("Target virtual seconds per real second. Preserves chip timing; limited by host performance.")
        self.speed_combo.currentIndexChanged.connect(
            lambda _: self.send("speed", value=self.speed_combo.currentData()))
        execution.addWidget(self.speed_combo)
        advanced = QWidget()
        advanced_row = QHBoxLayout(advanced)
        advanced_row.setContentsMargins(0, 0, 0, 0)
        advanced_row.addWidget(QLabel("Functional CPU clock "))
        self.clock_spin = QSpinBox()
        self.clock_spin.setRange(1_000, 133_000_000)
        self.clock_spin.setSingleStep(1_000)
        self.clock_spin.setGroupSeparatorShown(True)
        self.clock_spin.setSuffix(" Hz")
        self.clock_spin.setValue(self.project.clock_hz)
        self.clock_spin.setToolTip("Functional instruction clock. Applying reloads firmware and resets chip state and breakpoints.")
        advanced_row.addWidget(self.clock_spin)
        self.clock_button = QPushButton("Apply + Reset")
        self.clock_button.clicked.connect(lambda: self.send("clock", hz=self.clock_spin.value()))
        advanced_row.addWidget(self.clock_button)
        self.instruction_spin = QSpinBox()
        self.instruction_spin.setRange(1, 1_000_000)
        self.instruction_spin.setGroupSeparatorShown(True)
        self.instruction_spin.setValue(1_000)
        self.instruction_spin.setSuffix(" instructions")
        advanced_row.addWidget(self.instruction_spin)
        self.run_n_button = QPushButton("Run N")
        self.run_n_button.setToolTip("Run at the selected playback speed, then pause after N instructions or at a breakpoint.")
        self.run_n_button.clicked.connect(lambda: self.send("run_instructions", count=self.instruction_spin.value()))
        advanced_row.addWidget(self.run_n_button)
        self.advanced_widget_action = execution.addWidget(advanced)
        self.advanced_action.toggled.connect(self.advanced_widget_action.setVisible)
        self.advanced_widget_action.setVisible(False)
        self.identity_label = QLabel('Initializing…')
        self.identity_label.setMinimumWidth(0)
        execution.addWidget(self.identity_label)

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
        self.part_kind.addItems(["led", "button", "probe", "pullup", "pulldown", "i2c_sensor", "spi_loopback", "ssd1306"])
        self.part_kind.setAccessibleName('Component type to add')
        controls.addWidget(self.part_kind)
        self.add_part_button = QPushButton("+ Add")
        self.add_part_button.clicked.connect(self.add_part)
        controls.addWidget(self.add_part_button)
        fit = QPushButton("Fit canvas")
        fit.clicked.connect(self.view.fit_canvas)
        controls.addWidget(fit)
        for title, factor in [('−', 1 / 1.15), ('+', 1.15)]:
            zoom = QPushButton(title)
            zoom.setAccessibleName('Zoom out' if factor < 1 else 'Zoom in')
            zoom.clicked.connect(lambda checked=False, factor=factor: self.view.scale(factor, factor))
            controls.addWidget(zoom)
        grid = QCheckBox('Snap')
        grid.toggled.connect(lambda value: setattr(self.scene, 'grid', value))
        controls.addWidget(grid)
        controls.addStretch()
        self.connection_status = QLabel('Logical GPIOs · not Pico header numbers')
        self.connection_status.setWordWrap(True)
        layout.addLayout(controls)
        layout.addWidget(self.connection_status)
        layout.addWidget(self.view)

        self.inspector = inspector = QTabWidget()
        inspector.setMinimumWidth(260)
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
        self.breakpoint_items = QListWidget()
        self.breakpoint_items.setMaximumHeight(100)
        self.breakpoint_items.setAccessibleName('Breakpoints; activate an entry to remove it')
        self.breakpoint_items.itemActivated.connect(lambda item: self.toggle_address(int(item.data(Qt.ItemDataRole.UserRole))))
        cpu_layout.addWidget(self.breakpoint_items)
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
        self.properties_panel = self.make_properties()
        inspector.addTab(self.properties_panel, 'Properties')
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
        self.top_splitter = top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(workspace)
        self.inspector_panel = QWidget()
        panel_layout = QVBoxLayout(self.inspector_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_choice = QComboBox()
        self.inspector_choice.setAccessibleName('Inspector panel')
        self.inspector_header = QHBoxLayout()
        self.inspector_header.addWidget(self.inspector_choice, 1)
        panel_layout.addLayout(self.inspector_header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumSize(270, 100)
        scroll.setWidget(inspector)
        panel_layout.addWidget(scroll)
        top.addWidget(self.inspector_panel)
        self.inspector_action.setChecked(True)
        top.setSizes([1040, 350])

        self.tabs = QTabWidget()
        self.editor = SourceEditor()
        self.editor.setFont(QFont("monospace", 11))
        self.editor.setTabStopDistance(32)
        self.highlighter = self.editor.highlighter
        self.addAction(self.action('Toggle source breakpoint', lambda: self.source_breakpoint(self.editor.textCursor().blockNumber() + 1), 'F9'))
        self.editor_pane = EditorPane(self.editor)
        self.edit_menu.addSeparator()
        self.edit_menu.addActions(self.editor_pane.actions)
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
        self.serial_panel = serial
        self.tabs.addTab(serial, "Serial · UART0")
        self.waveform = Waveform()
        self.tabs.addTab(self.waveform, "Logic analyzer")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(5000)
        self.logs.setFont(QFont("monospace", 10))
        self.tabs.addTab(self.logs, "Build + diagnostics")
        from .runtime import SerialPlotter, BusInspector
        self.plotter = SerialPlotter(self.send)
        self.bus_inspector = BusInspector(self.send)
        self.tabs.addTab(self.plotter, 'Serial plotter')
        self.tabs.addTab(self.bus_inspector, 'Bus inspector')
        from .files import FilesPanel
        self.files_panel = FilesPanel(self)
        self.tabs.addTab(self.files_panel, 'Pico files')
        self.vertical = vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.addWidget(top)
        self.editor_area = QSplitter(Qt.Orientation.Vertical)
        self.editor_area.addWidget(self.editor_pane)
        self.editor_area.addWidget(self.tabs)
        self.editor_area.setSizes([500, 180])
        vertical.addWidget(self.editor_area)
        vertical.setSizes([650, 300])
        self.output_action.setChecked(True)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        self.guide = QWidget()
        guide_layout = QHBoxLayout(self.guide)
        guide = QLabel('Start: Run an example → add/wire parts while paused → Apply circuit → Build / Run Python. Playback slows the view.')
        guide.setWordWrap(True)
        guide_layout.addWidget(guide)
        examples = QPushButton('Examples…')
        examples.setMaximumWidth(120)
        examples.clicked.connect(self.choose_example)
        guide_layout.addWidget(examples)
        dismiss = QPushButton('Dismiss')
        dismiss.setMaximumWidth(90)
        dismiss.clicked.connect(self.guide.hide)
        guide_layout.addWidget(dismiss)
        central_layout.addWidget(self.guide)
        self.fault_banner = QLabel()
        self.fault_banner.setWordWrap(True)
        self.fault_banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.fault_banner.hide()
        central_layout.addWidget(self.fault_banner)
        self.recovery = QWidget()
        recovery_layout = QHBoxLayout(self.recovery)
        recovery_layout.setContentsMargins(4, 0, 4, 0)
        for title, callback in [('Details', lambda: self.panels.reveal('build')),
                                ('ROM / compiler setup', self.show_setup),
                                ('Export runtime flash…', self.export_flash),
                                ('Dismiss', lambda: (self.fault_banner.hide(), self.recovery.hide()))]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            recovery_layout.addWidget(button)
        recovery_layout.addStretch()
        self.recovery.hide()
        central_layout.addWidget(self.recovery)
        central_layout.addWidget(vertical, 1)
        self.setCentralWidget(central)
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
        self.status.setMinimumWidth(0)
        self.status.setWordWrap(True)
        self.statusBar().addPermanentWidget(self.startup_label)
        self.project_label = QLabel(self.project.name)
        self.statusBar().addPermanentWidget(self.project_label)
        QTimer.singleShot(100, self.view.fit_canvas)

    def output_context_menu(self, editor, position):
        actions = ([*self.editor_pane.actions, self.build_action, self.cancel_action] if editor is self.editor else
                   [self.interrupt_action] if editor is self.serial else [])
        menu = text_menu(editor, position, actions, clear=editor in (self.serial, self.logs))
        key = 'editor' if editor is self.editor else 'serial' if editor is self.serial else 'build'
        menu.addSeparator()
        floating = self.panels.panels[key].host is not None
        menu.addAction('Dock back' if floating else 'Open in separate window',
                       lambda: self.panels.dock(key) if floating else self.panels.detach(key))
        return menu

    # Register slots on the QObject subclass so Qt always dispatches GUI work
    # on its owning thread, including handlers implemented in Python mixins.
    @Slot(object)
    def command_completed(self, result):
        Workflow.command_completed(self, result)

    @Slot(object)
    def file_completed(self, result):
        self.files_panel.completed(result)

    @Slot(object)
    def built(self, result):
        Workflow.built(self, result)

    @Slot(str)
    def build_failed(self, message):
        self.assistance.compiled(message, self.build_text, [self.source_path])
        self.error(message)

    @Slot()
    def build_finished(self):
        Workflow.build_finished(self)

    @Slot(object)
    def show_memory(self, result):
        Workflow.show_memory(self, result)

    @Slot(QWidget, QWidget)
    def editor_focus_changed(self, old, new):
        if hasattr(self, 'panels') and not self.panels.owns_widget(new):
            return
        self.update_actions()


    @Slot(object)
    def update_state(self, packet):
        self._runtime_packet = packet
        if packet.get('reset'):
            self._display_frames.clear()
        self._display_frames.update(packet.get('displays', {}))
        self.state, self.running = packet["state"], packet["running"]
        self.files_busy = packet.get('files_busy', False)
        state = self.state
        self.applied = packet.get('applied', self.applied)
        self.loaded_source = packet.get('loaded_source')
        self.loaded_name = packet.get('loaded_name', '')
        self.flash_dirty = packet.get('flash_dirty', False)
        mode = {"sdk": "SDK ELF", "rom": "B2 ROM + boot2", "direct": "direct load"}[packet.get('startup', self.project.startup)]
        self.startup_label.setText(f"2 cores · functional timing · {mode}  ")
        self.playback_speed = packet["speed"]
        previous_python = getattr(self, 'python_status', '')
        self.python_status = packet.get('python_status', '')
        if self.python_status != previous_python and self.python_status in ('finished', 'error'):
            self.logs.appendPlainText('Python program finished' if self.python_status == 'finished' else 'Python program raised an exception; see UART output')
        python = self.project.builtin == 'micropython'
        self.interrupt_action.setEnabled(python and state.loaded and not self.files_busy)
        self.interrupt_button.setEnabled(self.interrupt_action.isEnabled())
        self.build_action.setText('Run Python' if python else 'Build + Load')
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
        self.scene.display(state, packet["pins"], self.running, packet.get("devices"), packet.get("endpoints"), self._display_frames)
        self.pio_inspector.display(packet["pio"], state.clock_hz)
        self.dma_inspector.display(packet["dma"])
        self.cores_inspector.display(packet["cores"])
        previous_values = getattr(self, 'register_values', ())
        values = state.registers + (state.xpsr,)
        for i, value in enumerate(values):
            self.registers.item(i, 1).setText(f"0x{value:08x}")
            self.registers.item(i, 1).setForeground(QColor('#efc57e' if previous_values and previous_values[i] != value else '#d6e4ec'))
        self.register_values = values
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
        mapped = location and self.source_matches(location.file)
        self.location_label.setText(f"{location.file}:{location.line}" if mapped else f"PC 0x{state.registers[15]:08x} · source unavailable")
        self.editor.set_execution(location.line if mapped else 0)
        text = "".join(chr(e.value) for e in packet["events"] if e.kind == 2 and e.source == 0)
        if text:
            from PySide6.QtGui import QTextCursor
            self.serial.moveCursor(QTextCursor.MoveOperation.End)
            self.serial.insertPlainText(text)
        self.waveform.feed(state, packet["events"], packet["reset"])
        self.plotter.display(packet)
        self.bus_inspector.display(packet)
        self.files_panel.display(packet)
        status = "RUNNING" if self.running else "PAUSED" if state.stop in (Stop.BUDGET, Stop.DEADLINE) else state.stop.name
        self.status.setText(f"{status}   ·   {state.time_ns / 1e6:,.2f} ms virtual   ·   "
                            f"{state.instructions:,} instructions   ·   "
                            f"{state.clock_hz / 1e6:g} MHz ticks   ·   {self.playback_speed:g}× playback   ·   "
                            f"{state.dropped_events} trace drops" +
                            (f"   ·   {packet['remaining_instructions']:,} instructions remaining"
                             if packet["remaining_instructions"] is not None else "") +
                            (f"   ·   GPIO contention 0x{state.gpio_conflict:08x}" if state.gpio_conflict else ""))
        self.inspect_port(self.endpoint_choice.currentText())
        self.update_actions()

    @Slot(str)
    def error(self, message):
        self.logs.appendPlainText(message)
        self.panels.reveal('build')
        self.status.setText("ERROR · " + message)
        first = message.splitlines()[0] if message else 'Operation failed'
        self.fault_banner.setText(first[:240] + ' · See Build + diagnostics for details. Help → ROM and compiler setup covers dependencies.')
        self.fault_banner.show()
        self.recovery.show()
        match = re.search(r'(?:editor\.c|firmware\.c):(\d+)(?::\d+)?:', message)
        if match and getattr(self, 'build_text', None) == self.editor.toPlainText():
            self.output_action.setChecked(True)
            self.panels.reveal('editor')
            self.editor.go_to_line(int(match.group(1)))

    def _read_symbols(self, path):
        self.symbols = None
        if self.project.builtin != 'micropython':
            try:
                self.symbols = Symbols(path.read_bytes() if path else demo.image(self.project.builtin))
            except Exception as error:
                self.logs.appendPlainText(f"Debug metadata unavailable: {error}")


    def export_recording(self):
        name, _ = QFileDialog.getSaveFileName(self, "Export input recording", "inputs.json", "JSON (*.json)")
        if name:
            self.send("recording", path=Path(name))


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
    preferences = None
    settings = None
    if screenshot:
        from tempfile import TemporaryDirectory
        from PySide6.QtCore import QSettings
        preferences = TemporaryDirectory(prefix='rp2040-screenshot-')
        settings = QSettings(str(Path(preferences.name) / 'settings.ini'), QSettings.Format.IniFormat)
    window = Window(project_path, settings=settings)
    window.show()
    if screenshot:
        QTimer.singleShot(200, lambda: window.send("run"))

        def capture():
            window.panels.reveal('logic')
            app.processEvents()
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(screenshot))
            window._closing = True  # Automated capture owns no interactive edits to save.
            window.close()

        QTimer.singleShot(1800, capture)
    result = app.exec()
    if preferences:
        preferences.cleanup()
    return result
