from rp2040_sim import demo
from rp2040_sim.controller import Simulation
from rp2040_sim.project import Project
from rp2040_sim.engine import Stop


def test_integrated_workbench_save_load_adc_button_buses_and_replay(tmp_path):
    path = tmp_path / "workbench.json"
    Project.workbench().save(path)
    project = Project.load(path)
    assert project.builtin == "workbench"
    sim = Simulation(project)
    try:
        sim.load(demo.image(project.builtin))
        events = []

        def run(deadline):
            while sim.engine.snapshot().time_ns < deadline:
                assert sim.run(deadline) in (Stop.BUDGET, Stop.DEADLINE)
                events.extend(sim.engine.events())

        run(300_000_000)
        sim.adc(0, 2321)
        sim.button("button1", True)
        run(450_000_000)
        sim.button("button1", False)
        run(800_000_000)
        serial = bytes(e.value for e in events if e.kind == 2)
        assert b"id=0042 temp=0019 adc=0911 spi=" in serial
        assert any(e.kind == 4 for e in events)
        assert any(e.kind == 5 for e in events)
        assert sum(e.kind == 1 and e.source == 0 for e in events) > 1000
        recording = tmp_path / "recording.json"
        sim.export_recording(recording)
        assert sim.replay(recording, 800_000_000) == events
    finally:
        sim.close()
