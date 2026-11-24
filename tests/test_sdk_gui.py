"""Imported-project UI keeps one editor/session and protects disk/recovery data."""
from pathlib import Path
import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from rp2040_sim.gui.app import Window
from rp2040_sim.project import Project
from rp2040_sim.sdk_projects import SdkProjectSpec


def wait(app, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        # PySide's qWait retains the GIL and starves Python file hashing in
        # the build thread. A short sleep models the normal Qt event loop.
        time.sleep(.005)
        assert time.monotonic() < deadline


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv('RP2040_CODE_ASSIST', '0')
    window = Window(settings=QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    window.show()
    wait(app, lambda: window.state is not None)
    monkeypatch.setattr(window, 'confirm_unsaved', lambda: True)
    yield window
    window._closing = True
    window.close()
    app.processEvents()


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / 'SDK project Ω'
    root.mkdir()
    (root / 'CMakeLists.txt').write_text('message(FATAL_ERROR "must not run on open")\n')
    for name in ('main.c', 'other.c'):
        (root / name).write_text('int value = 1;\n')
    return root


def test_attach_is_read_only_and_preserves_editor_worker_and_firmware(view, project_root):
    editor, worker, source = view.editor, view.worker, view.editor.toPlainText()
    firmware = view.worker.sim.image
    view.open_sdk_project(project_root)
    assert view.project.sdk_project.root == str(project_root)
    assert view.editor is editor and view.worker is worker
    assert view.editor.toPlainText() == source and view.worker.sim.image == firmware
    assert view.build_thread is None and view.sdk_targets == ()
    assert view.project.build_mode == 'sdk'
    view.busy = True
    view.update_actions()
    assert not view.sdk_source.isEnabled()
    view.busy = False
    view.update_actions()
    assert view.sdk_source.isEnabled()


def test_source_save_is_atomic_and_rejects_external_conflicts(view, project_root):
    view.open_sdk_project(project_root)
    source = project_root / 'main.c'
    assert view.open_sdk_source(source)
    view.editor.insertPlainText('/* edit */')
    assert view.save_sdk_source()
    assert source.read_text() == view.editor.toPlainText()
    view.editor.insertPlainText('/* newer buffer */')
    source.write_text('external change\n')
    assert not view.save_sdk_source()
    assert source.read_text() == 'external change\n'


def test_file_switch_cancel_preserves_source_and_editor_undo(view, project_root, monkeypatch):
    view.open_sdk_project(project_root)
    assert view.open_sdk_source(project_root / 'main.c')
    editor = view.editor
    editor.insertPlainText('/* unsaved */')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not view.open_sdk_source(project_root / 'other.c')
    assert view.source_path == project_root / 'main.c'
    assert view.editor is editor
    editor.undo()
    assert editor.toPlainText() == 'int value = 1;\n'


def test_imported_save_as_writes_copy_without_rebinding(view, project_root, tmp_path, monkeypatch):
    view.open_sdk_project(project_root)
    source = project_root / 'main.c'
    assert view.open_sdk_source(source)
    view.editor.insertPlainText('/* copy */')
    copy = tmp_path / 'copy.c'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(copy), 'C (*.c)'))
    view.save_source()
    assert copy.read_text() == view.editor.toPlainText()
    assert view.source_path == source
    assert source.read_text() == 'int value = 1;\n'


def test_editor_detachment_keeps_sdk_controls_and_source(view, project_root):
    view.open_sdk_project(project_root)
    assert view.open_sdk_source(project_root / 'main.c')
    editor, controls = view.editor, view.sdk_controls
    view.panels.detach('editor')
    view.select_workspace('Circuit')
    assert view.editor is editor and controls.parent() is view.editor_pane
    assert view.panels.panels['editor'].host is not None
    view.panels.dock('editor')
    assert view.editor is editor


def test_crlf_source_opens_clean_and_save_as_cannot_bypass_conflicts(view, project_root, monkeypatch):
    view.open_sdk_project(project_root)
    source = project_root / 'main.c'
    source.write_bytes(b'int value;\r\n')
    assert view.open_sdk_source(source)
    assert view.editor.toPlainText() == view.source_saved == 'int value;\n'
    view.editor.insertPlainText('/* edit */')
    source.write_text('external\n')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(source), 'C (*.c)'))
    assert not view.save_source()
    assert source.read_text() == 'external\n'


def test_superseded_failures_do_not_annotate_another_project(view, project_root):
    view.open_sdk_project(project_root)
    generation = view.sdk_generation
    view.detach_sdk()
    view.sdk_failed((generation, 'main.c:1:1: error: obsolete failure', {}))
    assert view.assistance.build_items == []


def test_external_library_headers_editable_but_generated_and_sdk_files_preview(view, project_root, tmp_path):
    from types import SimpleNamespace
    from rp2040_sim.sdk_projects import ConfiguredTarget
    view.open_sdk_project(project_root)
    library = tmp_path / 'library'
    library.mkdir()
    sdk, cache = tmp_path / 'sdk', tmp_path / 'cache'
    view.sdk_builder = SimpleNamespace(profile=SimpleNamespace(sdk=str(sdk), include_roots=()), cache=cache)
    view.project.sdk_project.target = 'app'
    view.sdk_targets = (ConfiguredTarget('app', 'id', (),
        (str(library / 'library.c'), str(sdk / 'driver.c')), (str(project_root / 'generated.h'),), ()),)
    assert view.is_sdk_source(library / 'include/api.h')
    assert not view.is_sdk_source(project_root / 'generated.h')
    assert not view.is_sdk_source(sdk / 'gpio.h')
    assert not view.is_sdk_source(cache / 'generated.h')
    view.sdk_builder = None


def test_recovered_buffer_never_overwrites_external_disk_edits(view, project_root, tmp_path, monkeypatch, app):
    view.open_sdk_project(project_root)
    source = project_root / 'main.c'
    assert view.open_sdk_source(source)
    view.editor.insertPlainText('/* recovered buffer */\n')
    recovered = view.editor.toPlainText()
    circuit = tmp_path / 'saved/circuit.json'
    circuit.parent.mkdir()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(circuit), 'JSON'))
    assert view.save_project()
    source.write_text('external replacement\n')
    reopened = Window(circuit, settings=QSettings(str(tmp_path / 'reopened.ini'), QSettings.IniFormat))
    try:
        wait(app, lambda: reopened.state is not None and not reopened.busy)
        assert reopened.editor.toPlainText() == recovered
        assert reopened.source_path == source
        assert not reopened.save_sdk_source()
        assert source.read_text() == 'external replacement\n'
    finally:
        reopened._closing = True
        reopened.close()
        app.processEvents()


def test_missing_imported_artifact_opens_unloaded_without_bundled_fallback(app, tmp_path, monkeypatch):
    monkeypatch.setenv('RP2040_CODE_ASSIST', '0')
    project = Project(startup='sdk', sdk_project=SdkProjectSpec(str(tmp_path / 'missing')),
                      source=str(tmp_path / 'missing/main.c'), firmware=str(tmp_path / 'missing/app.elf'))
    path = tmp_path / 'circuit.json'
    project.save(path)
    window = Window(path, settings=QSettings(str(tmp_path / 'missing.ini'), QSettings.IniFormat))
    try:
        wait(app, lambda: window.state is not None and not window.busy)
        assert not window.state.loaded
        assert not window.run_action.isEnabled()
        assert window.symbols is None and window.editor.toPlainText() == ''
    finally:
        window._closing = True
        window.close()
        app.processEvents()


@pytest.fixture
def real_project(view, app, tmp_path):
    if os.environ.get('RP2040_TEST_SDK_PROJECTS') != '1':
        pytest.skip('Set RP2040_TEST_SDK_PROJECTS=1 for imported desktop acceptance')
    import shutil
    from rp2040_sim.sdk_tools import ToolchainProfile
    profile = ToolchainProfile.discover()
    assert profile.host_cxx, 'A host C++ compiler is required for PIO acceptance'
    root = tmp_path / 'multi source project Ω'
    shutil.copytree(Path(__file__).resolve().parents[1] / 'firmware/sdk_project', root)
    view.open_sdk_project(root)
    previous = view.worker.sim.image
    view.configure_sdk()
    wait(app, lambda: view.build_thread is None, timeout=90)
    assert {t.name for t in view.sdk_targets} >= {'project_a', 'project_b'}, view.logs.toPlainText()
    assert view.worker.sim.image == previous
    view.sdk_target.setCurrentText('project_a')
    assert view.open_sdk_source(root / 'main.c')
    return root


def test_real_imported_build_mapping_edits_errors_and_cancel(view, real_project, app, monkeypatch):
    from rp2040_sim.engine import Stop
    root = real_project
    original = view.editor.toPlainText()
    view.panels.detach('editor')
    view.panels.detach('build')
    view.panels.reveal('editor')
    app.processEvents()
    QTest.qWait(20)
    view.editor.setFocus()
    QTest.keyClick(view.editor, Qt.Key.Key_F7)
    assert view.build_thread is not None
    view.editor.insertPlainText('/* next build */\n')
    assert not view.save_sdk_source()
    wait(app, lambda: view.build_thread is None and not view.busy and view.loaded_manifest is not None, timeout=120)
    assert view.loaded_manifest.verified, view.logs.toPlainText()
    assert view.sdk_source.isEnabled() and view.sdk_configure_button.isEnabled()
    assert not view.source_matches(root / 'main.c')
    view.editor.undo()
    assert view.editor.toPlainText() == original
    assert view.source_matches(root / 'main.c')
    line = next(i for i, text in enumerate(original.splitlines(), 1) if 'project_total = left_value' in text)
    view.editor.go_to_line(line)
    view.panels.reveal('editor')
    app.processEvents()
    view.editor.setFocus()
    QTest.keyClick(view.editor, Qt.Key.Key_F9)
    assert view.breakpoints
    view.send('run')
    wait(app, lambda: not view.running and view.state.stop == Stop.BREAKPOINT, timeout=30)
    assert view.state.registers[15] in view.breakpoints
    view.send('reset')
    wait(app, lambda: view.state.instructions == 0 and not view.busy)
    assert view.loaded_manifest and view.source_matches(root / 'main.c')
    view.clear_breakpoints()
    previous = view.worker.sim.image
    (root / 'right/value.c').write_text('#include "values.h"\nint right_value(void) { return missing_value; }\n')
    view.build_source()
    wait(app, lambda: view.build_thread is None and not view.busy, timeout=120)
    assert view.worker.sim.image == previous
    item = next(d for d in view.editor_pane.build_diagnostics if 'missing_value' in d['message'])
    assert item['file'] == str(root / 'right/value.c') and not item['stale']
    view.assistance.navigate_diagnostic(item)
    assert view.source_path == root / 'right/value.c'
    assert view.panels.panels['editor'].host is not None
    assert not view.source_matches(root / 'left/value.c')
    view.configure_sdk()
    view.cancel_build()
    wait(app, lambda: view.build_thread is None, timeout=30)
    assert view.worker.sim.image == previous


def test_real_imported_clangd_project_sdk_and_generated_headers(view, real_project, app, monkeypatch):
    if os.environ.get('RP2040_TEST_LANGUAGE_SERVERS') != '1':
        pytest.skip('Set RP2040_TEST_LANGUAGE_SERVERS=1 for real imported clangd acceptance')
    from rp2040_sim.gui.assistance import executable
    assert executable('clangd'), 'clangd is required for delivery'
    root = real_project
    view.sdk_target.setCurrentText('project_b')
    view.build_source()
    wait(app, lambda: view.build_thread is None and not view.busy and view.loaded_manifest is not None, timeout=120)
    assert view.loaded_manifest.target == 'project_b', view.logs.toPlainText()
    monkeypatch.delenv('RP2040_CODE_ASSIST', raising=False)
    assert view.open_sdk_source(root / 'main.c')
    assistant = view.assistance
    assistant.activate(True)
    assistant.restart()
    wait(app, lambda: assistant.client is not None and assistant.client.initialized and assistant.client.uri is not None, timeout=60)
    text = view.editor.toPlainText()

    def point(needle, offset=1):
        start = text.index(needle)
        return {'line': text[:start].count('\n'), 'character': start - text.rfind('\n', 0, start) - 1 + offset}

    def request(method, position):
        responses = []
        assistant.request(method, responses.append, position)
        wait(app, lambda: bool(responses), timeout=30)
        return responses[0]

    completions = request('completion', point('gpio_init', 5))
    entries = completions.get('items', []) if isinstance(completions, dict) else completions
    assert any('gpio_init' in entry['label'] for entry in entries)
    assert request('hover', point('gpio_init'))
    project = request('definition', point('left_value'))
    sdk = request('definition', point('gpio_init'))
    generated = request('definition', point('"gate.pio.h"', 3))
    def uri(locations):
        item = locations[0] if isinstance(locations, list) else locations
        return item.get('uri', item.get('targetUri'))
    assert '/left/value.c' in uri(project) or '/values.h' in uri(project)
    assert '/hardware/gpio.h' in uri(sdk)
    assert '/gate.pio.h' in uri(generated)
    assistant.navigate(uri(sdk), {'start': {'line': 0, 'character': 0}})
    assert assistant.preview.findChild(type(view.editor)).isReadOnly()
    assistant.preview.close()
    assistant.navigate(uri(project), {'start': {'line': 1, 'character': 0}})
    assert view.source_path in (root / 'values.h', root / 'left/value.c')
    assert view.open_sdk_source(root / 'main.c')
    wait(app, lambda: assistant.client is not None and assistant.client.initialized and assistant.client.uri is not None, timeout=60)
    view.editor.insertPlainText('int invalid = missing_project_name;\n')
    assistant.flush()
    wait(app, lambda: any('missing_project_name' in d['message'] for d in view.editor_pane.live_diagnostics), timeout=30)
    assert not any('file not found' in d['message'] for d in view.editor_pane.live_diagnostics)
