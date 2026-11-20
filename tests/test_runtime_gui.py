import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from rp2040_sim import demo
from rp2040_sim.gui.app import Window
from rp2040_sim.gui.runtime import bus_groups
from rp2040_sim.gui.worker import Worker
from rp2040_sim.controller import Simulation
from rp2040_sim.project import Project
from rp2040_sim.capture import BusExchange


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def wait(app, condition, timeout=20):
    end=time.monotonic()+timeout
    while not condition():
        app.processEvents(); QTest.qWait(5)
        assert time.monotonic()<end, 'GUI runtime check timed out'


def test_runtime_layout_views_oled_add_and_preferences(app, tmp_path):
    settings=QSettings(str(tmp_path/'ui.ini'), QSettings.Format.IniFormat)
    window=Window(settings=settings)
    try:
        window.show(); wait(app, lambda: window.state is not None)
        window.resize(1366,768)
        window.add_part(kind='ssd1306', x=650, y=100)
        component=window.project.components[-1]
        assert component.address==0x3c
        window.select_component(component.id)
        assert window.property_address.minimum()==0x3c and window.property_address.maximum()==0x3d
        text=window.editor.toPlainText()
        for name in ('Code','Circuit','Debug'):
            window.select_workspace(name)
            assert window.editor.toPlainText()==text
        window.select_workspace('Code'); window.output_action.setChecked(True)
        for view in (window.plotter, window.bus_inspector, window.files_panel):
            window.tabs.setCurrentWidget(view); app.processEvents()
            assert window.editor.isVisible() and view.isVisible()
            assert window.minimumSizeHint().width()<=1366 and window.minimumSizeHint().height()<=768
        assert not window.files_panel.refresh_button.isEnabled()
    finally:
        window._closing=True; window.close(); app.processEvents()


def test_bus_grouping_interleaving_gaps_and_filtering(app):
    records=[BusExchange(1,0,'I2C',0,0x48,'sensor',0,0,0,True),
             BusExchange(2,1,'SPI',0,None,'loop',0xaa,0xaa,0,True),
             BusExchange(3,2,'I2C',0,0x48,'sensor',0,0x42,7,True),
             BusExchange(5,3,'I2C',0,0x3d,'',0,0,2,False)]
    groups=bus_groups(records)
    assert [len(g) for g in groups]==[2,1,1]
    from rp2040_sim.gui.runtime import BusInspector
    view=BusInspector(lambda *a,**kw:None)
    view.display({'bus':tuple(records), 'bus_dropped':1})
    assert view.model.rowCount()==3
    view.filter.setText('NACK'); assert view.model.rowCount()==1
    view.filter.setText('0x48'); assert view.model.rowCount()==1
    view.selected(view.model.index(0,0)); assert 'RESTART' in view.details.toPlainText()
    view.close()


def test_files_reject_busy_without_stopping_user_program(app):
    worker=Worker()
    worker.sim=Simulation(Project.micropython())
    worker.sim.load(demo.image('micropython'))
    worker.running=True
    worker.loaded_source='user source'
    results=[]; worker.file_result.connect(results.append)
    try:
        before=worker.sim.engine.snapshot()
        worker.command({'name':'files','id':1,'operation':'list','path':'/'})
        assert results and not results[-1].ok
        assert worker.running and worker.loaded_source=='user source'
        assert worker.sim.engine.snapshot()==before
    finally:
        worker.sim.close()


def test_worker_file_jobs_preserve_source_playback_and_exclude_management(app):
    worker=Worker()
    worker.sim=Simulation(Project.micropython())
    worker.sim.load(demo.image('micropython'))
    worker.loaded_source='exact user program'
    worker.speed=.01
    results=[]; worker.file_result.connect(results.append)
    try:
        worker.command({'name':'files_connect'})
        for _ in range(3000):
            worker.tick()
            if worker.python_idle:
                break
        assert worker.python_idle and not worker.running
        worker.command({'name':'files','id':5,'operation':'upload','path':'/worker.bin','data':b'256\n'*100})
        job=worker.file_job
        worker.command({'name':'run'})  # conflicts are rejected without pausing the active job
        assert worker.file_job is job and worker.running
        for _ in range(5000):
            worker.tick()
            if results:
                break
        assert results[-1].ok
        assert worker.python_idle and not worker.running and worker.speed==.01
        assert worker.loaded_source=='exact user program' and not worker.plot.channels
        assert worker.flash_dirty is False  # No baseline was installed by this isolated fixture.
        assert worker.sim.recording  # Management UART input remains replayable.
    finally:
        worker.sim.close()


def test_real_files_panel_editor_revision_and_source_identity(app, tmp_path, monkeypatch):
    settings=QSettings(str(tmp_path/'files.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a,**kw: QMessageBox.StandardButton.Discard)
    window=Window(settings=settings)
    try:
        window.show(); wait(app, lambda: window.state is not None)
        window.new_micropython()
        wait(app, lambda: window.project.builtin=='micropython' and not window.busy)
        panel=window.files_panel
        window.output_action.setChecked(True)
        window.tabs.setCurrentWidget(panel)
        panel.connect_button.click()
        wait(app, lambda: panel.seen_idle and not window.files_busy and panel.pending is None)
        assert panel.connected
        source=b'print("device file")\n'
        panel.start('upload', '/script.py', data=source)
        wait(app, lambda: panel.pending is None and not window.files_busy and not panel.refresh_pending)
        assert window.loaded_source is None
        item=next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
                  if panel.tree.topLevelItem(i).text(0)=='script.py')
        panel.tree.setCurrentItem(item)
        panel.open_selected()
        window.editor.insertPlainText('# newer edit\n')
        wait(app, lambda: panel.pending is None and not window.files_busy)
        assert 'Editor changed during transfer' in panel.status.text()
        assert window.device_source_path is None and '# newer edit' in window.editor.toPlainText()
        panel.open_selected()
        wait(app, lambda: panel.pending is None and not window.files_busy)
        assert window.editor.toPlainText()==source.decode()
        assert window.source_path is None and window.project.source==''
        assert window.editor_pane.title.text().startswith('Pico:/script.py')
        assert window.loaded_source is None
        window.editor.insertPlainText('# next edit\n')
        assert '*' in window.editor_pane.title.text()
    finally:
        window._closing=True; window.close(); app.processEvents()
