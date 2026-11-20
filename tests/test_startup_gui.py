"""Selection is staged; only a successful build/load changes the running image."""
import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from rp2040_sim import demo
from rp2040_sim.build import compiler
from rp2040_sim.images import bootrom
from rp2040_sim.project import Project
from rp2040_sim.gui.app import Window


def wait(app, condition, timeout=12):
    end=time.monotonic()+timeout
    while not condition():
        app.processEvents(); QTest.qWait(5)
        assert time.monotonic()<end, 'Startup GUI check timed out'


@pytest.fixture
def window(tmp_path, monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a,**kw: QMessageBox.StandardButton.Discard)
    try:
        bootrom()
    except RuntimeError as error:
        pytest.skip(str(error))
    project=tmp_path/'circuit.json'; Project.demo().save(project)
    view=Window(project, settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat))
    view.show()
    wait(app, lambda:view.state is not None and not view.busy)
    yield app, view
    view._closing=True; view.close(); app.processEvents()
    assert not view.thread.isRunning()


def select(view, mode):
    view.startup_combo.setCurrentIndex(view.startup_combo.findData(mode))


def test_mode_selection_preserves_source_and_image_and_saves_pending_target(window, tmp_path, monkeypatch):
    app, view=window
    state, text, loaded = view.state, view.editor.toPlainText(), view.loaded_source
    select(view,'rom')
    assert view.project.startup=='direct' and view.project.build_mode=='rom'
    assert not view.circuit_pending and view.startup_pending
    assert view.editor.toPlainText()==text and view.loaded_source==loaded and view.state==state
    assert view.assistance.context[1]=='rom'
    assert not view.run_action.isEnabled() and view.build_action.isEnabled()
    assert 'F7' in view.identity_label.text()
    assert 'direct load' in view.startup_label.text()
    destination=tmp_path/'saved.json'
    monkeypatch.setattr(QFileDialog,'getSaveFileName', lambda *a,**kw:(str(destination),'Circuit projects (*.json)'))
    assert view.save_project()
    restored=Project.load(destination)
    assert restored.startup=='direct' and restored.build_mode=='rom'
    # Reopening still loads the valid direct image, retaining the next-build choice.
    view.replace_document(restored, restored.source_text)
    wait(app,lambda:not view.busy)
    assert view.startup_combo.currentData()=='rom' and view.project.startup=='direct'
    select(view,'direct')
    assert not view.startup_pending and view.run_action.isEnabled()


def test_missing_rom_reverts_selection_without_touching_program(window, monkeypatch):
    _, view=window
    text, state=view.editor.toPlainText(), view.state
    def unavailable(*a,**kw):
        raise RuntimeError('Select the official B2 ROM binary')
    monkeypatch.setattr('rp2040_sim.images.bootrom', unavailable)
    select(view,'rom')
    assert view.startup_combo.currentData()=='direct'
    assert view.project.build_startup is None
    assert view.editor.toPlainText()==text and view.state==state
    assert 'Select the official B2 ROM' in view.logs.toPlainText()


def test_real_editor_build_switches_direct_rom_and_sdk(window):
    app, view=window
    from rp2040_sim.sdk_build import sdk_path
    try:
        compiler(); sdk_path()
    except RuntimeError as error:
        pytest.skip(str(error))
    select(view,'rom')
    source=demo.source('sdk_blink')
    view.editor.setPlainText(source)
    view.build_source()
    assert not view.startup_combo.isEnabled()
    wait(app,lambda:view.build_thread is None and not view.busy and view.project.startup=='rom', timeout=60)
    assert view.project.build_startup is None and view.loaded_source==source
    assert view.state.registers[15]<0x4000  # Reset starts in the actual B2 ROM.
    assert 'B2 ROM' in view.startup_label.text()
    view.send('run')
    wait(app,lambda:view.state.time_ns>100_000_000)
    assert not view.startup_combo.isEnabled() and view.state.instructions>29000
    view.send('pause'); wait(app,lambda:not view.running)
    select(view,'sdk'); view.build_source()
    wait(app,lambda:view.build_thread is None and not view.busy and view.project.startup=='sdk', timeout=60)
    assert view.state.registers[15]>=0x10000100
    assert view.loaded_source==source
    # Switching back to bare-metal direct code is explicit and preserves the buffer.
    select(view,'direct'); view.editor.setPlainText(demo.source('blink'))
    view.build_source()
    wait(app,lambda:view.build_thread is None and not view.busy and view.project.startup=='direct', timeout=30)
    assert view.state.registers[15]>=0x10000000 and view.startup_combo.currentData()=='direct'
    assert view.loaded_source==demo.source('blink')
    select(view,'rom')
    view.new_rom()
    view.send('run')  # Run queues behind replacement even with an old pending target.
    wait(app,lambda:not view.busy and view.project.startup=='rom' and view.running)
    view.send('pause'); wait(app,lambda:not view.running)


def test_failed_build_retains_active_mode_image_and_source(window):
    app, view=window
    state, loaded=view.state, view.loaded_source
    select(view,'rom')
    view.editor.setPlainText('#error intentional startup test failure\n')
    view.build_source()
    wait(app,lambda:view.build_thread is None and not view.busy, timeout=60)
    assert view.project.startup=='direct' and view.project.build_mode=='rom'
    assert view.state==state and view.loaded_source==loaded
    assert 'intentional startup test failure' in view.logs.toPlainText()
