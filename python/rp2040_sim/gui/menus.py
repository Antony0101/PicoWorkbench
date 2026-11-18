"""Context menus shared by the desktop's copied-state inspection widgets."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu


def popup_menu(widget, menu, global_position):
    # Keep the nonblocking popup alive, and release the previous menu's actions.
    previous = getattr(widget, "_context_menu", None)
    if previous is not None:
        previous.close()
        previous.deleteLater()
    widget._context_menu = menu
    menu.popup(global_position)


def copy_action(menu, title, text):
    action = menu.addAction(title)
    action.setEnabled(bool(text))
    action.triggered.connect(lambda: QApplication.clipboard().setText(text))
    return action


def table_menu(table, position):
    menu = QMenu(table)
    item = table.itemAt(position)
    if item is not None and not item.isSelected():
        table.setCurrentItem(item)
    copy_action(menu, "Copy value", item.text() if item else "")

    def cell(row, column):
        entry = table.item(row, column)
        return entry.text() if entry else ""

    selected = {(index.row(), index.column()) for index in table.selectedIndexes()}
    rows = sorted({row for row, _ in selected})
    columns = sorted({column for _, column in selected})
    text = "\n".join("\t".join(cell(row, column) if (row, column) in selected else ""
                               for column in columns) for row in rows)
    copy_action(menu, "Copy selection", text)
    headers = [table.horizontalHeaderItem(column) for column in range(table.columnCount())]
    lines = ["\t".join(header.text() if header else "" for header in headers)]
    lines.extend("\t".join(cell(row, column) for column in range(table.columnCount()))
                 for row in range(table.rowCount()))
    copy_action(menu, "Copy table", "\n".join(lines))
    return menu


def install_table_menu(table):
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.customContextMenuRequested.connect(
        lambda position: popup_menu(table, table_menu(table, position),
                                    table.viewport().mapToGlobal(position)))


def text_menu(editor, position, actions=(), clear=False):
    # Preserve Qt's cut/copy/paste, undo and selection behavior.
    menu = editor.createStandardContextMenu(position)
    if clear:
        menu.addSeparator()
        copy_action(menu, "Copy all", editor.toPlainText())
        action = menu.addAction("Clear output")
        action.setEnabled(not editor.document().isEmpty())
        action.triggered.connect(editor.clear)
    if actions:
        menu.addSeparator()
        menu.addActions(actions)
    return menu
