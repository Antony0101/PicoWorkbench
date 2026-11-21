"""Detached tools remain views of one session, including real firmware and LSP."""
import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QByteArray, QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QContextMenuEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from rp2040_sim import demo
from rp2040_sim.gui.app import Window
from rp2040_sim.gui.panels import fit_on_screen
from rp2040_sim.project import Project


def wait(app, condition, timeout=20):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        QTest.qWait(5)
        assert time.monotonic() < deadline, 'Detached panel check timed out'


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def windows(app, tmp_path, monkeypatch):
    monkeypatch.setenv('RP2040_CODE_ASSIST', '0')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: QMessageBox.StandardButton.Discard)
    created = []
    def create(project=None, settings=None):
        directory = tmp_path / str(len(created))
        directory.mkdir()
        path = directory / 'project.json'
        (project or Project.demo()).save(path)
        view = Window(path, settings=settings or QSettings(str(directory/'ui.ini'), QSettings.Format.IniFormat))
        created.append(view)
        view.show()
        wait(app, lambda: view.state is not None and not view.busy)
        return view
    yield create
    for view in created:
        if view.thread.isRunning():
            view._closing = True
            view.close()
        app.processEvents()
        assert not view.thread.isRunning()
        view.deleteLater()
    app.processEvents()


def focus(app, view, widget):
    host = widget.window()
    host.show()
    host.activateWindow()
    widget.setFocus()
    wait(app, widget.hasFocus)
    view.update_actions()


def test_all_panels_keep_identity_and_return_in_original_order(app, windows):
    view = windows()
    manager = view.panels
    source, document = view.editor.toPlainText(), view.editor.document()
    state, dirty = view.state, view.document_dirty
    worker, assistance = view.worker, view.assistance
    keys = list(manager.panels)
    assert len(keys) == 15
    widgets = {key: p.widget for key, p in manager.panels.items()}
    for key in keys:
        manager.detach(key)
        panel = manager.panels[key]
        assert panel.host.centralWidget() is widgets[key]
        assert widgets[key].isVisible()
        host = panel.host
        manager.detach(key)  # Opens the existing host.
        assert panel.host is host
    assert view.tabs.count() == view.inspector.count() == 0
    assert not view.output_action.isEnabled() and not view.inspector_action.isEnabled()
    assert not view.tabs.isVisible() and not view.inspector_panel.isVisible()
    for name in ('Code', 'Debug', 'Circuit'):
        view.select_workspace(name)
        assert all(p.host.isVisible() and p.widget.isVisible() for p in manager.panels.values())
        assert manager.placeholder.isVisible() == (name == 'Code')
    # Redocking in a different order must restore fixed home order.
    for key in keys[::2] + keys[1::2]:
        manager.dock(key, reveal=False)
    for home in (manager.output_home, manager.inspector_home):
        assert [home.tabs.widget(i) for i in range(home.tabs.count())] == [p.widget for p in home.order]
    assert view.editor.document() is document and view.editor.toPlainText() == source
    assert view.worker is worker and view.assistance is assistance
    assert view.state == state and view.document_dirty == dirty


def test_close_reveals_home_and_reset_preserves_source_and_captures(app, windows):
    view = windows()
    view.panels.detach('editor')
    view.editor.insertPlainText('// unsaved\n')
    text, document = view.editor.toPlainText(), view.editor.document()
    view.panels.panels['editor'].host.close()
    assert view.current_workspace == 'Code' and view.editor.isVisible()
    assert view.editor.document() is document and view.editor.toPlainText() == text
    view.panels.detach('cpu')
    view.panels.panels['cpu'].host.close()
    assert view.current_workspace == 'Debug' and view.inspector.currentWidget() is view.panels.panels['cpu'].widget
    view.select_workspace('Circuit')
    view.panels.detach('serial')
    view.serial.setPlainText('captured output')
    view.panels.panels['serial'].host.close()
    assert view.current_workspace == 'Circuit' and view.serial.isVisible()
    assert view.output_action.isChecked()
    view.panels.detach('serial')
    view.panels.detach('editor')
    state = view.state
    view.reset_layout()
    assert all(p.host is None for p in view.panels.panels.values())
    assert view.current_workspace == 'Circuit'
    assert view.editor.toPlainText() == text and view.serial.toPlainText() == 'captured output'
    assert view.state == state


def test_navigation_finds_floating_targets_without_changing_workspace(app, windows):
    view = windows()
    view.panels.detach('editor')
    view.panels.detach('build')
    view.select_workspace('Circuit')
    source = view.editor.toPlainText()
    view.build_text = source
    view.error('editor.c:3:2: error: navigation check')
    assert view.current_workspace == 'Circuit'
    assert view.editor.textCursor().blockNumber() == 2
    assert 'navigation check' in view.logs.toPlainText()
    view.assistance.navigate_diagnostic({'range': {'start': {'line': 5, 'character': 0}}})
    assert view.current_workspace == 'Circuit'
    assert view.editor.textCursor().blockNumber() == 5
    assert view.editor.toPlainText() == source
    view.panels.detach('pio')
    host = view.panels.panels['pio'].host
    view.new_pio_blink()
    wait(app, lambda: view.project.builtin == 'pio_blink' and not view.busy)
    assert view.panels.panels['pio'].host is host
    assert view.project.name in host.windowTitle()


def test_editor_undo_find_and_session_shortcuts_are_scoped(app, windows, monkeypatch, tmp_path):
    first, second = windows(), windows()
    first.panels.detach('editor')
    second.panels.detach('editor')
    before = first.editor.toPlainText()
    focus(app, first, first.editor)
    first.editor.insertPlainText('// typing\n')
    QTest.keyClick(first.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert first.editor.toPlainText() == before
    QTest.keyClick(first.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert first.editor.toPlainText() != before
    QTest.keyClick(first.editor, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert first.editor_pane.find_bar.isVisible()
    first.editor.setFocus()
    QTest.keyClick(first.editor, Qt.Key.Key_Escape)
    assert not first.editor_pane.find_bar.isVisible()
    calls = []
    for i, view in enumerate((first, second)):
        view.build_action.triggered.disconnect()
        view.build_action.triggered.connect(lambda checked=False, i=i: calls.append(i))
    focus(app, first, first.editor)
    QTest.keyClick(first.editor, Qt.Key.Key_F7)
    assert calls == [0]
    focus(app, second, second.editor)
    QTest.keyClick(second.editor, Qt.Key.Key_F7)
    assert calls == [0, 1]
    assert first.text_undo_target() is None
    # A modal dialog owns its shortcuts; session F7 must not run behind it.
    modal = QDialog(second.panels.panels['editor'].host)
    modal.setModal(True)
    modal.show()
    app.processEvents()
    QTest.keyClick(modal, Qt.Key.Key_F7)
    assert calls == [0, 1]
    modal.close()
    destination = tmp_path/'saved.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(destination), ''))
    focus(app, first, first.editor)
    QTest.keyClick(first.editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert Project.load(destination).source_text == first.editor.toPlainText()
    assert not first.document_dirty
    # Returning the editor keeps undo and selection in the same document.
    first.editor.selectAll()
    selection = first.editor.textCursor().selectedText()
    first.panels.dock('editor')
    assert first.editor.textCursor().selectedText() == selection
    first.editor.undo()
    assert first.editor.toPlainText() == before


def test_output_focus_cannot_undo_the_circuit(app, windows):
    view = windows()
    view.add_part(kind='probe')
    history = view.history.index
    view.panels.detach('build')
    focus(app, view, view.logs)
    assert not view.undo_action.isEnabled()
    view.undo()
    assert view.history.index == history
    view.panels.detach('serial')
    focus(app, view, view.serial_input)
    view.serial_input.insert('command')
    view.undo()
    assert view.serial_input.text() == '' and view.history.index == history
    view.select_workspace('Circuit')
    focus(app, view, view.view)
    view.undo()
    assert view.history.index == history - 1


def test_controls_context_menus_and_dynamic_inspector_selector(app, windows):
    view = windows()
    manager = view.panels
    view.select_workspace('Debug')
    view.inspector_choice.setCurrentIndex(view.inspector_choice.findData('memory'))
    assert view.inspector.currentWidget() is manager.panels['memory'].widget
    button = view.inspector_header.itemAt(1).widget()
    button.click()
    assert manager.panels['memory'].host is not None
    assert view.inspector_choice.findData('memory') < 0
    manager.dock('memory')
    assert view.inspector_choice.currentData() == 'memory'
    view.tabs.setCurrentWidget(view.plotter)
    view.tabs.cornerWidget().click()
    assert manager.panels['plot'].host is not None
    view.editor_pane.title.customContextMenuRequested.emit(QPoint(1, 1))
    menu = manager.context_menu
    menu.actions()[0].trigger()
    menu.close()
    assert manager.panels['editor'].host is not None
    editor_menu = view.output_context_menu(view.editor, QPoint(1, 1))
    assert any(a.text() == 'Dock back' for a in editor_menu.actions())
    editor_menu.close()
    view.tabs.setCurrentWidget(view.waveform)
    bar = view.tabs.tabBar()
    keyboard_menu = QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(-1, -1),
                                     bar.mapToGlobal(QPoint(0, 0)))
    QApplication.sendEvent(bar, keyboard_menu)
    menu = manager.context_menu
    menu.actions()[0].trigger()
    menu.close()
    assert manager.panels['logic'].host is not None
    assert len([a for a in view.panels_menu.actions() if a.menu()]) == 15


def test_preferences_restore_panels_selection_and_geometry(app, windows, tmp_path):
    settings = QSettings(str(tmp_path/'shared.ini'), QSettings.Format.IniFormat)
    view = windows(settings=settings)
    view.select_workspace('Debug')
    for key in ('editor', 'plot', 'memory'):
        view.panels.detach(key)
        view.panels.panels[key].host.resize(740, 520)
    view.panels.reveal('bus')
    view.panels.reveal('pio')
    view._closing = True
    view.close()
    app.processEvents()
    reopened = windows(settings=settings)
    assert reopened.current_workspace == 'Debug'
    assert {p.key for p in reopened.panels.panels.values() if p.host} == {'editor', 'plot', 'memory'}
    assert reopened.panels.selected(reopened.panels.output_home) == 'bus'
    assert reopened.panels.selected(reopened.panels.inspector_home) == 'pio'
    for key in ('editor', 'plot', 'memory'):
        panel = reopened.panels.panels[key]
        assert panel.widget.isVisible() and panel.host.width() >= 700
    reopened.panels.dock_all()
    reopened.save_preferences()
    assert settings.value('panels/floating') == []


@pytest.mark.parametrize('floating', [42, 'unknown', ['unknown', 'editor']])
def test_old_and_malformed_preferences_remain_usable(app, windows, tmp_path, floating):
    settings = QSettings(str(tmp_path/'invalid.ini'), QSettings.Format.IniFormat)
    settings.setValue('version', 1)
    settings.setValue('workspace', 'Code')
    settings.setValue('panels/version', 1)
    settings.setValue('panels/floating', floating)
    settings.setValue('panels/geometry/editor', QByteArray(b'not a geometry'))
    settings.setValue('panels/selected/output', ['invalid'])
    settings.setValue('panels/editor_splitter', 'invalid')
    view = windows(settings=settings)
    assert view.current_workspace == 'Code'
    if floating == ['unknown', 'editor']:
        assert view.editor.isVisible() and view.panels.placeholder.isVisible()
    else:
        assert view.editor.isVisible() and view.panels.panels['editor'].host is None
    view.reset_layout()
    assert view.current_workspace == 'Circuit' and view.view.isVisible()


def test_screen_recovery_preserves_secondary_monitor_and_clamps_removed_screen(app, monkeypatch):
    class Screen:
        def __init__(self, rect): self.rect = rect
        def availableGeometry(self): return self.rect
    class WindowGeometry:
        def __init__(self, rect): self.rect = rect
        def frameGeometry(self): return self.rect
        def width(self): return self.rect.width()
        def height(self): return self.rect.height()
        def resize(self, width, height): self.rect.setWidth(width); self.rect.setHeight(height)
        def move(self, x, y): self.rect.moveTo(x, y)
    primary = Screen(QRect(0, 0, 1366, 768))
    secondary = Screen(QRect(-1280, 0, 1280, 1024))
    monkeypatch.setattr(QApplication, 'screens', lambda: [primary, secondary])
    monkeypatch.setattr(QApplication, 'primaryScreen', lambda: primary)
    window = WindowGeometry(QRect(-1200, 100, 900, 600))
    fit_on_screen(window)
    assert window.rect == QRect(-1200, 100, 900, 600)
    monkeypatch.setattr(QApplication, 'screens', lambda: [primary])
    fit_on_screen(window)
    assert primary.rect.contains(window.rect)
    window.rect = QRect(9999, -9999, 3000, 2000)
    fit_on_screen(window)
    assert primary.rect.contains(window.rect)


def test_cancelled_main_close_keeps_hosts_and_accepted_close_saves_layout(app, windows, monkeypatch):
    view = windows()
    view.panels.detach('editor')
    view.panels.detach('plot')
    host = view.panels.panels['editor'].host
    monkeypatch.setattr(view, 'confirm_unsaved', lambda: False)
    view.close()
    app.processEvents()
    assert view.isVisible() and host.isVisible() and view.thread.isRunning()
    monkeypatch.setattr(view, 'confirm_unsaved', lambda: True)
    view.close()
    wait(app, lambda: not view.thread.isRunning())
    assert view.panels.stopping and not view.isVisible()
    assert set(view.settings.value('panels/floating')) == {'editor', 'plot'}


def test_real_f7_build_f9_and_edit_during_build_in_floating_editor(app, windows):
    view = windows()
    view.panels.detach('editor')
    view.panels.detach('build')
    view.panels.detach('cpu')
    source = view.editor.toPlainText()
    focus(app, view, view.editor)
    QTest.keyClick(view.editor, Qt.Key.Key_F7)
    assert view.build_thread is not None
    view.editor.moveCursor(QTextCursor.MoveOperation.End)
    view.editor.insertPlainText('\n// newer buffer\n')
    wait(app, lambda: view.build_thread is None and not view.busy and view.loaded_name.endswith('editor.elf'), timeout=60)
    assert view.loaded_source == source and view.editor.toPlainText() != source
    assert view.editor.execution_line == 0
    view.editor.undo()
    assert view.editor.toPlainText() == source
    location = next(loc for loc in view.symbols.lines if loc.line and view.source_matches(loc.file))
    view.editor.go_to_line(location.line)
    focus(app, view, view.editor)
    QTest.keyClick(view.editor, Qt.Key.Key_F9)
    assert view.breakpoints
    QTest.keyClick(view.editor, Qt.Key.Key_F5)
    wait(app, lambda: view.state.stop.name == 'BREAKPOINT')
    assert view.editor.execution_line > 0
    assert view.panels.panels['cpu'].host.isVisible()


@pytest.mark.parametrize('python', [False, True])
def test_real_dashboard_updates_floating_tools_and_python_files(app, windows, python):
    project = Project.dashboard(python)
    if python:
        project.source_text = demo.dashboard_source().replace('while True:', 'for _ in range(1):')
    view = windows(project)
    for key in ('editor', 'serial', 'plot', 'bus', 'cpu', 'io', 'logic'):
        view.panels.detach(key)
    view.select_workspace('Circuit')
    view.adc_sliders[0][0].setValue(2048)
    if python:
        view.build_action.trigger()
        wait(app, lambda: getattr(view, 'python_status', '') == 'finished', timeout=40)
    else:
        view.run_action.trigger()
    wait(app, lambda: bool(view.plotter.latest.get('temperature')) and bool(view.bus_inspector.records) and bool(view._display_frames), timeout=40)
    if view.running:
        view.send('pause')
        wait(app, lambda: not view.running)
    assert view.plotter.latest['temperature'][-1].value == 50
    assert 'temperature:50' in view.serial.toPlainText()
    assert any(r.status == 'NACK' for r in view.bus_inspector.records)
    assert any(r.bus == 'SPI' for r in view.bus_inspector.records)
    assert view.waveform.history
    if not python:
        return
    source = view.loaded_source
    view.panels.detach('files')
    panel = view.files_panel
    if not panel.connected:
        panel.connect_button.click()
    wait(app, lambda: panel.connected and panel.pending is None and not view.files_busy)
    data = b'print("opened in floating editor")\n'
    panel.start('upload', '/floating.py', data=data)
    wait(app, lambda: panel.pending is None and not view.files_busy and not panel.refresh_pending)
    assert view.loaded_source == source
    item = next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
                if panel.tree.topLevelItem(i).text(0) == 'floating.py')
    panel.tree.setCurrentItem(item)
    panel.open_selected()
    wait(app, lambda: panel.pending is None and not view.files_busy)
    assert view.editor.toPlainText() == data.decode()
    assert view.current_workspace == 'Circuit'
    assert view.loaded_source == source and view.device_source_path == '/floating.py'


@pytest.mark.parametrize('language', ['c', 'python'])
def test_real_language_service_survives_detach_dock_and_previews(app, windows, monkeypatch, language):
    if os.environ.get('RP2040_TEST_LANGUAGE_SERVERS') != '1':
        pytest.skip('Set RP2040_TEST_LANGUAGE_SERVERS=1 for real floating-editor assistance checks')
    project = Project.micropython() if language == 'python' else Project.demo()
    view = windows(project)
    monkeypatch.delenv('RP2040_CODE_ASSIST')
    view.panels.detach('editor')
    editor, assistant = view.editor, view.assistance
    if language == 'python':
        editor.setPlainText('from machine import Pin\nimport time\npin = Pin(25, Pin.OUT)\npin.va\ntime.sleep_ms(10)\n')
        completion, definition, hover = ({'line':3, 'character':6}, {'line':0, 'character':21}, {'line':4, 'character':8})
    else:
        editor.setPlainText('#include <stdint.h>\nuint32_t counter = 25;\nvoid reset_handler(void) { counter++; }\n')
        completion, definition, hover = ({'line':2, 'character':30}, {'line':1, 'character':3}, {'line':1, 'character':3})
    assistant.restart()
    wait(app, lambda: assistant.client is not None and assistant.client.initialized and assistant.client.uri is not None, timeout=90)
    client, document = assistant.client, editor.document()
    answers, locations, hovers = [], [], []
    assistant.request('completion', answers.append, completion)
    assistant.request('definition', locations.append, definition)
    assistant.request('hover', hovers.append, hover)
    wait(app, lambda: bool(answers) and bool(locations) and bool(hovers), timeout=30)
    entries = answers[0].get('items', []) if isinstance(answers[0], dict) else answers[0]
    assert entries and locations[0] and hovers[0]
    focus(app, view, editor)
    editor.go_to_line(completion['line'] + 1, completion['character'] + 1)
    assistant.show_completion(answers[0])
    assert assistant.popup.isVisible()
    assert editor.screen().availableGeometry().intersects(assistant.popup.frameGeometry())
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert not assistant.popup.isVisible()
    location = locations[0][0] if isinstance(locations[0], list) else locations[0]
    assistant.navigate(location.get('uri', location.get('targetUri')), location.get('range', location.get('targetSelectionRange')))
    assert assistant.preview.isVisible()
    assistant.preview.close()
    view.panels.dock('editor')
    view.panels.detach('editor')
    assert assistant.client is client and editor.document() is document
    editor.appendPlainText('missing_name()' if language == 'python' else 'int invalid = missing_symbol;')
    assistant.flush()
    wait(app, lambda: any('missing_' in d['message'] for d in view.editor_pane.live_diagnostics), timeout=30)
