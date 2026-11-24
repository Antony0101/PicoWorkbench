"""Independent, bounded runtime oracles for the pinned official examples."""
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
import hashlib
import json
import tempfile

from elftools.elf.elffile import ELFFile
from .controller import Simulation
from .engine import Drive, Stop
from .project import Component, Project


def example_project(entry, startup):
    project = Project(name='Official ' + entry['target'], startup=startup)
    if entry['target'] == 'i2c_bus_scan':
        project.components = [Component('sensor', 'i2c_sensor', address=0x48)]
        project.wires = [('pico:GP4', 'sensor:sda'), ('pico:GP5', 'sensor:scl')]
    return project


def symbol_values(image):
    elf = ELFFile(BytesIO(image))
    return {s.name: (s['st_value'], s['st_size']) for section in elf.iter_sections()
            if section['sh_type'] == 'SHT_SYMTAB' for s in section.iter_symbols() if s.name}


def validate_example(entry, firmware, startup, budgets=(257, 4096)):
    """Run/reset with different chunk sizes. Assertions come from audited source."""
    observations = []
    target = entry['target']
    simulation = Simulation(example_project(entry, startup))
    image = Path(firmware).read_bytes()
    exit_address, exit_size = symbol_values(image).get('_exit', (0, 0))
    exit_address &= ~1

    def at_sdk_exit():
        # BKPT advances PC before reporting the stop. Do not resume arbitrary
        # user breakpoints, assertions or hardware faults in these checks.
        pc = simulation.engine.snapshot().registers[15] & ~1
        return exit_size and exit_address < pc <= exit_address + exit_size

    try:
        simulation.load(Path(firmware), startup=startup)
        for index, budget in enumerate(budgets):
            if index:
                simulation.reset()
            if target == 'hello_gpio_irq':
                simulation.gpio_input(2, Drive.LOW)
            if target == 'hello_adc':
                simulation.adc(0, 2048)
            deadline = entry['duration_ms'] * 1_000_000
            events = []
            exit_stops = []
            pending = [(60_000_000, False), (90_000_000, True)] if target == 'hello_gpio_irq' else []
            for _ in range(300_000):
                until = pending[0][0] if pending else deadline
                reason = simulation.run(until, budget)
                events.extend(simulation.engine.events())
                if reason == Stop.DEADLINE and pending:
                    when, pressed = pending.pop(0)
                    simulation.gpio_input(2, Drive.LOW if pressed else Drive.HIGH)
                    continue
                if reason == Stop.BREAKPOINT:
                    assert at_sdk_exit(), 'Unexpected breakpoint outside the SDK _exit loop'
                    exit_stops.append(simulation.engine.snapshot().time_ns)
                    # Returning from core0's main pauses the debugger before
                    # core1 finishes printing. Explicitly resume this audited
                    # exit loop until both FIFO participants report success.
                    if target == 'hello_multicore' and 'well on core 1!' not in bytes(
                            e.value for e in events if e.kind == 2).decode('utf-8', 'replace'):
                        continue
                    if target == 'hello_pwm' and exit_stops[-1] < exit_stops[0] + 32_000:
                        continue  # Observe eight complete PWM periods after setup.
                if reason != Stop.BUDGET:
                    break
            else:
                raise AssertionError('Workload exceeded bounded instruction slices')
            state = simulation.engine.snapshot()
            assert state.dropped_events == 0, 'Native event capture overflowed'
            assert reason in (Stop.DEADLINE, Stop.BREAKPOINT), f'Unexpected stop {reason}'
            uart = bytes(e.value for e in events if e.kind == 2 and e.source == 0).decode('utf-8', 'replace')
            pins = [(e.time_ns, e.source, e.value) for e in events if e.kind == 1]
            if target == 'blink':
                edges = [value for _, pin, value in pins if pin == 25]
                assert edges[:5] == [1, 0, 1, 0, 1], edges
                times = [when for when, pin, _ in pins if pin == 25]
                assert all(250_000_000 <= b - a <= 251_000_000 for a, b in zip(times, times[1:]))
            elif target == 'hello_serial':
                assert uart.count('Hello, world!\r\n') >= 2, uart
            elif target == 'hello_gpio_irq':
                assert uart.count('GPIO 2 EDGE_RISE') == 1 and uart.count('GPIO 2 EDGE_FALL') == 1, uart
            elif target == 'hello_adc':
                assert 'Raw value: 0x800, voltage: 1.650000 V' in uart, uart
            elif target == 'hello_pwm':
                assert int.from_bytes(simulation.engine.peek(0x4005000c), 'little') == 0x00030001, 'PWM CC differs from the upstream 1/3 levels'
                assert int.from_bytes(simulation.engine.peek(0x40050010), 'little') == 3, 'PWM TOP differs from the upstream wrap value'
                for pin, high_ns in ((0, 1000), (1, 3000)):
                    edges = [(when, value) for when, p, value in pins if p == pin and when >= exit_stops[0]]
                    assert len(edges) >= 12
                    assert all(b - a == (high_ns if value == 1 else 4000 - high_ns)
                        for (a, value), (b, _) in zip(edges, edges[1:]))
            elif target == 'i2c_bus_scan':
                bus = simulation.bus_capture.records
                assert len(bus) == 112 and uart.count('@') == 1 and 'Done.' in uart, (len(bus), uart)
                assert [r.target for r in bus if r.acknowledged] == [0x48]
                assert any(not r.acknowledged for r in bus)
            elif target == 'hello_pio':
                assert 'Using gpio 25' in uart, uart
                assert [value for _, pin, value in pins if pin == 25][:3] == [1, 0, 1]
                times = [when for when, pin, _ in pins if pin == 25]
                assert all(500_000_000 <= b - a <= 501_000_000 for a, b in zip(times, times[1:]))
                assert any(sm.enabled for block in (0, 1) for sm in simulation.engine.pio(block).sm)
            elif target == 'hello_dma':
                assert 'Hello, world! (from DMA)' in uart, uart
                symbols = symbol_values(image)
                source, length = symbols['src']
                destination, _ = symbols['dst']
                assert simulation.engine.peek(source, length) == simulation.engine.peek(destination, length)
                assert not simulation.engine.dma().channels[0].busy
            elif target == 'hello_multicore':
                assert 'all gone well on core 0!' in uart and 'all gone well on core 1!' in uart, uart
                cores = simulation.engine.cores()
                assert all(core.instructions > 0 for core in cores.core)
            exchange_data = [asdict(r) for r in simulation.bus_capture.records]
            observations.append({'uart': uart, 'gpio_events': len(pins),
                'gpio_sha256': hashlib.sha256(json.dumps(pins).encode()).hexdigest(),
                'bus_sha256': hashlib.sha256(json.dumps(exchange_data, sort_keys=True).encode()).hexdigest(),
                'bus_exchanges': len(exchange_data), 'instructions': state.instructions,
                'time_ns': state.time_ns, 'stop': reason.name,
                'sdk_exit_stops': len(exit_stops), 'first_exit_ns': exit_stops[0] if exit_stops else None})
        assert all(item == observations[0] for item in observations), 'Reset/budget replay changed observations'
        with tempfile.TemporaryDirectory(prefix='picoworkbench-sdk-replay-') as directory:
            path = Path(directory) / 'inputs.json'
            simulation.export_recording(path)
            # A returning SDK main stops before the requested deadline. Replay
            # to the observed time, preserving strict replay's breakpoint policy.
            # Multicore additionally repeats the explicit _exit resumes above.
            first = observations[0]
            end = first['time_ns']
            replayed = simulation.replay(path, first['first_exit_ns'] or end, budget=min(budgets))
            for _ in range(300_000):
                if simulation.engine.snapshot().time_ns >= end:
                    break
                reason = simulation.run(end, min(budgets))
                replayed.extend(simulation.engine.events())
                assert reason in (Stop.BUDGET, Stop.DEADLINE) or (reason == Stop.BREAKPOINT and at_sdk_exit())
            else:
                raise AssertionError('SDK exit replay exceeded bounded instruction slices')
            assert simulation.engine.snapshot().instructions == first['instructions']
            pins = [(e.time_ns, e.source, e.value) for e in replayed if e.kind == 1]
            uart = bytes(e.value for e in replayed if e.kind == 2 and e.source == 0).decode('utf-8', 'replace')
            assert uart == observations[0]['uart']
            assert hashlib.sha256(json.dumps(pins).encode()).hexdigest() == observations[0]['gpio_sha256']
            exchanges = [asdict(r) for r in simulation.bus_capture.records]
            assert hashlib.sha256(json.dumps(exchanges, sort_keys=True).encode()).hexdigest() == observations[0]['bus_sha256']
        return {'passed': True, 'target': target, 'startup': startup,
                'firmware_sha256': hashlib.sha256(image).hexdigest(), 'runs': observations, 'recorded_replay': True}
    finally:
        simulation.close()
