"""Versioned circuit files; screen geometry never determines connectivity."""
from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import math
import re
from .demo import NAMES

SCHEMA = 1
PINS = {"led": ("signal",), "button": ("signal",), "pullup": ("signal",),
        "pulldown": ("signal",), "probe": ("signal",),
        "i2c_sensor": ("sda", "scl"), "spi_loopback": ("mosi", "miso", "sck", "cs")}
KINDS = set(PINS)
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


@dataclass
class Component:
    id: str
    kind: str
    x: float = 600
    y: float = 200
    label: str = ""
    address: int = 0x48


@dataclass
class Project:
    name: str = "Untitled circuit"
    firmware: str = ""
    source: str = ""
    linker: str = ""
    builtin: str = "blink"
    source_text: str | None = None
    clock_hz: int = 1_000_000
    startup: str = "direct"
    bootrom: str = ""
    components: list[Component] = field(default_factory=list)
    wires: list[tuple[str, str]] = field(default_factory=list)
    path: Path | None = field(default=None, repr=False)

    @classmethod
    def demo(cls):
        return cls(name="Pico blink + button", components=[
            Component("led1", "led", 620, 140, "External LED"),
            Component("button1", "button", 620, 330, "Hold to blink faster"),
            Component("probe1", "probe", 620, 490, "GPIO25 probe"),
        ], wires=[("pico:GP25", "led1:signal"),
                  ("pico:GP14", "button1:signal"), ("pico:GP25", "probe1:signal")])

    @classmethod
    def workbench(cls):
        return cls(name="RP2040 peripheral workbench", builtin="workbench", components=[
            Component("led1", "led", 620, 100, "Blink on GP25"),
            Component("button1", "button", 620, 240, "Hold to blink faster"),
            Component("sensor", "i2c_sensor", 620, 380, "ID=42, temperature=25"),
            Component("loopback", "spi_loopback", 620, 560, "Echo each SPI byte"),
            Component("pwm_led", "led", 880, 120, "GP0: PWM 1kHz, 25%"),
        ], wires=[("pico:GP25", "led1:signal"), ("pico:GP14", "button1:signal"),
                  ("pico:GP4", "sensor:sda"), ("pico:GP5", "sensor:scl"),
                  ("pico:GP19", "loopback:mosi"), ("pico:GP16", "loopback:miso"),
                  ("pico:GP18", "loopback:sck"), ("pico:GP17", "loopback:cs"),
                  ("pico:GP0", "pwm_led:signal")])

    @classmethod
    def pio_blink(cls):
        return cls(name="PIO blink · CPU asleep", builtin="pio_blink", components=[
            Component("led1", "led", 620, 140, "PIO0 SM0 · 2 Hz"),
            Component("probe1", "probe", 620, 330, "GPIO25 PIO waveform"),
        ], wires=[("pico:GP25", "led1:signal"), ("pico:GP25", "probe1:signal")])

    @classmethod
    def dma_pio(cls):
        return cls(name="DMA to PIO · CPU asleep", builtin="dma_pio", components=[
            Component("led1", "led", 620, 140, "DMA0 → PIO0 SM0"),
            Component("probe1", "probe", 620, 330, "GPIO25 · six pulses"),
        ], wires=[("pico:GP25", "led1:signal"), ("pico:GP25", "probe1:signal")])

    @classmethod
    def multicore(cls):
        return cls(name="Two cores · FIFO and shared memory", builtin="multicore", components=[
            Component("led1", "led", 620, 140, "Core 1 · GPIO25"),
            Component("probe1", "probe", 620, 330, "Core 0 sends work every 250 ms"),
        ], wires=[("pico:GP25", "led1:signal"), ("pico:GP25", "probe1:signal")])

    @classmethod
    def sdk_blink(cls):
        return cls(name="Pico SDK 2.2.0 blink", builtin="sdk_blink", startup="sdk", components=[
            Component("led1", "led", 620, 140, "SDK sleep_ms(250)"),
            Component("probe1", "probe", 620, 330, "GPIO25 SDK waveform"),
        ], wires=[("pico:GP25", "led1:signal"), ("pico:GP25", "probe1:signal")])

    @classmethod
    def sdk_uart(cls):
        return cls(name="Pico SDK 2.2.0 UART", builtin="sdk_uart", startup="sdk")

    @classmethod
    def rom_blink(cls):
        project = cls.sdk_blink()
        project.name = "B2 ROM + boot2 + SDK blink"
        project.startup = 'rom'
        return project

    @classmethod
    def rom_flash(cls):
        return cls(name="B2 ROM + SDK flash erase/program", builtin="sdk_flash", startup="rom")

    @classmethod
    def micropython(cls):
        project = cls.rom_blink()
        project.name = 'MicroPython 1.26.1 · UART REPL'
        project.builtin = 'micropython'
        project.clock_hz = 10_000_000
        project.components[0].label = 'Python GPIO25 output'
        project.components[1].label = 'GPIO25 Python waveform'
        return project

    def resolve(self, value: str) -> Path:
        p = Path(value)
        return (self.path.parent if self.path else Path.cwd()) / p if not p.is_absolute() else p

    def endpoints(self) -> set[str]:
        return {f"pico:GP{p}" for p in range(30)} | {
            f"{c.id}:{pin}" for c in self.components for pin in PINS.get(c.kind, ())}

    def validate(self):
        if not isinstance(self.name, str) or len(self.name) > 256:
            raise ValueError("Invalid project name")
        if self.startup not in ("direct", "sdk", "rom"):
            raise ValueError("Startup must be direct, sdk or rom")
        if self.builtin not in NAMES:
            raise ValueError("Unknown bundled firmware")
        if self.source_text is not None and (not isinstance(self.source_text, str) or len(self.source_text) > 500_000):
            raise ValueError("Invalid embedded source text")
        if not isinstance(self.clock_hz, int) or isinstance(self.clock_hz, bool) or not 1000 <= self.clock_hz <= 133_000_000:
            raise ValueError("Invalid virtual instruction clock")
        if len(self.components) > 128 or len(self.wires) > 512:
            raise ValueError("Project exceeds component/wire limits")
        seen = {"pico"}
        for c in self.components:
            if not isinstance(c, Component) or not isinstance(c.id, str) or not ID.fullmatch(c.id) or c.id in seen:
                raise ValueError("Duplicate or invalid component identifier")
            seen.add(c.id)
            if c.kind not in KINDS:
                raise ValueError(f"Unsupported component type: {c.kind}")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 100000 for v in (c.x, c.y)):
                raise ValueError("Invalid component position")
            if not isinstance(c.label, str) or len(c.label) > 256:
                raise ValueError("Invalid component label")
            if not isinstance(c.address, int) or not 8 <= c.address <= 119:
                raise ValueError("Device address must be between 0x08 and 0x77")
        endpoints, wires = self.endpoints(), set()
        for wire in self.wires:
            if not isinstance(wire, (tuple, list)) or len(wire) != 2 or not all(isinstance(e, str) for e in wire):
                raise ValueError("A wire must have two endpoint identifiers")
            a, b = wire
            if a not in endpoints or b not in endpoints or a == b:
                raise ValueError(f"Invalid wire endpoints: {a}, {b}")
            key = tuple(sorted((a, b)))
            if key in wires:
                raise ValueError("Duplicate wire")
            wires.add(key)
        if not all(isinstance(v, str) for v in (self.firmware, self.source, self.linker, self.bootrom)):
            raise ValueError("Firmware, source, and linker paths must be strings")

    def groups(self) -> list[set[str]]:
        self.validate()
        parent = {e: e for e in self.endpoints()}

        def find(e):
            while parent[e] != e:
                parent[e] = parent[parent[e]]
                e = parent[e]
            return e

        for a, b in self.wires:
            parent[find(a)] = find(b)
        result: dict[str, set[str]] = {}
        for e in sorted(parent):
            result.setdefault(find(e), set()).add(e)
        return list(result.values())

    def save(self, path: Path):
        self.validate()
        path = path.resolve()
        paths = {}
        import os
        for key in ("firmware", "source", "linker", "bootrom"):
            value = getattr(self, key)
            paths[key] = os.path.relpath(self.resolve(value), path.parent) if value else ""
        document = {"schema": SCHEMA, "board": "pico", "name": self.name,
                    "builtin": self.builtin,
                    "source_text": self.source_text,
                    "clock_hz": self.clock_hz, "startup": self.startup, **paths,
                    "components": [asdict(c) for c in self.components], "wires": self.wires}
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n")
        temporary.replace(path)
        self.path = path
        for key, value in paths.items():
            setattr(self, key, value)

    @classmethod
    def load(cls, path: Path):
        if path.stat().st_size > 1_000_000:
            raise ValueError("Project file is too large")
        d = json.loads(path.read_text())
        if not isinstance(d, dict) or d.get("schema") != SCHEMA or d.get("board") != "pico":
            raise ValueError("Unsupported project schema or board")
        allowed = {"schema", "board", "name", "clock_hz", "startup", "bootrom", "firmware", "source", "source_text", "linker", "builtin", "components", "wires"}
        if set(d) - allowed:
            raise ValueError("Unknown project fields")
        try:
            project = cls(name=d.get("name", "Untitled circuit"), firmware=d.get("firmware", ""),
                          source=d.get("source", ""), linker=d.get("linker", ""),
                          builtin=d.get("builtin", "blink"),
                          source_text=d.get("source_text"),
                          clock_hz=d.get("clock_hz", 1_000_000),
                          startup=d.get("startup", "direct"),
                          bootrom=d.get("bootrom", ""),
                          components=[Component(**c) for c in d.get("components", [])],
                          wires=d.get("wires", []), path=path.resolve())
            project.validate()
        except (TypeError, KeyError, AttributeError) as error:
            raise ValueError(f"Malformed project: {error}") from error
        return project
