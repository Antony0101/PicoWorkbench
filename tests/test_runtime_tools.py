"""Phase 3 models and real firmware acceptance checks."""
from importlib.resources import files

import pytest

from rp2040_sim import demo
from rp2040_sim.controller import Simulation
from rp2040_sim.engine import Stop
from rp2040_sim.images import bootrom
from rp2040_sim.oled import SSD1306
from rp2040_sim.project import Component, Project
from rp2040_sim.repl import PythonTransfer
from rp2040_sim.capture import BusCapture, BusExchange, SerialPlot
from rp2040_sim.engine import Event
from rp2040_sim.device_files import FileJob


def oled_project(python=False):
    project = Project.micropython() if python else Project()
    project.components = [Component('oled', 'ssd1306')]
    project.wires = [('pico:GP4', 'oled:sda'), ('pico:GP5', 'oled:scl')]
    return project


def commands(model, *values):
    for value in values:
        model.exchange(0x80, 0)
        model.exchange(value, 2)


def test_oled_commands_cross_stop_boundaries_and_address_modes():
    m = SSD1306()
    commands(m, 0xaf, 0xa1, 0xc8, 0x81, 255, 0x20, 0, 0x21, 1, 2, 0x22, 0, 1)
    m.exchange(0x40, 0)
    for b in (1, 2, 4, 8):
        m.exchange(b, 0)
    m.exchange(16, 2)
    assert (m.ram[1], m.ram[2], m.ram[129], m.ram[130]) == (16, 2, 4, 8)
    assert m.snapshot().pixels[4*128+1] == 255
    commands(m, 0xae)
    assert not any(m.snapshot().pixels)
    commands(m, 0xaf, 0xa7)
    assert m.snapshot().pixels[0] == 255
    commands(m, 0xa6, 0x20, 1, 0x21, 0, 1, 0x22, 0, 1)
    m.exchange(0x40, 0)
    m.exchange(0xaa, 0); m.exchange(0x55, 2)
    assert m.ram[0] == 0xaa and m.ram[128] == 0x55


def execute(sim, source, ready=False, timeout=12_000_000_000, plot=None):
    transfer = PythonTransfer(source, ready=ready)
    end = sim.engine.snapshot().time_ns + timeout
    events = []
    while not transfer.done and sim.engine.snapshot().time_ns < end:
        transfer.advance(sim)
        assert sim.run(min(end, sim.engine.snapshot().time_ns + 2_000_000), 20000) in (Stop.DEADLINE, Stop.BUDGET)
        batch = sim.engine.events()
        transfer.consume(sim, batch)
        if plot is not None:
            plot.feed(transfer.stdout_events)
        events.extend(batch)
    assert transfer.done, (transfer.phase, bytes(transfer.received))
    assert not transfer.stderr, transfer.stderr
    return transfer, events


@pytest.fixture
def python_sim():
    try:
        bootrom()
    except RuntimeError:
        pytest.skip('Prepare the official B2 ROM')
    sim = Simulation(oled_project(python=True))
    sim.load(demo.image('micropython'))
    try:
        yield sim
    finally:
        sim.close()


def test_real_micropython_upstream_oled_and_nack(python_sim):
    driver = files('rp2040_sim').joinpath('assets/ssd1306.py').read_text()
    source = driver + '''
from machine import Pin, I2C
i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=100000)
display = SSD1306_I2C(128, 64, i2c)
display.fill(0)
display.pixel(3, 5, 1)
display.show()
try:
    i2c.writeto(0x3d, b'hello')
except OSError:
    print('expected NACK')
print('oled complete')
'''
    _, events = execute(python_sim, source)
    output = bytes(e.value for e in events if e.kind == 2)
    assert b'expected NACK' in output and b'oled complete' in output
    model = python_sim.devices['oled']
    assert model.ram[3] == 32 and sum(model.ram) == 32
    assert model.snapshot().pixels[5 * 128 + 3] == 255


def uart_events(data, start=0, port=0):
    return [Event(start+i, i, 2, port, v) for i, v in enumerate(data)]


def test_plot_parser_fragmentation_bounds_unicode_and_export():
    plot = SerialPlot(limit=2)
    plot.feed(uart_events(b'log\n25,'))
    plot.feed(uart_events(b'3.3\n1e2,-.25\n3,4\n', start=100))
    assert [p.value for p in plot.channels['ch1']] == [100, 3]
    assert plot.channels['ch2'][-1].time_ns == 116
    assert plot.dropped_samples == 2
    plot.feed(uart_events('αβ\nNaN\n1,inf\n'.encode()))
    assert len(plot.channels) == 2
    plot.clear()
    plot.feed(uart_events(b'temperature:25,voltage:3.3\n'))
    assert plot.channels['temperature'][0].value == 25
    plot.feed(uart_events(b'x'*4097 + b'1,2\n'))
    assert plot.gaps == 1 and len(plot.channels) == 2
    assert 'time_ns,channel,value' in plot.csv()
    plot.gap(); plot.feed(uart_events(b'temperature:99\ntemperature:26\n'))
    assert [p.value for p in plot.channels['temperature']] == [25, 26]


def test_raw_repl_first_plot_sample_and_stderr(python_sim):
    plot = SerialPlot()
    transfer, _ = execute(python_sim, 'print("temperature:25,voltage:3.3")\nprint("26,3.4")\n', plot=plot)
    assert bytes(transfer.stdout) == b'temperature:25,voltage:3.3\r\n26,3.4\r\n'
    assert plot.channels['temperature'][0].value == 25 and plot.channels['ch1'][0].value == 26


def run_c_dashboard(sim, duration=120_000_000):
    events = []
    end = sim.engine.snapshot().time_ns + duration
    while sim.engine.snapshot().time_ns < end:
        assert sim.run(end, 20000) in (Stop.BUDGET, Stop.DEADLINE)
        events.extend(sim.engine.events())
    return events


def test_real_sdk_dashboard_oled_telemetry_and_bus():
    with_sim = Simulation(Project.dashboard())
    try:
        with_sim.load(demo.image('sdk_dashboard'))
        with_sim.adc(0, 2048)
        events = run_c_dashboard(with_sim)
        model = with_sim.devices['oled']
        assert model.on and any(model.ram)
        output = bytes(e.value for e in events if e.kind == 2)
        assert b'temperature:50,voltage:1.650' in output
        records = list(with_sim.bus_capture.records)
        assert any(r.bus == 'SPI' and r.tx == r.rx == 0xa5 for r in records)
        assert any(r.target == 0x3d and r.status == 'NACK' for r in records)
        assert any(r.target == 0x48 and r.flags & 1 and r.rx == 0x42 for r in records)
    finally:
        with_sim.close()


def test_real_micropython_dashboard_matches_c(python_sim):
    python_sim.close()
    sim = Simulation(Project.dashboard(True))
    try:
        sim.load(demo.image('micropython'))
        sim.adc(0, 2048)
        # One iteration retains the actual dashboard's imports, drivers and bus setup.
        source = demo.dashboard_source().replace('while True:', 'for _ in range(1):')
        plot = SerialPlot()
        transfer, _ = execute(sim, source, plot=plot)
        assert b'temperature:50,voltage:1.650' in transfer.stdout
        c = Simulation(Project.dashboard())
        try:
            c.load(demo.image('sdk_dashboard')); c.adc(0, 2048)
            events = run_c_dashboard(c)
            assert c.devices['oled'].snapshot().pixels == sim.devices['oled'].snapshot().pixels
            c_plot = SerialPlot(); c_plot.feed(events)
            assert c_plot.channels['temperature'][0].value == plot.channels['temperature'][0].value == 50
        finally:
            c.close()
        assert any(r.bus == 'SPI' and r.rx == 0xa5 for r in sim.bus_capture.records)
    finally:
        sim.close()


def test_bus_capture_bounds_and_exports():
    capture = BusCapture(limit=2)
    for i in range(3):
        capture.append(BusExchange(i, i*100, 'I2C', 0, 0x3c, 'oled', 0x40, 0, 2, True))
    assert len(capture.records) == 2 and capture.dropped == 1
    assert '"dropped": 1' in capture.export()
    assert 'acknowledged' in capture.export('csv')
    capture.enabled = False
    capture.append(capture.records[0])
    assert capture.dropped == 1


def file_job(sim, operation, path='/', **fields):
    job = FileJob(123, operation, path, **fields)
    end = sim.engine.snapshot().time_ns + 30_000_000_000
    while not job.done and sim.engine.snapshot().time_ns < end:
        job.advance(sim)
        assert sim.run(sim.engine.snapshot().time_ns + 2_000_000, 20000) in (Stop.BUDGET, Stop.DEADLINE)
        job.consume(sim, sim.engine.events())
    assert job.done, (job.operation, job.progress, job.transfer.phase)
    assert job.result().ok, job.result().error
    return job


def test_real_files_binary_unicode_operations_and_flash(python_sim, tmp_path):
    execute(python_sim, 'print("ready")\n')
    original = bytes(range(256))*3 + b'\x00\x04\xff'
    file_job(python_sim, 'mkdir', '/lib')
    file_job(python_sim, 'upload', '/lib/μ.py', data=original)
    listing = file_job(python_sim, 'list', '/lib').value
    assert listing['entries'][0]['name'] == 'μ.py'
    assert listing['entries'][0]['size'] == len(original) and listing['free'] > 0
    assert file_job(python_sim, 'read', '/lib/μ.py').value == original
    path = tmp_path / 'binary.bin'
    file_job(python_sim, 'download', '/lib/μ.py', host=path)
    assert path.read_bytes() == original
    file_job(python_sim, 'rename', '/lib/μ.py', destination='/lib/saved.bin')
    image = python_sim.engine.flash_image()
    python_sim.load(image)
    execute(python_sim, 'print("reopened")\n')
    assert file_job(python_sim, 'read', '/lib/saved.bin').value == original
    file_job(python_sim, 'delete', '/lib/saved.bin')
    file_job(python_sim, 'delete', '/lib')


def drive_job(sim, job, hook=lambda job: None):
    for _ in range(20000):
        hook(job)
        job.advance(sim)
        assert sim.run(sim.engine.snapshot().time_ns + 2_000_000, 20000) in (Stop.BUDGET, Stop.DEADLINE)
        job.consume(sim, sim.engine.events())
        if job.done:
            return job.result()
    pytest.fail('Filesystem job exceeded bounded execution')


def test_cancel_upload_and_download_keep_originals_and_clean_staging(python_sim, tmp_path):
    sim = python_sim
    execute(sim, 'print("ready")\n')
    file_job(sim, 'upload', '/keep.bin', data=b'original')
    upload = FileJob(124, 'upload', '/keep.bin', data=b'x'*2048)
    result = drive_job(sim, upload, lambda job: job.cancel() if job.progress >= 256 else None)
    assert not result.ok and result.error == 'Cancelled'
    assert file_job(sim, 'read', '/keep.bin').value == b'original'
    assert not any(e['name'].endswith('.tmp') for e in file_job(sim, 'list').value['entries'])
    file_job(sim, 'upload', '/large.bin', data=b'y'*768)
    target = tmp_path/'keep.bin'; target.write_bytes(b'host original')
    download = FileJob(125, 'download', '/large.bin', host=target)
    result = drive_job(sim, download, lambda job: job.cancel() if job.progress>=256 else None)
    assert not result.ok and target.read_bytes() == b'host original'
    assert not list(tmp_path.glob('*.part'))


def test_file_helper_errors_and_owned_timeout_cleanup(python_sim):
    sim = python_sim
    execute(sim, 'print("ready")\n')
    missing = FileJob(120, 'read', '/missing.bin')
    assert not drive_job(sim, missing).ok
    job = FileJob(126, 'upload', '/timeout.bin', data=b'x'*1024)
    forced = False
    def timeout(job):
        nonlocal forced
        if job.progress >= 256 and not forced and job.transfer:
            forced = True
            # Wait until a helper owns raw REPL, then force its deadline.
            if job.transfer.phase not in ('boot', 'enter'):
                job.deadline_ns = -1
            else:
                forced = False
    result = drive_job(sim, job, timeout)
    assert forced and not result.ok and 'timed out' in result.error
    assert not any(e['name'].endswith('.tmp') for e in file_job(sim, 'list').value['entries'])


def test_sdk_register_aliases_and_transfer_format_validation(engine, thumb):
    i2c, spi = 0x40044000, 0x4003c000
    thumb.write(i2c, 0x165).write(i2c+0x2000, 0x100).write(i2c+0x1000, 0x20)
    thumb.write(i2c+0x88, 3).write(i2c+0xa0, 5)
    thumb.write(spi+0x1000, 0xef00).write(spi+0x1000, 0xef07)
    thumb.write(spi+0x2024, 3).write(spi+0x3024, 1)
    engine.load(thumb.elf())
    assert engine.run(budget=300) == Stop.BREAKPOINT
    read = lambda a: int.from_bytes(engine.peek(a), 'little')
    assert read(i2c) == 0x145 and read(i2c+0x88)==3 and read(i2c+0xa0)==5
    assert read(spi) == 7 and read(spi+0x24)==2
    from conftest import Thumb
    from rp2040_sim.engine import SimulationError
    engine.load(Thumb().write(spi, 8).write(spi+4, 2).write(spi+8, 0x42).elf())
    with pytest.raises(SimulationError, match='8-bit Motorola'):
        engine.run(budget=100)


def test_bus_fault_is_recorded_without_changing_error():
    from conftest import Thumb
    from rp2040_sim.engine import SimulationError
    sim = Simulation(Project())
    try:
        sim.load(Thumb().write(0x4003c000, 7).write(0x4003c004, 2).write(0x4003c008, 0xaa).elf())
        with pytest.raises(SimulationError, match='no selected'):
            sim.run(budget=200)
        record = sim.bus_capture.records[-1]
        assert record.tx==0xaa and record.status=='FAULT' and not record.acknowledged
    finally:
        sim.close()


def test_dashboard_replay_preserves_frame_plot_and_bus(tmp_path):
    sim = Simulation(Project.dashboard())
    try:
        sim.load(demo.image('sdk_dashboard')); sim.adc(0, 2048)
        first = run_c_dashboard(sim)
        pixels = sim.devices['oled'].snapshot().pixels
        records = tuple(sim.bus_capture.records)
        plot = SerialPlot(); plot.feed(first)
        path = tmp_path/'recording.json'; sim.export_recording(path)
        replayed = sim.replay(path, sim.engine.snapshot().time_ns)
        assert replayed == first and sim.devices['oled'].snapshot().pixels == pixels
        assert tuple(sim.bus_capture.records) == records
        second = SerialPlot(); second.feed(replayed)
        assert second.snapshot() == plot.snapshot()
    finally:
        sim.close()


def test_install_driver_main_and_boot_from_exported_flash(python_sim):
    sim=python_sim
    execute(sim, 'print("ready")\n')
    driver=files('rp2040_sim').joinpath('assets/ssd1306.py').read_bytes()
    main=b'from ssd1306 import SSD1306_I2C\n'+files('rp2040_sim').joinpath('assets/dashboard.py').read_bytes()
    # Use the complete dashboard circuit for autorun, including its SPI device.
    sim.close()
    sim=Simulation(Project.dashboard(True))
    try:
        sim.load(demo.image('micropython'))
        execute(sim, 'print("ready")\n')
        file_job(sim, 'mkdir', '/lib')
        file_job(sim, 'upload', '/lib/ssd1306.py', data=driver)
        file_job(sim, 'upload', '/main.py', data=main)
        image=sim.engine.flash_image()
        sim.load(image); sim.adc(0, 2048)
        output=bytearray()
        for _ in range(3000):
            assert sim.run(sim.engine.snapshot().time_ns+2_000_000, 20000) in (Stop.BUDGET, Stop.DEADLINE)
            output.extend(e.value for e in sim.engine.events() if e.kind==2)
            if b'temperature:50,voltage:1.650' in output:
                break
        else:
            pytest.fail(bytes(output).decode('utf8','replace'))
        assert sim.devices['oled'].on and any(sim.devices['oled'].ram)
        sim.uart(b'\x03\x02')  # explicit user interruption before file operations
        for _ in range(1000):
            sim.run(sim.engine.snapshot().time_ns+2_000_000, 20000)
            output.extend(e.value for e in sim.engine.events() if e.kind==2)
            if output.endswith(b'>>> '):
                break
        assert output.endswith(b'>>> ')
        assert file_job(sim, 'read', '/main.py').value==main
    finally:
        sim.close()
