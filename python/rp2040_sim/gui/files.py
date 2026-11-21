"""Single-buffer editor integration with the simulated MicroPython filesystem."""
from pathlib import Path
import posixpath

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..device_files import device_path
from .menus import popup_menu


class FilesPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.number = 0
        self.pending = None
        self.connected = self.active = self.python = False
        self.refresh_pending = False
        self.seen_idle = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        connect = QHBoxLayout()
        self.connect_button = QPushButton('Boot/connect to REPL')
        self.connect_button.clicked.connect(lambda: window.send('files_connect'))
        connect.addWidget(self.connect_button)
        self.interrupt = QPushButton('Interrupt Python')
        self.interrupt.clicked.connect(lambda: window.send('interrupt_python'))
        connect.addWidget(self.interrupt)
        layout.addLayout(connect)
        row = QHBoxLayout()
        self.path = QLineEdit('/')
        self.path.setAccessibleName('Device directory')
        self.path.returnPressed.connect(self.refresh)
        row.addWidget(self.path, 1)
        self.up = QPushButton('Up')
        self.up.clicked.connect(lambda: self.navigate(posixpath.dirname(self.path.text()) or '/'))
        row.addWidget(self.up)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Name', 'Size', 'Type'])
        self.tree.setRootIsDecorated(False)
        self.tree.setAccessibleName('Pico files')
        self.tree.itemActivated.connect(lambda *_: self.open_selected())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        body = QHBoxLayout()
        body.addWidget(self.tree, 1)
        layout.addLayout(body, 1)
        grid = QGridLayout()
        self.actions = {}
        for i, (label, fn) in enumerate([
            ('Open Python', self.open_selected), ('Save to Pico…', self.save_editor),
            ('Upload…', self.upload), ('Download…', self.download),
            ('New folder…', self.mkdir), ('Rename…', self.rename), ('Delete…', self.delete)]):
            button = QPushButton(label)
            button.clicked.connect(fn)
            grid.addWidget(button, i//2, i%2)
            self.actions[label] = button
        controls = QWidget()
        controls.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setMinimumWidth(240)
        scroll.setMaximumWidth(360)
        body.addWidget(scroll)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('Device transfer progress')
        footer = QHBoxLayout()
        footer.addWidget(self.progress, 1)
        self.cancel = QPushButton('Cancel file operation')
        self.cancel.clicked.connect(lambda: window.send('files_cancel'))
        footer.addWidget(self.cancel)
        layout.addLayout(footer)
        self.status = QLabel('MicroPython files require an idle REPL. Interrupt a running program first.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def display(self, packet):
        self.python = self.window.project.builtin == 'micropython'
        self.connected = self.python and packet.get('python_idle', False)
        self.active = packet.get('files_busy', False) or self.pending is not None
        if packet.get('reset'):
            self.seen_idle = False
            self.tree.clear()
        enabled = self.connected and not self.active and not self.window.busy and not self.window.circuit_pending
        for button in [*self.actions.values(), self.up, self.refresh_button, self.path]:
            button.setEnabled(enabled)
        self.connect_button.setEnabled(self.python and not self.active and not packet.get('repl_connect') and not self.connected)
        self.interrupt.setEnabled(self.python and not self.active)
        self.cancel.setEnabled(bool(packet.get('files_busy')))
        progress = packet.get('files_progress')
        if progress:
            done, total = progress
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
        else:
            self.progress.setRange(0, 1)
        if not self.python:
            self.status.setText('Files are available in MicroPython projects.')
        elif packet.get('repl_connect'):
            self.status.setText('Waiting for the idle REPL; use Interrupt Python if main.py is running.')
        elif self.active:
            self.status.setText('Transferring through the interpreter; virtual time advances.')
        elif not self.connected:
            self.status.setText('Program running or REPL unconfirmed. Boot/connect, or Interrupt Python first.')
        if enabled and self.isVisible() and (self.refresh_pending or not self.seen_idle):
            self.seen_idle = True
            self.refresh_pending = False
            self.refresh()

    def start(self, operation, path=None, callback=None, **fields):
        if self.pending is not None or not self.connected:
            self.status.setText('Wait for an idle Python prompt before using files.')
            return
        try:
            path = device_path(path or self.path.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.number += 1
        self.pending = (self.number, callback)
        self.window.send('files', id=self.number, operation=operation, path=path, **fields)
        for button in self.actions.values():
            button.setEnabled(False)

    def completed(self, result):
        if self.pending is None or result.request != self.pending[0]:
            return
        _, callback = self.pending
        self.pending = None
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if result.ok else 0)
        if not result.ok:
            self.status.setText(result.error)
            self.window.logs.appendPlainText(f'Pico files: {result.error}')
        elif callback:
            callback(result)
        else:
            self.status.setText(f'{result.operation.title()} completed: Pico:{result.path}')
        if result.ok and result.operation in ('upload', 'mkdir', 'rename', 'delete'):
            self.refresh_pending = True

    def refresh(self):
        def listed(result):
            self.tree.clear()
            self.path.setText(result.path)
            for entry in sorted(result.value['entries'], key=lambda e: (not e['directory'], e['name'])):
                item = QTreeWidgetItem([entry['name'], '' if entry['directory'] else str(entry['size']),
                                       'Folder' if entry['directory'] else 'File'])
                item.setData(0, Qt.ItemDataRole.UserRole, entry['directory'])
                self.tree.addTopLevelItem(item)
            self.tree.resizeColumnToContents(0)
            self.status.setText(f'{len(result.value["entries"])} entries · {result.value["free"]:,} bytes free')
        self.start('list', callback=listed)

    def navigate(self, path):
        self.path.setText(device_path(path))
        self.refresh()

    def selected(self):
        item = self.tree.currentItem()
        if item is None:
            self.status.setText('Select a file or folder first.')
            return None
        return device_path(posixpath.join(self.path.text(), item.text(0))), bool(item.data(0, Qt.ItemDataRole.UserRole))

    def open_selected(self):
        selected = self.selected()
        if selected is None:
            return
        path, directory = selected
        if directory:
            self.navigate(path)
            return
        if not path.endswith('.py'):
            self.status.setText('Use Download for binary and non-Python files.')
            return
        if not self.window.confirm_unsaved():
            return
        revision = self.window.editor.document().revision()
        def opened(result):
            if self.window.editor.document().revision() != revision:
                self.status.setText('Editor changed during transfer; file was not opened. Retry Open or Download.')
                return
            try:
                text = result.value.decode('utf8')
            except UnicodeError:
                self.status.setText('File is not UTF-8 Python source. Use Download.')
                return
            window = self.window
            window.device_source_path = result.path
            window.device_source_saved = text
            window.source_path = None
            window.project.source = ''
            window.source_saved = text
            window.editor.setPlainText(text)
            window.document_changed()
            window.panels.reveal('editor')
            self.status.setText('Opened Pico:' + result.path + ' · use Save to Pico for device changes')
        self.start('read', path, opened)

    def save_editor(self):
        window = self.window
        path, ok = QInputDialog.getText(self, 'Save editor to Pico', 'Device file path',
            text=getattr(window, 'device_source_path', None) or posixpath.join(self.path.text(), 'main.py'))
        if not ok:
            return
        if QMessageBox.question(self, 'Save to Pico', 'Replace the device file if it exists?\nPico:' + path) != QMessageBox.StandardButton.Yes:
            return
        text = window.editor.toPlainText()
        revision = window.editor.document().revision()
        def saved(result):
            if window.editor.document().revision() == revision:
                window.device_source_path = result.path
                window.device_source_saved = text
                window.document_changed()
            elif window.device_source_path == result.path:
                window.device_source_saved = text
                window.document_changed()
            self.status.setText('Saved Pico:' + result.path + ('; editor has newer changes' if window.editor.document().revision()!=revision else ''))
        self.start('upload', path, saved, data=text.encode('utf8'))

    def upload(self):
        host, _ = QFileDialog.getOpenFileName(self, 'Upload file to Pico')
        if not host:
            return
        path = posixpath.join(self.path.text(), Path(host).name)
        if any(self.tree.topLevelItem(i).text(0) == Path(host).name for i in range(self.tree.topLevelItemCount())):
            if QMessageBox.question(self, 'Replace device file?', 'Replace Pico:' + path + '?') != QMessageBox.StandardButton.Yes:
                return
        self.start('upload', path, host=host)

    def download(self):
        selected = self.selected()
        if not selected or selected[1]:
            return
        path = selected[0]
        host, _ = QFileDialog.getSaveFileName(self, 'Download from Pico', posixpath.basename(path))
        if host:
            self.start('download', path, host=host)

    def mkdir(self):
        name, ok = QInputDialog.getText(self, 'New device folder', 'Folder name')
        if ok and name:
            self.start('mkdir', posixpath.join(self.path.text(), name))

    def rename(self):
        selected = self.selected()
        if not selected:
            return
        path = selected[0]
        name, ok = QInputDialog.getText(self, 'Rename device entry', 'New path', text=path)
        if ok:
            self.start('rename', path, destination=name)

    def delete(self):
        selected = self.selected()
        if selected and QMessageBox.question(self, 'Delete device entry?', 'Delete Pico:' + selected[0] + '?\nFolders must be empty.') == QMessageBox.StandardButton.Yes:
            self.start('delete', selected[0])

    def context_menu(self, position):
        item = self.tree.itemAt(position)
        if item:
            self.tree.setCurrentItem(item)
        menu = QMenu(self)
        for name in ('Open Python', 'Save to Pico…', 'Upload…', 'Download…', 'New folder…', 'Rename…', 'Delete…'):
            button = self.actions[name]
            action = menu.addAction(name)
            action.setEnabled(button.isEnabled())
            action.triggered.connect(button.click)
        action = menu.addAction('Refresh')
        action.setEnabled(self.refresh_button.isEnabled())
        action.triggered.connect(self.refresh)
        popup_menu(self.tree, menu, self.tree.viewport().mapToGlobal(position))
