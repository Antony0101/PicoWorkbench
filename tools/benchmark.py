"""Measure the documented Linux workloads and optional offscreen GUI pause latency."""
from pathlib import Path
import argparse
import json
import os
import platform
import resource
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from rp2040_sim import demo
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Engine, Stop
from rp2040_sim.project import Project


def busy_image():
    program = struct.pack('<IIHH', 0x20042000, 0x10000009, 0x3001, 0xe7fd)
    header = struct.pack('<16sHHIIIIIHHHHHH', b'\x7fELF\x01\x01\x01' + bytes(9),
                         2, 40, 1, 0x10000009, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    ph = struct.pack('<8I', 1, 256, 0x10000000, 0x10000000, 12, 12, 5, 4)
    return header + ph + bytes(256 - len(header) - len(ph)) + program


def headless():
    rows = []
    with Engine(133_000_000) as engine:
        engine.load(busy_image())
        start = time.perf_counter()
        engine.run(1_000_000_000, 1_000_000)
        elapsed = time.perf_counter() - start
        rows.append({'workload': 'busy Thumb loop', 'instructions': engine.snapshot().instructions,
                     'wall_seconds': elapsed, 'instructions_per_second': 1_000_000 / elapsed})
    for name in ('workbench', 'micropython'):
        sim = Simulation(getattr(Project, name)())
        try:
            sim.load(demo.image(name))
            start = time.perf_counter()
            events = 0
            while sim.engine.snapshot().time_ns < 1_000_000_000:
                assert sim.run(1_000_000_000) in (Stop.BUDGET, Stop.DEADLINE)
                events += len(sim.engine.events())
            state = sim.engine.snapshot()
            elapsed = time.perf_counter() - start
            rows.append({'workload': name, 'instructions': state.instructions,
                         'wall_seconds': elapsed, 'events': events, 'dropped_events': state.dropped_events})
        finally:
            sim.close()
    return rows


def gui_pause():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QSettings
    from tempfile import TemporaryDirectory
    from PySide6.QtWidgets import QApplication
    from rp2040_sim.gui.app import Window
    app = QApplication.instance() or QApplication([])
    preferences = TemporaryDirectory(prefix='rp2040-benchmark-')
    window = Window(ROOT / 'examples/micropython.json',
                    settings=QSettings(str(Path(preferences.name) / 'settings.ini'), QSettings.Format.IniFormat))

    def wait(condition, timeout=15):
        end = time.monotonic() + timeout
        while not condition():
            app.processEvents()
            QTest.qWait(1)
            if time.monotonic() >= end:
                raise RuntimeError(window.logs.toPlainText() or 'GUI benchmark timed out')

    try:
        window.show()
        wait(lambda: window.state is not None and not window.busy and window.project.startup == 'rom')
        window.editor.setPlainText('from machine import Pin\np=Pin(25, Pin.OUT)\nwhile True:\n    p.toggle()\n')
        window.build_source()
        wait(lambda: getattr(window, 'python_status', '') == 'execute')
        samples = []
        for _ in range(5):
            window.send('run')
            wait(lambda: window.running)
            QTest.qWait(200)
            start = time.perf_counter()
            window.send('pause')
            wait(lambda: not window.running)
            samples.append((time.perf_counter() - start) * 1000)
        return {'workload': 'MicroPython Pin.toggle loop, trace and inspectors enabled',
                'pause_ms': samples, 'max_pause_ms': max(samples),
                'target_100ms_passed': max(samples) < 100,
                'dropped_events': window.state.dropped_events}
    finally:
        window._closing = True  # Discard this benchmark's synthetic script and flash state.
        window.close()
        app.processEvents()
        preferences.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = {'platform': platform.platform(), 'python': platform.python_version(),
              'machine': platform.machine(), 'processor': platform.processor(),
              'headless': headless()}
    if args.gui:
        result['gui'] = gui_pause()
    result['peak_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n')
    print(rendered)


if __name__ == '__main__':
    main()
