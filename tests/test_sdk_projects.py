"""Project builds, provenance, lifecycle, and opt-in real upstream firmware gates."""
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os
import sys
import threading
import time

import pytest

from rp2040_sim.project import Project
from rp2040_sim.sdk_projects import (BuildManifest, Cancelled, ConfiguredTarget, InputWatch,
    ProjectBuilder, SdkProjectSpec, atomic_write, digest)
from rp2040_sim.sdk_tools import ToolchainProfile


def fake_profile():
    return ToolchainProfile('/sdk', '/arm/bin/arm-none-eabi-gcc', ('cmake',), '/usr/bin/make',
        '/usr/bin/gcc', '/usr/bin/g++', '2.2.0', 'test gcc', 'cmake 3.25', '/libc.a', 'test-profile')


@pytest.fixture
def builder(tmp_path):
    root = tmp_path / 'project with space Ω'
    root.mkdir()
    (root / 'CMakeLists.txt').write_text('project(example C)\n')
    return ProjectBuilder(SdkProjectSpec(str(root), 'app'), fake_profile(),
                          session='session', generation=7, cache_root=tmp_path / 'cache')


@pytest.mark.parametrize('variables', [{'PICO_BOARD': 'pico_w'}, {'CMAKE_TOOLCHAIN_FILE': 'x'},
    {'CMAKE_GENERATOR_PLATFORM': 'x'}, {'BAD;KEY': 'x'}, {'good': 1}])
def test_reserved_and_malformed_definitions(variables):
    with pytest.raises(ValueError):
        SdkProjectSpec('/project', cache_variables=variables).validate()


def test_sdk_binding_round_trip_rebase_missing_paths_and_old_documents(tmp_path):
    project = Project.sdk_blink()
    project.sdk_project = SdkProjectSpec(str(tmp_path / 'missing root'), 'hello', 'Debug', {'FEATURE': 'ON'})
    first = tmp_path / 'a/circuit.json'
    first.parent.mkdir()
    project.save(first)
    reopened = Project.load(first)
    assert reopened.resolve(reopened.sdk_project.root).resolve() == tmp_path / 'missing root'
    second = tmp_path / 'b/circuit.json'
    second.parent.mkdir()
    reopened.save(second)
    assert Project.load(second).resolve(reopened.sdk_project.root).resolve() == tmp_path / 'missing root'
    project.sdk_project = None
    project.save(first)
    assert 'sdk_project' not in json.loads(first.read_text())
    assert Project.load(first).sdk_project is None


def test_binding_rejects_python_and_direct_startup():
    for project in (Project.demo(), Project.micropython()):
        project.sdk_project = SdkProjectSpec('/missing')
        with pytest.raises(ValueError, match='SDK/ROM'):
            project.validate()


def test_manifest_uses_full_paths_and_invalidates_header_changes(tmp_path):
    first, second = tmp_path / 'a/main.c', tmp_path / 'b/main.c'
    for file in (first, second):
        file.parent.mkdir()
        file.write_text('int main(void) {}\n')
    header = tmp_path / 'common.h'
    header.write_text('#define VALUE 1\n')
    manifest = BuildManifest('s', 1, str(tmp_path), 'app', 'Debug', {},
        {str(first): digest(first), str(header): digest(header)}, 'elf', True)
    assert manifest.matches(first, first.read_text())
    assert not manifest.matches(second, second.read_text())
    assert manifest.current()
    header.write_text('#define VALUE 2\n')
    assert not manifest.current()


def test_manifest_reopen_checks_firmware_hash(tmp_path):
    elf = tmp_path / 'app.elf'
    elf.write_bytes(b'firmware')
    manifest = BuildManifest('s', 1, '/root', 'app', 'Debug', {}, {}, digest(elf), True)
    Path(str(elf) + '.manifest.json').write_text(json.dumps(manifest.to_dict()))
    assert BuildManifest.load(elf).elf_sha256 == digest(elf)
    elf.write_bytes(b'other firmware')
    assert BuildManifest.load(elf) is None


@pytest.mark.parametrize('updates', [{'inputs': []}, {'inputs': {'relative.c': 'bad'}},
    {'generation': True}, {'text_hashes': {'/unknown.c': 'a' * 64}}, {'root': []}, {'verified': 1}])
def test_malformed_sidecar_disables_mapping(tmp_path, updates):
    elf = tmp_path / 'app.elf'
    elf.write_bytes(b'firmware')
    data = BuildManifest('s', 0, '/root', 'app', 'Debug', {}, {}, digest(elf), True).to_dict()
    data.update(updates)
    Path(str(elf) + '.manifest.json').write_text(json.dumps(data))
    assert BuildManifest.load(elf) is None


def test_manifest_normalizes_crlf_without_losing_disk_identity(tmp_path):
    source = tmp_path / 'source.c'
    source.write_bytes(b'int value;\r\n')
    text = source.read_text()
    manifest = BuildManifest('s', 0, str(tmp_path), 'app', 'Debug', {},
        {str(source): digest(source)}, 'elf', True,
        text_hashes={str(source): hashlib.sha256(text.encode()).hexdigest()})
    assert manifest.current() and manifest.matches(source, 'int value;\n')
    source.write_bytes(b'int value;\n')
    assert not manifest.current()


def test_input_watch_detects_change_then_restore(builder):
    source = builder.root / 'main.c'
    source.write_text('original')
    watcher = InputWatch([builder.root], builder.cache)
    try:
        source.write_text('modified')
        source.write_text('original')
        watcher.drain()
        assert str(source.resolve()) in watcher.changed
    finally:
        watcher.close()


def test_cache_is_owned_and_configuration_isolated(builder):
    other = ProjectBuilder(SdkProjectSpec(str(builder.root), 'app', 'RelWithDebInfo'), fake_profile(),
                           cache_root=builder.cache.parent)
    assert other.cache != builder.cache
    builder.cache.mkdir(parents=True)
    (builder.cache / 'user-data').write_text('leave me')
    with pytest.raises(ValueError, match='unowned'):
        with builder.locked():
            pass
    assert (builder.cache / 'user-data').read_text() == 'leave me'


def test_cancel_kills_children_and_streams_logs(builder, tmp_path):
    marker = tmp_path / 'child-finished'
    child = f'import time; from pathlib import Path; time.sleep(4); Path({str(marker)!r}).write_text("bad")'
    parent = f'import subprocess,sys,time; print("started", flush=True); subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(30)'
    received = []
    builder.log_callback = received.append
    timer = threading.Timer(.25, builder.cancel)
    timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            builder.run([sys.executable, '-c', parent], 10)
    finally:
        timer.cancel()
    assert time.monotonic() - start < 3
    assert any('started' in line for line in received)
    assert not marker.exists()


def test_timeout_kills_process_ignoring_term(builder):
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='timed out'):
        builder.run([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'], .2)
    assert time.monotonic() - start < 4


def test_interrupted_log_consumer_terminates_process_group(builder):
    process_ids = []
    def interrupted(text):
        if text.startswith('CHILD:'):
            process_ids.append(int(text.split(':')[1]))
            raise KeyboardInterrupt
    builder.log_callback = interrupted
    child = 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'
    parent = f'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(.1); print("CHILD:"+str(p.pid),flush=True); time.sleep(30)'
    with pytest.raises(KeyboardInterrupt):
        builder.run([sys.executable, '-c', parent], 10)
    assert builder.process is None and process_ids
    status = Path(f'/proc/{process_ids[0]}/stat')
    end = time.monotonic() + .5
    while status.exists() and not status.read_text().split(')')[1].strip().startswith('Z') and time.monotonic() < end:
        time.sleep(.005)
    assert not status.exists() or status.read_text().split(')')[1].strip().startswith('Z')


def reply_fixture(builder, *, generation=7):
    reply = builder.cache / '.cmake/api/v1/reply'
    reply.mkdir(parents=True)
    def write(name, data):
        (reply / name).write_text(json.dumps(data))
    objects = {
        'codemodel': {'paths': {'source': str(builder.root)}, 'configurations': [{'name': 'Debug',
                      'targets': [{'id': 'app::id', 'jsonFile': 'app.json'}]}]},
        'cache': {'entries': [{'name': key, 'value': value} for key, value in
                    [('PICO_BOARD', 'pico'), ('PICO_PLATFORM', 'rp2040'), ('CMAKE_BUILD_TYPE', 'Debug')]]},
        'toolchains': {'toolchains': []}, 'cmakeFiles': {'inputs': [{'path': 'CMakeLists.txt'}]}}
    for name, value in objects.items():
        write(name + '.json', value)
    write('index-001.json', {'reply': {'client-picoworkbench': {'query.json': {
        'client': {'session': 'session', 'generation': generation},
        'responses': [{'kind': name, 'jsonFile': name + '.json'} for name in objects]}}}})
    write('app.json', {'id': 'app::id', 'name': 'app', 'type': 'EXECUTABLE',
        'artifacts': [{'path': 'app.elf'}], 'sources': [{'path': 'main.c'}]})
    (builder.cache / 'compile_commands.json').write_text(json.dumps([
        {'file': str(builder.root / 'main.c'), 'directory': str(builder.cache),
         'arguments': ['gcc', '-DAPP=1', '-o', 'CMakeFiles/app.dir/main.c.o', '-c', str(builder.root / 'main.c')]},
        {'file': str(builder.root / 'main.c'), 'directory': str(builder.cache),
         'arguments': ['gcc', '-DOTHER=1', '-o', 'CMakeFiles/other.dir/main.c.o', '-c', str(builder.root / 'main.c')]}]))
    return reply


def test_file_api_discovers_target_and_selected_compile_context(builder, tmp_path):
    reply_fixture(builder)
    target, = builder.read_reply()
    assert target.name == 'app' and target.elf == builder.cache / 'app.elf'
    assert len(target.compile_commands) == 1
    view = builder.compilation_view(target, tmp_path / 'analysis')
    assert '-DAPP=1' in json.loads((view / 'compile_commands.json').read_text())[0]['arguments']


def test_file_api_rejects_stale_replies(builder):
    reply_fixture(builder, generation=6)
    with pytest.raises(ValueError, match='Stale'):
        builder.read_reply()


def test_target_requires_unambiguous_elf():
    for artifacts in ((), ('one.elf', 'two.elf')):
        with pytest.raises(ValueError, match='one ELF'):
            ConfiguredTarget('app', 'id', artifacts, (), (), ()).elf


@pytest.fixture(scope='session')
def real_sdk_profile():
    if os.environ.get('RP2040_TEST_SDK_PROJECTS') != '1':
        pytest.skip('Set RP2040_TEST_SDK_PROJECTS=1 for required real-tool acceptance')
    profile = ToolchainProfile.discover()
    assert profile.host_cxx, 'Install a host C++ compiler (g++) for PIO generation'
    return profile


@pytest.fixture(scope='session')
def upstream_builds(real_sdk_profile, tmp_path_factory):
    from rp2040_sim.sdk_examples import examples_directory, load_catalog
    root = examples_directory()
    cache = Path(os.environ.get('RP2040_SDK_TEST_CACHE', tmp_path_factory.mktemp('sdk-cache')))
    return root, real_sdk_profile, load_catalog(), cache


@pytest.mark.parametrize('target', ['blink', 'hello_serial', 'hello_gpio_irq', 'hello_adc',
    'hello_pwm', 'i2c_bus_scan', 'hello_pio', 'hello_dma', 'hello_multicore'])
def test_real_official_sdk_firmware(upstream_builds, target):
    from rp2040_sim.sdk_acceptance import validate_example
    root, profile, catalog, cache = upstream_builds
    entry = next(e for e in catalog['workloads'] if e['target'] == target)
    job = ProjectBuilder(SdkProjectSpec(str(root), target), profile, cache_root=cache)
    result = job.build()
    assert result.manifest.verified, result.manifest.reasons
    modes = ['sdk', 'rom'] if target in ('blink', 'hello_serial') else ['rom']
    for mode in modes:
        observations = validate_example(entry, result.path, mode)
        assert observations['passed'], observations


def test_real_multi_source_targets_generated_headers_and_identity(real_sdk_profile, tmp_path):
    import shutil
    from rp2040_sim.symbols import Symbols
    from rp2040_sim.controller import Simulation
    from rp2040_sim.engine import Stop
    root = tmp_path / 'nested project Ω with spaces'
    shutil.copytree(Path(__file__).resolve().parents[1] / 'firmware/sdk_project', root)
    builder = ProjectBuilder(SdkProjectSpec(str(root), 'project_a'), real_sdk_profile, cache_root=tmp_path / 'cache')
    targets = builder.configure()
    assert {target.name for target in targets} >= {'project_a', 'project_b'}
    result = builder.build(tmp_path / 'program.elf')
    assert result.manifest.verified, result.manifest.reasons
    assert result.manifest.current()
    symbols = Symbols(result.path.read_bytes())
    left = [line for line in symbols.lines if Path(line.file) == root / 'left/value.c']
    right = [line for line in symbols.lines if Path(line.file) == root / 'right/value.c']
    assert left and right and {line.address for line in left}.isdisjoint(line.address for line in right)
    simulation = Simulation(Project.sdk_blink())
    try:
        simulation.load(result.path)
        output = bytearray()
        while True:
            stop = simulation.run(100_000_000, 2048)
            output.extend(e.value for e in simulation.engine.events() if e.kind == 2)
            if stop != Stop.BUDGET:
                break
        assert b'project:42\r\n' in output
    finally:
        simulation.close()
    # The second target builds a generated PIO header with the host compiler.
    builder.spec.target = 'project_b'
    second = builder.build()
    assert second.manifest.verified, second.manifest.reasons
    assert any(Path(path).name == 'gate.pio.h' for path in second.manifest.inputs)
    assert any('-DPROJECT_B=1' in entry['arguments'] for entry in second.target.compile_commands)
    source = root / 'right/value.c'
    original = source.read_bytes()
    def change_and_restore(command):
        if command.startswith('$ ') and '--build' in command:
            source.write_bytes(original + b'/* external edit during build */\n')
            source.write_bytes(original)
    builder.log_callback = change_and_restore
    raced = builder.build()
    assert not raced.manifest.verified
    assert any('changed' in reason.lower() for reason in raced.manifest.reasons)
    (root / 'values.h').write_text('#define CHANGED 1\nint left_value(void); int right_value(void);\n')
    assert not result.manifest.current() and not second.manifest.current()


def test_real_sdk_spi_fixture_and_existing_dashboard(real_sdk_profile, tmp_path):
    from rp2040_sim.controller import Simulation
    from rp2040_sim.engine import Stop
    from rp2040_sim import demo
    base = Path(__file__).resolve().parents[1]
    result = ProjectBuilder(SdkProjectSpec(str(base / 'firmware/sdk_spi'), 'sdk_spi'), real_sdk_profile,
                            cache_root=tmp_path / 'cache').build()
    assert result.manifest.verified, result.manifest.reasons
    project = Project.dashboard()
    project.startup = 'rom'
    simulation = Simulation(project)
    try:
        simulation.load(result.path)
        output = bytearray()
        for _ in range(1000):
            reason = simulation.run(100_000_000, 1024)
            output.extend(e.value for e in simulation.engine.events() if e.kind == 2)
            if reason != Stop.BUDGET:
                break
        assert b'SPI 00 2a 7f ff\r\n' in output
        records = list(simulation.bus_capture.records)
        assert [(r.tx, r.rx, r.status) for r in records] == [(v, v, 'ACK') for v in (0, 42, 127, 255)]
        simulation.load(demo.image('sdk_dashboard'), startup='sdk')
        simulation.adc(0, 2048)
        for _ in range(1000):
            reason = simulation.run(200_000_000, 1024)
            simulation.engine.events()
            if reason != Stop.BUDGET:
                break
        assert any(simulation.devices['oled'].snapshot().pixels)
        assert any(r.bus == 'I2C' and r.acknowledged for r in simulation.bus_capture.records)
        assert any(r.bus == 'SPI' for r in simulation.bus_capture.records)
    finally:
        simulation.close()
