#!/usr/bin/env python3
"""Phase 3 real desktop smoke checks; writes only review artifacts.

QT_QPA_PLATFORM=offscreen .venv/bin/python tools/validate_runtime_desktop.py
Repeat with the local Wayland platform for WSLg evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from rp2040_sim.gui.app import STYLE, Window
from rp2040_sim.project import Project


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'build/phase3-runtime-review')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    settings=QSettings(str((args.output/'settings.ini').resolve()),QSettings.Format.IniFormat)
    settings.clear()
    project=args.output/'dashboard.json'; Project.dashboard().save(project)
    app=QApplication.instance() or QApplication([])
    app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    window=Window(project,settings=settings)
    results={'platform':platform.platform(),'qt_platform':app.platformName(),
             'checks':{},'requested_size':[1366,768]}

    def wait(condition,timeout=30):
        end=time.monotonic()+timeout
        while not condition():
            app.processEvents(); QTest.qWait(5)
            if time.monotonic()>end:
                raise AssertionError('Runtime desktop condition timed out: '+window.status.text()+' / '+window.files_panel.status.text())

    def capture(name):
        app.processEvents(); QTest.qWait(25); app.processEvents()
        assert window.editor.isVisible() or window.current_workspace=='Circuit'
        window.grab().save(str(args.output/(name+'.png')))
        assert window.width()<=1366 and window.height()<=768, (window.size(),window.minimumSizeHint(),window.tabs.minimumSizeHint(),window.plotter.minimumSizeHint())

    try:
        window.show(); window.resize(1366,768)
        wait(lambda:window.state is not None and not window.busy and window.state.loaded)
        window.send('adc',channel=0,value=2048)
        window.send('run')
        wait(lambda:'temperature' in window.plotter.latest)
        start=time.monotonic(); window.send('pause'); wait(lambda:not window.running)
        results['pause_latency_ms']=(time.monotonic()-start)*1000
        assert results['pause_latency_ms']<500
        window.select_workspace('Debug'); window.output_action.setChecked(True)
        window.tabs.setCurrentWidget(window.plotter)
        assert window.scene.parts['oled'].display_frame and any(window.scene.parts['oled'].display_frame.pixels)
        assert window.plotter.latest['temperature'][-1].value==50
        capture('oled-and-plot')
        results['checks']['live_oled_and_c_plot']=True
        before=window.scene.parts['oled'].display_frame
        window.refresh_circuit()
        assert window.scene.parts['oled'].display_frame==before
        results['checks']['paused_oled_survives_scene_refresh']=True
        window.tabs.setCurrentWidget(window.bus_inspector)
        window.bus_inspector.filter.setText('NACK')
        assert window.bus_inspector.model.rowCount()>0
        capture('bus-nack')
        results['checks']['bus_filter_and_outcomes']=True
        window.new_dashboard(True)
        wait(lambda:window.project.builtin=='micropython' and not window.busy)
        window.output_action.setChecked(True); window.tabs.setCurrentWidget(window.files_panel)
        window.files_panel.connect_button.click()
        wait(lambda:window.files_panel.seen_idle and not window.files_busy and window.files_panel.pending is None)
        window.files_panel.start('upload','/demo.py',data=b'print("Hello Pico")\n')
        wait(lambda:not window.files_busy and window.files_panel.pending is None and not window.files_panel.refresh_pending)
        capture('device-files')
        results['checks']['idle_repl_file_upload_and_listing']=True
        for size in (11,16,24):
            window.set_editor_font(size)
            for name in ('Code','Debug','Circuit'):
                window.select_workspace(name); app.processEvents()
                assert window.minimumSizeHint().width()<=1366 and window.minimumSizeHint().height()<=768
        results['checks']['workspace_switch_and_font_scale']=True
        window.set_editor_font(11)
        results['actual_size']=[window.width(),window.height()]
        sources=list((ROOT/'python/rp2040_sim').rglob('*.py'))+list((ROOT/'engine/src').glob('*.[ch]'))
        results['source_sha256']=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(sources))).hexdigest()
        (args.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps(results))
    finally:
        window._closing=True; window.close(); app.processEvents()


if __name__=='__main__':
    main()
