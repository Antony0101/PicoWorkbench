"""Headless application model, also used by the Qt simulation worker."""
from pathlib import Path
import hashlib
import json

from .engine import Drive, Engine, Event, Stop
from .components import RegisterSensor, SpiLoopback
from .project import Project


class Simulation:
    def __init__(self, project: Project | None = None):
        self.project = project or Project.demo()
        self.project.validate()
        self.engine = Engine(self.project.clock_hz)
        self.pressed: set[str] = set()
        self.recording: list[dict] = []
        self.image: bytes = b""
        self._circuit()

    def close(self):
        self.engine.close()

    def load(self, image: bytes | Path, *, startup: str | None = None):
        data = image if isinstance(image, bytes) else image.read_bytes()
        mode = self.project.startup if startup is None else startup
        self.engine.load(image, startup=mode, bootrom=self.project.resolve(self.project.bootrom) if self.project.bootrom else None)
        self.project.startup = mode
        self.image = data
        self.pressed.clear()
        self.recording.clear()
        self._circuit()

    def reset(self):
        self.engine.reset()
        self.pressed.clear()
        self.recording.clear()
        self._circuit()

    def _circuit(self):
        self.project.validate()
        self.groups = self.project.groups()
        self.pin_for_component = {}
        self.endpoint_pins = {}
        self.devices = {c.id: RegisterSensor() if c.kind == "i2c_sensor" else SpiLoopback()
                        for c in self.project.components if c.kind in ("i2c_sensor", "spi_loopback")}
        for group in self.groups:
            pins = sorted(int(e.split("GP")[1]) for e in group if e.startswith("pico:GP"))
            if not pins:
                continue
            for pin in pins:
                self.engine.connect_gpio(pin, pins[0])
            for endpoint in group:
                self.endpoint_pins[endpoint] = pins
            for c in self.project.components:
                if c.id + ":signal" in group:
                    self.pin_for_component[c.id] = pins[0]
        self._drive_components()

    def _drive_components(self):
        drives: dict[int, set[Drive]] = {}
        for c in self.project.components:
            if c.id not in self.pin_for_component:
                continue
            drive = (Drive.LOW if c.id in self.pressed else Drive.FLOATING) if c.kind == "button" else {
                "pullup": Drive.PULL_HIGH, "pulldown": Drive.PULL_LOW,
            }.get(c.kind, Drive.FLOATING)
            drives.setdefault(self.pin_for_component[c.id], set()).add(drive)
        for pin, values in sorted(drives.items()):
            if Drive.LOW in values and Drive.HIGH in values:
                drive = Drive.CONFLICT
            elif Drive.LOW in values:
                drive = Drive.LOW
            elif Drive.HIGH in values:
                drive = Drive.HIGH
            elif Drive.PULL_LOW in values and Drive.PULL_HIGH in values:
                # Both weak pulls cannot be represented by one drive; report this
                # explicitly rather than silently choosing one.
                raise ValueError(f"Opposing external pulls on GPIO{pin}")
            elif Drive.PULL_LOW in values:
                drive = Drive.PULL_LOW
            elif Drive.PULL_HIGH in values:
                drive = Drive.PULL_HIGH
            else:
                drive = Drive.FLOATING
            self.engine.gpio(pin, drive)

    def button(self, component: str, pressed: bool, record: bool = True):
        if not any(c.id == component and c.kind == "button" for c in self.project.components):
            raise ValueError(f"Unknown button: {component}")
        if pressed:
            self.pressed.add(component)
        else:
            self.pressed.discard(component)
        self._drive_components()
        if record:
            self.recording.append({"time_ns": self.engine.snapshot().time_ns,
                                   "kind": "button", "component": component, "pressed": pressed})

    def uart(self, data: bytes, port: int = 0):
        self.engine.uart(data, port)
        self.recording.append({"time_ns": self.engine.snapshot().time_ns,
                               "kind": "uart", "port": port, "hex": data.hex()})

    def adc(self, channel: int, value: int):
        self.engine.adc(channel, value)
        self.recording.append({"time_ns": self.engine.snapshot().time_ns,
                               "kind": "adc", "channel": channel, "value": value})

    def _device_response(self):
        request = self.engine.device_request()
        if request is None:
            raise RuntimeError("Engine stopped without a device request")
        candidates = []
        state = self.engine.snapshot()
        for component in self.project.components:
            if (request.bus == 0 and component.kind != "i2c_sensor") or (request.bus == 1 and component.kind != "spi_loopback"):
                continue
            def pin(name):
                pins = self.endpoint_pins.get(component.id + ":" + name, [])
                return pins[0] if len(pins) == 1 else None

            def function(p, expected):
                return p is not None and int.from_bytes(self.engine.peek(0x40014004 + p * 8), "little") & 31 == expected

            if request.bus == 0:
                sda, scl = pin("sda"), pin("scl")
                if not (function(sda, 3) and function(scl, 3)):
                    continue
                if sda % 2 or scl % 2 != 1 or (sda // 2) % 2 != request.controller or (scl // 2) % 2 != request.controller:
                    continue
                if component.address != request.target:
                    continue
            else:
                mosi, miso, sck, cs = pin("mosi"), pin("miso"), pin("sck"), pin("cs")
                if not (function(mosi, 1) and function(miso, 1) and function(sck, 1) and function(cs, 5)):
                    continue
                if not (mosi % 4 == 3 and miso % 4 == 0 and sck % 4 == 2):
                    continue
                if any((p // 8) % 2 != request.controller for p in (mosi, miso, sck)):
                    continue
                if state.pin(cs) != Drive.LOW or not state.gpio_oe & (1 << cs):
                    continue
            candidates.append(component)
        if len(candidates) > 1:
            raise ValueError("Multiple external devices responded to the same bus transfer")
        if not candidates:
            self.engine.complete_device(request, 0, False)
        else:
            model = self.devices[candidates[0].id]
            self.engine.complete_device(request, model.exchange(request.value, request.flags), True)

    def run(self, deadline_ns=None, budget=10_000):
        start = self.engine.snapshot().instructions
        while True:
            remaining = budget - (self.engine.snapshot().instructions - start)
            reason = self.engine.run(deadline_ns, max(0, remaining))
            if reason != Stop.DEVICE_REQUEST:
                return reason
            self._device_response()
            if remaining <= 0:
                return Stop.BUDGET

    def step(self):
        reason = self.engine.step()
        if reason == Stop.DEVICE_REQUEST:
            self._device_response()
            return Stop.BUDGET
        return reason

    def export_recording(self, path: Path):
        path.write_text(json.dumps({"schema": 1, "engine": "0.1.0", "startup": self._startup_identity(),
                                    "firmware_sha256": hashlib.sha256(self.image).hexdigest(),
                                    "circuit_sha256": self._circuit_digest(),
                                    "inputs": self.recording}, indent=2) + "\n")

    def _startup_identity(self):
        return {"direct": "direct-load", "sdk": "sdk-elf", "rom": "b2-rom"}[self.project.startup]

    def _circuit_digest(self):
        circuit = {"clock_hz": self.project.clock_hz,
                   "components": sorted((c.id, c.kind, c.address) for c in self.project.components),
                   "wires": sorted(tuple(sorted(w)) for w in self.project.wires)}
        if self.project.startup != "direct":
            circuit["startup"] = self.project.startup
        return hashlib.sha256(json.dumps(circuit, sort_keys=True).encode()).hexdigest()

    def replay(self, path: Path, deadline_ns: int) -> list[Event]:
        if path.stat().st_size > 5_000_000:
            raise ValueError("Recording file is too large")
        document = json.loads(path.read_text())
        if not isinstance(document, dict) or document.get("schema") != 1 or document.get("engine") != "0.1.0" or document.get("startup") != self._startup_identity():
            raise ValueError("Unsupported recording format")
        if document.get("firmware_sha256") != hashlib.sha256(self.image).hexdigest():
            raise ValueError("Recording belongs to a different firmware image")
        if document.get("circuit_sha256") != self._circuit_digest():
            raise ValueError("Recording belongs to a different circuit")
        inputs = document.get("inputs")
        if not isinstance(inputs, list):
            raise ValueError("Invalid recorded inputs")
        previous = 0
        for event in inputs:
            if not isinstance(event, dict):
                raise ValueError("Invalid recorded input")
            when = event.get("time_ns")
            if not isinstance(when, int) or when < previous or when > deadline_ns:
                raise ValueError("Recorded timestamps must be ordered and within the replay deadline")
            kind = event.get("kind")
            if kind == "button":
                if not isinstance(event.get("pressed"), bool) or not any(
                    c.id == event.get("component") and c.kind == "button" for c in self.project.components):
                    raise ValueError("Invalid recorded button input")
            elif kind == "uart":
                if event.get("port") not in (0, 1) or not isinstance(event.get("hex"), str):
                    raise ValueError("Invalid recorded UART input")
                try:
                    bytes.fromhex(event["hex"])
                except ValueError:
                    raise ValueError("Invalid recorded UART bytes") from None
            elif kind == "adc":
                channel, value = event.get("channel"), event.get("value")
                if not isinstance(channel, int) or not 0 <= channel <= 4 or not isinstance(value, int) or not 0 <= value <= 4095:
                    raise ValueError("Invalid recorded ADC input")
            else:
                raise ValueError("Unknown recorded input kind")
            previous = when
        self.load(self.image)  # Replay starts with the recorded original flash, including after flash programming.
        events = []
        for event in inputs:
            while self.engine.snapshot().time_ns < event["time_ns"]:
                stop = self.run(event["time_ns"])
                events.extend(self.engine.events())
                if int(stop) > 1:
                    raise ValueError(f"Replay interrupted: {stop.name}")
            if self.engine.snapshot().time_ns != event["time_ns"]:
                raise ValueError("Input timestamp is not an instruction boundary for this engine")
            if event["kind"] == "button":
                self.button(event["component"], event["pressed"], record=False)
            elif event["kind"] == "uart":
                self.engine.uart(bytes.fromhex(event["hex"]), event["port"])
            elif event["kind"] == "adc":
                self.engine.adc(event["channel"], event["value"])
            else:
                raise ValueError("Unknown recorded input kind")
        while self.engine.snapshot().time_ns < deadline_ns:
            stop = self.run(deadline_ns)
            events.extend(self.engine.events())
            if int(stop) > 1:
                raise ValueError(f"Replay interrupted: {stop.name}")
        if self.engine.snapshot().dropped_events:
            raise ValueError("Trace overflow during replay")
        return events
