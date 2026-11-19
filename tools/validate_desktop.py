#!/usr/bin/env python3
"""Repeatable Qt desktop checks. Requires a prepared checkout and local display.

QT_QPA_PLATFORM=offscreen .venv/bin/python tools/validate_desktop.py
Use a real Qt display platform to repeat against the desktop compositor.
This is automated UI evaluation, not a participant study or screen-reader audit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QAccessible, QContextMenuEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from rp2040_sim.gui.app import STYLE, Window
from rp2040_sim.gui.document import fingerprint


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('build/phase2-review'))
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    settings=QSettings(str((args.output/'settings.ini').resolve()),QSettings.Format.IniFormat)
    settings.clear()
    window=Window(settings=settings)
    results={'platform':platform.platform(),'qt_platform':app.platformName(),
             'scale':app.primaryScreen().devicePixelRatio(),'checks':{}}
    root=Path(__file__).resolve().parents[1]
    sources=sorted((root/'python/rp2040_sim/gui').glob('*.py'))+[root/'python/rp2040_sim/symbols.py',root/'python/rp2040_sim/project.py']
    results['source_sha256']=hashlib.sha256(b''.join(path.read_bytes() for path in sources)).hexdigest()

    def wait(condition):
        deadline=time.monotonic()+15
        while not condition():
            app.processEvents()
            QTest.qWait(5)
            if time.monotonic()>deadline:
                raise AssertionError('Desktop condition timed out')

    def capture(name):
        app.processEvents()
        QTest.qWait(10)
        app.processEvents()
        window.grab().save(str(args.output/(name+'.png')))

    try:
        window.show()
        wait(lambda:window.state is not None)
        window.resize(1366,768)
        app.processEvents()
        results['requested_size']=[1366,768]
        results['actual_size']=[window.width(),window.height()]
        results['minimum_size']=[window.minimumSizeHint().width(),window.minimumSizeHint().height()]
        assert window.height()<=768 and window.width()<=1366
        assert not window.advanced_widget_action.isVisible()
        results['checks']['laptop_and_advanced_controls']=True
        capture('circuit')
        before=fingerprint(window.project,window.editor.toPlainText())
        state=window.state
        for name in ('Code','Debug','Circuit'):
            window.select_workspace(name)
            capture(name.lower())
        assert fingerprint(window.project,window.editor.toPlainText())==before and window.state==state
        results['checks']['presentation_preserves_document_and_chip']=True
        QTest.mouseClick(window.workspace_tabs,Qt.MouseButton.LeftButton,pos=window.workspace_tabs.tabRect(1).center())
        app.processEvents()
        assert window.top_splitter.isHidden() and window.editor.isVisible()
        QTest.mouseClick(window.editor.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(80,20))
        QTest.keyClick(window.editor,Qt.Key.Key_H,Qt.KeyboardModifier.ControlModifier)
        assert window.editor_pane.find_bar.isVisible() and window.editor_pane.find_bar.replace_row.isVisible()
        window.editor_pane.find_bar.find_text.setText('REG')
        assert window.editor_pane.find_bar.matches
        QTest.keyClick(window.editor_pane.find_bar.find_text,Qt.Key.Key_Escape)
        assert window.editor_pane.find_bar.isHidden()
        window.output_action.setChecked(True)
        window.tabs.setCurrentWidget(window.logs)
        app.processEvents()
        assert window.editor.isVisible() and window.tabs.isVisible()
        assert fingerprint(window.project,window.editor.toPlainText())==before
        results['checks']['full_editor_keyboard_search_and_independent_output']=True
        capture('code-editor')
        window.select_workspace('Circuit')
        window.inspector.setCurrentWidget(window.properties_panel)
        window.component_choice.setFocus()
        window.component_choice.setCurrentText('led1')
        window.property_label.setFocus()
        window.property_label.selectAll()
        QTest.keyClicks(window.property_label,'Keyboard LED')
        window.properties_apply.setFocus()
        QTest.keyClick(window.properties_apply,Qt.Key.Key_Space)
        assert window.project.components[0].label=='Keyboard LED'
        window.view.setFocus()
        window.undo()
        assert window.project.components[0].label=='External LED'
        results['checks']['keyboard_property_edit_and_undo']=True
        window.component_choice.setCurrentText('led1')
        window.view.setFocus()
        position=QPoint(20,20)
        QApplication.sendEvent(window.view.viewport(),QContextMenuEvent(
            QContextMenuEvent.Reason.Keyboard,position,window.view.viewport().mapToGlobal(position)))
        assert any(action.text()=='Edit label…' for action in window.view._context_menu.actions())
        window.view._context_menu.close()
        results['checks']['keyboard_context_targets_selection']=True
        names={}
        for key,widget in [('source',window.editor),('indentation',window.editor_pane.indentation),
                           ('problems',window.editor_pane.problems),('component',window.component_choice),
                           ('port',window.endpoint_choice),('net',window.net_details)]:
            interface=QAccessible.queryAccessibleInterface(widget)
            assert interface is not None
            names[key]=interface.text(QAccessible.Text.Name)
            assert names[key]
        results['accessible_names']=names
        results['checks']['standard_widget_accessibility_interfaces']=True
        window.endpoint_choice.setCurrentText('led1:signal')
        window.connect_button.click()
        assert window.scene.pending=='led1:signal'
        position=window.view.viewport().rect().center()
        # Wayland forbids pointer warping; deliver the toolkit event directly.
        QApplication.sendEvent(window.view.viewport(), QMouseEvent(QEvent.Type.MouseMove,
            QPointF(position), QPointF(window.view.viewport().mapToGlobal(position)),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        app.processEvents()
        assert not window.scene.preview.path().isEmpty()
        QTest.keyClick(window.view,Qt.Key.Key_Escape)
        assert window.scene.pending is None
        results['checks']['wire_preview_and_escape']=True
        window.set_editor_font(16)
        window.save_preferences()
        window.set_editor_font(10)
        window.restore_preferences()
        assert window.editor.font().pointSize()==16
        results['checks']['font_preferences']=True
        capture('properties-large-font')
        (args.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps(results))
    finally:
        window._closing=True
        window.close()
        app.processEvents()


if __name__=='__main__':
    main()
