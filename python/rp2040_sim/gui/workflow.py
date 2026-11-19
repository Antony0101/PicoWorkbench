"""Document editing and asynchronous application commands for the desktop."""
from copy import deepcopy
from pathlib import Path
import re

from PySide6.QtCore import QThread, Qt, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QMessageBox,
                              QListWidgetItem, QLineEdit, QPlainTextEdit)

from .. import demo
from ..project import Component, Project
from .document import CircuitHistory, fingerprint


class Workflow:
    def text_undo_target(self):
        focus = QApplication.focusWidget()
        return focus if isinstance(focus, (QLineEdit, QPlainTextEdit)) and not focus.isReadOnly() else None

    @property
    def circuit_pending(self):
        return fingerprint(self.project, circuit=True) != self.applied

    @property
    def document_dirty(self):
        return fingerprint(self.project, self.editor.toPlainText()) != self.saved_document

    def document_changed(self):
        if self._loading_document:
            return
        dirty = self.document_dirty
        self.setWindowTitle(f'{self.project.name}{" *" if dirty else ""} — RP2040 Lab')
        self.project_label.setText(self.project.name + (' *' if dirty else ''))
        python = self.project.builtin == 'micropython'
        language = 'python' if python else 'c'
        self.editor_pane.update_document(self.source_path, language, self.source_saved != self.editor.toPlainText())
        self.assistance.set_context(language, self.project.startup, self.source_path)
        self.build_action.setText('Run Python' if python else 'Build + Load')
        if self.loaded_source != self.editor.toPlainText():
            self.editor.set_execution(0)
        self.refresh_breakpoints()
        self.update_actions()

    def update_actions(self):
        pending = self.circuit_pending
        loaded = bool(self.state and self.state.loaded)
        idle = not self.running and not self.busy
        self.editor.setReadOnly(self.busy)
        self.run_action.setText('Apply + Run' if pending else '▶ Run')
        self.run_action.setEnabled(idle and loaded)
        self.reset_action.setEnabled(loaded and not self.busy)
        self.pause_action.setEnabled(self.running)
        self.step_action.setEnabled(idle and loaded and not pending)
        self.apply_action.setEnabled(idle and loaded and pending)
        self.run_n_button.setEnabled(idle and loaded and not pending)
        self.clock_button.setEnabled(idle and loaded and not pending)
        self.clock_spin.setEnabled(idle and not pending)
        self.instruction_spin.setEnabled(idle and not pending)
        self.add_part_button.setEnabled(idle)
        self.part_kind.setEnabled(idle)
        self.scene.running = not idle
        for part in self.scene.parts.values():
            part.setFlag(part.GraphicsItemFlag.ItemIsMovable, idle)
        self.apply_action.setToolTip('Apply staged wiring/settings and restart execution. CPU/RAM and breakpoints reset; ROM flash files are retained.')
        self.build_action.setEnabled(not self.busy and self.build_thread is None)
        target = self.text_undo_target()
        editing = target is not None
        document = target.document() if isinstance(target, QPlainTextEdit) else target
        self.undo_action.setEnabled(not self.busy and (document.isUndoAvailable() if editing else not self.running and self.history.index > 0))
        self.redo_action.setEnabled(not self.busy and (document.isRedoAvailable() if editing else not self.running and self.history.index < len(self.history.entries)))
        self.undo_action.setText('Undo typing' if editing else 'Undo circuit')
        self.redo_action.setText('Redo typing' if editing else 'Redo circuit')
        if not editing and self.history.index:
            self.undo_action.setText('Undo ' + self.history.entries[self.history.index - 1][0])
        if not editing and self.history.index < len(self.history.entries):
            self.redo_action.setText('Redo ' + self.history.entries[self.history.index][0])
        if hasattr(self, 'connect_button'):
            for widget in (self.connect_button, self.disconnect_button, self.properties_apply):
                widget.setEnabled(idle)
        if self.busy:
            phase = 'Applying / loading'
        elif self.build_thread is not None:
            phase = 'Building'
        elif getattr(self, 'python_status', '') in ('boot', 'enter', 'upload'):
            phase = 'Uploading Python'
        elif self.running:
            phase = 'Running'
        elif getattr(self, 'python_status', '') == 'finished':
            phase = 'Finished'
        elif getattr(self, 'python_status', '') == 'error':
            phase = 'Python error'
        elif self.state and self.state.stop.name == 'FAULT':
            phase = 'Fault'
        else:
            phase = 'Paused'
        if pending:
            difference = 'Circuit pending · Apply restarts execution'
        elif self.editor.toPlainText() != self.loaded_source:
            difference = 'Edited — upload required' if self.project.builtin == 'micropython' else 'Edited — rebuild required'
        else:
            difference = 'Source matches last upload' if self.project.builtin == 'micropython' else 'Source matches loaded program'
        if self.loaded_source is None and not pending:
            difference = 'Python source not uploaded' if self.project.builtin == 'micropython' else 'Source not associated with loaded firmware'
        name = Path(self.loaded_name).name
        self.identity_label.setText(f'{phase} · {name} · {difference}')
        self.identity_label.setToolTip(self.loaded_name + '\n' + difference)

    def request(self, name, callback, **fields):
        self.request_number += 1
        number = self.request_number
        self.callbacks[number] = callback
        if name in ('project', 'load', 'clock', 'inspect_flash'):
            self.busy = True
            self.busy_request = number
        self.dispatch.emit({'name': name, 'request': number, **fields})
        self.update_actions()

    @Slot(object)
    def command_completed(self, result):
        if result['request'] == getattr(self, 'busy_request', None):
            self.busy = False
        callback = self.callbacks.pop(result['request'], None)
        if callback:
            callback(result['ok'])
        if result['ok'] and self._after_load_run and not self.busy:
            self._after_load_run = False
            self.send('run')
        elif not result['ok']:
            self._after_load_run = False
        self.document_changed()

    def send(self, name, **fields):
        if name == 'run':
            if self.busy:
                self._after_load_run = True
                return
            if self.circuit_pending:
                self.apply_circuit(run=True)
                return
        if name == 'clock':
            def changed(ok):
                if ok:
                    self.project.clock_hz = fields['hz']
                    self.clear_breakpoints()
            self.request(name, changed, discard_flash=self.discard_flash, **fields)
            self.discard_flash = False
            return
        if name in ('step', 'run_instructions') and self.circuit_pending:
            self.error('Apply the pending circuit before stepping or running an instruction limit.')
            return
        self.dispatch.emit({'name': name, **fields})

    def apply_circuit(self, checked=False, *, run=False):
        if self.running or self.busy:
            return
        def applied(ok):
            if ok:
                self.clear_breakpoints()
                self.fault_banner.hide()
                if run:
                    self.send('run')
        self.request('project', applied, project=deepcopy(self.project), preserve=True,
                     discard_flash=self.discard_flash)
        self.discard_flash = False

    def edit_circuit(self, change, title):
        if self.running or self.busy:
            return
        try:
            if self.history.commit(self.project, change, title):
                self.refresh_circuit()
        except (ValueError, KeyError, StopIteration) as error:
            self.error(str(error))

    def refresh_circuit(self):
        selected = {item.data(0) for item in self.scene.selectedItems() if item.data(0) in self.scene.parts}
        self.scene.set_project(self.project)
        self.connection_status.setText('Logical GPIOs · Apply pending circuit changes before execution')
        self.scene.running = self.running
        self.refresh_properties()
        for key in selected:
            if key in self.scene.parts:
                self.scene.parts[key].setSelected(True)
        self.document_changed()

    def undo(self):
        if self.busy:
            return
        target = self.text_undo_target()
        if target is not None:
            target.undo()
        elif not self.running and not self.busy:
            self.history.undo(self.project)
            self.refresh_circuit()

    def redo(self):
        if self.busy:
            return
        target = self.text_undo_target()
        if target is not None:
            target.redo()
        elif not self.running and not self.busy:
            self.history.redo(self.project)
            self.refresh_circuit()

    def add_part(self, checked=False, *, kind=None, x=None, y=None):
        kind = kind or self.part_kind.currentText()
        def add(project):
            ids = {component.id for component in project.components}
            index = 1
            while f'{kind}{index}' in ids:
                index += 1
            project.components.append(Component(f'{kind}{index}', kind, 650 if x is None else x,
                                                110 + 110 * (len(ids) % 5) if y is None else y))
        self.edit_circuit(add, 'add component')

    def add_wire(self, a, b):
        if a == b or any(set((a, b)) == set(wire) for wire in self.project.wires):
            return
        self.edit_circuit(lambda project: project.wires.append((a, b)), 'connect wire')

    def disconnect_port(self, endpoint):
        self.edit_circuit(lambda project: setattr(project, 'wires', [wire for wire in project.wires if endpoint not in wire]),
                          'disconnect port')

    def move_part(self, key, x, y):
        positions = {key: (part.pos().x(), part.pos().y()) for key, part in self.scene.parts.items()}
        positions[key] = (x, y)
        def move(project):
            for component in project.components:
                component.x, component.y = positions[component.id]
        self.edit_circuit(move, 'move component')

    def nudge_selected(self, dx, dy):
        keys = {item.data(0) for item in self.scene.selectedItems()}
        if not keys:
            keys = {self.component_choice.currentText()}
        def move(project):
            for component in project.components:
                if component.id in keys:
                    component.x += dx
                    component.y += dy
        self.edit_circuit(move, 'move component')
        for key in keys:
            if key in self.scene.parts:
                self.scene.parts[key].setSelected(True)

    def duplicate_selected(self):
        keys = {item.data(0) for item in self.scene.selectedItems()}
        def duplicate(project):
            replacements = {}
            ids = {component.id for component in project.components}
            for component in list(project.components):
                if component.id not in keys:
                    continue
                new = deepcopy(component)
                number = 1
                while f'{component.kind}{number}' in ids:
                    number += 1
                new.id = f'{component.kind}{number}'
                ids.add(new.id)
                replacements[component.id] = new.id
                new.x += 32
                new.y += 32
                project.components.append(new)
            for a, b in list(project.wires):
                ka, pa = a.split(':')
                kb, pb = b.split(':')
                if ka in replacements and kb in replacements:
                    project.wires.append((replacements[ka] + ':' + pa, replacements[kb] + ':' + pb))
        self.edit_circuit(duplicate, 'duplicate components')

    def delete_selected(self):
        selected = {item.data(0) for item in self.scene.selectedItems()}
        deleted = {key for key in selected if key in self.scene.parts}
        wires = {int(key.split(':')[1]) for key in selected if isinstance(key, str) and key.startswith('wire:')}
        def remove(project):
            project.components = [component for component in project.components if component.id not in deleted]
            project.wires = [wire for index, wire in enumerate(project.wires) if index not in wires and
                             not any(endpoint.split(':')[0] in deleted for endpoint in wire)]
        self.edit_circuit(remove, 'delete selection')

    def edit_part(self, key):
        if self.running or self.busy:
            return
        component = next((component for component in self.project.components if component.id == key), None)
        if component is None:
            return
        sensor = component.kind == 'i2c_sensor'
        value, accepted = QInputDialog.getText(self, 'I²C sensor' if sensor else 'Component label',
            'Device address (hex or decimal)' if sensor else 'Label',
            text=f'0x{component.address:02x}' if sensor else component.label)
        if accepted:
            try:
                value = int(value, 0) if sensor else value[:256]
                def change(project):
                    item = next(item for item in project.components if item.id == key)
                    setattr(item, 'address' if sensor else 'label', value)
                self.edit_circuit(change, 'edit component')
            except ValueError:
                self.error('I²C address must be between 0x08 and 0x77')

    def confirm_unsaved(self):
        if not self.document_dirty:
            return True
        choice = QMessageBox.warning(self, 'Save project changes?',
            'The source buffer and circuit have unsaved project changes.',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return self.save_project() if choice == QMessageBox.StandardButton.Save else choice == QMessageBox.StandardButton.Discard

    def confirm_flash(self, continuation):
        # Pause and inspect in queue order; do not trust a stale running snapshot.
        self.dispatch.emit({'name':'pause'})
        def inspected(ok):
            if not ok:
                return
            if not self.flash_dirty:
                continuation()
                return
            choice = QMessageBox.warning(self, 'Runtime flash has changed',
                'Files/programmed flash are separate from project JSON. Save exports the full flash image; Discard allows replacement.',
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Discard:
                self.discard_flash = True
                continuation()
            elif choice == QMessageBox.StandardButton.Save:
                self.export_flash(lambda saved: continuation() if saved else None)
        self.request('inspect_flash', inspected)

    def replace_document(self, project, text=None):
        if self.busy or self.build_thread is not None or not self.confirm_unsaved():
            return
        text = text if text is not None else demo.source(project.builtin)
        def replace():
            def loaded(ok):
                if not ok:
                    return
                self._loading_document = True
                self.project = project
                self.source_path = project.resolve(project.source) if project.source else None
                self.editor.setPlainText(text)
                try:
                    self.source_saved = self.source_path.read_text() if self.source_path else text
                except (OSError, UnicodeError):
                    self.source_saved = None
                self.history = CircuitHistory()
                self.saved_document = fingerprint(project, text)
                self._loading_document = False
                self.clear_breakpoints()
                self.clock_spin.setValue(project.clock_hz)
                self._read_symbols(project.resolve(project.firmware) if project.firmware else None)
                self.refresh_circuit()
                self.fault_banner.hide()
                self.recovery.hide()
                if project.builtin == 'micropython':
                    self.select_workspace('Code')
                for builtin, inspector in [('pio_blink', self.pio_inspector), ('dma_pio', self.dma_inspector), ('multicore', self.cores_inspector)]:
                    if project.builtin == builtin:
                        self.inspector.setCurrentWidget(inspector)
            self.request('project', loaded, project=deepcopy(project), source=text,
                         discard_flash=self.discard_flash)
            self.discard_flash = False
        self.confirm_flash(replace)

    def new_project(self): self.replace_document(Project.demo())
    def new_workbench(self): self.replace_document(Project.workbench())
    def new_pio_blink(self): self.replace_document(Project.pio_blink())
    def new_dma_pio(self): self.replace_document(Project.dma_pio())
    def new_multicore(self): self.replace_document(Project.multicore())
    def new_sdk(self, name='sdk_blink'): self.replace_document(Project.sdk_blink() if name == 'sdk_blink' else Project.sdk_uart())

    def rom_project(self, project):
        project.bootrom = str(self.project.resolve(self.project.bootrom)) if self.project.bootrom else ''
        self.replace_document(project)

    def new_rom(self, flash_test=False): self.rom_project(Project.rom_flash() if flash_test else Project.rom_blink())
    def new_micropython(self): self.rom_project(Project.micropython())

    def select_bootrom(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Select official B2 boot ROM', '', 'ROM binary (*.bin)')
        if name:
            from ..images import bootrom
            try:
                bootrom(Path(name))
                self.project.bootrom = str(Path(name).resolve())
                self.document_changed()
            except (OSError, ValueError) as error:
                self.error(str(error))

    def open_project(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Open project', '', 'Circuit projects (*.json)')
        if name:
            try:
                project = Project.load(Path(name))
                text = project.source_text if project.source_text is not None else project.resolve(project.source).read_text() if project.source else demo.source(project.builtin)
                self.replace_document(project, text)
            except (OSError, ValueError) as error:
                self.error(str(error))

    def save_project(self):
        name, _ = QFileDialog.getSaveFileName(self, 'Save project', str(self.project.path or 'circuit.json'), 'Circuit projects (*.json)')
        if not name:
            return False
        try:
            project = deepcopy(self.project)
            project.source_text = self.editor.toPlainText()
            project.save(Path(name))
            self.project = project
            self.scene.project = project
            self.saved_document = fingerprint(project, self.editor.toPlainText())
            self.document_changed()
            return True
        except (OSError, ValueError) as error:
            self.error(str(error))
            return False

    def open_source(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Open source', '', 'Source (*.c *.py)')
        if name:
            try:
                text = Path(name).read_text()
                if not self.confirm_unsaved():
                    return
                if name.endswith('.py') != (self.project.builtin == 'micropython'):
                    self.error('Choose a MicroPython project for .py source, or a C project for .c source, through Examples.')
                    return
                self.source_path = Path(name).resolve()
                self.project.source = str(self.source_path)
                self.source_saved = text
                self.editor.setPlainText(text)
                self.select_workspace('Code')
            except (OSError, UnicodeError) as error:
                self.error(str(error))

    def save_source(self):
        python = self.project.builtin == 'micropython'
        name, _ = QFileDialog.getSaveFileName(self, 'Save source', str(self.source_path or ('main.py' if python else 'firmware.c')), 'Python (*.py)' if python else 'C (*.c)')
        if not name:
            return False
        try:
            path = Path(name).resolve()
            temporary = path.with_suffix(path.suffix + '.tmp')
            temporary.write_text(self.editor.toPlainText())
            temporary.replace(path)
            self.source_path = path
            self.project.source = str(path)
            self.source_saved = self.editor.toPlainText()
            self.document_changed()
            return True
        except OSError as error:
            self.error(str(error))
            return False

    def open_firmware(self):
        filters = 'Direct-load ARM ELF (*.elf *.axf);;Pico SDK ELF — skip boot2 (*.elf *.axf);;RP2040 ROM boot (*.uf2 *.bin *.elf)'
        name, selected = QFileDialog.getOpenFileName(self, 'Load firmware', '', filters, filters.split(';;')[0])
        if not name or self.busy:
            return
        startup = 'rom' if selected.startswith('RP2040') else 'sdk' if selected.startswith('Pico SDK') else 'direct'
        def load():
            def loaded(ok):
                if ok:
                    self.project.firmware = str(Path(name).resolve())
                    self.project.startup = startup
                    self.clear_breakpoints()
                    self._read_symbols(Path(name))
            self.request('load', loaded, path=Path(name), startup=startup, discard_flash=self.discard_flash)
            self.discard_flash = False
        self.confirm_flash(load)

    def build_source(self):
        if self.busy or self.build_thread is not None:
            return
        if self.circuit_pending:
            self.error('Apply the pending circuit before building or uploading source.')
            return
        if self.project.builtin == 'micropython':
            source = self.editor.toPlainText()
            self.request('python', lambda ok: None, source=source)
            self.output_action.setChecked(True)
            self.tabs.setCurrentWidget(self.serial_panel)
            return
        from .app import BuildTask
        try:
            root = (self.project.path.parent if self.project.path else Path.cwd()) / 'build'
            root.mkdir(parents=True, exist_ok=True)
            linker = self.project.resolve(self.project.linker) if self.project.linker else root / 'direct.ld'
            if not self.project.linker:
                linker.write_text(demo.linker())
            self.build_text = self.editor.toPlainText()
            self.build_task = BuildTask(self.build_text, root / 'editor.c', root / 'editor.elf', linker, self.project.startup)
            self.build_thread = QThread(self)
            self.build_task.moveToThread(self.build_thread)
            self.build_thread.started.connect(self.build_task.run)
            self.build_task.done.connect(self.built)
            self.build_task.failed.connect(self.build_failed)
            self.build_task.finished.connect(self.build_thread.quit, Qt.ConnectionType.DirectConnection)
            self.build_thread.finished.connect(self.build_task.deleteLater)
            self.build_thread.finished.connect(self.build_finished)
            self.cancel_action.setEnabled(True)
            self.build_thread.start()
            self.output_action.setChecked(True)
            self.tabs.setCurrentWidget(self.logs)
            self.update_actions()
        except OSError as error:
            self.error(str(error))

    @Slot(object)
    def built(self, result):
        path, log, startup = result
        self.logs.appendPlainText(log + f'Built {path}')
        source = self.build_text
        self.assistance.compiled(log, source, [self.source_path])
        def loaded(ok):
            if ok:
                self.project.firmware = str(path.resolve())
                self.project.startup = startup
                self._read_symbols(path)
                self.clear_breakpoints()
        def load():
            self.request('load', loaded, path=path, startup=startup, source=source, discard_flash=self.discard_flash)
            self.discard_flash = False
        self.confirm_flash(load)

    @Slot()
    def build_finished(self):
        self.build_thread.wait()
        self.build_thread.deleteLater()
        self.build_thread = self.build_task = None
        self.cancel_action.setEnabled(False)
        self.update_actions()

    def cancel_build(self):
        if self.build_task:
            self.build_task.cancel()

    def export_flash(self, callback=None):
        name, _ = QFileDialog.getSaveFileName(self, 'Export flash image', 'flash.bin', 'Flash binary (*.bin)')
        if name:
            self.request('save_flash', callback if callable(callback) else lambda ok: None, path=Path(name))
        elif callable(callback):
            callback(False)

    def send_serial(self):
        self.send('uart', data=(self.serial_input.text() + '\n').encode())
        self.serial_input.clear()

    def read_memory(self):
        try:
            address = int(self.memory_address.text(), 0)
            length = 128 if 0x10000000 <= address < 0x10200000 or 0x20000000 <= address < 0x20042000 else 4
            self.send('memory', address=address, length=length)
        except ValueError:
            self.error('Enter a hexadecimal or decimal memory address')

    @Slot(object)
    def show_memory(self, result):
        address, data = result
        self.memory_text.setPlainText('\n'.join(f'{address + i:08x}  ' + ' '.join(f'{b:02x}' for b in data[i:i + 16]) for i in range(0, len(data), 16)))

    def clear_breakpoints(self):
        self.breakpoints.clear()
        self.refresh_breakpoints()

    def refresh_breakpoints(self):
        self.break_list.setText(', '.join(f'0x{address:08x}' for address in sorted(self.breakpoints)) or 'No breakpoints')
        self.breakpoint_items.clear()
        self.editor.break_lines = set()
        for address in sorted(self.breakpoints):
            location = self.symbols.location(address) if self.symbols else None
            label = f'0x{address:08x}' + (f' · {location.file}:{location.line}' if location else '')
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, address)
            self.breakpoint_items.addItem(item)
            if location and self.source_matches(location.file):
                self.editor.break_lines.add(location.line)
        self.editor.gutter.update()

    def source_matches(self, filename):
        if self.project.builtin == 'micropython' or self.loaded_source != self.editor.toPlainText():
            return False
        expected = Path(self.source_path).name if self.source_path else self.project.builtin + '.c'
        return Path(filename).name in (expected, 'editor.c')

    def source_breakpoint(self, line):
        if not self.symbols or self.project.builtin == 'micropython':
            self.error('Source breakpoints need mapped C debug information. Use CPU address/function breakpoints for firmware; Python line debugging is unavailable.')
            return
        matches = [location.address for location in self.symbols.lines if location.line == line and self.source_matches(location.file)]
        if not matches:
            self.error('No executable C instruction maps to that line in the loaded source.')
            return
        self.toggle_address(min(matches))

    def toggle_address(self, address):
        if not 0 <= address <= 0xffffffff or address & 1:
            self.error('Enter an even instruction address or a known ELF function name')
            return
        enabled = address not in self.breakpoints
        self.send('breakpoint', address=address, enabled=enabled)
        if enabled:
            self.breakpoints.add(address)
        else:
            self.breakpoints.remove(address)
        self.refresh_breakpoints()

    def toggle_breakpoint(self):
        text = self.break_address.text().strip()
        try:
            self.toggle_address(self.symbols.names[text] if self.symbols and text in self.symbols.names else int(text, 0))
        except ValueError:
            self.error('Enter an even instruction address or a known ELF function name')

    def closeEvent(self, event):
        if self._closing:
            self.save_preferences()
            self.assistance.dispose()
            self.cancel_build()
            self.dispatch.emit({'name':'shutdown'})
            if not self.thread.wait(5000) or self.build_thread is not None and not self.build_thread.wait(5000):
                event.ignore()
                return
            event.accept()
            return
        event.ignore()
        if self.busy or not self.confirm_unsaved():
            return
        def close():
            self._closing = True
            self.close()
        self.confirm_flash(close)
