from pathlib import Path
import shutil

import pytest

from rp2040_sim.build import ROOT, build, compiler, FirmwareBuild, SdkFirmwareBuild
from rp2040_sim.engine import Engine
from rp2040_sim.symbols import Symbols


def test_cross_compile_load_and_debug_metadata(tmp_path):
    try:
        compiler()
    except RuntimeError:
        pytest.skip("ARM cross compiler not installed")
    output = tmp_path / "blink.elf"
    build(ROOT / "firmware/fixtures/blink.c", output, ROOT / "firmware/fixtures/direct.ld")
    with Engine() as engine:
        engine.load(output)
        engine.run(budget=1000)
        assert engine.snapshot().instructions == 1000
        debug = Symbols(output.read_bytes())
        assert debug.names["reset_handler"] == 0x10000008
        assert debug.location(engine.snapshot().registers[15]).line > 0


def test_canceled_build_terminates_process_group(tmp_path, monkeypatch):
    fake = tmp_path / "fake-compiler"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setenv("ARM_CC", str(fake))
    job = FirmwareBuild(tmp_path / "test.c", tmp_path / "test.elf", tmp_path / "test.ld")
    job.cancel()
    with pytest.raises(RuntimeError):
        job.finish(timeout=2)


def test_sdk_editor_build_runs_real_sdk_crt0_and_uart(tmp_path):
    from rp2040_sim import demo
    from rp2040_sim.engine import Stop
    from rp2040_sim.sdk_build import sdk_path
    try:
        compiler()
        sdk_path()
    except RuntimeError as error:
        pytest.skip(str(error))
    source = tmp_path / 'edited.c'
    source.write_text(demo.source('sdk_uart').replace('UART ready', 'editor build works'))
    output = tmp_path / 'edited.elf'
    SdkFirmwareBuild(source, output).finish()
    with Engine() as engine:
        engine.load(output, startup='sdk')
        assert engine.run(10_000_000, 10000) == Stop.DEADLINE
        assert bytes(e.value for e in engine.events() if e.kind == 2) == b'Pico SDK editor build works\r\n'
        assert Symbols(output.read_bytes()).names['main'] >= 0x10000100


def test_missing_sdk_has_actionable_build_error(tmp_path, monkeypatch):
    monkeypatch.setenv('PICO_SDK_PATH', str(tmp_path / 'absent'))
    with pytest.raises(RuntimeError, match='PICO_SDK_PATH'):
        SdkFirmwareBuild(tmp_path / 'source.c', tmp_path / 'out.elf')
