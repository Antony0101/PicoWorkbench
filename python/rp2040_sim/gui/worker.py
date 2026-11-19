from copy import deepcopy
import math
import hashlib
from time import monotonic_ns

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..controller import Simulation
from ..engine import Stop
from .. import demo
from ..repl import PythonTransfer
from .document import fingerprint, validate_circuit


class Worker(QObject):
    updated = Signal(object)
    failed = Signal(str)
    memory = Signal(object)
    finished = Signal()
    acknowledged = Signal(object)

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
        self.loaded_name = 'blink'
        self.flash_baseline = None
        self.exported_flash = None
        self.flash_dirty = False
        self.frames = 0
        self.upload_source = None

    def rebase_playback(self):
        self._wall_ns = self.wall_clock()
        self._target_ns = self.sim.engine.snapshot().time_ns

    def replace_simulation(self, project, image):
        validate_circuit(project)
        replacement = Simulation(project)
        try:
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
                self.python_transfer = None
                self.uart_tail = b''
                self.python_interrupt_pending = False
            state = self.sim.engine.snapshot()
            self.frames += 1
            events = self.sim.engine.events()
            self.uart_tail = (self.uart_tail + bytes(e.value for e in events if e.kind == 2 and e.source == 0))[-512:]
            if self.python_transfer:
                self.python_transfer.consume(self.sim, events)
                if self.python_transfer.phase in ('execute', 'exit') or self.python_transfer.done:
                    self.loaded_source = self.upload_source
                if self.python_transfer.done:
                    self.running = False
                if self.python_interrupt_pending and not self.python_transfer.scheduled:
                    self._interrupt_python()
            if self.flash_baseline and (not self.running or self.frames % 60 == 0):
                current = self.flash_hash()
                self.flash_dirty = current != self.flash_baseline and current != self.exported_flash
            self.updated.emit({"state": state, "events": events,
                               "pins": dict(self.sim.pin_for_component), "running": self.running,
                               "reset": reset, "devices": {k: v.summary() for k, v in self.sim.devices.items()},
                               "endpoints": {k: v[0] for k, v in self.sim.endpoint_pins.items() if v},
                               "pwm": state.pwm, "pio": tuple(self.sim.engine.pio(i) for i in range(2)),
                               "dma": self.sim.engine.dma(),
                               "cores": self.sim.engine.cores(),
                               "startup": self.sim.project.startup,
                               "applied": fingerprint(self.sim.project, circuit=True),
                               "loaded_source": self.loaded_source, "loaded_name": self.loaded_name,
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
                    self.sim.close()
                    self.sim = None
                self.finished.emit()
                return
            if self.sim is None:
                raise RuntimeError("Simulation engine is not initialized")
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
                self.replace_simulation(project, message['path'])
                self.loaded_source = message.get('source')
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
                    image = project.resolve(project.firmware) if project.firmware else demo.image(project.builtin)
                self.replace_simulation(project, image)
                if not preserve:
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
            elif name == "python":
                if self.python_transfer and not self.python_transfer.done:
                    raise RuntimeError("A Python upload/program is already active")
                self.python_transfer = PythonTransfer(message["source"], ready=self.uart_tail.endswith(b'>>> '))
                self.upload_source = message['source']
                self.running = True
                self.remaining_instructions = None
                self.rebase_playback()
            elif name == "interrupt_python":
                if self.python_transfer and self.python_transfer.scheduled:
                    self.python_interrupt_pending = True
                else:
                    self._interrupt_python()
                self.running = True
                self.remaining_instructions = None
                self.rebase_playback()
            elif name == "adc":
                self.sim.adc(message["channel"], message["value"])
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
        self.sim.uart(b'\x03\x02')
