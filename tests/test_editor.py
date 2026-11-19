"""Editing behavior and asynchronous language-service contracts."""
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QProcess, QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rp2040_sim.gui.editor import SourceEditor, utf16_length
from rp2040_sim.gui.editor_pane import EditorPane
from rp2040_sim.gui.lsp import LspClient
from rp2040_sim.gui.assistance import CodeAssistance, compiler_diagnostics, executable, stub_directory


def wait(app, condition, timeout=12):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        QTest.qWait(5)
        if time.monotonic() > deadline:
            raise AssertionError('Editor condition timed out')


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pane(app):
    editor = SourceEditor()
    pane = EditorPane(editor)
    pane.resize(900, 650)
    pane.show()
    editor.setFocus()
    app.processEvents()
    yield pane
    pane.close()
    pane.deleteLater()
    app.processEvents()


def end(editor):
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)


@pytest.mark.parametrize('language,initial,expected', [
    ('python', 'if True:', 'if True:\n    '),
    ('c', 'void f() {', 'void f() {\n    '),
    ('python', '    print("x")', '    print("x")\n    '),
])
def test_newline_indentation_and_undo(pane, language, initial, expected):
    editor = pane.editor
    editor.set_language(language)
    editor.setPlainText(initial)
    end(editor)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == expected
    editor.undo()
    assert editor.toPlainText() == initial


def test_pairing_selection_braces_readonly_and_literal_context(pane):
    editor = pane.editor
    QTest.keyClicks(editor, '{}')
    assert editor.toPlainText() == '{}'
    editor.go_to_line(1, 2)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == '{\n    \n}'
    assert editor.position() == {'line': 1, 'character': 4}
    editor.setPlainText('value')
    editor.selectAll()
    QTest.keyClicks(editor, '(')
    assert editor.toPlainText() == '(value)'
    editor.undo()
    assert editor.toPlainText() == 'value'
    editor.setPlainText('"text')
    end(editor)
    QTest.keyClicks(editor, '(')
    assert editor.toPlainText() == '"text('
    editor.setPlainText('')
    QTest.keyClicks(editor, '(')
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == ''
    editor.setReadOnly(True)
    QTest.keyClicks(editor, '{}')
    editor.toggle_comment()
    assert editor.toPlainText() == ''


def test_multiline_indent_comment_and_undo(pane):
    editor = pane.editor
    original = 'one\n  two\nthree'
    editor.setPlainText(original)
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == '    one\n      two\n    three'
    editor.undo()
    assert editor.toPlainText() == original
    editor.selectAll()
    editor.toggle_comment()
    assert editor.toPlainText() == '// one\n  // two\n// three'
    editor.undo()
    assert editor.toPlainText() == original
    editor.set_language('python')
    editor.selectAll()
    editor.toggle_comment()
    assert editor.toPlainText() == '# one\n  # two\n# three'
    editor.selectAll()
    editor.toggle_comment()
    assert editor.toPlainText() == original
    editor.setPlainText('    one\n    two\nthree')
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(16, QTextCursor.MoveMode.KeepAnchor)  # selection ends at start of third line
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key.Key_Backtab)
    assert editor.toPlainText() == 'one\ntwo\nthree'


def test_multiline_lexing_and_unicode_offsets(pane, app):
    editor = pane.editor
    editor.setPlainText('/* comment\nint "quoted" */ int x;\nconst char *s = "// not a comment";')
    editor.highlighter.rehighlight()
    block = editor.document().findBlockByNumber(1)
    assert block.userData().excluded == [(0, 15)]
    assert editor.in_literal(block, 4)
    assert not editor.in_literal(block, 16)
    editor.set_language('python')
    editor.setPlainText('s = """hello\n# string 😀\nend"""\nx = 1 # comment')
    editor.highlighter.rehighlight()
    assert editor.document().findBlockByNumber(1).userState() == 3
    assert editor.document().findBlockByNumber(2).userState() == 0
    editor.go_to_line(2, 12)
    assert editor.position()['character'] == 11
    editor.setPlainText('😀x = 1')
    assert editor.apply_text_edits([{'range': {'start': {'line':0,'character':2}, 'end': {'line':0,'character':3}}, 'newText':'pin'}])
    assert editor.toPlainText() == '😀pin = 1'
    editor.undo()
    assert editor.toPlainText() == '😀x = 1'


def test_completion_with_include_keeps_caret_at_insert_and_undo_is_atomic(pane):
    editor = pane.editor
    original = '\nint main() { gp; }'
    editor.setPlainText(original)
    editor.apply_text_edits([
        {'range': {'start': {'line':1,'character':13}, 'end': {'line':1,'character':15}}, 'newText':'gpio_init'},
        {'range': {'start': {'line':0,'character':0}, 'end': {'line':0,'character':0}}, 'newText':'#include "pico/stdlib.h"\n'},
    ])
    assert editor.toPlainText() == '#include "pico/stdlib.h"\n\nint main() { gpio_init; }'
    assert editor.position() == {'line':2,'character':22}
    editor.undo()
    assert editor.toPlainText() == original


def test_search_replace_options_unicode_and_invalid_patterns(pane):
    editor, find = pane.editor, pane.find_bar
    editor.setPlainText('😀Pin pin pins\npin')
    find.open(True)
    find.find_text.setText('pin')
    assert len(find.matches) == 4
    find.words.setChecked(True)
    assert len(find.matches) == 3
    find.case.setChecked(True)
    assert len(find.matches) == 2
    find.case.setChecked(False)
    find.replace_text.setText('LED')
    find.replace_all()
    assert editor.toPlainText() == '😀LED LED pins\nLED'
    editor.undo()
    assert editor.toPlainText() == '😀Pin pin pins\npin'
    find.regex.setChecked(True)
    find.find_text.setText('(pin)')
    find.replace_text.setText(r'\1_25')
    find.replace_all()
    assert editor.toPlainText() == '😀Pin_25 pin_25 pins\npin_25'
    editor.undo()
    find.replace_text.setText(r'\9')
    find.replace_all()
    assert 'Invalid replacement' in find.message.text()
    assert editor.toPlainText() == '😀Pin pin pins\npin'
    find.find_text.setText('[')
    assert 'Invalid regular expression' in find.message.text()
    find.replace_all()
    assert 'Invalid regular expression' in find.message.text()
    assert not editor.search_ranges
    find.dismiss()
    assert find.isHidden()


def test_annotations_coexist_and_brackets_ignore_literals(pane):
    editor = pane.editor
    editor.setPlainText('call("(", x)')
    editor.highlighter.rehighlight()
    editor.go_to_line(1, 6)
    editor.set_execution(1)
    editor.search_ranges = [(10, 11)]
    editor.diagnostics = [{'severity':1, 'range': {'start':{'line':0,'character':10}, 'end':{'line':0,'character':11}}}]
    editor.update_annotations()
    assert len(editor.extraSelections()) >= 4
    editor.go_to_line(1, 5)
    matches = editor.bracket_selections()
    assert len(matches) == 2
    assert sorted(s.cursor.selectionStart() for s in matches) == [4, 11]


@pytest.fixture
def fake_server(tmp_path):
    script = tmp_path / 'server.py'
    script.write_text('''import json,sys,time,threading,os
lock=threading.Lock()
def send(value,split=False):
 data=json.dumps(value).encode()
 packet=("Content-Length: %d\\r\\n\\r\\n"%len(data)).encode()+data
 with lock:
  if split:
   for chunk in (packet[:9],packet[9:24],packet[24:]):
    sys.stdout.buffer.write(chunk);sys.stdout.buffer.flush();time.sleep(.01)
  else:
   sys.stdout.buffer.write(packet);sys.stdout.buffer.flush()
while True:
 line=sys.stdin.buffer.readline()
 if not line:break
 size=int(line.decode().split(":",1)[1]);sys.stdin.buffer.readline()
 message=json.loads(sys.stdin.buffer.read(size))
 method=message.get('method');number=message.get('id')
 if method=='initialize':
  send({'id':number,'result':{'capabilities':{'textDocumentSync':2}}},True)
 elif method=='textDocument/completion':
  value={'id':number,'result':[{'label':'Pin','textEdit':{'range':{'start':{'line':0,'character':2},'end':{'line':0,'character':3}},'newText':'Pin'}}]}
  threading.Timer(.15,lambda value=value:send(value)).start()
 elif method=='testing/diagnostics':
  send({'method':'textDocument/publishDiagnostics','params':message['params']})
 elif method=='testing/crash':os._exit(3)
 elif method=='shutdown':send({'id':number,'result':None})
 elif method=='exit':break
 elif method=='testing/echo':send({'id':number,'result':message['params']})
''')
    return script


@pytest.fixture
def client(app, fake_server, tmp_path):
    client = LspClient(timeout_ms=300)
    client.start(sys.executable, [str(fake_server)], tmp_path.as_uri())
    wait(app, lambda: client.initialized)
    client.open_document((tmp_path/'main.py').as_uri(), 'python', '😀x')
    yield client
    client.dispose()
    client.deleteLater()
    app.processEvents()


def test_lsp_fragmentation_utf16_sync_stale_replies_and_diagnostics(client, app):
    replies, diagnostics = [], []
    client.diagnostics.connect(diagnostics.append)
    client.request('textDocument/completion', {}, replies.append)
    client.change_document('😀xy')
    QTest.qWait(250)
    app.processEvents()
    assert replies == []
    client.notify('testing/diagnostics', {'uri': client.uri, 'version': 1, 'diagnostics':[{'message':'stale'}]})
    client.notify('testing/diagnostics', {'uri': client.uri, 'version': 2, 'diagnostics':[{'message':'current'}]})
    wait(app, lambda: bool(diagnostics))
    assert len(diagnostics) == 1 and diagnostics[0]['diagnostics'][0]['message'] == 'current'
    client.request('testing/echo', {'unicode':'😀'}, replies.append)
    wait(app, lambda: bool(replies))
    assert replies == [{'unicode':'😀'}]


def test_lsp_timeout_crash_and_clean_shutdown(client, app):
    messages = []
    client.status.connect(messages.append)
    client.request('testing/timeout', {}, lambda value: pytest.fail('Timed-out request received a response'))
    wait(app, lambda: any('timed out' in value for value in messages))
    client.notify('testing/crash')
    wait(app, lambda: client.process.state() == QProcess.ProcessState.NotRunning)
    assert any('stopped' in value for value in messages)


def test_lsp_clean_shutdown(client, app):
    client.stop()
    wait(app, lambda: client.process.state() == QProcess.ProcessState.NotRunning)
    assert client.process.exitCode() == 0
    assert not client.pending


def test_moving_caret_discards_pending_completion(client, pane, app, tmp_path):
    editor = pane.editor
    editor.setPlainText('😀x')
    end(editor)
    assistant = CodeAssistance(editor, pane, QSettings(str(tmp_path/'prefs.ini'), QSettings.IniFormat))
    assistant.client = client
    answers = []
    try:
        assistant.request('completion', answers.append)
        editor.go_to_line(1)
        QTest.qWait(250)
        app.processEvents()
        assert not answers and editor.toPlainText() == '😀x'
    finally:
        assistant.client = None  # The client fixture owns this process.
        assistant.dispose()


def test_compiler_locations_and_stale_build_annotations(pane, app, tmp_path):
    source = 'int main() { /* 😀 */ invalid; }'
    column = len(source[:source.index('invalid')].encode()) + 1
    log = f'/tmp/editor.c:1:{column}: error: invalid is undeclared'
    items = compiler_diagnostics(log, source)
    assert items[0]['range']['start']['character'] == utf16_length(source[:source.index('invalid')])
    editor = pane.editor
    editor.setPlainText(source)
    assistant = CodeAssistance(editor, pane, QSettings(str(tmp_path/'prefs.ini'), QSettings.IniFormat))
    try:
        assistant.compiled(log, source)
        assert editor.diagnostics and pane.problems.topLevelItemCount() == 1
        editor.insertPlainText('// changed\n')
        assert not editor.diagnostics
        assert pane.build_diagnostics[0]['stale']
        editor.undo()
        assert editor.diagnostics
    finally:
        assistant.dispose()


def test_completion_keyboard_acceptance_stale_edit_and_readonly_preview(pane, app, tmp_path):
    editor = pane.editor
    editor.setPlainText('pi')
    end(editor)
    assistant = CodeAssistance(editor, pane, QSettings(str(tmp_path/'prefs.ini'), QSettings.IniFormat))
    try:
        assistant.show_completion([{'label':'pin', 'insertText':'pin'}])
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        assert editor.toPlainText() == 'pin'
        editor.undo()
        assert editor.toPlainText() == 'pi'
        end(editor)
        assistant.show_completion([{'label':'pin', 'insertText':'pin'}])
        item = assistant.popup.currentItem()
        editor.insertPlainText('_new')
        assistant.accept_completion(item)
        assert editor.toPlainText() == 'pi_new'
        definition = tmp_path/'machine.pyi'
        definition.write_text('class Pin:\n    OUT: int\n')
        assistant.navigate(definition.as_uri(), {'start':{'line':0,'character':6}})
        app.processEvents()
        preview = assistant.preview.findChild(SourceEditor)
        assert preview.isReadOnly()
        QTest.keyClicks(preview, 'changes')
        assert preview.toPlainText() == definition.read_text()
        assert editor.toPlainText() == 'pi_new'
        assistant.preview.close()
    finally:
        assistant.dispose()


def test_missing_tools_leave_editing_available(pane, app, tmp_path, monkeypatch):
    monkeypatch.delenv('RP2040_CODE_ASSIST', raising=False)
    prefs = QSettings(str(tmp_path/'prefs.ini'), QSettings.IniFormat)
    prefs.setValue('editor/clangd', str(tmp_path/'absent-clangd'))
    assistant = CodeAssistance(pane.editor, pane, prefs)
    try:
        assistant.set_context('c', 'direct')
        assistant.activate(True)
        assert 'not found' in pane.service_status.text()
        QTest.keyClicks(pane.editor, 'int pin;')
        assert pane.editor.toPlainText() == 'int pin;'
    finally:
        assistant.dispose()


def test_code_workspace_output_keyboard_and_preferences(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from rp2040_sim.gui.app import Window
    monkeypatch.setenv('RP2040_CODE_ASSIST', '0')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: QMessageBox.StandardButton.Discard)
    prefs = QSettings(str(tmp_path/'prefs.ini'), QSettings.IniFormat)
    window = Window(settings=prefs)
    try:
        window.resize(1366, 768)
        window.show()
        wait(app, lambda: window.state is not None)
        window.select_workspace('Code')
        app.processEvents()
        assert window.top_splitter.isHidden()
        assert window.editor.isVisible()
        original = window.editor.toPlainText()
        QTest.keyClick(window.editor, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
        assert window.editor_pane.find_bar.isVisible()
        assert window.editor_pane.find_bar.replace_row.isVisible()
        QTest.keyClick(window.editor_pane.find_bar.find_text, Qt.Key.Key_Escape)
        assert window.editor_pane.find_bar.isHidden()
        window.output_action.setChecked(True)
        window.tabs.setCurrentWidget(window.logs)
        app.processEvents()
        assert window.editor.isVisible() and window.tabs.isVisible()
        window.editor.go_to_line(8, 5)
        position = window.editor.textCursor().position()
        for mode in ('Debug', 'Circuit', 'Code'):
            window.select_workspace(mode)
        assert window.editor.textCursor().position() == position
        assert window.editor.toPlainText() == original
        assert window.width() == 1366 and window.height() == 768
        window.editor_pane.indentation.setCurrentIndex(0)
        window.editor_pane.pairs.setChecked(False)
        window.save_preferences()
        window.editor_pane.indentation.setCurrentIndex(1)
        window.editor_pane.pairs.setChecked(True)
        window.restore_preferences()
        assert window.editor.indent_width == 2 and not window.editor.auto_pairs
        assert window.top_splitter.isHidden()
        assert window.width() <= 1366 and window.height() <= 768  # restore clamps to the available screen
    finally:
        window._closing = True
        window.close()
        app.processEvents()


def test_build_snapshot_and_current_compiler_problems(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from rp2040_sim.build import compiler
    from rp2040_sim.gui.app import Window
    try:
        compiler()
    except RuntimeError as error:
        pytest.skip(str(error))
    monkeypatch.setenv('RP2040_CODE_ASSIST', '0')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: QMessageBox.StandardButton.Discard)
    window = Window(settings=QSettings(str(tmp_path/'prefs.ini'), QSettings.IniFormat))
    try:
        window.show()
        wait(app, lambda: window.state is not None and not window.busy)
        window.select_workspace('Code')
        original = window.editor.toPlainText()
        QTest.keyClick(window.editor, Qt.Key.Key_F7)
        window.editor.insertPlainText('// newer buffer\n')
        newer = window.editor.toPlainText()
        wait(app, lambda: window.build_thread is None and not window.busy and window.loaded_name.endswith('editor.elf'))
        assert window.loaded_source == original
        assert window.editor.toPlainText() == newer
        assert not window.source_matches('editor.c')
        window.editor.setPlainText('void reset_handler(void) { missing_name; }')
        QTest.keyClick(window.editor, Qt.Key.Key_F7)
        wait(app, lambda: window.build_thread is None)
        assert any('missing_name' in item['message'] for item in window.editor_pane.build_diagnostics)
        assert window.editor.diagnostics
        assert window.editor.isVisible() and window.tabs.isVisible()
        window.editor.set_execution(1)
        window.editor.insertPlainText('// newer again\n')
        assert window.editor.execution_line == 0 and not window.editor.diagnostics
        assert all(item['stale'] for item in window.editor_pane.build_diagnostics)
    finally:
        window._closing = True
        window.close()
        app.processEvents()


@pytest.mark.parametrize('language,startup', [('c','direct'), ('c','sdk'), ('python','rom')])
def test_real_language_services(pane, app, tmp_path, monkeypatch, language, startup):
    """Opt-in evidence uses real compiler headers and matching firmware stubs."""
    if os.environ.get('RP2040_TEST_LANGUAGE_SERVERS') != '1':
        pytest.skip('Set RP2040_TEST_LANGUAGE_SERVERS=1 to exercise local language servers')
    monkeypatch.delenv('RP2040_CODE_ASSIST', raising=False)
    name = 'clangd' if language == 'c' else 'pyright-langserver'
    assert executable(name), f'{name} required for delivery validation'
    editor = pane.editor
    if language == 'python':
        assert stub_directory()
        text = 'from machine import Pin\nimport time\npin = Pin(25, Pin.OUT)\npin.va\ntime.sleep_ms(10)\n'
        position = {'line': 3, 'character': 6}
    elif startup == 'sdk':
        text = '#include "pico/stdlib.h"\nint main(void) { gpio_init(25); gpio_put(25, 1); return 0; }\n'
        position = {'line': 1, 'character': 22}
    else:
        text = '#include <stdint.h>\nuint32_t pin = 25;\nvoid reset_handler(void) { pin++; }\n'
        position = {'line': 2, 'character': 27}
    editor.set_language(language)
    editor.setPlainText(text)
    assistant = CodeAssistance(editor, pane, QSettings(str(tmp_path/'prefs.ini'), QSettings.IniFormat))
    try:
        assistant.set_context(language, startup)
        assistant.activate(True)
        wait(app, lambda: assistant.client is not None and assistant.client.initialized and assistant.client.uri is not None, timeout=90)
        answers = []
        assistant.request('completion', answers.append, position)
        wait(app, lambda: bool(answers), timeout=30)
        items = answers[0].get('items', []) if isinstance(answers[0], dict) else answers[0]
        assert items, pane.service_status.text() + '\n' + assistant.client.error_log
        if language == 'python':
            assert any('value' in item['label'] for item in items)
            locations, hovers = [], []
            assistant.request('definition', locations.append, {'line':0,'character':21})
            assistant.request('hover', hovers.append, {'line':4,'character':8})
            wait(app, lambda: bool(locations) and bool(hovers), timeout=30)
            assert locations[0] and hovers[0]
            for point, expected in [({'line':0,'character':21}, 'Pin'), ({'line':4,'character':11}, 'sleep_ms')]:
                completions = []
                assistant.request('completion', completions.append, point)
                wait(app, lambda: bool(completions), timeout=30)
                entries = completions[0].get('items', []) if isinstance(completions[0], dict) else completions[0]
                assert any(expected in entry['label'] for entry in entries)
            assert not any('could not be resolved' in d['message'] for d in pane.live_diagnostics)
            editor.setPlainText(text + '\nmissing_name()\n')
            assistant.flush()
            wait(app, lambda: any('missing_name' in d['message'] for d in pane.live_diagnostics), timeout=30)
        else:
            editor.setPlainText(text + '\nint invalid = missing_symbol;\n')
            assistant.flush()
            wait(app, lambda: any('missing_symbol' in d['message'] for d in pane.live_diagnostics), timeout=30)
    finally:
        assistant.dispose()
