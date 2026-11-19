"""Small nonblocking LSP transport. Processes and callbacks belong to the GUI thread."""
import json
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, Slot


class LspClient(QObject):
    status = Signal(str)
    ready = Signal()
    diagnostics = Signal(object)
    stopped = Signal()
    MAX_MESSAGE = 16 * 1024 * 1024

    def __init__(self, parent=None, *, timeout_ms=10000):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.started.connect(self._initialize)
        self.process.errorOccurred.connect(self._error)
        self.process.finished.connect(self._finished)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._timeouts)
        self.terminate_timer = QTimer(self)
        self.terminate_timer.setSingleShot(True)
        self.terminate_timer.timeout.connect(self._terminate)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self._kill)
        self.timeout_ms = timeout_ms
        self.buffer = bytearray()
        self.pending = {}
        self.number = 0
        self.generation = 0
        self.initialized = False
        self.closing = False
        self.uri = None
        self.version = 0
        self.text = ''
        self.language = ''
        self.capabilities = {}
        self.configuration = {}
        self.error_log = ''

    def start(self, executable, args, root, *, initialization=None, configuration=None):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            raise RuntimeError('Stop the previous language server before starting another')
        self.generation += 1
        self.terminate_timer.stop()
        self.kill_timer.stop()
        self.buffer.clear()
        self.pending.clear()
        self.initialized = self.closing = False
        self.uri = None
        self.root = root
        self.initialization = initialization or {}
        self.configuration = configuration or {}
        self.error_log = ''
        self.status.emit('Starting code assistance…')
        self.process.setProgram(executable)
        self.process.setArguments(list(args))
        self.process.start()
        self.timer.start()

    def _send(self, message):
        if self.process.state() != QProcess.ProcessState.Running:
            return
        payload = json.dumps(message, ensure_ascii=False).encode('utf-8')
        self.process.write(f'Content-Length: {len(payload)}\r\n\r\n'.encode('ascii') + payload)

    def notify(self, method, params=None):
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params or {}})

    def request(self, method, params, callback, *, document=True):
        if self.closing and method != 'shutdown':
            return None
        self.number += 1
        number = self.number
        identity = (self.generation, self.uri, self.version) if document else None
        self.pending[number] = (time.monotonic(), identity, callback, method)
        self._send({'jsonrpc': '2.0', 'id': number, 'method': method, 'params': params})
        return number

    @Slot()
    def _initialize(self):
        self.request('initialize', {'processId': None, 'rootUri': self.root,
            'workspaceFolders': [{'uri': self.root, 'name': 'RP2040 editor'}],
            'capabilities': {'general': {'positionEncodings': ['utf-16']},
                'workspace': {'configuration': True},
                'textDocument': {'synchronization': {'didSave': False},
                    'completion': {'completionItem': {'snippetSupport': False,
                        'insertReplaceSupport': True, 'documentationFormat': ['plaintext']}},
                    'hover': {'contentFormat': ['plaintext']},
                    'signatureHelp': {'signatureInformation': {'documentationFormat': ['plaintext']}},
                    'publishDiagnostics': {'versionSupport': True}}},
            'initializationOptions': self.initialization}, self._initialized, document=False)

    def _initialized(self, result):
        self.capabilities = result.get('capabilities', {}) if isinstance(result, dict) else {}
        if self.capabilities.get('positionEncoding', 'utf-16') != 'utf-16':
            self.status.emit('Language server selected an unsupported position encoding')
            self.stop()
            return
        self.initialized = True
        self.notify('initialized')
        self.notify('workspace/didChangeConfiguration', {'settings': self.configuration})
        self.status.emit('Code assistance ready')
        self.ready.emit()

    def open_document(self, uri, language, text):
        if self.uri:
            self.notify('textDocument/didClose', {'textDocument': {'uri': self.uri}})
        self.cancel_document_requests()
        self.uri, self.language, self.text, self.version = uri, language, text, 1
        self.notify('textDocument/didOpen', {'textDocument': {'uri': uri,
            'languageId': language, 'version': self.version, 'text': text}})

    def change_document(self, text):
        if not self.uri or text == self.text:
            return
        old = self.text
        self.text = text
        self.version += 1
        self.cancel_document_requests()
        sync = self.capabilities.get('textDocumentSync', 1)
        kind = sync.get('change', 1) if isinstance(sync, dict) else sync
        change = {'text': text}
        if kind == 2:
            # An entire-document replacement is a valid incremental change.
            lines = old.split('\n')
            change['range'] = {'start': {'line': 0, 'character': 0}, 'end': {
                'line': len(lines) - 1, 'character': len(lines[-1].encode('utf-16-le')) // 2}}
        self.notify('textDocument/didChange', {'textDocument': {'uri': self.uri, 'version': self.version},
                                             'contentChanges': [change]})

    def cancel_document_requests(self):
        for number, (_, identity, _, _) in list(self.pending.items()):
            if identity is not None:
                self.notify('$/cancelRequest', {'id': number})
                self.pending.pop(number, None)

    @Slot()
    def _read(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        try:
            while True:
                boundary = self.buffer.find(b'\r\n\r\n')
                if boundary < 0:
                    if len(self.buffer) > 8192:
                        raise ValueError('Invalid LSP header')
                    return
                headers = self.buffer[:boundary].decode('ascii').split('\r\n')
                lengths = [int(line.split(':', 1)[1]) for line in headers if line.lower().startswith('content-length:')]
                if len(lengths) != 1 or not 0 < lengths[0] <= self.MAX_MESSAGE:
                    raise ValueError('Invalid LSP message size')
                end = boundary + 4 + lengths[0]
                if len(self.buffer) < end:
                    return
                message = json.loads(bytes(self.buffer[boundary + 4:end]))
                del self.buffer[:end]
                self._receive(message)
        except (ValueError, UnicodeError, TypeError, AttributeError) as error:
            self.status.emit(f'Code assistance protocol error: {error}')
            self.stop()

    def _receive(self, message):
        if not isinstance(message, dict):
            raise ValueError('Expected an LSP object')
        if 'method' in message:
            method, params = message['method'], message.get('params', {})
            if 'id' in message:
                result = None
                if method == 'workspace/configuration':
                    result = []
                    for item in params.get('items', []):
                        value = self.configuration
                        for key in item.get('section', '').split('.'):
                            if key:
                                value = value.get(key, {}) if isinstance(value, dict) else {}
                        result.append(value)
                elif method == 'workspace/workspaceFolders':
                    result = [{'uri': self.root, 'name': 'RP2040 editor'}]
                elif method not in ('client/registerCapability', 'client/unregisterCapability', 'window/workDoneProgress/create'):
                    self._send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported client request'}})
                    return
                self._send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
            elif method == 'textDocument/publishDiagnostics' and params.get('uri') == self.uri:
                if params.get('version', self.version) == self.version and not self.closing:
                    self.diagnostics.emit(params)
            return
        pending = self.pending.pop(message.get('id'), None)
        if not pending:
            return
        _, identity, callback, method = pending
        if identity is not None and identity != (self.generation, self.uri, self.version):
            return
        if 'error' in message:
            self.status.emit(f"Code assistance: {message['error'].get('message', 'request failed')}")
            if method == 'initialize':
                self.stop()
            return
        callback(message.get('result'))

    @Slot()
    def _stderr(self):
        self.error_log = (self.error_log + bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace'))[-16000:]

    @Slot()
    def _timeouts(self):
        now = time.monotonic()
        for number, (started, _, _, method) in list(self.pending.items()):
            if (now - started) * 1000 > self.timeout_ms:
                self.pending.pop(number, None)
                self.notify('$/cancelRequest', {'id': number})
                self.status.emit(f'Code assistance timed out: {method}')
                if method == 'initialize':
                    self.stop()

    @Slot(QProcess.ProcessError)
    def _error(self, error):
        if not self.closing:
            self.status.emit('Code assistance unavailable: ' + self.process.errorString())
        if error == QProcess.ProcessError.FailedToStart:
            self.timer.stop()
            self.pending.clear()
            self.stopped.emit()

    @Slot(int, QProcess.ExitStatus)
    def _finished(self, code, status):
        self.initialized = False
        self.timer.stop()
        self.terminate_timer.stop()
        self.kill_timer.stop()
        self.pending.clear()
        if not self.closing:
            self.status.emit(f'Code assistance stopped ({code}); use setup to restart')
        self.stopped.emit()

    def stop(self):
        if self.closing:
            return
        self.closing = True
        self.cancel_document_requests()
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.timer.stop()
            return
        if self.initialized:
            if self.uri:
                self.notify('textDocument/didClose', {'textDocument': {'uri': self.uri}})
            self.request('shutdown', None, lambda result: self.notify('exit'), document=False)
        else:
            self.process.terminate()
        # Owned timers disappear with the client; no callback can outlive QProcess.
        self.terminate_timer.start(800)
        self.kill_timer.start(1600)

    @Slot()
    def _terminate(self):
        if self.closing and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()

    @Slot()
    def _kill(self):
        if self.closing and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def dispose(self):
        """Application teardown only: bounded cleanup before QObject destruction."""
        self.stop()
        if not self.process.waitForFinished(200):
            self.process.kill()
            self.process.waitForFinished(200)
