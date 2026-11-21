"""Optional local language services; independent of the simulator worker and firmware."""
import html
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import sys
import tempfile
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QEvent, QObject, QProcess, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QPlainTextEdit, QToolTip, QVBoxLayout)

from ..build import ROOT, compiler, compile_flags
from .editor import SourceEditor, utf16_length, py_index
from .lsp import LspClient


def executable(name, configured=''):
    if configured:
        return shutil.which(configured) or (str(Path(configured).expanduser()) if Path(configured).expanduser().is_file() else '')
    found = shutil.which(name)
    if found:
        return found
    directory = ROOT / '.tools/editor'
    candidates = ([directory / 'node_modules/.bin/pyright-langserver'] if name == 'pyright-langserver' else
                  [directory / 'bin/clangd', *sorted(directory.glob('clangd*/bin/clangd'), reverse=True)])
    if name == 'clangd':
        candidates += [Path(found) for version in range(30, 13, -1) if (found := shutil.which(f'clangd-{version}'))]
    return next((str(p.resolve()) for p in candidates if p.is_file() and os.access(p, os.X_OK)), '')


def stub_directory(configured=''):
    directory = Path(configured).expanduser() if configured else ROOT / '.tools/editor/micropython-stubs'
    # Accept the target directory produced by pip, or a typings directory.
    candidates = [directory, directory / 'stdlib', directory / 'stubs']
    return next((p.resolve() for p in candidates if (p / 'machine.pyi').is_file() and
                 ((p / 'stdlib/builtins.pyi').is_file() or (p / 'builtins.pyi').is_file())), None)


def compiler_diagnostics(log, source, aliases=()):
    result = []
    own_files = {'editor.c', 'firmware.c', *(Path(p).name for p in aliases if p)}
    for line in log.splitlines():
        match = re.match(r'^(.+?):(\d+):(?:(\d+):)?\s*(fatal error|error|warning|note):\s*(.*)', line)
        if not match:
            continue
        filename, line_number, column, severity, message = match.groups()
        row, byte_column = max(0, int(line_number) - 1), max(0, int(column or 1) - 1)
        external = Path(filename).name not in own_files
        text = source.split('\n')
        if not external and row < len(text):
            # GCC is explicitly configured to report byte columns.
            prefix = text[row].encode('utf-8')[:byte_column].decode('utf-8', errors='ignore')
            column_number = utf16_length(prefix)
        else:
            column_number = byte_column
        result.append({'origin': 'Compiler', 'file': filename, 'external': external,
            'severity': 1 if 'error' in severity else 2 if severity == 'warning' else 3,
            'message': message, 'range': {'start': {'line': row, 'character': column_number},
                                        'end': {'line': row, 'character': column_number + 1}}})
    return result


class CodeAssistance(QObject):
    def __init__(self, editor, pane, settings, parent=None):
        super().__init__(parent)
        self.editor, self.pane, self.settings = editor, pane, settings
        self.directory = tempfile.TemporaryDirectory(prefix='rp2040-editor-')
        self.root = Path(self.directory.name)
        self.client = None
        self.preparation = None
        self.context = None
        self.active = False
        self.disposed = False
        self.build_source = None
        self.build_items = []
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self.flush)
        self.completion_timer = QTimer(self)
        self.completion_timer.setSingleShot(True)
        self.completion_timer.setInterval(350)
        self.completion_timer.timeout.connect(self.auto_complete)
        self.popup = QListWidget(editor)
        self.popup.setWindowFlags(Qt.WindowType.ToolTip)
        self.popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.popup.setAccessibleName('Code completion suggestions')
        self.popup.itemClicked.connect(self.accept_completion)
        self.popup.hide()
        self.completion_revision = None
        editor.installEventFilter(self)
        editor.textChanged.connect(self.changed)
        editor.cursorPositionChanged.connect(self.dismiss)
        editor.completion_requested.connect(self.complete)
        editor.signature_requested.connect(self.signature)
        editor.definition_requested.connect(self.definition)
        editor.hover_requested.connect(self.hover)
        editor.escape_requested.connect(self.dismiss)
        pane.setup_requested.connect(self.setup_dialog)
        pane.diagnostic_activated.connect(self.navigate_diagnostic)

    def status(self, text):
        self.pane.service_status.setText(text)
        self.pane.service_status.setToolTip(text + ('\n' + self.client.error_log[-2000:] if self.client else ''))

    def set_context(self, language, startup, source_path=None):
        # Filename changes (Save As) start a fresh document session.
        context = (language, startup, str(source_path or ''))
        if context != self.context:
            self.context = context
            self.build_source = None
            self.build_items = []
            self.pane.set_diagnostics('Compiler', [])
            self.restart()

    def activate(self, active):
        if active and not self.active:
            self.active = True
            if not self.client and not self.preparation:
                self.restart()

    def restart(self):
        self.dismiss()
        self.timer.stop()
        self.pane.set_diagnostics('Analysis', [])
        if self.client:
            old, self.client = self.client, None
            old.stop()
            # Retain the QObject until its bounded asynchronous shutdown finishes.
            if old.process.state() != QProcess.ProcessState.NotRunning:
                old.stopped.connect(old.deleteLater)
            else:
                old.deleteLater()
        self.cancel_preparation()
        if not self.context or not self.active or self.disposed:
            return
        if os.environ.get('RP2040_CODE_ASSIST') == '0':
            self.status('Code assistance disabled by RP2040_CODE_ASSIST')
            return
        language, startup, _ = self.context
        name = 'pyright-langserver' if language == 'python' else 'clangd'
        program = executable(name, self.settings.value(f'editor/{name}', '', str))
        if not program:
            self.status(f'{name} not found · Code assistance… for setup')
            return
        self.source = self.root / ('main.py' if language == 'python' else 'editor.c')
        self.source.write_text(self.editor.toPlainText(), encoding='utf-8')
        try:
            if language == 'python':
                stubs = stub_directory(self.settings.value('editor/stubs', '', str))
                if not stubs:
                    self.status('MicroPython 1.26 stubs not found · Code assistance… for setup')
                    return
                # Isolate analysis from this application's host Python environment.
                typeshed = stubs if (stubs / 'stdlib').is_dir() else stubs.parent
                config = {'include': ['main.py'], 'typeCheckingMode': 'basic',
                    'stubPath': str(stubs), 'typeshedPath': str(typeshed),
                    'reportMissingModuleSource': 'none', 'useLibraryCodeForTypes': False,
                    'pythonPlatform': 'Linux', 'pythonVersion': '3.9'}
                (self.root / 'pyrightconfig.json').write_text(json.dumps(config), encoding='utf-8')
                self.start_client(program, ['--stdio'], configuration={'python': {'analysis': {
                    'typeCheckingMode': 'basic', 'stubPath': str(stubs), 'typeshedPaths': [str(typeshed)],
                    'diagnosticMode': 'openFilesOnly', 'useLibraryCodeForTypes': False,
                    'diagnosticSeverityOverrides': {'reportMissingModuleSource': 'none'}}}})
            elif startup in ('sdk', 'rom'):
                self.prepare_sdk(program)
            else:
                command = [str(Path(shutil.which(compiler()) or compiler()).resolve()), *compile_flags(), '-c', str(self.source)]
                self.start_clangd(program, command, self.root)
        except (OSError, ValueError, RuntimeError) as error:
            self.status(f'Code assistance setup: {error}')

    def start_client(self, program, arguments, *, configuration=None):
        client = LspClient(self, timeout_ms=20000)
        self.client = client
        client.status.connect(lambda text: self.status(text) if self.client is client else None)
        client.ready.connect(lambda: client.open_document(self.source.as_uri(), self.context[0], self.editor.toPlainText())
                             if self.client is client else None)
        client.diagnostics.connect(lambda packet: self.analysis_diagnostics(packet) if self.client is client else None)
        client.start(program, arguments, self.root.as_uri(), configuration=configuration)

    def start_clangd(self, program, command, directory):
        (self.root / 'compile_commands.json').write_text(json.dumps([{'directory': str(directory),
            'file': str(self.source), 'arguments': command}]), encoding='utf-8')
        cc = str(Path(shutil.which(compiler()) or compiler()).resolve())
        self.start_client(program, [f'--compile-commands-dir={self.root}', f'--query-driver={cc}',
            '--background-index=0', '--pch-storage=memory', '-j=2', '--log=error'])

    def prepare_sdk(self, program):
        from ..sdk_build import sdk_path
        sdk = sdk_path()
        cc = Path(shutil.which(compiler()) or compiler()).resolve()
        process = QProcess(self)
        self.preparation = process
        process.setProgram(sys.executable)
        process.setArguments([str(ROOT / 'python/rp2040_sim/sdk_build.py') if (ROOT / 'python/rp2040_sim/sdk_build.py').is_file()
            else str(Path(__file__).resolve().parents[1] / 'sdk_build.py'), str(self.source), str(self.root / 'editor.elf'),
            str(sdk), str(cc.parent.parent), '--configure-only'])
        if hasattr(process, 'setUnixProcessParameters'):
            parameters = QProcess.UnixProcessParameters()
            parameters.flags = QProcess.UnixProcessFlag.CreateNewSession
            process.setUnixProcessParameters(parameters)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.prepare_log = ''
        process.readyReadStandardOutput.connect(lambda: self.read_preparation(process))
        process.finished.connect(lambda code, status: self.prepared(process, program, code))
        process.errorOccurred.connect(lambda error: self.status('SDK analysis configuration: ' + process.errorString())
                                      if self.preparation is process else None)
        process.start()
        self.status('Preparing Pico SDK headers…')
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: self.preparation_timeout(process))
        timeout.start(90000)

    def read_preparation(self, process):
        text = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace')
        if self.preparation is process:
            self.prepare_log = (self.prepare_log + text)[-16000:]

    def prepared(self, process, program, code):
        if self.preparation is not process:
            return
        self.read_preparation(process)
        self.preparation = None
        process.deleteLater()
        if code:
            self.status('SDK analysis configuration failed · ' + self.prepare_log[-800:])
            return
        try:
            database = json.loads((self.root / 'sdk-editor/out/compile_commands.json').read_text())
            entry = next(e for e in database if Path(e['file']).name == 'editor.c')
            command = entry.get('arguments') or shlex.split(entry['command'])
            command = [str(self.source) if Path(arg) == Path(entry['file']) else arg for arg in command]
            self.start_clangd(program, command, Path(entry['directory']))
        except (OSError, ValueError, KeyError, StopIteration) as error:
            self.status(f'SDK compilation metadata unavailable: {error}')

    def preparation_timeout(self, process):
        if self.preparation is process:
            self.cancel_preparation()
            self.status('SDK analysis configuration timed out · use setup to restart')

    def cancel_preparation(self):
        process, self.preparation = self.preparation, None
        if process is None:
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            if os.name == 'posix' and process.processId():
                try:
                    os.killpg(process.processId(), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                process.terminate()
            if not process.waitForFinished(100):
                if os.name == 'posix' and process.processId():
                    try:
                        os.killpg(process.processId(), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.kill()
                process.waitForFinished(100)
        process.deleteLater()

    def changed(self):
        self.dismiss()
        if self.client:
            self.client.cancel_document_requests()
        self.pane.set_diagnostics('Analysis', [])
        self.update_build_diagnostics()
        self.timer.start()
        self.completion_timer.start()

    def flush(self):
        self.timer.stop()
        if self.client and self.client.initialized and not self.client.closing:
            self.client.change_document(self.editor.toPlainText())

    def request(self, method, callback, position=None):
        self.flush()
        client = self.client
        if not client or not client.initialized or client.closing:
            return
        revision = self.editor.document().revision()
        cursor_position = self.editor.textCursor().position() if position is None else None
        client.request('textDocument/' + method, {'textDocument': {'uri': client.uri},
            'position': position or self.editor.position()}, lambda result: callback(result)
                if self.client is client and self.editor.document().revision() == revision and
                   (cursor_position is None or self.editor.textCursor().position() == cursor_position) else None)

    def auto_complete(self):
        if not self.editor.hasFocus() or self.editor.isReadOnly():
            return
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        if len(cursor.selectedText()) >= 2 and not self.editor.in_literal(self.editor.textCursor().block(),
                                                                       self.editor.textCursor().positionInBlock() - 1):
            self.complete()

    def complete(self):
        if self.editor.isReadOnly():
            return
        self.request('completion', lambda result: self.show_completion(result) if self.editor.hasFocus() else None)

    def show_completion(self, result):
        self.popup.clear()
        items = result.get('items', []) if isinstance(result, dict) else result or []
        self.completion_revision = self.editor.document().revision()
        cursor = self.editor.textCursor()
        prefix = cursor.block().text()[:py_index(cursor.block().text(), cursor.positionInBlock())]
        prefix = re.search(r'\w*$', prefix).group().casefold()
        items = sorted(items, key=lambda entry: entry.get('sortText', entry.get('label', '')))
        filtered = [entry for entry in items if entry.get('filterText', entry.get('label', '')).casefold().startswith(prefix)]
        for entry in filtered[:150]:
            if entry.get('insertTextFormat', 1) != 1:
                continue
            item = QListWidgetItem(entry.get('label', '') + ('  ·  ' + entry['detail'][:100] if entry.get('detail') else ''))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.popup.addItem(item)
        if self.popup.count():
            self.popup.setCurrentRow(0)
            rect = self.editor.cursorRect()
            self.popup.resize(min(560, max(260, self.editor.width() - 40)), min(240, self.popup.count() * 24 + 8))
            point = self.editor.viewport().mapToGlobal(rect.bottomLeft())
            screen = self.editor.screen().availableGeometry()
            point.setX(min(point.x(), screen.right() - self.popup.width()))
            if point.y() + self.popup.height() > screen.bottom():
                point.setY(self.editor.viewport().mapToGlobal(rect.topLeft()).y() - self.popup.height())
            self.popup.move(point)
            self.popup.show()

    def accept_completion(self, item=None):
        item = item or self.popup.currentItem()
        if item is None or self.completion_revision != self.editor.document().revision():
            self.dismiss()
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        edit = entry.get('textEdit')
        if edit:
            edit = {'range': edit.get('range', edit.get('replace', edit.get('insert'))), 'newText': edit['newText']}
        else:
            cursor = self.editor.textCursor()
            end = cursor.position()
            before = cursor.block().text()[:py_index(cursor.block().text(), cursor.positionInBlock())]
            length = utf16_length(re.search(r'\w*$', before).group())
            edit = {'range': {'start': self.editor.position(end - length), 'end': self.editor.position(end)},
                    'newText': entry.get('insertText', entry.get('label', ''))}
        edits = [edit] + entry.get('additionalTextEdits', [])
        self.dismiss()
        self.editor.apply_text_edits(edits)
        self.editor.setFocus()

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() == QEvent.Type.KeyPress and self.popup.isVisible():
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                delta = 1 if key == Qt.Key.Key_Down else -1
                self.popup.setCurrentRow((self.popup.currentRow() + delta) % self.popup.count())
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                self.accept_completion()
                return True
            if key == Qt.Key.Key_Escape:
                self.dismiss()
                return True
            self.popup.hide()
        return super().eventFilter(watched, event)

    def dismiss(self):
        self.popup.hide()
        QToolTip.hideText()

    @staticmethod
    def documentation(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return '\n\n'.join(CodeAssistance.documentation(v) for v in value)
        if isinstance(value, dict):
            return value.get('value', '')
        return ''

    def tooltip(self, text, point=None):
        if text:
            QToolTip.showText(point or self.editor.viewport().mapToGlobal(self.editor.cursorRect().bottomLeft()),
                '<pre style="white-space:pre-wrap">' + html.escape(text[:6000]) + '</pre>', self.editor)

    def hover(self, payload):
        self.request('hover', lambda result: self.tooltip(self.documentation(result.get('contents')),
            payload['global']) if result else None, payload['position'])

    def signature(self):
        self.request('signatureHelp', lambda result: self.tooltip('\n'.join(
            item.get('label', '') for item in (result or {}).get('signatures', [])[:3])))

    def definition(self):
        def located(result):
            locations = result if isinstance(result, list) else [result] if result else []
            if locations:
                location = locations[0]
                self.navigate(location.get('uri', location.get('targetUri')), location.get('range', location.get('targetSelectionRange')))
            else:
                self.status('No definition found at this position')
        self.request('definition', located)

    def navigate(self, uri, range):
        if not uri or not range:
            return
        start = range.get('start', {})
        if self.client and uri == self.client.uri:
            self.editor.go_to_line(start.get('line', 0) + 1, start.get('character', 0) + 1)
            return
        parsed = urlsplit(uri)
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            self.status('Definition is not in a local file')
            return
        path = Path(unquote(parsed.path))
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            self.status(f'Cannot open definition: {error}')
            return
        preview = QDialog(self.pane)
        preview.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        preview.setWindowTitle(f'{path.name} — definition (read only)')
        preview.resize(850, 560)
        layout = QVBoxLayout(preview)
        label = QLabel(str(path))
        label.setWordWrap(True)
        layout.addWidget(label)
        editor = SourceEditor()
        editor.setAccessibleName('Read-only definition preview')
        editor.setFont(self.editor.font())
        editor.set_language('python' if path.suffix in ('.py', '.pyi') else 'c')
        editor.setPlainText(text)
        editor.setReadOnly(True)
        editor.go_to_line(start.get('line', 0) + 1, start.get('character', 0) + 1)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(preview.close)
        layout.addWidget(buttons)
        self.preview = preview
        preview.show()

    def analysis_diagnostics(self, packet):
        # Analysis from before the debounce is not an annotation of the new buffer.
        if self.client.text != self.editor.toPlainText():
            return
        items = [{**item, 'origin': 'Analysis', 'file': self.pane.filename} for item in packet.get('diagnostics', [])]
        self.pane.set_diagnostics('Analysis', items)

    def compiled(self, log, source, aliases=()):
        self.build_source = source
        self.build_items = compiler_diagnostics(log, source, aliases)
        self.update_build_diagnostics()
        if self.build_items:
            self.pane.problems_button.setChecked(True)

    def update_build_diagnostics(self):
        stale = self.build_source is not None and self.build_source != self.editor.toPlainText()
        self.pane.set_diagnostics('Compiler', [{**item, 'stale': stale} for item in self.build_items])

    def navigate_diagnostic(self, diagnostic):
        if diagnostic.get('stale'):
            self.status('This compiler result refers to an older buffer · rebuild for current locations')
            return
        if diagnostic.get('external'):
            self.navigate(Path(diagnostic['file']).resolve().as_uri(), diagnostic['range'])
        else:
            start = diagnostic['range']['start']
            if hasattr(self.parent(), 'panels'):
                self.parent().panels.reveal('editor')
            self.editor.go_to_line(start.get('line', 0) + 1, start.get('character', 0) + 1)

    def setup_dialog(self):
        dialog = QDialog(self.pane)
        dialog.setWindowTitle('Code assistance setup')
        dialog.resize(740, 480)
        layout = QVBoxLayout(dialog)
        info = QLabel('Local tools provide completion, documentation, definitions and diagnostics. Basic editing works without them.')
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        fields = {}
        for key, title in [('clangd', 'clangd executable'), ('pyright-langserver', 'Pyright executable'), ('stubs', 'MicroPython 1.26 stubs directory')]:
            row = QHBoxLayout()
            field = QLineEdit(self.settings.value(f'editor/{key}', '', str))
            field.setPlaceholderText('Auto-detect')
            field.setAccessibleName(title)
            fields[key] = field
            row.addWidget(field)
            browse = QPushButton('Browse…')
            def choose(checked=False, key=key, field=field):
                path = QFileDialog.getExistingDirectory(dialog, 'Select MicroPython stubs') if key == 'stubs' else QFileDialog.getOpenFileName(dialog, 'Select language server executable')[0]
                if path:
                    field.setText(path)
            browse.clicked.connect(choose)
            row.addWidget(browse)
            form.addRow(title, row)
        layout.addLayout(form)
        detected = QLabel()
        detected.setWordWrap(True)
        def detect():
            rows = [f'{name}: {executable(name, fields[name].text()) or "not found"}' for name in ('clangd', 'pyright-langserver')]
            rows.append('Stubs: ' + str(stub_directory(fields['stubs'].text()) or 'not found (machine.pyi and stdlib required)'))
            detected.setText('\n'.join(rows))
        for field in fields.values():
            field.textChanged.connect(detect)
        detect()
        layout.addWidget(detected)
        instructions = QPlainTextEdit()
        instructions.setReadOnly(True)
        instructions.setAccessibleName('Code assistance installation instructions')
        instructions.setPlainText('Ubuntu / Debian: install clangd and Node.js/npm using your package manager.\n'
            'From the simulator checkout, install optional tools locally:\n\n'
            'npm install --prefix .tools/editor pyright@1.1.414\n'
            f'{sys.executable} -m pip install --target .tools/editor/micropython-stubs '
            'micropython-rp2-rpi_pico-stubs==1.26.0.post1 micropython-stdlib-stubs==1.26.0.post3\n\n'
            'You can select existing installations above. No commands run automatically.\n'
            'C requires the ARM toolchain; SDK code also requires Pico SDK 2.2.0.\n'
            'Stubs describe the MicroPython API, not simulator peripheral compatibility.')
        layout.addWidget(instructions, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close)
        apply = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply.setText('Apply + Restart')
        def save():
            for key, field in fields.items():
                self.settings.setValue(f'editor/{key}', field.text().strip())
            self.settings.sync()
            self.active = True
            self.restart()
            detect()
        apply.clicked.connect(save)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.timer.stop()
        self.completion_timer.stop()
        self.dismiss()
        self.cancel_preparation()
        for client in self.findChildren(LspClient):
            client.dispose()
        self.client = None
        self.directory.cleanup()
