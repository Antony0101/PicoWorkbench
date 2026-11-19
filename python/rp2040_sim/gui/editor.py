"""Native single-document editor. All positions at the widget boundary are UTF-16."""
import keyword
import re

from PySide6.QtCore import QRect, QSize, Qt, Signal, QEvent
from PySide6.QtGui import (QAction, QColor, QPainter, QSyntaxHighlighter, QTextBlockUserData,
                           QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget, QToolTip


def utf16_length(text):
    return len(text.encode('utf-16-le')) // 2


def py_index(text, offset):
    """Convert a UTF-16 column to a Python string index, clamping at boundaries."""
    total = 0
    for index, char in enumerate(text):
        if total >= offset:
            return index
        total += utf16_length(char)
    return len(text)


def utf16_offsets(text):
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
    return offsets


class Tokens(QTextBlockUserData):
    def __init__(self, excluded):
        super().__init__()
        self.excluded = excluded


class Highlighter(QSyntaxHighlighter):
    language = 'c'
    COLORS = {'keyword': '#82c5fc', 'number': '#efc57e', 'string': '#84d8ae',
              'comment': '#8da6b4', 'preprocessor': '#c3a6f5'}
    C_WORDS = ('void int unsigned signed const volatile typedef static extern char short long '
               'float double if else for while do return struct union enum sizeof break continue '
               'switch case default inline restrict _Bool _Atomic bool true false NULL').split()

    def highlightBlock(self, text):
        excluded = []
        state = max(0, self.previousBlockState())
        self.setCurrentBlockState(0)

        def color(start, end, kind):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self.COLORS[kind]))
            self.setFormat(utf16_length(text[:start]), utf16_length(text[start:end]), fmt)
            if kind in ('string', 'comment'):
                excluded.append((start, end))

        # Lex strings/comments first, with block state for multiline constructs.
        index = 0
        if state:
            delimiter = {1: '*/', 2: "'''", 3: '"""'}.get(state, '')
            end = self._end(text, 0, delimiter) if delimiter else -1
            stop = len(text) if end < 0 else end + len(delimiter)
            color(0, stop, 'comment' if state == 1 else 'string')
            if end < 0:
                self.setCurrentBlockState(state)
            index = stop
        python = self.language == 'python'
        while index < len(text):
            start = index
            if (python and text[index] == '#') or (not python and text.startswith('//', index)):
                color(index, len(text), 'comment')
                break
            if not python and text.startswith('/*', index):
                end = text.find('*/', index + 2)
                index = len(text) if end < 0 else end + 2
                color(start, index, 'comment')
                if end < 0:
                    self.setCurrentBlockState(1)
                continue
            if text[index] in ('"', "'"):
                quote = text[index]
                triple = python and text.startswith(quote * 3, index)
                delimiter = quote * (3 if triple else 1)
                end = self._end(text, index + len(delimiter), delimiter)
                index = len(text) if end < 0 else end + len(delimiter)
                color(start, index, 'string')
                if triple and end < 0:
                    self.setCurrentBlockState(2 if quote == "'" else 3)
                continue
            index += 1
        self.setCurrentBlockUserData(Tokens(excluded))

        def available(start, end):
            return not any(start < b and end > a for a, b in excluded)

        words = keyword.kwlist + ['True', 'False', 'None', 'self'] if python else self.C_WORDS
        for match in re.finditer(r'\b(?:' + '|'.join(words) + r')\b', text):
            if available(*match.span()):
                color(*match.span(), 'keyword')
        for match in re.finditer(r'\b(?:0[xX][\da-fA-F]+|0[bB][01]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b', text):
            if available(*match.span()):
                color(*match.span(), 'number')
        if not python:
            match = re.match(r'\s*#\s*\w+', text)
            if match and available(*match.span()):
                color(*match.span(), 'preprocessor')

    @staticmethod
    def _end(text, start, delimiter):
        while delimiter:
            index = text.find(delimiter, start)
            if index < 0:
                return -1
            escapes, before = 0, index - 1
            while before >= 0 and text[before] == '\\':
                escapes += 1
                before -= 1
            if escapes % 2 == 0:
                return index
            start = index + 1
        return -1


class Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setAccessibleName('Line numbers and breakpoint markers')
        self.setToolTip('Left-click a line number or press F9 to toggle a mapped C breakpoint')

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        block = self.editor.firstVisibleBlock()
        while block.isValid():
            top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
            if top <= event.position().y() < top + self.editor.blockBoundingRect(block).height():
                self.editor.breakpoint_requested.emit(block.blockNumber() + 1)
                return
            block = block.next()


class SourceEditor(QPlainTextEdit):
    breakpoint_requested = Signal(int)
    completion_requested = Signal()
    signature_requested = Signal()
    definition_requested = Signal()
    hover_requested = Signal(object)
    escape_requested = Signal()

    def __init__(self):
        super().__init__()
        self.gutter = Gutter(self)
        self.break_lines = set()
        self.execution_line = 0
        self.indent_width = 4
        self.auto_pairs = True
        self.search_ranges = []
        self.diagnostics = []
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setAccessibleName('Source code editor')
        self.blockCountChanged.connect(self.update_margins)
        self.updateRequest.connect(self.update_gutter)
        self.cursorPositionChanged.connect(self.update_annotations)
        self.highlighter = Highlighter(self.document())
        self.update_margins()
        self.editor_actions = []
        for title, shortcut, callback in [
            ('Indent selection', 'Tab', lambda: self.indent(False)),
            ('Unindent selection', 'Shift+Tab', lambda: self.indent(True)),
            ('Toggle line comment', 'Ctrl+/', self.toggle_comment),
            ('Complete code', 'Ctrl+Space', self.completion_requested.emit),
            ('Go to definition', 'F12', self.definition_requested.emit),
        ]:
            action = QAction(title, self)
            # Tab is handled below so plain Tab inserts at the current column.
            if shortcut not in ('Tab', 'Shift+Tab'):
                action.setShortcut(shortcut)
                action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(callback)
            self.addAction(action)
            self.editor_actions.append(action)

    def set_language(self, language):
        if language != self.highlighter.language:
            self.highlighter.language = language
            self.highlighter.rehighlight()

    def gutter_width(self):
        return 22 + self.fontMetrics().horizontalAdvance('9') * max(2, len(str(self.blockCount())))

    def update_margins(self, *_):
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * self.indent_width)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self.gutter.update()

    def update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_margins()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor('#182a35'))
        block = self.firstVisibleBlock()
        while block.isValid():
            top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            if top > event.rect().bottom():
                break
            line = block.blockNumber() + 1
            if block.isVisible():
                painter.setPen(QColor('#d4e4ef' if line == self.execution_line else '#9bb1bf'))
                painter.drawText(18, top, self.gutter.width() - 22, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, str(line))
                if line in self.break_lines:
                    painter.setBrush(QColor('#fc6e7c'))
                    painter.drawEllipse(3, top + 3, 10, 10)
                if line == self.execution_line:
                    painter.drawText(2, top + self.fontMetrics().ascent(), '▶')
            block = block.next()

    def set_execution(self, line):
        if line != self.execution_line:
            self.execution_line = line
            self.update_annotations()
            self.gutter.update()

    def position(self, value=None):
        cursor = self.textCursor() if value is None else QTextCursor(self.document())
        if value is not None:
            cursor.setPosition(value)
        return {'line': cursor.blockNumber(), 'character': cursor.positionInBlock()}

    def offset(self, position):
        block = self.document().findBlockByNumber(max(0, position.get('line', 0)))
        if not block.isValid():
            return self.document().characterCount() - 1
        return block.position() + min(max(0, position.get('character', 0)), block.length() - 1)

    def go_to_line(self, line, column=1):
        cursor = QTextCursor(self.document())
        cursor.setPosition(self.offset({'line': max(0, line - 1), 'character': max(0, column - 1)}))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    def in_literal(self, block, column):
        data = block.userData()
        index = py_index(block.text(), column)
        return bool(data and any(a <= index < b for a, b in data.excluded))

    def selection(self, start, end, color, *, underline=False, full=False):
        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(self.document())
        limit = self.document().characterCount() - 1
        selection.cursor.setPosition(min(max(0, start), limit))
        selection.cursor.setPosition(min(max(0, end), limit), QTextCursor.MoveMode.KeepAnchor)
        if underline:
            selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            selection.format.setUnderlineColor(QColor(color))
        else:
            selection.format.setBackground(QColor(color))
        if full:
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        return selection

    def update_annotations(self):
        cursor = self.textCursor()
        selections = [self.selection(cursor.block().position(), cursor.block().position(), '#152b36', full=True)]
        if self.execution_line:
            block = self.document().findBlockByNumber(self.execution_line - 1)
            if block.isValid():
                selections.append(self.selection(block.position(), block.position(), '#274b49', full=True))
        selections.extend(self.selection(a, b, '#514c2d') for a, b in self.search_ranges[:2000])
        for diagnostic in self.diagnostics:
            value = diagnostic.get('range', {})
            a = self.offset(value.get('start', {}))
            b = self.offset(value.get('end', {}))
            selections.append(self.selection(a, max(a + 1, b),
                '#fc6e7c' if diagnostic.get('severity', 1) == 1 else '#efc57e', underline=True))
        selections.extend(self.bracket_selections())
        self.setExtraSelections(selections)

    def bracket_selections(self):
        cursor = self.textCursor()
        local = cursor.block().text()
        local_index = py_index(local, cursor.positionInBlock())
        if not ((local_index and local[local_index - 1] in '()[]{}') or
                (local_index < len(local) and local[local_index] in '()[]{}')):
            return []
        text = self.toPlainText()
        offsets = utf16_offsets(text)
        index = py_index(text, cursor.position())
        brackets = '()[]{}'
        if index and text[index - 1] in brackets:
            index -= 1
        elif index >= len(text) or text[index] not in brackets:
            return []
        absolute = offsets[index]
        block = self.document().findBlock(absolute)
        if self.in_literal(block, absolute - block.position()):
            return []
        char = text[index]
        direction = 1 if char in '([{' else -1
        match = dict(zip('()[]{}', ')(][}{'))[char]
        depth = 1
        # Bound work on every cursor movement; navigation must stay immediate.
        stop = min(len(text), index + 10001) if direction > 0 else max(-1, index - 10001)
        for other in range(index + direction, stop, direction):
            if text[other] not in (char, match):
                continue
            offset = offsets[other]
            block = self.document().findBlock(offset)
            if self.in_literal(block, offset - block.position()):
                continue
            depth += 1 if text[other] == char else -1
            if depth == 0:
                return [self.selection(absolute, absolute + 1, '#355b64'),
                        self.selection(offset, offset + 1, '#355b64')]
        return [self.selection(absolute, absolute + 1, '#793f4b')]

    def selected_blocks(self):
        cursor = self.textCursor()
        first = self.document().findBlock(cursor.selectionStart())
        end = cursor.selectionEnd()
        last = self.document().findBlock(end - 1 if cursor.hasSelection() and end > first.position() else end)
        blocks = []
        while first.isValid() and first.blockNumber() <= last.blockNumber():
            blocks.append(first)
            first = first.next()
        return blocks

    def indent(self, backward=False):
        if self.isReadOnly():
            return
        cursor = self.textCursor()
        if not backward and not cursor.hasSelection():
            cursor.insertText(' ' * (self.indent_width - cursor.positionInBlock() % self.indent_width))
            return
        edits = []
        for block in self.selected_blocks():
            text = block.text()
            count = 1 if text.startswith('\t') else min(len(text) - len(text.lstrip(' ')), self.indent_width)
            edits.append((block.position(), count if backward else 0, '' if backward else ' ' * self.indent_width))
        self.edit_offsets(edits)

    def toggle_comment(self):
        if self.isReadOnly():
            return
        prefix = '# ' if self.highlighter.language == 'python' else '// '
        blocks = [block for block in self.selected_blocks() if block.text().strip()]
        remove = bool(blocks) and all(block.text().lstrip().startswith(prefix.rstrip()) for block in blocks)
        edits = []
        for block in blocks:
            text = block.text()
            leading = len(text) - len(text.lstrip())
            start = block.position() + utf16_length(text[:leading])
            count = len(prefix.rstrip())
            if remove and text[leading + count:leading + count + 1] == ' ':
                count += 1
            edits.append((start, count if remove else 0, '' if remove else prefix))
        self.edit_offsets(edits)

    def edit_offsets(self, edits):
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for start, count, text in sorted(edits, reverse=True):
            cursor.setPosition(start)
            cursor.setPosition(start + count, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(text)
        cursor.endEditBlock()

    def apply_text_edits(self, edits):
        if self.isReadOnly() or not edits:
            return False
        offsets = [(self.offset(e['range']['start']), self.offset(e['range']['end']), e['newText']) for e in edits]
        ordered = sorted(offsets)
        if any(a > b or (index and a < ordered[index - 1][1]) for index, (a, b, _) in enumerate(ordered)):
            return False
        cursor = self.textCursor()
        caret = QTextCursor(self.document())
        caret.setPosition(offsets[0][0])
        cursor.beginEditBlock()
        for start, end, text in reversed(ordered):
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(caret)
        return True

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key.Key_Escape:
            QToolTip.hideText()
            self.escape_requested.emit()
            return
        if self.isReadOnly():
            return super().keyPressEvent(event)
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and not modifiers & Qt.KeyboardModifier.ControlModifier:
            self.indent(key == Qt.Key.Key_Backtab or bool(modifiers & Qt.KeyboardModifier.ShiftModifier))
            return
        cursor = self.textCursor()
        text = cursor.block().text()
        index = py_index(text, cursor.positionInBlock())
        before, after = text[:index], text[index:]
        plain = not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        literal = self.in_literal(cursor.block(), max(0, cursor.positionInBlock() - 1))
        if plain and key == Qt.Key.Key_Backspace and not cursor.hasSelection() and before and not before.strip():
            count = len(before) % self.indent_width or self.indent_width
            cursor.beginEditBlock()
            for _ in range(min(count, len(before))):
                cursor.deletePreviousChar()
            cursor.endEditBlock()
            return
        if plain and event.text() == '}' and self.highlighter.language == 'c' and not before.strip() and before and not literal:
            cursor.beginEditBlock()
            for _ in range(min(self.indent_width, len(before))):
                cursor.deletePreviousChar()
            cursor.insertText('}')
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return
        if plain and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            leading = re.match(r'[ \t]*', before).group()
            extra = before.rstrip().endswith(':' if self.highlighter.language == 'python' else '{') and not literal
            cursor.beginEditBlock()
            cursor.insertText('\n' + leading + (' ' * self.indent_width if extra else ''))
            if extra and self.highlighter.language == 'c' and after.lstrip().startswith('}'):
                position = cursor.position()
                cursor.insertText('\n' + leading)
                cursor.setPosition(position)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return
        pairs = {'(': ')', '[': ']', '{': '}', '"': '"', "'": "'"}
        char = event.text()
        if plain and self.auto_pairs:
            if key == Qt.Key.Key_Backspace and not cursor.hasSelection() and before and after and pairs.get(before[-1]) == after[0]:
                cursor.beginEditBlock()
                cursor.deletePreviousChar()
                cursor.deleteChar()
                cursor.endEditBlock()
                return
            # Skip an existing closer only in code, or the end of a string.
            if char and char in ')]}\"\'' and after.startswith(char) and not cursor.hasSelection():
                if not literal or char in ('"', "'") and (not before or before[-1] != '\\'):
                    cursor.movePosition(QTextCursor.MoveOperation.Right)
                    self.setTextCursor(cursor)
                    return
            if char in pairs and char and not literal and (not after or after[0].isspace() or after[0] in ')]},;'):
                selected = cursor.selectedText().replace('\u2029', '\n')
                cursor.beginEditBlock()
                start = cursor.selectionStart()
                cursor.insertText(char + selected + pairs[char])
                cursor.setPosition(start + 1)
                if selected:
                    cursor.setPosition(start + 1 + utf16_length(selected), QTextCursor.MoveMode.KeepAnchor)
                cursor.endEditBlock()
                self.setTextCursor(cursor)
                if char == '(':
                    self.signature_requested.emit()
                return
        super().keyPressEvent(event)
        if plain and char == '(':
            self.signature_requested.emit()
        elif plain and char == '.':
            self.completion_requested.emit()

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            cursor = self.cursorForPosition(event.pos())
            self.hover_requested.emit({'position': self.position(cursor.position()), 'global': event.globalPos()})
            return True
        return super().viewportEvent(event)
