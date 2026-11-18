"""Execution pacing checked against virtual state, without wall-time sleeps."""
import pytest

pytest.importorskip("PySide6")

from rp2040_sim import demo
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Stop
from rp2040_sim.gui.worker import Worker
from rp2040_sim.project import Project
from conftest import make_elf


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, ns=16_000_000):
        self.now += ns


@pytest.fixture
def worker():
    clock = Clock()
    worker = Worker(wall_clock=clock)
    worker.sim = Simulation()
    worker.sim.load(make_elf(b"\xfe\xe7"))  # one-instruction endless branch
    yield worker, clock
    worker.command({"name": "shutdown"})


def test_playback_changes_wall_pace_without_changing_chip_clock(worker):
    worker, clock = worker
    worker.command({"name": "speed", "value": 0.1})
    worker.command({"name": "run"})
    clock.advance()
    worker.tick()
    state = worker.sim.engine.snapshot()
    assert state.time_ns == 1_600_000
    assert state.instructions == 1_600
    assert state.clock_hz == 1_000_000
    worker.command({"name": "speed", "value": 1})
    assert worker.sim.engine.snapshot() == state
    clock.advance()
    worker.tick()
    assert worker.sim.engine.snapshot().time_ns == 17_600_000


def test_slow_playback_accumulates_sub_instruction_intervals(worker):
    worker, clock = worker
    worker.command({"name": "clock", "hz": 1_000})
    worker.command({"name": "speed", "value": 0.01})
    worker.command({"name": "run"})
    for _ in range(20):
        clock.advance()
        worker.tick()
    state = worker.sim.engine.snapshot()
    assert 3_200_000 <= state.time_ns < 4_200_000
    assert state.instructions == 4


def test_pause_and_host_stalls_do_not_create_unbounded_catchup(worker):
    worker, clock = worker
    worker.command({"name": "run"})
    clock.advance()
    worker.tick()
    worker.command({"name": "pause"})
    state = worker.sim.engine.snapshot()
    clock.advance(10_000_000_000)
    worker.tick()
    assert worker.sim.engine.snapshot() == state
    worker.command({"name": "run"})
    clock.advance()
    worker.tick()
    assert worker.sim.engine.snapshot().time_ns == state.time_ns + 16_000_000
    clock.advance(10_000_000_000)
    worker.tick()
    # A single tick and its outstanding target remain bounded after a stall.
    assert worker.sim.engine.snapshot().instructions == state.instructions + 32_000
    assert worker._target_ns <= state.time_ns + 66_000_000


def test_run_n_spans_batches_and_stops_exactly(worker):
    worker, clock = worker
    worker.command({"name": "speed", "value": 4})
    worker.command({"name": "run_instructions", "count": 200_003})
    for _ in range(4):
        clock.advance()
        worker.tick()
    assert worker.sim.engine.snapshot().instructions == 200_003
    assert not worker.running
    assert worker.remaining_instructions is None
    clock.advance()
    worker.tick()
    assert worker.sim.engine.snapshot().instructions == 200_003


def test_run_n_respects_breakpoints_and_pause_cancels_the_limit(worker):
    worker, clock = worker
    worker.sim.load(make_elf(b"\x00\xbf\x00\xbf\xfe\xe7"))
    worker.sim.engine.breakpoint(0x1000000c)
    worker.command({"name": "run_instructions", "count": 100})
    clock.advance()
    worker.tick()
    state = worker.sim.engine.snapshot()
    assert state.instructions == 2 and state.stop == Stop.BREAKPOINT
    assert not worker.running and worker.remaining_instructions is None
    worker.command({"name": "run_instructions", "count": 100})
    worker.command({"name": "pause"})
    assert worker.remaining_instructions is None
    worker.sim.engine.breakpoint(0x1000000c, False)
    worker.command({"name": "run"})
    clock.advance()
    worker.tick()
    assert worker.running
    assert worker.sim.engine.snapshot().instructions > 100


def test_clock_change_resets_exact_loaded_image_and_recording(worker):
    worker, clock = worker
    image = worker.sim.image
    worker.command({"name": "run"})
    clock.advance()
    worker.tick()
    worker.sim.button("button1", True)
    worker.command({"name": "clock", "hz": 100_000})
    assert not worker.running
    assert worker.sim.image == image
    assert worker.sim.engine.snapshot().instructions == 0
    assert worker.sim.engine.snapshot().clock_hz == worker.sim.project.clock_hz == 100_000
    assert not worker.sim.recording
    worker.command({"name": "run"})
    clock.advance()
    worker.tick()
    assert worker.sim.engine.snapshot().instructions == 1_600


@pytest.mark.parametrize("command", [
    {"name": "speed", "value": float("nan")},
    {"name": "speed", "value": 0},
    {"name": "run_instructions", "count": True},
    {"name": "run_instructions", "count": 1_000_001},
    {"name": "clock", "hz": 999},
])
def test_invalid_controls_preserve_engine(worker, command):
    worker, _ = worker
    failures = []
    worker.failed.connect(failures.append)
    engine = worker.sim.engine
    worker.command(command)
    assert len(failures) == 1
    assert worker.sim.engine is engine
    assert worker.speed == 1 and not worker.running


def test_slow_motion_preserves_firmware_event_timestamps(worker):
    worker, clock = worker
    worker.sim.load(demo.image())
    events = []
    worker.updated.connect(lambda packet: events.extend(packet["events"]))
    worker.command({"name": "speed", "value": 0.1})
    worker.command({"name": "run"})
    for _ in range(200):
        clock.advance()
        worker.tick()
    assert worker.sim.engine.snapshot().time_ns == 320_000_000
    with_sim = Simulation(Project.demo())
    try:
        with_sim.load(demo.image())
        while with_sim.engine.snapshot().time_ns < 320_000_000:
            with_sim.run(320_000_000)
        assert events == with_sim.engine.events()
    finally:
        with_sim.close()
