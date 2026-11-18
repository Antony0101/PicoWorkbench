from pathlib import Path
import hashlib
import json

import pytest

from rp2040_sim import demo
from rp2040_sim.cli import main
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Drive, Stop
from rp2040_sim.images import bootrom
from rp2040_sim.project import Project
from rp2040_sim.repl import PythonTransfer


@pytest.fixture
def simulation():
    try:
        bootrom()
    except RuntimeError:
        pytest.skip('Prepare the official B2 ROM for MicroPython execution')
    sim = Simulation(Project.micropython())
    sim.load(demo.image('micropython'))
    try:
        yield sim
    finally:
        sim.close()


def execute(sim, source, timeout_ns=5_000_000_000, budget=10000):
    transfer = PythonTransfer(source)
    end = sim.engine.snapshot().time_ns + timeout_ns
    events = []
    while not transfer.done and sim.engine.snapshot().time_ns < end:
        transfer.advance(sim)
        deadline = min(end, sim.engine.snapshot().time_ns + 2_000_000)
        assert sim.run(deadline, budget) in (Stop.BUDGET, Stop.DEADLINE)
        batch = sim.engine.events()
        transfer.consume(sim, batch)
        events.extend(batch)
    assert transfer.done, (transfer.phase, bytes(transfer.received), sim.engine.snapshot())
    return transfer, events


def text(events):
    return bytes(e.value for e in events if e.kind == 2 and e.source == 0).decode('utf-8', 'replace')


def test_interpreter_arithmetic_rom_float_and_raw_repl(simulation):
    transfer, events = execute(simulation, 'import math\nprint("answer", 6*7, round(math.sin(0.5), 6))\n')
    assert not transfer.stderr
    assert 'MicroPython v1.26.1' in text(events)
    assert 'answer 42 0.479426' in text(events)
    assert text(events).endswith('>>> ')


def test_python_gpio_sleep_and_adc(simulation):
    simulation.adc(0, 2048)
    transfer, events = execute(simulation, '''
from machine import Pin, ADC
import time
led = Pin(25, Pin.OUT)
led.value(1)
time.sleep_ms(100)
led.value(0)
print("ADC", ADC(26).read_u16())
''')
    assert not transfer.stderr
    assert 'ADC 32776' in text(events)
    led = [e for e in events if e.kind == 1 and e.source == 25 and e.value in (0, 1)]
    assert [e.value for e in led][-2:] == [1, 0]
    assert 100_000_000 <= led[-1].time_ns - led[-2].time_ns < 101_000_000
    assert simulation.engine.snapshot().pin(25) == Drive.LOW


def test_python_rtc_calendar(simulation):
    transfer, events = execute(simulation, '''
from machine import RTC
import time
r = RTC()
r.datetime((2024, 2, 28, 2, 23, 59, 59, 0))
time.sleep_ms(1100)
print("calendar", r.datetime()[:3])
''')
    assert not transfer.stderr
    assert 'calendar (2024, 2, 29)' in text(events)


def test_python_files_persist_through_reset_and_export(simulation, tmp_path):
    transfer, events = execute(simulation, '''
with open("hello.txt", "w") as f:
    f.write("saved in serial flash")
print("saved")
''')
    assert not transfer.stderr
    before = simulation.engine.flash_image()
    simulation.reset()
    transfer, events = execute(simulation, 'print("loaded", open("hello.txt").read())\n')
    assert not transfer.stderr
    assert 'loaded saved in serial flash' in text(events)
    assert simulation.engine.flash_image() == before
    path = tmp_path / 'saved.bin'; path.write_bytes(before)
    simulation.load(path)
    transfer, events = execute(simulation, 'print(open("hello.txt").read())\n')
    assert not transfer.stderr and 'saved in serial flash' in text(events)


def test_python_traceback_finishes_upload_and_returns_prompt(simulation):
    transfer, events = execute(simulation, 'raise ValueError("expected failure")\n')
    assert b'ValueError: expected failure' in transfer.stderr
    assert text(events).endswith('>>> ')


def test_large_upload_and_recorded_replay(simulation, tmp_path):
    source = '# ' + 'padding ' * 70 + '\nfrom machine import Pin\nPin(25, Pin.OUT).value(1)\nprint("upload finished")\n'
    transfer, events = execute(simulation, source)
    assert not transfer.stderr and 'upload finished' in text(events)
    assert len(simulation.recording) > 512
    end = simulation.engine.snapshot().time_ns
    path = tmp_path / 'inputs.json'
    simulation.export_recording(path)
    assert simulation.replay(path, end) == events


@pytest.mark.parametrize('source', ['', 'x' * 32769, '\x00', '\x04'])
def test_invalid_script_size_and_control_characters(source):
    with pytest.raises(ValueError):
        PythonTransfer(source)


def test_micropython_asset_provenance():
    from importlib.resources import files
    manifest = json.loads(files('rp2040_sim').joinpath('assets/micropython-provenance.json').read_text())
    assert manifest['version'] == '1.26.1'
    assert hashlib.sha256(demo.image('micropython')).hexdigest() == manifest['uf2_sha256']


def test_cli_python_script_and_saved_flash(simulation, tmp_path, capsys):
    script = tmp_path / 'script.py'; script.write_text('print("CLI Python", 6*7)\n')
    project = tmp_path / 'mp.json'; Project.micropython().save(project)
    output = tmp_path / 'flash.bin'
    assert main(['run', '--project', str(project), '--script', str(script), '--duration-ms', '1000', '--save-flash', str(output)]) == 0
    assert 'CLI Python 42' in capsys.readouterr().out
    assert output.stat().st_size == 2 * 1024 * 1024
