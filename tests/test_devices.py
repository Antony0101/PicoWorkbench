from dataclasses import replace
import struct

import pytest

from rp2040_sim.build import ROOT, build, compiler
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Engine, Stop, SimulationError
from rp2040_sim.project import Component, Project


def bus_project():
    return Project(components=[Component("sensor", "i2c_sensor"), Component("loopback", "spi_loopback")],
                   wires=[("pico:GP4", "sensor:sda"), ("pico:GP5", "sensor:scl"),
                          ("pico:GP19", "loopback:mosi"), ("pico:GP16", "loopback:miso"),
                          ("pico:GP18", "loopback:sck"), ("pico:GP17", "loopback:cs")])


@pytest.fixture
def firmware(tmp_path):
    try:
        compiler()
    except RuntimeError:
        pytest.skip("ARM cross compiler not installed")
    output = tmp_path / "buses.elf"
    build(ROOT / "firmware/fixtures/buses.c", output, ROOT / "firmware/fixtures/direct.ld")
    return output


def test_python_sensor_and_spi_loopback_answer_before_cpu_continues(firmware):
    simulation = Simulation(bus_project())
    try:
        simulation.load(firmware)
        assert simulation.run(20_000_000, budget=10000) == Stop.BREAKPOINT
        assert struct.unpack("<6I", simulation.engine.peek(0x20000000, 24)) == (0x42, 25, 0x9c, 1, 0xa5, 0x5a)
        assert simulation.devices["loopback"].last == 0x5a
        assert simulation.devices["sensor"].registers[2] == 0x9c
        first = simulation.engine.events()
        simulation.reset()
        simulation.run(20_000_000, budget=10000)
        assert simulation.engine.events() == first
    finally:
        simulation.close()


def test_pending_request_blocks_cpu_and_wrong_token_is_rejected(firmware):
    with Engine() as engine:
        engine.load(firmware)
        assert engine.run(budget=10000) == Stop.DEVICE_REQUEST
        before = engine.snapshot()
        assert engine.run(budget=10000) == Stop.DEVICE_REQUEST
        assert engine.snapshot() == before
        request = engine.device_request()
        assert request.bus == 0 and request.target == 0x48
        with pytest.raises(SimulationError):
            engine.complete_device(replace(request, id=request.id + 1), 0, True)
        engine.complete_device(request, 0, True)
        assert engine.run(budget=10000) == Stop.DEVICE_REQUEST
        assert engine.device_request().flags == 7


def test_bus_pins_and_cs_are_required(firmware):
    project = bus_project()
    project.wires = [w for w in project.wires if "loopback:cs" not in w]
    simulation = Simulation(project)
    try:
        simulation.load(firmware)
        with pytest.raises(SimulationError, match="no selected"):
            simulation.run(20_000_000, budget=10000)
    finally:
        simulation.close()
