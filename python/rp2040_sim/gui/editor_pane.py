"""Editor chrome, in-document search, and navigation of compiler/live diagnostics."""
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .editor import utf16_offsets


class FindBar(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.matches = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.find_text = QLineEdit()
        self.find_text.setPlaceholderText('Find in source')
        self.find_text.setAccessibleName('Find in source')
        row.addWidget(self.find_text, 1)
        self.case = QCheckBox('Case')
        self.words = QCheckBox('Word')
        self.regex = QCheckBox('Regex')
        for control in (self.case, self.words, self.regex):
            row.addWidget(control)
            control.toggled.connect(self.refresh)
        for label, callback in [('Previous', lambda: self.navigate(-1)), ('Next', self.navigate), ('Close', self.dismiss)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, fn=callback: fn())
            row.addWidget(button)
        layout.addLayout(row)
        self.replace_row = QWidget()
        replacement = QHBoxLayout(self.replace_row)
        replacement.setContentsMargins(0, 0, 0, 0)
        self.replace_text = QLineEdit()
        self.replace_text.setPlaceholderText('Replace with')
        self.replace_text.setAccessibleName('Replacement text')
        replacement.addWidget(self.replace_text, 1)
        for label, callback in [('Replace', self.replace_one), ('Replace all', self.replace_all)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            replacement.addWidget(button)
        layout.addWidget(self.replace_row)
        self.message = QLabel()
        self.message.setAccessibleName('Search results')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.find_text.textChanged.connect(self.refresh)
        self.find_text.returnPressed.connect(self.navigate)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(100)
        self.refresh_timer.timeout.connect(self.refresh)
        editor.textChanged.connect(lambda: self.refresh_timer.start() if self.isVisible() else None)
        for field in (self.find_text, self.replace_text):
            action = QAction('Close find', field)
            action.setShortcut('Escape')
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(self.dismiss)
            field.addAction(action)
        self.hide()

    def open(self, replace=False):
        self.setVisible(True)
        self.replace_row.setVisible(replace)
        selected = self.editor.textCursor().selectedText()
        if selected and '\u2029' not in selected:
            self.find_text.setText(re.escape(selected) if self.regex.isChecked() else selected)
        self.find_text.setFocus()
        self.find_text.selectAll()
        self.refresh()

    def dismiss(self):
        self.hide()
        self.editor.search_ranges = []
        self.editor.update_annotations()
        self.editor.setFocus()

    def refresh(self, *_):
        self.refresh_timer.stop()
        self.valid = True
        self.matches = []
        self.editor.search_ranges = []
        query = self.find_text.text()
        if query:
            pattern = query if self.regex.isChecked() else re.escape(query)
            if self.words.isChecked():
                pattern = r'(?<!\w)(?:' + pattern + r')(?!\w)'
            try:
                text = self.editor.toPlainText()
                self.matches = list(re.finditer(pattern, text, re.MULTILINE | (0 if self.case.isChecked() else re.IGNORECASE)))
                offsets = utf16_offsets(text)
                self.editor.search_ranges = [(offsets[m.start()], offsets[m.end()]) for m in self.matches]
                self.message.setText(f'{len(self.matches)} matches' if self.matches else 'No matches')
            except re.error as error:
                self.valid = False
                self.message.setText(f'Invalid regular expression: {error}')
        else:
            self.message.setText('Enter text to search')
        self.editor.update_annotations()

    def navigate(self, direction=1):
        self.refresh()
        ranges = self.editor.search_ranges
        if not ranges:
            return
        cursor = self.editor.textCursor()
        if direction == -1:
            candidates = [i for i, (a, b) in enumerate(ranges) if a < cursor.selectionStart()]
            index = candidates[-1] if candidates else len(ranges) - 1
        else:
            candidates = [i for i, (a, b) in enumerate(ranges) if a >= cursor.selectionEnd() and
                          not (cursor.hasSelection() and a == cursor.selectionStart())]
            index = candidates[0] if candidates else 0
            if ranges[index][0] == ranges[index][1] == cursor.position():
                index = (index + 1) % len(ranges)
        start, end = ranges[index]
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.message.setText(f'{index + 1} of {len(ranges)} matches')

    def _replacement(self, match):
        return match.expand(self.replace_text.text()) if self.regex.isChecked() else self.replace_text.text()

    def replace_one(self):
        if self.editor.isReadOnly():
            return
        self.refresh()
        cursor = self.editor.textCursor()
        for index, (start, end) in enumerate(self.editor.search_ranges):
            if (start, end) == (cursor.selectionStart(), cursor.selectionEnd()):
                try:
                    replacement = self._replacement(self.matches[index])
                except (re.error, IndexError) as error:
                    self.message.setText(f'Invalid replacement: {error}')
                    return
                cursor.insertText(replacement)
                self.editor.setTextCursor(cursor)
                break
        self.navigate()

    def replace_all(self):
        if self.editor.isReadOnly():
            return
        self.refresh()
        if not self.valid:
            return
        try:
            edits = [(a, b - a, self._replacement(match)) for (a, b), match in zip(self.editor.search_ranges, self.matches)]
        except (re.error, IndexError) as error:
            self.message.setText(f'Invalid replacement: {error}')
            return
        self.editor.edit_offsets(edits)
        self.refresh()
        self.message.setText(f'Replaced {len(edits)} matches')


class EditorPane(QWidget):
    setup_requested = Signal()
    diagnostic_activated = Signal(object)

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.live_diagnostics = []
        self.build_diagnostics = []
        self.filename = 'firmware.c'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        self.title = QLabel('firmware.c · C')
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setAccessibleName('Source filename and language')
        header.addWidget(self.title, 1)
        self.problems_button = QPushButton('Problems · 0')
        self.problems_button.setCheckable(True)
        self.problems_button.toggled.connect(lambda checked: self.problems.setVisible(checked))
        header.addWidget(self.problems_button)
        setup = QPushButton('Code assistance…')
        setup.clicked.connect(self.setup_requested.emit)
        header.addWidget(setup)
        layout.addLayout(header)
        self.find_bar = FindBar(editor, self)
        layout.addWidget(self.find_bar)
        layout.addWidget(editor, 1)
        self.problems = QTreeWidget()
        self.problems.setColumnCount(4)
        self.problems.setHeaderLabels(['Severity', 'Origin', 'Location', 'Message'])
        self.problems.setRootIsDecorated(False)
        self.problems.setAccessibleName('Source problems')
        self.problems.setMinimumHeight(60)
        self.problems.setMaximumHeight(160)
        self.problems.itemActivated.connect(lambda item, column: self.diagnostic_activated.emit(item.data(0, Qt.ItemDataRole.UserRole)))
        self.problems.itemClicked.connect(lambda item, column: self.diagnostic_activated.emit(item.data(0, Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.problems)
        self.problems.hide()
        footer = QHBoxLayout()
        self.cursor_label = QLabel()
        footer.addWidget(self.cursor_label)
        self.indentation = QComboBox()
        self.indentation.setAccessibleName('Indentation width in spaces')
        for width in (2, 4, 8):
            self.indentation.addItem(f'{width} spaces', width)
        self.indentation.setCurrentIndex(1)
        self.indentation.currentIndexChanged.connect(self.set_indentation)
        footer.addWidget(self.indentation)
        self.pairs = QCheckBox('Pair brackets')
        self.pairs.setChecked(True)
        self.pairs.toggled.connect(lambda value: setattr(editor, 'auto_pairs', value))
        footer.addWidget(self.pairs)
        self.service_status = QLabel('Code assistance: not configured')
        self.service_status.setMinimumWidth(0)
        self.service_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.service_status.setAccessibleName('Code assistance status')
        footer.addWidget(self.service_status, 1)
        layout.addLayout(footer)
        editor.cursorPositionChanged.connect(self.update_cursor)
        editor.escape_requested.connect(self.find_bar.dismiss)
        self.update_cursor()
        self.actions = list(editor.editor_actions)
        for title, shortcut, callback in [
            ('Find in source', 'Ctrl+F', lambda: self.find_bar.open()),
            ('Replace in source', 'Ctrl+H', lambda: self.find_bar.open(True)),
            ('Go to line / column', 'Ctrl+G', self.go_to),
            ('Find next', 'F3', self.find_bar.navigate),
            ('Find previous', 'Shift+F3', lambda: self.find_bar.navigate(-1)),
        ]:
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(lambda checked=False, fn=callback: fn())
            self.addAction(action)
            self.actions.append(action)

    def set_indentation(self, *_):
        self.editor.indent_width = self.indentation.currentData() or 4
        self.editor.update_margins()

    def update_cursor(self):
        cursor = self.editor.textCursor()
        self.cursor_label.setText(f'Ln {cursor.blockNumber() + 1}, Col {cursor.positionInBlock() + 1}')

    def update_document(self, path, language, modified):
        self.filename = str(path or ('main.py' if language == 'python' else 'firmware.c'))
        title = Path(self.filename).name + (' *' if modified else '') + (' · MicroPython' if language == 'python' else ' · C')
        self.title.setText(title)
        self.title.setToolTip(self.filename + ('\nSource differs from its last source-file save; Ctrl+S saves the project separately.' if modified else ''))
        self.editor.set_language(language)

    def go_to(self):
        text, accepted = QInputDialog.getText(self, 'Go to source location', 'Line or line:column (starting at 1):')
        if accepted:
            match = re.fullmatch(r'\s*([1-9]\d*)(?::([1-9]\d*))?\s*', text)
            if match:
                self.editor.go_to_line(int(match[1]), int(match[2] or 1))
            else:
                self.service_status.setText('Enter a positive line or line:column')

    def set_diagnostics(self, origin, diagnostics):
        if origin == 'Compiler':
            self.build_diagnostics = diagnostics
        else:
            self.live_diagnostics = diagnostics
        self.problems.clear()
        combined = self.build_diagnostics + self.live_diagnostics
        self.editor.diagnostics = [d for d in combined if not d.get('external') and not d.get('stale')]
        for diagnostic in combined:
            start = diagnostic.get('range', {}).get('start', {})
            location = f"{Path(diagnostic.get('file', self.filename)).name}:{start.get('line', 0) + 1}:{start.get('character', 0) + 1}"
            item = QTreeWidgetItem([{1:'Error', 2:'Warning', 3:'Info', 4:'Hint'}.get(diagnostic.get('severity'), 'Info'),
                diagnostic.get('origin', origin) + (' · old build' if diagnostic.get('stale') else ''),
                location, diagnostic.get('message', '')])
            item.setData(0, Qt.ItemDataRole.UserRole, diagnostic)
            item.setToolTip(3, diagnostic.get('message', ''))
            self.problems.addTopLevelItem(item)
        for column in (0, 1, 2):
            self.problems.resizeColumnToContents(column)
        self.problems_button.setText(f'Problems · {len(combined)}')
        self.editor.update_annotations()
