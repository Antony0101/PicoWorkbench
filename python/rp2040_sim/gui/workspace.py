"""Presentation settings, properties and accessible alternatives to canvas gestures."""
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QFont, QColor, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget)


class Workspace:
    def setup_workspace(self):
        self.settings = getattr(self, 'settings', None) or QSettings('RP2040Lab', 'Desktop-v2')
        from .assistance import CodeAssistance
        self.assistance = CodeAssistance(self.editor, self.editor_pane, self.settings, self)
        from .panels import PanelManager
        self.panels = PanelManager(self, self.panels_menu)
        self.panels.register_defaults()
        self.inspector_action.toggled.connect(self.panels.refresh)
        self.output_action.toggled.connect(self.panels.refresh)
        self.editor.textChanged.connect(self.document_changed)
        self.editor.undoAvailable.connect(lambda available: self.update_actions())
        self.editor.redoAvailable.connect(lambda available: self.update_actions())
        QApplication.instance().focusChanged.connect(self.editor_focus_changed)
        self.editor.breakpoint_requested.connect(self.source_breakpoint)
        self.scene.selectionChanged.connect(self.selection_changed)
        self.scene.port_selected.connect(self.inspect_port)
        self.view.duplicate_requested.connect(self.duplicate_selected)
        self.view.nudge_requested.connect(self.nudge_selected)
        self.scene.pending_changed.connect(self.connection_status.setText)
        self.workspace_tabs.currentChanged.connect(
            lambda index: self.set_workspace(self.workspace_tabs.tabText(index)))
        self.inspector.tabBar().hide()
        self.inspector.setAccessibleName('Chip and circuit inspector')
        self.view.setAccessibleName('Logical GPIO circuit canvas')
        self.view.setAccessibleDescription('Use the Properties inspector for keyboard selection, wiring and component settings.')
        self.editor.setAccessibleDescription('F9 toggles a mapped C breakpoint. Source changes require Build and Load, or Run Python.')
        self.restore_preferences()
        self.refresh_properties()

    def make_properties(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.component_choice = QComboBox()
        self.component_choice.setAccessibleName('Selected component')
        self.component_choice.currentTextChanged.connect(self.select_component)
        layout.addWidget(self.component_choice)
        form = QFormLayout()
        self.property_label = QLineEdit()
        self.property_address = QSpinBox()
        self.property_address.setRange(8, 119)
        self.property_address.setDisplayIntegerBase(16)
        self.property_address.setPrefix('0x')
        form.addRow('Label', self.property_label)
        form.addRow('I²C address', self.property_address)
        layout.addLayout(form)
        self.properties_apply = QPushButton('Update component')
        self.properties_apply.clicked.connect(self.apply_properties)
        layout.addWidget(self.properties_apply)
        self.endpoint_choice = QComboBox()
        self.endpoint_choice.setAccessibleName('Port to inspect or connect')
        self.endpoint_choice.currentTextChanged.connect(self.inspect_port)
        layout.addWidget(self.endpoint_choice)
        row = QHBoxLayout()
        self.connect_button = QPushButton('Connect / finish wire')
        self.connect_button.clicked.connect(lambda: self.scene.port_clicked(self.endpoint_choice.currentText()))
        self.disconnect_button = QPushButton('Disconnect')
        self.disconnect_button.clicked.connect(lambda: self.disconnect_port(self.endpoint_choice.currentText()))
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        layout.addLayout(row)
        cancel = QPushButton('Cancel wire · Esc')
        cancel.clicked.connect(self.scene.cancel_connection)
        layout.addWidget(cancel)
        self.net_details = QPlainTextEdit()
        self.net_details.setReadOnly(True)
        self.net_details.setPlaceholderText('Select a port to inspect its connected net and logic state.')
        self.net_details.setAccessibleName('Selected net endpoints and logic state')
        layout.addWidget(self.net_details)
        move = QHBoxLayout()
        for title, x, y in [('←', -16, 0), ('↑', 0, -16), ('↓', 0, 16), ('→', 16, 0)]:
            button = QPushButton(title)
            button.setAccessibleName('Move component ' + {'←':'left','↑':'up','↓':'down','→':'right'}[title])
            button.clicked.connect(lambda checked=False, x=x, y=y: self.nudge_selected(x, y))
            move.addWidget(button)
        layout.addLayout(move)
        for title, callback in [('Duplicate component', self.duplicate_selected), ('Delete selected', self.delete_selected)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            layout.addWidget(button)
        return panel

    def refresh_properties(self):
        key = self.component_choice.currentText()
        endpoint = self.endpoint_choice.currentText()
        self.component_choice.blockSignals(True)
        self.component_choice.clear()
        self.component_choice.addItems(['Select component…'] + [c.id for c in self.project.components])
        self.component_choice.setCurrentIndex(max(0, self.component_choice.findText(key)))
        self.component_choice.blockSignals(False)
        self.endpoint_choice.blockSignals(True)
        self.endpoint_choice.clear()
        self.endpoint_choice.addItems(['Select port…'] + sorted(self.project.endpoints()))
        self.endpoint_choice.setCurrentIndex(max(0, self.endpoint_choice.findText(endpoint)))
        self.endpoint_choice.blockSignals(False)
        self.select_component(self.component_choice.currentText(), select=False)

    def selection_changed(self):
        selected = self.scene.selectedItems()
        key = next((item.data(0) for item in selected if item.data(0) in self.scene.parts), None)
        if key:
            self.component_choice.blockSignals(True)
            self.component_choice.setCurrentText(key)
            self.component_choice.blockSignals(False)
            self.select_component(key, select=False)

    def select_component(self, key, select=True):
        component = next((c for c in self.project.components if c.id == key), None)
        self.property_label.setText(component.label if component else '')
        self.property_address.setEnabled(bool(component and component.kind in ('i2c_sensor', 'ssd1306') and not self.running))
        if component:
            self.property_address.setRange(0x3c, 0x3d) if component.kind == 'ssd1306' else self.property_address.setRange(8, 119)
            self.property_address.setValue(component.address)
        self.properties_apply.setEnabled(bool(component) and not self.running)
        if component and select:
            self.scene.clearSelection()
            self.scene.parts[key].setSelected(True)
            self.view.ensureVisible(self.scene.parts[key])

    def apply_properties(self):
        key = self.component_choice.currentText()
        label, address = self.property_label.text(), self.property_address.value()
        def change(project):
            for component in project.components:
                if component.id == key:
                    component.label = label[:256]
                    if component.kind in ('i2c_sensor', 'ssd1306'):
                        component.address = address
        self.edit_circuit(change, 'Edit component')

    def inspect_port(self, endpoint):
        if not endpoint or endpoint not in self.project.endpoints():
            return
        self.endpoint_choice.blockSignals(True)
        self.endpoint_choice.setCurrentText(endpoint)
        self.endpoint_choice.blockSignals(False)
        group = next(group for group in self.project.groups() if endpoint in group)
        self.scene.highlighted = group
        pins = [int(e.split('GP')[1]) for e in group if e.startswith('pico:GP')]
        states = ', '.join(f'GP{pin}: {self.state.pin(pin).name}' for pin in sorted(pins)) if self.state else 'Not loaded'
        text = (states or 'No GPIO connection') + '\n\n' + '\n'.join(sorted(group))
        if text != self.net_details.toPlainText():
            self.net_details.setPlainText(text)
        for a, b, wire in self.scene.wire_items:
            wire.setPen(QPen(QColor('#fcdd8a' if a in group else wire.data(1) or '#4c8d87'),
                             3 if a in group or wire.isSelected() else 2))
        self.scene.update()

    @property
    def current_workspace(self):
        return self.workspace_tabs.tabText(self.workspace_tabs.currentIndex())

    def select_workspace(self, name):
        for index in range(self.workspace_tabs.count()):
            if self.workspace_tabs.tabText(index) == name:
                self.workspace_tabs.setCurrentIndex(index)
                if name == 'Code' and self.panels.panels['editor'].host is None:
                    self.editor.setFocus()
                return

    def set_workspace(self, name):
        tabs_focused = self.workspace_tabs.hasFocus()
        self.top_splitter.setVisible(name != 'Code')
        self.inspector_action.setChecked(name != 'Code')
        self.output_action.setChecked(name == 'Debug')
        if name == 'Code':
            self.vertical.setSizes([0, 700])
            if not tabs_focused and self.panels.panels['editor'].host is None:
                self.editor.setFocus()
        elif name == 'Debug':
            self.vertical.setSizes([420, 260])
            self.top_splitter.setSizes([650, 400])
            cpu = self.panels.panels['cpu']
            if cpu.host is None:
                cpu.home.select(cpu)
        else:
            self.vertical.setSizes([500, 180])
            self.top_splitter.setSizes([800, 330])
        self.panels.refresh()
        QTimer.singleShot(0, self.view.fit_canvas)

    def restore_preferences(self):
        try:
            if self.settings.value('version', 0, int) == 1:
                preset = self.settings.value('workspace', 'Circuit')
                if preset in ('Circuit', 'Code', 'Debug'):
                    self.select_workspace(preset)
                    self.set_workspace(preset)
                geometry = self.settings.value('geometry')
                if geometry is not None:
                    self.restoreGeometry(geometry)
                for key, splitter in [('vertical', self.vertical), ('horizontal', self.top_splitter)]:
                    state = self.settings.value(key)
                    if state is not None:
                        splitter.restoreState(state)
                self.inspector_action.setChecked(self.settings.value('inspector', True, bool))
                self.output_action.setChecked(self.settings.value('output', True, bool))
                self.advanced_action.setChecked(self.settings.value('advanced', False, bool))
                self.guide.setVisible(self.settings.value('guide', True, bool))
            else:
                if self.project.builtin == 'micropython':
                    self.select_workspace('Code')
                else:
                    self.set_workspace('Circuit')
            size = max(9, min(24, self.settings.value('font_size', 11, int)))
            self.set_editor_font(size)
            width = self.settings.value('editor/indent_width', 4, int)
            self.editor_pane.indentation.setCurrentIndex(max(0, self.editor_pane.indentation.findData(width if width in (2,4,8) else 4)))
            self.editor_pane.pairs.setChecked(self.settings.value('editor/auto_pairs', True, bool))
            self.plotter.uart.setCurrentIndex(max(0, min(1, self.settings.value('runtime/plot_uart', 0, int))))
            self.plotter.canvas.window_ns = max(1000, min(3_600_000_000_000, self.settings.value('runtime/plot_window_ns', 5_000_000_000, int)))
            self.bus_inspector.filter.setText(self.settings.value('runtime/bus_filter', ''))
        except (TypeError, ValueError, OverflowError):
            self.reset_layout()
        from .panels import fit_on_screen
        fit_on_screen(self)
        self.panels.restore(self.settings)
        QTimer.singleShot(0, self.view.fit_canvas)

    def save_preferences(self):
        for key, value in {'version':1, 'geometry':self.saveGeometry(),
            'vertical':self.vertical.saveState(), 'horizontal':self.top_splitter.saveState(),
            'inspector':self.inspector_action.isChecked(), 'output':self.output_action.isChecked(),
            'advanced':self.advanced_action.isChecked(), 'guide':not self.guide.isHidden(),
            'font_size':self.editor.font().pointSize(),
            'editor/indent_width':self.editor.indent_width,
            'editor/auto_pairs':self.editor.auto_pairs,
            'runtime/plot_uart':self.plotter.uart.currentIndex(),
            'runtime/plot_window_ns':self.plotter.canvas.window_ns,
            'runtime/bus_filter':self.bus_inspector.filter.text()}.items():
            self.settings.setValue(key, value)
        self.settings.setValue('workspace', self.current_workspace)
        self.panels.save(self.settings)
        self.settings.sync()

    def reset_layout(self):
        self.panels.dock_all()
        self.inspector_action.setChecked(True)
        self.output_action.setChecked(True)
        self.advanced_action.setChecked(False)
        self.select_workspace('Circuit')
        self.set_workspace('Circuit')
        self.view.fit_canvas()

    def set_editor_font(self, size):
        size = max(9, min(24, size))
        for editor in (self.editor, self.serial, self.logs, self.memory_text):
            editor.setFont(QFont('monospace', size))
            editor.setStyleSheet(f'font-family: monospace; font-size: {size}pt;')
        self.editor.update_margins()

    def choose_example(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Start a project')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Choose an example, then use Run or Build / Run Python.'))
        choices = QListWidget()
        examples = [('C blink + button', self.new_project), ('MicroPython · UART REPL', self.new_micropython),
                    ('C OLED + plotter dashboard', lambda: self.new_dashboard()),
                    ('MicroPython OLED + plotter dashboard', lambda: self.new_dashboard(True)),
                    ('Peripheral workbench', self.new_workbench), ('PIO blink', self.new_pio_blink),
                    ('DMA to PIO', self.new_dma_pio), ('Two cores', self.new_multicore),
                    ('Pico SDK C blink', self.new_sdk), ('Real ROM boot blink', self.new_rom)]
        choices.addItems([title for title, _ in examples])
        choices.setCurrentRow(0)
        choices.setAccessibleName('Example projects')
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        choices.itemDoubleClicked.connect(lambda _: dialog.accept())
        choices.itemActivated.connect(lambda _: dialog.accept())
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            examples[choices.currentRow()][1]()

    def show_setup(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, 'ROM and compiler setup',
            'Bundled C demos run without a compiler. MicroPython and ROM projects require the official local B2 ROM.\n\n'
            'From this checkout:\n.venv/bin/python tools/prepare_bootrom.py\n\n'
            'Select an existing ROM through File → Select B2 boot ROM. C editor builds need ARM GCC; SDK projects also need the pinned SDK.\n\n'
            'See docs/boot.md, docs/sdk-startup.md and docs/micropython.md. Setup never downloads automatically.')
