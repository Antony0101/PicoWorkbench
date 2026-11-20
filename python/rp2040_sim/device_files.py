"""MicroPython files through bounded interpreter jobs, never host flash parsing."""
from dataclasses import dataclass
import io
import json
from pathlib import Path
import posixpath
import tempfile
from time import monotonic

from .repl import PythonTransfer

MARKER = '\x1eRP3:'


def device_path(value):
    if not isinstance(value, str) or not value or len(value.encode('utf8')) > 200 or any(ord(c)<32 for c in value):
        raise ValueError('Device path must contain 1–200 UTF-8 bytes without control characters')
    return posixpath.normpath('/' + value.lstrip('/'))


@dataclass(frozen=True)
class FileResult:
    request: int
    operation: str
    path: str
    ok: bool
    value: object = None
    error: str = ''


class FileJob:
    """One serialized job. Every command returns to the friendly prompt.

    Cancel between commands so staged writes can be cleaned without interrupting
    user code. The worker enforces the initial confirmed-idle precondition.
    """
    def __init__(self, request, operation, path='/', *, data=None, host=None, destination=None):
        self.request, self.operation = request, operation
        self.path = device_path(path)
        self.destination = device_path(destination) if destination is not None else None
        self.stage = self.path + f'.rp3-{request}.tmp'
        self.host = Path(host) if host is not None else None
        self.done = self.cancelled = self.cleaning = False
        self.progress = self.total = 0
        self.value = self.error = None
        self.transfer = None
        self.stream = None
        self.temporary = None
        self.staged = False
        self.committing = False
        self.recovering = False
        self.interrupt_sent = False
        self.recovery_tail = bytearray()
        self.deadline_wall = self.deadline_ns = 0
        self.generator = self._commands(data)

    @staticmethod
    def helper(body):
        # Isolated globals avoid overwriting the running program's names.
        code = 'import os, json, binascii\n' + body
        return 'exec(' + repr(code) + ', {})\n'

    @staticmethod
    def reply(expression):
        return f'print({MARKER!r} + json.dumps({expression}))\n'

    def _commands(self, data):
        p = repr(self.path)
        if self.operation == 'list':
            reply = yield (
                f'entries=[]\nfor name in os.listdir({p}):\n'
                f' s=os.stat({p}.rstrip("/")+"/"+name)\n'
                ' entries.append({"name":name,"directory":bool(s[0]&0x4000),"size":s[6]})\n'
                ' if len(entries)>1000: raise ValueError("Directory exceeds 1000-entry listing limit")\n'
                f's=os.statvfs({p})\n' + self.reply('{"entries":entries,"free":s[0]*s[3]}'))
            self.value = reply
        elif self.operation in ('mkdir', 'delete', 'rename'):
            if self.path == '/':
                raise ValueError('The root directory cannot be changed')
            if self.operation == 'mkdir':
                body = f'os.mkdir({p})\n'
            elif self.operation == 'rename':
                if self.destination is None or self.destination == '/':
                    raise ValueError('Choose a destination file or directory')
                body = f'os.rename({p},{self.destination!r})\n'
            else:
                body = f's=os.stat({p})\n(os.rmdir if s[0]&0x4000 else os.remove)({p})\n'
            yield body + 'os.sync()\n' + self.reply('True')
        elif self.operation == 'upload':
            if self.path == '/':
                raise ValueError('Choose a destination file')
            self.stream = io.BytesIO(data) if data is not None else self.host.open('rb')
            self.total = len(data) if data is not None else self.host.stat().st_size
            # Refuse an existing staging file, including stale files from an old session.
            yield (f'try:\n os.stat({self.stage!r})\nexcept OSError as e:\n'
                   ' if e.args[0]!=2: raise\nelse:\n raise ValueError("Staging file already exists")\n'
                   f'with open({self.stage!r},"wb") as f: pass\n' + self.reply('True'))
            self.staged = True
            while True:
                chunk = self.stream.read(256)
                if not chunk:
                    break
                written = yield (f'with open({self.stage!r},"ab") as f:\n'
                    f' n=f.write(binascii.unhexlify({chunk.hex()!r}))\n' + self.reply('n'))
                if written != len(chunk):
                    raise OSError('Incomplete device write')
                self.progress += written
            self.committing = True
            yield (f'if os.stat({self.stage!r})[6]!={self.total}: raise OSError("Upload size mismatch")\n'
                   f'os.rename({self.stage!r},{p})\nos.sync()\n' + self.reply('True'))
            self.staged = False
        elif self.operation in ('download', 'read'):
            self.total = yield f's=os.stat({p})\n' + self.reply('s[6]')
            if not isinstance(self.total, int) or self.total < 0:
                raise ValueError('Invalid device file size')
            if self.operation == 'read':
                if self.total > 1_000_000:
                    raise ValueError('File exceeds editor limit; use Download instead')
                self.stream = io.BytesIO()
            else:
                temporary = tempfile.NamedTemporaryFile(dir=self.host.parent,
                    prefix=self.host.name+'.', suffix='.part', delete=False)
                self.temporary = Path(temporary.name)
                self.stream = temporary
            while self.progress < self.total:
                reply = yield (f'with open({p},"rb") as f:\n f.seek({self.progress})\n b=f.read(256)\n'
                               + self.reply('binascii.hexlify(b).decode()'))
                if not isinstance(reply, str):
                    raise ValueError('Invalid device file response')
                chunk = bytes.fromhex(reply)
                if not chunk or len(chunk)>256 or self.progress+len(chunk)>self.total:
                    raise ValueError('Device file changed or transfer was truncated')
                self.stream.write(chunk)
                self.progress += len(chunk)
            if self.operation == 'read':
                self.value = self.stream.getvalue()
            else:
                self.stream.close()
                self.temporary.replace(self.host)
                self.temporary = None
        else:
            raise ValueError('Unknown filesystem operation')

    def _cleanup(self):
        return (f'try:\n os.remove({self.stage!r})\nexcept OSError as e:\n'
                ' if e.args[0]!=2: raise\n' + self.reply('True'))

    def _start(self, simulation, body):
        self.transfer = PythonTransfer(self.helper(body), ready=True, interrupt=False,
                                       byte_cycles=100, minimum_period_ns=10000)
        self.deadline_wall = monotonic() + 30
        self.deadline_ns = simulation.engine.snapshot().time_ns + 5_000_000_000

    def advance(self, simulation):
        if self.done:
            return
        if self.recovering:
            if monotonic() > self.deadline_wall or simulation.engine.snapshot().time_ns > self.deadline_ns:
                self.error += '; reconnect required; staged file may require manual removal: ' + self.stage
                self.finish()
            elif not self.interrupt_sent and not self.transfer.scheduled:
                self.interrupt_sent = True
                simulation.uart(b'\x03\x02')
            return
        if self.transfer is None:
            self._next(simulation)
        if self.transfer is not None:
            if monotonic() > self.deadline_wall or simulation.engine.snapshot().time_ns > self.deadline_ns:
                self.error = 'Filesystem helper timed out; reconnect/interrupt Python before retrying'
                raw_owned = self.transfer.phase not in ('boot', 'enter')
                if raw_owned:
                    self.recovering = True
                    self.transfer.phase = 'recover'
                    self.transfer.pending.clear()
                    self.deadline_wall = monotonic() + 5
                    self.deadline_ns = simulation.engine.snapshot().time_ns + 1_000_000_000
                else:
                    self.finish()  # Do not interrupt a possibly running user program.
                return
            self.transfer.advance(simulation)

    def consume(self, simulation, events):
        if self.done or self.transfer is None:
            return
        self.transfer.consume(simulation, events)
        if self.recovering:
            if self.interrupt_sent:
                self.recovery_tail.extend(e.value for e in events if e.kind==2 and e.source==0)
                del self.recovery_tail[:-512]
                if self.recovery_tail.endswith(b'>>> '):
                    self.recovering = False
                    self.transfer = None
                    if self.staged:
                        self.cleaning = True
                        self._start(simulation, self._cleanup())
                    else:
                        self.finish()
            return
        if self.transfer.done:
            self._next(simulation, completed=True)

    def _next(self, simulation, completed=False):
        reply = None
        error = None
        if completed:
            transfer = self.transfer
            self.transfer = None
            if transfer.stderr:
                error = transfer.stderr.decode('utf8', 'replace')
            elif transfer.stdout_truncated:
                error = 'Filesystem response exceeds 64 KiB limit'
            else:
                try:
                    frames = [line[len(MARKER):] for line in bytes(transfer.stdout).decode('utf8').split('\n') if line.startswith(MARKER)]
                    if len(frames) != 1:
                        raise ValueError('Missing filesystem reply')
                    reply = json.loads(frames[0])
                except (ValueError, UnicodeError) as exc:
                    error = str(exc)
        if self.cleaning:
            if error:
                self.error += '; staging cleanup failed: ' + error
            self.staged = False
            self.finish()
            return
        if completed and not error and self.operation == 'upload' and not self.staged and not self.committing:
            self.staged = True
        if self.cancelled or error:
            self.error = error or 'Cancelled'
            if self.staged:
                self.cleaning = True
                self._start(simulation, self._cleanup())
            else:
                self.finish()
            return
        try:
            body = self.generator.send(reply) if completed else next(self.generator)
            self._start(simulation, body)
        except StopIteration:
            self.finish()
        except Exception as exc:
            self.error = str(exc)
            if self.staged:
                self.cleaning = True
                self._start(simulation, self._cleanup())
            else:
                self.finish()

    def finish(self):
        self.done = True
        if self.stream is not None:
            self.stream.close()
        if self.temporary is not None:
            self.temporary.unlink(missing_ok=True)
            self.temporary = None

    def cancel(self):
        if not self.committing:
            self.cancelled = True

    def result(self):
        return FileResult(self.request, self.operation, self.path, not self.error,
                          self.value, self.error or '')
