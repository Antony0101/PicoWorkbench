from copy import deepcopy
from pathlib import Path
import math
import hashlib
from time import monotonic_ns

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..controller import Simulation
from ..engine import Stop
from .. import demo
from ..repl import PythonTransfer
from ..capture import SerialPlot
from ..device_files import FileJob, FileResult
from .document import fingerprint, validate_circuit


class Worker(QObject):
    updated = Signal(object)
    failed = Signal(str)
    memory = Signal(object)
    finished = Signal()
    acknowledged = Signal(object)
    file_result = Signal(object)

    def __init__(self, wall_clock=monotonic_ns):
        super().__init__()
        self.sim = None
        self.running = False
        self.timer = None
        self.speed = 1.0
        self.remaining_instructions = None
        self.wall_clock = wall_clock
        self._wall_ns = 0
        self._target_ns = 0
        self.python_transfer = None
        self.uart_tail = b''
        self.python_interrupt_pending = False
        self.loaded_source = demo.source()
        self.loaded_manifest = None
        self.loaded_name = 'blink'
        self.flash_baseline = None
        self.exported_flash = None
        self.flash_dirty = False
        self.frames = 0
        self.upload_source = None
        self.display_revisions = {}
        self.plot = SerialPlot()
        self.plot_revision = self.bus_revision = -1
        self.plot_epoch = 0
        self.trace_drops = 0
        self.file_job = None
        self.python_idle = False
        self.repl_connect = False
        self.repl_connect_started = 0
        self.file_restore_running = False
        self.file_restore_instructions = None

    def rebase_playback(self):
        self._wall_ns = self.wall_clock()
        self._target_ns = self.sim.engine.snapshot().time_ns

    def replace_simulation(self, project, image):
        validate_circuit(project)
        replacement = Simulation(project)
        try:
            if image is not None:
                replacement.load(image)
        except Exception:
            replacement.close()
            raise
        self.sim.close()
        self.sim = replacement

    @Slot()
    def initialize(self):
        try:
            self.sim = Simulation()
            self.sim.load(demo.image())
            self.flash_baseline = self.flash_hash()
            self.timer = QTimer(self)
            self.timer.setInterval(16)
            self.timer.timeout.connect(self.tick)
            self.timer.start()
            self.publish(reset=True)
        except Exception as error:
            self.failed.emit(str(error))

    def publish(self, reset=False, clock_changed=False):
        if self.sim is not None:
            if reset:
                if self.file_job:
                    self.file_job.error = 'Simulation reset during file operation'
                    self.file_job.finish()
                    self.file_result.emit(self.file_job.result())
                    self.file_job = None
                self.python_idle = False
                self.repl_connect = False
                self.plot.clear()
                self.plot_epoch += 1
                self.plot_revision = self.bus_revision = -1
                self.trace_drops = 0
                self.display_revisions.clear()
                self.python_transfer = None
                self.uart_tail = b''
                self.python_interrupt_pending = False
            state = self.sim.engine.snapshot()
            self.frames += 1
            events = self.sim.engine.events()
            self.uart_tail = (self.uart_tail + bytes(e.value for e in events if e.kind == 2 and e.source == 0))[-512:]
            management = self.file_job is not None
            if self.file_job:
                self.file_job.consume(self.sim, events)
                if self.file_job.done:
                    result = self.file_job.result()
                    self.file_job = None
                    self.python_idle = self.uart_tail.endswith(b'>>> ')
                    self.running = self.file_restore_running
                    self.remaining_instructions = self.file_restore_instructions
                    self.rebase_playback()
                    self.file_result.emit(result)
            elif self.python_transfer:
                self.python_transfer.consume(self.sim, events)
                if self.python_transfer.phase in ('execute', 'exit') or self.python_transfer.done:
                    self.loaded_source = self.upload_source
                if self.python_transfer.done:
                    self.running = False
                    self.python_idle = True
                if self.python_interrupt_pending and not self.python_transfer.scheduled:
                    self._interrupt_python()
            elif self.sim.project.builtin == 'micropython' and any(e.kind == 2 and e.source == 0 for e in events):
                self.python_idle = self.uart_tail.endswith(b'>>> ')
                if self.python_idle and self.repl_connect:
                    self.repl_connect = False
                    self.running = False
            if state.dropped_events != self.trace_drops:
                self.plot.gap()
                self.trace_drops = state.dropped_events
            plot_events = events
            if self.python_transfer and self.plot.port == 0:
                plot_events = self.python_transfer.stdout_events
            if not management:
                self.plot.feed(plot_events)
            if self.flash_baseline and (not self.running or self.frames % 60 == 0):
                current = self.flash_hash()
                self.flash_dirty = current != self.flash_baseline and current != self.exported_flash
            displays = {}
            for key, device in self.sim.devices.items():
                if hasattr(device, 'snapshot'):
                    frame = device.snapshot()
                    if self.display_revisions.get(key) != frame:
                        displays[key] = frame
                        self.display_revisions[key] = frame
            plot_data = bus_data = None
            if reset or not self.running or self.frames % 6 == 0:
                if self.plot_revision != self.plot.revision:
                    plot_data = self.plot.snapshot()
                    self.plot_revision = self.plot.revision
                if self.bus_revision != self.sim.bus_capture.revision:
                    bus_data = tuple(self.sim.bus_capture.records)
                    self.bus_revision = self.sim.bus_capture.revision
            self.updated.emit({"plot": plot_data, "plot_epoch": self.plot_epoch,
                               "python_idle": self.python_idle,
                               "files_busy": self.file_job is not None,
                               "files_progress": (self.file_job.progress, self.file_job.total) if self.file_job else None,
                               "repl_connect": self.repl_connect,
                               "plot_gaps": self.plot.gaps, "plot_dropped": self.plot.dropped_samples,
                               "bus": bus_data, "bus_dropped": self.sim.bus_capture.dropped,
                               "displays": displays, "state": state, "events": events,
                               "pins": dict(self.sim.pin_for_component), "running": self.running,
                               "reset": reset, "devices": {k: v.summary() for k, v in self.sim.devices.items()},
                               "endpoints": {k: v[0] for k, v in self.sim.endpoint_pins.items() if v},
                               "pwm": state.pwm, "pio": tuple(self.sim.engine.pio(i) for i in range(2)),
                               "dma": self.sim.engine.dma(),
                               "cores": self.sim.engine.cores(),
                               "startup": self.sim.project.startup,
                               "applied": fingerprint(self.sim.project, circuit=True),
                               "loaded_source": self.loaded_source, "loaded_name": self.loaded_name,
                               "manifest": self.loaded_manifest,
                               "flash_dirty": self.flash_dirty,
                               "python_status": ("error" if self.python_transfer.stderr else "finished") if self.python_transfer and self.python_transfer.done else
                                                self.python_transfer.phase if self.python_transfer else "",
                               "speed": self.speed,
                               "remaining_instructions": self.remaining_instructions,
                               "clock_changed": clock_changed})

    @Slot()
    def tick(self):
        if not self.running or self.sim is None:
            return
        try:
            if self.file_job or self.repl_connect:
                if self.file_job:
                    self.file_job.advance(self.sim)
                if self.repl_connect and self.wall_clock() - self.repl_connect_started > 30_000_000_000:
                    self.repl_connect = False
                    self.running = False
                    self.failed.emit('Python has not reached an idle prompt. Use Interrupt Python if main.py is running.')
                else:
                    reason = self.sim.run(budget=16000)
                    if reason not in (Stop.BUDGET, Stop.DEADLINE, Stop.IDLE):
                        raise RuntimeError(f'REPL operation stopped: {reason.name}')
                self.publish()
                return
            state = self.sim.engine.snapshot()
            now = state.time_ns
            wall = self.wall_clock()
            # Keep accumulated targets so sub-instruction slow-motion intervals
            # do not each force a full instruction. Bound lag after host stalls.
            elapsed = max(0, wall - self._wall_ns)
            self._wall_ns = wall
            self._target_ns = min(self._target_ns + int(elapsed * self.speed),
                                  now + max(1, int(50_000_000 * self.speed)))
            if self._target_ns <= now:
                return
            # Allow fast playback without making one command monopolize the
            # worker at high configured clocks. Speed remains a host-limited target.
            budget = min(64_000, max(16_000, math.ceil(state.clock_hz * 0.016 * self.speed)))
            if self.remaining_instructions is not None:
                budget = min(budget, self.remaining_instructions)
            before = state.instructions
            if self.python_transfer and not self.python_transfer.done and not self.python_interrupt_pending:
                self.python_transfer.advance(self.sim)
            reason = self.sim.run(deadline_ns=self._target_ns, budget=budget)
            if self.remaining_instructions is not None:
                self.remaining_instructions -= self.sim.engine.snapshot().instructions - before
                if self.remaining_instructions <= 0:
                    self.running = False
            if reason not in (Stop.BUDGET, Stop.DEADLINE):
                self.running = False
            if not self.running:
                self.remaining_instructions = None
            self.publish()
        except Exception as error:
            self.running = False
            self.remaining_instructions = None
            if self.file_job:
                self.file_job.error = str(error)
                self.file_job.finish()
                self.file_result.emit(self.file_job.result())
                self.file_job = None
                self.python_idle = False
            self.publish()
            self.failed.emit(str(error))

    @Slot(object)
    def command(self, message):
        try:
            name = message["name"]
            if name == "shutdown":
                if self.timer:
                    self.timer.stop()
                if self.sim:
                    if self.file_job:
                        self.file_job.error = 'Application closed'
                        self.file_job.finish()
                    self.sim.close()
                    self.sim = None
                self.finished.emit()
                return
            if self.sim is None:
                raise RuntimeError("Simulation engine is not initialized")
            if self.file_job and name not in ('files_cancel', 'plot_clear', 'plot_port', 'plot_export', 'bus_clear', 'bus_capture', 'bus_export', 'memory', 'adc', 'button'):
                raise RuntimeError('Wait for the file operation or cancel it before changing execution')
            reset = False
            clock_changed = False
            if name == "run":
                self.running = True
                self.remaining_instructions = None
                self.rebase_playback()
            elif name == "run_instructions":
                count = message["count"]
                if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1_000_000:
                    raise ValueError("Instruction count must be between 1 and 1,000,000")
                self.remaining_instructions = count
                self.running = True
                self.rebase_playback()
            elif name == "speed":
                speed = message["value"]
                if (isinstance(speed, bool) or not isinstance(speed, (int, float))
                        or not math.isfinite(speed) or not 0.01 <= speed <= 4):
                    raise ValueError("Playback speed must be between 0.01 and 4")
                self.speed = float(speed)
                self.rebase_playback()
            elif name == "pause":
                self.running = False
                self.remaining_instructions = None
            elif name == "step":
                self.running = False
                self.remaining_instructions = None
                self.sim.step()
            elif name == "reset":
                self.running = False
                self.remaining_instructions = None
                self.sim.reset()
                reset = True
            elif name == "load":
                self.running = False
                self.remaining_instructions = None
                self.check_flash_replacement(message)
                project = deepcopy(self.sim.project)
                project.startup = message.get('startup', project.startup)
                project.firmware = str(message['path'])
                image = message['path']
                manifest = message.get('manifest')
                if manifest:
                    image = Path(image).read_bytes()
                    if hashlib.sha256(image).hexdigest() != manifest.elf_sha256 or not manifest.current():
                        raise ValueError('Build artifact or inputs changed before loading; rebuild first')
                self.replace_simulation(project, image)
                self.loaded_source = message.get('source')
                self.loaded_manifest = message.get('manifest')
                self.loaded_name = str(message['path'])
                self.flash_baseline = self.flash_hash()
                self.exported_flash = None
                reset = True
            elif name == "project":
                self.running = False
                self.remaining_instructions = None
                project = deepcopy(message["project"])
                preserve = message.get('preserve', False)
                if preserve:
                    if project.startup == 'rom':
                        image = self.sim.engine.flash_image()
                    else:
                        if self.flash_baseline and self.flash_hash() != self.flash_baseline and not message.get('discard_flash'):
                            raise ValueError('Modified SDK flash cannot be reapplied automatically. Export flash, then explicitly discard it before applying.')
                        image = self.sim.image
                else:
                    self.check_flash_replacement(message)
                    if project.sdk_project:
                        image = project.resolve(project.firmware) if project.firmware and project.resolve(project.firmware).is_file() else None
                    else:
                        image = project.resolve(project.firmware) if project.firmware else demo.image(project.builtin)
                self.replace_simulation(project, image)
                if not preserve:
                    from ..sdk_projects import BuildManifest
                    self.loaded_manifest = BuildManifest.load(image) if isinstance(image, Path) else None
                    self.loaded_source = (None if project.builtin == 'micropython' else
                        message.get('source', demo.source(project.builtin) if not project.firmware else None))
                    self.loaded_name = project.firmware or project.builtin
                    self.flash_baseline = self.flash_hash()
                    self.exported_flash = None
                reset = True
            elif name == "clock":
                self.running = False
                self.remaining_instructions = None
                project = deepcopy(self.sim.project)
                project.clock_hz = message["hz"]
                # Reload the exact current image, including unsaved scratch builds.
                image = self.sim.engine.flash_image() if project.startup == 'rom' else self.sim.image
                if project.startup != 'rom':
                    self.check_flash_replacement(message)
                self.replace_simulation(project, image)
                reset = clock_changed = True
            elif name == "button":
                self.sim.button(message["id"], message["pressed"])
            elif name == "uart":
                if self.python_transfer and not self.python_transfer.done:
                    raise RuntimeError("Interrupt the current Python program before sending other UART input")
                self.sim.uart(message["data"])
                self.python_idle = False
                self.python_transfer = None
            elif name == "python":
                if self.python_transfer and not self.python_transfer.done:
                    raise RuntimeError("A Python upload/program is already active")
                self.python_transfer = PythonTransfer(message["source"], ready=self.uart_tail.endswith(b'>>> '))
                self.upload_source = message['source']
                self.python_idle = False
                self.repl_connect = False
                self.running = True
                self.remaining_instructions = None
                self.rebase_playback()
            elif name == "interrupt_python":
                self.repl_connect = True
                self.repl_connect_started = self.wall_clock()
                if self.python_transfer and self.python_transfer.scheduled:
                    self.python_interrupt_pending = True
                else:
                    self._interrupt_python()
                self.running = True
                self.remaining_instructions = None
                self.rebase_playback()
            elif name == "adc":
                self.sim.adc(message["channel"], message["value"])
            elif name == 'files_connect':
                if self.sim.project.builtin != 'micropython':
                    raise ValueError('Files require a MicroPython project')
                if not self.python_idle:
                    self.repl_connect = True
                    self.repl_connect_started = self.wall_clock()
                    self.running = True
            elif name == 'files':
                if self.sim.project.builtin != 'micropython' or not self.python_idle or self.repl_connect:
                    raise ValueError('Interrupt Python and wait for an idle prompt before using files')
                if self.python_transfer and not self.python_transfer.done:
                    raise ValueError('A Python program is active')
                self.file_job = FileJob(message['id'], message['operation'], message.get('path', '/'),
                    **{key: message[key] for key in ('data', 'host', 'destination') if key in message})
                self.file_restore_running = self.running
                self.file_restore_instructions = self.remaining_instructions
                self.python_transfer = None
                self.python_idle = False
                self.running = True
                self.remaining_instructions = None
            elif name == 'files_cancel':
                if self.file_job:
                    self.file_job.cancel()
            elif name in ('plot_clear', 'plot_port'):
                if name == 'plot_port':
                    if message['port'] not in (0, 1):
                        raise ValueError('Plot UART must be 0 or 1')
                    self.plot.port = message['port']
                self.plot.clear()
                self.plot_epoch += 1
                self.plot_revision = -1
            elif name == 'plot_export':
                Path(message['path']).write_text(self.plot.csv())
            elif name == 'bus_clear':
                self.sim.bus_capture.clear()
                self.bus_revision = -1
            elif name == 'bus_capture':
                self.sim.bus_capture.enabled = bool(message['enabled'])
            elif name == 'bus_export':
                Path(message['path']).write_text(self.sim.bus_capture.export(message.get('format', 'json')))
            elif name == "breakpoint":
                self.sim.engine.breakpoint(message["address"], message["enabled"])
            elif name == "memory":
                address, length = message["address"], message.get("length", 128)
                self.memory.emit((address, self.sim.engine.peek(address, length)))
            elif name == "recording":
                self.sim.export_recording(message["path"])
            elif name == "save_flash":
                message['path'].write_bytes(self.sim.engine.flash_image())
                self.exported_flash = self.flash_hash()
            elif name == 'inspect_flash':
                pass
            else:
                raise ValueError(f"Unknown controller command: {name}")
            self.publish(reset=reset, clock_changed=clock_changed)
            if 'request' in message:
                self.acknowledged.emit({'request': message['request'], 'ok': True, 'name': name})
        except Exception as error:
            if message.get('name') == 'files':
                self.file_result.emit(FileResult(message.get('id', 0), message.get('operation', ''),
                                                message.get('path', '/'), False, error=str(error)))
            if self.file_job is None and message.get('name') not in ('files', 'files_connect'):
                self.running = False
                self.remaining_instructions = None
            self.publish()
            self.failed.emit(str(error))
            if 'request' in message:
                self.acknowledged.emit({'request': message['request'], 'ok': False, 'error': str(error)})

    def flash_hash(self):
        return hashlib.sha256(self.sim.engine.flash_image()).digest()

    def check_flash_replacement(self, message):
        if (self.flash_baseline and not message.get('discard_flash') and
                self.flash_hash() not in (self.flash_baseline, self.exported_flash)):
            raise ValueError('Modified runtime flash would be lost. Export flash or explicitly discard runtime changes before replacing firmware.')

    def _interrupt_python(self):
        self.python_transfer = None
        self.python_interrupt_pending = False
        self.python_idle = False
        self.uart_tail = b''
        self.sim.uart(b'\x03\x02')
