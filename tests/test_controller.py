import json

import pytest

from rp2040_sim import demo
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Stop
from rp2040_sim.project import Project


def run(sim, deadline):
    result = []
    while sim.engine.snapshot().time_ns < deadline:
        assert sim.engine.run(deadline) in (Stop.BUDGET, Stop.DEADLINE)
        result.extend(sim.engine.events())
    return result


def test_button_changes_real_firmware_output_and_replays(tmp_path):
    sim = Simulation()
    try:
        sim.load(demo.image())
        trace = run(sim, 300_000_000)
        sim.button("button1", True)
        trace += run(sim, 450_000_000)
        sim.button("button1", False)
        trace += run(sim, 800_000_000)
        serial = bytes(e.value for e in trace if e.kind == 2)
        assert b"Button pressed\r\n" in serial
        assert b"Button released\r\n" in serial
        leds = [e.time_ns for e in trace if e.kind == 1 and e.source == 25]
        assert any(50_000_000 <= b - a <= 50_050_000 for a, b in zip(leds, leds[1:]))
        recording = tmp_path / "input.json"
        sim.export_recording(recording)
        final = sim.engine.snapshot()
        assert sim.replay(recording, 800_000_000) == trace
        assert sim.engine.snapshot() == final
    finally:
        sim.close()


def test_recording_rejects_different_firmware_and_circuit(tmp_path):
    sim = Simulation()
    try:
        sim.load(demo.image())
        path = tmp_path / "recording.json"
        sim.export_recording(path)
        document = json.loads(path.read_text())
        document["firmware_sha256"] = "wrong"
        path.write_text(json.dumps(document))
        with pytest.raises(ValueError, match="firmware"):
            sim.replay(path, 1000)
        sim.export_recording(path)
        sim.project.wires.clear()
        with pytest.raises(ValueError, match="circuit"):
            sim.replay(path, 1000)
    finally:
        sim.close()


def test_button_invalid_id():
    sim = Simulation()
    try:
        with pytest.raises(ValueError, match="Unknown button"):
            sim.button("not-found", True)
    finally:
        sim.close()


@pytest.mark.parametrize("input", [123, {"time_ns": 0, "kind": "unknown"},
    {"time_ns": 0, "kind": "adc", "channel": 8, "value": 0},
    {"time_ns": 0, "kind": "uart", "port": 0, "hex": "nothex"}])
def test_malformed_recording_does_not_reset_machine(tmp_path, input):
    sim = Simulation()
    try:
        sim.load(demo.image())
        sim.run(budget=100)
        before = sim.engine.snapshot()
        path = tmp_path / "inputs.json"
        sim.export_recording(path)
        document = json.loads(path.read_text())
        document["inputs"] = [input]
        path.write_text(json.dumps(document))
        with pytest.raises(ValueError):
            sim.replay(path, 1000000)
        assert sim.engine.snapshot() == before
    finally:
        sim.close()
