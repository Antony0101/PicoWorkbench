"""Phase 2 workflows: saved work, transactional editing, program identity and traces."""
from copy import deepcopy
from collections import deque
from types import SimpleNamespace
import os
import time

import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings, QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from rp2040_sim import demo
from rp2040_sim.engine import Drive, Event
from rp2040_sim.project import Project, Component
from rp2040_sim.gui.app import Window
from rp2040_sim.gui.document import CircuitHistory, fingerprint
from rp2040_sim.gui.waveform import TracePlot, parse_time, format_time


def wait(app, condition, timeout=8):
    end=time.monotonic()+timeout
    while not condition():
        app.processEvents()
        QTest.qWait(5)
        if time.monotonic()>end:
            raise AssertionError('Phase 2 GUI condition timed out')


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app,tmp_path,monkeypatch):
    monkeypatch.setattr(QMessageBox,'warning',lambda *a,**k:QMessageBox.StandardButton.Discard)
    view=Window(settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat))
    view.show()
    wait(app,lambda:view.state is not None)
    yield view
    view._closing=True
    view.close()
    app.processEvents()
    assert not view.thread.isRunning()


def test_circuit_history_is_atomic_and_restores_attached_wires():
    project=Project.demo()
    history=CircuitHistory()
    original=fingerprint(project,demo.source())
    def remove(candidate):
        candidate.components=[c for c in candidate.components if c.id!='led1']
        candidate.wires=[w for w in candidate.wires if 'led1:signal' not in w]
    history.commit(project,remove,'remove LED')
    assert len(project.components)==2
    history.undo(project)
    assert fingerprint(project,demo.source())==original
    history.redo(project)
    assert len(project.components)==2
    before=deepcopy(project)
    with pytest.raises(ValueError):
        history.commit(project,lambda p:p.wires.append(('missing:pin','pico:GP1')),'bad wire')
    assert project==before and history.index==1
    history.undo(project)
    history.commit(project,lambda p:setattr(p.components[0],'label','changed'),'label')
    assert len(history.entries)==1  # New branch replaces redo history.


def test_document_identity_survives_relative_path_save(tmp_path):
    project=Project.demo()
    project.source=str(tmp_path/'source.c')
    project.firmware=str(tmp_path/'firmware.elf')
    before=fingerprint(project,'buffer')
    project.save(tmp_path/'saved.json')
    assert fingerprint(project,'buffer')==before


def test_laptop_layout_presets_and_font_restore(window,app):
    window.resize(1366,768)
    app.processEvents()
    assert window.width()==1366 and window.height()==768
    assert window.minimumSizeHint().height()<768
    original=fingerprint(window.project,window.editor.toPlainText())
    instructions=window.state.instructions
    QTest.mouseClick(window.workspace_tabs, Qt.MouseButton.LeftButton, pos=window.workspace_tabs.tabRect(1).center())
    assert window.inspector_panel.isHidden()
    assert window.workspace_tabs.hasFocus()
    QTest.keyClick(window.workspace_tabs, Qt.Key.Key_Right)
    assert not window.inspector_panel.isHidden()
    window.set_editor_font(15)
    window.save_preferences()
    window.set_editor_font(10)
    window.restore_preferences()
    assert window.editor.font().pointSize()==15
    assert window.current_workspace=='Debug'
    assert window.state.instructions==instructions
    assert fingerprint(window.project,window.editor.toPlainText())==original


def test_invalid_preferences_recover_without_changing_document(window,app):
    before=fingerprint(window.project,window.editor.toPlainText())
    window.settings.setValue('version',1)
    window.settings.setValue('geometry','invalid geometry data')
    window.settings.setValue('font_size','not a number')
    window.restore_preferences()
    assert window.current_workspace=='Circuit'
    assert window.inspector_action.isChecked()
    assert not window.advanced_action.isChecked()
    assert fingerprint(window.project,window.editor.toPlainText())==before


def test_staged_edit_undo_and_apply_acknowledgement(window,app):
    window.send('run')
    wait(app,lambda:window.state.instructions>100)
    window.send('pause')
    wait(app,lambda:not window.running)
    before=window.state.instructions
    applied=window.applied
    window.add_part(kind='led')
    app.processEvents()
    assert window.state.instructions==before
    assert window.circuit_pending and window.applied==applied
    assert window.run_action.text()=='Apply + Run'
    window.view.setFocus()
    window.undo()
    assert not window.circuit_pending and not window.document_dirty
    window.redo()
    window.apply_circuit()
    wait(app,lambda:not window.busy)
    assert not window.circuit_pending and window.state.instructions==0
    assert window.document_dirty


def test_failed_apply_keeps_working_session(window,app):
    previous=window.applied
    source=window.loaded_source
    window.project.bootrom='/missing/rom.bin'
    window.project.startup='rom'
    window.apply_circuit()
    wait(app,lambda:not window.busy)
    assert window.applied==previous and window.loaded_source==source
    assert window.circuit_pending
    assert 'ROM' in window.logs.toPlainText()
    assert window.state.loaded


def test_save_cancel_failure_and_close_leave_work_available(window,app,tmp_path,monkeypatch):
    window.editor.insertPlainText('// modified\n')
    assert window.document_dirty
    monkeypatch.setattr(QMessageBox,'warning',lambda *a,**k:QMessageBox.StandardButton.Cancel)
    assert not window.close()
    assert window.thread.isRunning() and window.document_dirty
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:('', ''))
    assert window.save_project() is False
    assert window.document_dirty
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(tmp_path/'missing'/'p.json'),''))
    assert window.save_project() is False
    assert window.document_dirty
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(tmp_path/'p.json'),''))
    assert window.save_project()
    assert not window.document_dirty
    assert Project.load(tmp_path/'p.json').source_text==window.editor.toPlainText()
    assert window.source_saved!=window.editor.toPlainText()  # Embedded project save is separate from source-file save.


def test_bad_project_replacement_keeps_document_and_main_thread(window,app,tmp_path,monkeypatch):
    project=Project.demo()
    project.firmware='missing.elf'
    path=tmp_path/'bad.json'
    project.save(path)
    before=window.project
    threads=[]
    # The callback itself must be invoked on the GUI thread, regardless of mixins.
    window.request('inspect_flash',lambda ok:threads.append(QThread.currentThread()))
    wait(app,lambda:bool(threads))
    assert threads[0] is app.thread()
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),''))
    window.open_project()
    wait(app,lambda:not window.busy)
    assert window.project is before
    assert window.state.loaded


def test_editor_undo_keeps_circuit_history(window,app):
    window.add_part(kind='probe')
    index=window.history.index
    window.select_workspace('Code')
    window.editor.setFocus()
    original=window.editor.toPlainText()
    QTest.keyClicks(window.editor,'abc')
    QTest.keyClick(window.editor,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
    assert window.editor.toPlainText()==original
    assert window.history.index==index


def test_properties_keyboard_wiring_and_undo(window,app):
    window.inspector.setCurrentWidget(window.properties_panel)
    window.component_choice.setCurrentText('led1')
    window.property_label.setText('Status')
    window.apply_properties()
    assert next(c for c in window.project.components if c.id=='led1').label=='Status'
    window.endpoint_choice.setCurrentText('probe1:signal')
    window.disconnect_button.click()
    window.connect_button.click()
    assert window.scene.pending=='probe1:signal'
    window.endpoint_choice.setCurrentText('pico:GP0')
    window.connect_button.click()
    assert any(set(w)=={'probe1:signal','pico:GP0'} for w in window.project.wires)
    window.view.setFocus()
    window.undo()
    assert all(set(w)!={'probe1:signal','pico:GP0'} for w in window.project.wires)


def test_mapped_breakpoint_and_edited_source_fallback(window,app):
    window.build_source()
    wait(app,lambda:window.build_thread is None and not window.busy and window.loaded_name.endswith('editor.elf'))
    assert window.symbols is not None
    location=next(loc for loc in window.symbols.lines if loc.line and window.source_matches(loc.file))
    window.source_breakpoint(location.line)
    assert window.breakpoints
    window.send('run')
    wait(app,lambda:window.state.stop.name=='BREAKPOINT')
    assert window.editor.execution_line>0
    window.editor.insertPlainText('// edit\n')
    assert 'rebuild required' in window.identity_label.text()
    window.source_breakpoint(location.line)
    assert 'No executable' in window.logs.toPlainText()
    assert window.symbols.location(0xffffffff) is None


def test_python_runtime_files_survive_circuit_apply(window,app):
    from rp2040_sim.images import bootrom
    try: bootrom()
    except RuntimeError: pytest.skip('B2 ROM not prepared')
    window.new_micropython()
    wait(app,lambda:window.project.builtin=='micropython' and not window.busy)
    window.editor.setPlainText("with open('phase2.txt','w') as f:\n    f.write('retained')\nprint('FILE WRITTEN')\n")
    window.build_source()
    wait(app,lambda:getattr(window,'python_status','')=='finished',timeout=15)
    assert 'FILE WRITTEN' in window.serial.toPlainText()
    assert window.flash_dirty
    window.add_part(kind='probe')
    window.apply_circuit()
    wait(app,lambda:not window.busy)
    window.editor.setPlainText("print(open('phase2.txt').read())\n")
    window.build_source()
    wait(app,lambda:getattr(window,'python_status','')=='finished',timeout=15)
    assert 'retained' in window.serial.toPlainText()


def test_trace_measurement_virtual_time_and_loss(app):
    plot=TracePlot()
    def snapshot(now,dropped=0):
        return SimpleNamespace(time_ns=now,dropped_events=dropped,pin=lambda pin:Drive.LOW)
    plot.feed(snapshot(0),[],reset=True)
    events=[Event(t,i,1,25,value) for i,(t,value) in enumerate([(100,1),(200,0),(500,1),(600,0),(900,1)])]
    plot.feed(snapshot(1000),events)
    plot.cursor_a,plot.cursor_b=100,900
    result=plot.measurement()
    assert result['delta_ns']==800
    assert result['frequency_hz']==pytest.approx(2_500_000)
    assert result['duty']==pytest.approx(.25)
    plot.feed(snapshot(1100,1),[])
    assert 'error' in plot.measurement()
    plot.clear_trace()
    assert not plot.loss
    plot.history=deque(maxlen=2)
    plot.feed(snapshot(2000,1),events)
    assert plot.loss
    assert parse_time('0.25 ms')==250000
    assert parse_time('1 us')==1000
    assert format_time(1)=='1 ns'
    with pytest.raises(ValueError): parse_time('1.1 ns')
