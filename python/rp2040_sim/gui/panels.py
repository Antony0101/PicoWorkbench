"""Move existing GUI panels between their home slots and owned windows."""
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QObject, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QPushButton, QToolBar, QVBoxLayout, QWidget)


def fit_on_screen(window):
    """Keep a restored window reachable, including after a monitor is removed."""
    screens = QApplication.screens()
    if not screens:
        return
    frame = window.frameGeometry()
    screen = max(screens, key=lambda s: (frame.intersected(s.availableGeometry()).width()
                                        * frame.intersected(s.availableGeometry()).height()))
    if not any(frame.intersects(s.availableGeometry()) for s in screens):
        screen = QApplication.primaryScreen() or screens[0]
    area = screen.availableGeometry()
    window.resize(min(window.width(), area.width()), min(window.height(), area.height()))
    frame = window.frameGeometry()
    window.move(max(area.left(), min(frame.left(), area.right() - frame.width() + 1)),
                max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1)))


class TabHome:
    def __init__(self, tabs, group):
        self.tabs, self.group = tabs, group
        self.order = []

    def remove(self, panel):
        self.tabs.removeTab(self.tabs.indexOf(panel.widget))

    def insert(self, panel):
        # IDs, rather than saved indexes, survive other tools being detached.
        rank = self.order.index(panel)
        index = sum(self.tabs.indexOf(p.widget) >= 0 for p in self.order[:rank])
        self.tabs.insertTab(index, panel.widget, panel.title)

    def select(self, panel):
        self.tabs.setCurrentWidget(panel.widget)


class EditorHome:
    group = 'editor'

    def __init__(self, window):
        self.window = window
        self.slot = QWidget()
        self.layout = QVBoxLayout(self.slot)
        self.layout.setContentsMargins(0, 0, 0, 0)
        splitter = window.editor_area
        sizes = splitter.sizes()
        old = splitter.replaceWidget(0, self.slot)
        self.layout.addWidget(old)
        splitter.setSizes(sizes)

    def remove(self, panel):
        self.layout.removeWidget(panel.widget)

    def insert(self, panel):
        self.layout.addWidget(panel.widget)

    def select(self, panel):
        panel.widget.show()


@dataclass(eq=False)
class Panel:
    key: str
    title: str
    widget: QWidget
    home: object
    host: QMainWindow | None = None
    geometry: QByteArray | None = None


class FloatingPanel(QMainWindow):
    def __init__(self, manager, panel):
        super().__init__(manager.window, Qt.WindowType.Window)
        self.manager, self.panel = manager, panel
        self.setObjectName('floating-' + panel.key)
        self.setCentralWidget(panel.widget)
        # Removing a tab (or a hidden editor) sets an explicit hidden flag.
        panel.widget.show()
        toolbar = QToolBar('Panel controls', self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        dock = QAction('Dock back', self)
        dock.triggered.connect(lambda: manager.dock(panel.key))
        toolbar.addAction(dock)
        show = QAction('Show main window', self)
        show.triggered.connect(manager.show_main)
        toolbar.addAction(show)
        if panel.key == 'editor':
            toolbar.addSeparator()
            toolbar.addActions([manager.window.build_action, manager.window.pause_action])
        for action in manager.window.session_actions:
            if not action.shortcut().isEmpty():
                self.addAction(action)

    def closeEvent(self, event):
        event.ignore()
        if not self.manager.stopping:
            self.manager.dock(self.panel.key)


class PanelManager(QObject):
    def __init__(self, window, menu):
        super().__init__(window)
        self.window, self.menu = window, menu
        self.panels = {}
        self.stopping = False
        self.restoring = False
        self.output_home = TabHome(window.tabs, 'output')
        self.inspector_home = TabHome(window.inspector, 'inspector')
        self.editor_home = EditorHome(window)
        self.placeholder = QWidget(self.editor_home.slot)
        placeholder_layout = QVBoxLayout(self.placeholder)
        label = QLabel('Editor is open in a separate window')
        label.setWordWrap(True)
        placeholder_layout.addWidget(label)
        buttons = QHBoxLayout()
        for title, callback in [('Focus editor', lambda: self.reveal('editor')),
                                ('Dock back', lambda: self.dock('editor'))]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        placeholder_layout.addLayout(buttons)
        self.editor_home.layout.addWidget(self.placeholder)
        self.placeholder.hide()

    def register(self, key, widget, home, title):
        panel = Panel(key, title, widget, home)
        self.panels[key] = panel
        if isinstance(home, TabHome):
            home.order.append(panel)
        menu = self.menu.addMenu(title)
        show = menu.addAction('Show / Focus')
        show.triggered.connect(lambda: self.reveal(key))
        detach = menu.addAction('Open in separate window')
        detach.triggered.connect(lambda: self.detach(key))
        dock = menu.addAction('Dock back')
        dock.triggered.connect(lambda: self.dock(key))
        menu.aboutToShow.connect(lambda: (detach.setEnabled(panel.host is None),
                                         dock.setEnabled(panel.host is not None)))

    def register_defaults(self):
        w = self.window
        self.register('editor', w.editor_pane, self.editor_home, 'Editor')
        for home, keys in [(self.output_home, ('serial', 'logic', 'build', 'plot', 'bus', 'files')),
                           (self.inspector_home, ('cpu', 'memory', 'io', 'pio', 'dma', 'cores', 'properties', 'support'))]:
            for index, key in enumerate(keys):
                self.register(key, home.tabs.widget(index), home, home.tabs.tabText(index))
        self.menu.addSeparator()
        self.menu.addAction('Dock all panels', lambda: self.dock_all())
        self._button(w.editor_pane.header, lambda: 'editor')
        self._button(w.inspector_header, lambda: self.selected(self.inspector_home))
        output_button = self._button(None, lambda: self.selected(self.output_home))
        w.tabs.setCornerWidget(output_button, Qt.Corner.TopRightCorner)
        self._context(w.editor_pane.title, lambda _: 'editor')
        self._context(w.tabs.tabBar(), lambda pos: self.key_for_widget(
            w.tabs.widget(w.tabs.tabBar().tabAt(pos)) or w.tabs.currentWidget()))
        self._context(w.inspector_choice, lambda _: self.selected(self.inspector_home))
        w.inspector.currentChanged.connect(self.sync_inspector)
        w.inspector_choice.currentIndexChanged.connect(self.choose_inspector)
        self.sync_inspector()

    def _button(self, layout, selected):
        button = QPushButton('↗')
        button.setMaximumWidth(34)
        button.setToolTip('Open in separate window')
        button.setAccessibleName('Open panel in separate window')
        button.clicked.connect(lambda: self.detach(selected()))
        if layout is not None:
            layout.addWidget(button)
        return button

    def _context(self, widget, selected):
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        def open_menu(pos):
            key = selected(pos)
            if key not in self.panels:
                return
            menu = QMenu(widget)
            menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            menu.addAction('Open in separate window', lambda: self.detach(key))
            menu.popup(widget.mapToGlobal(pos))
            self.context_menu = menu
        widget.customContextMenuRequested.connect(open_menu)

    def key_for_widget(self, widget):
        return next((key for key, panel in self.panels.items() if panel.widget is widget), None)

    def selected(self, home):
        return self.key_for_widget(home.tabs.currentWidget())

    def sync_inspector(self, *_):
        w = self.window
        with QSignalBlocker(w.inspector_choice):
            w.inspector_choice.clear()
            for i in range(w.inspector.count()):
                w.inspector_choice.addItem(w.inspector.tabText(i),
                                           self.key_for_widget(w.inspector.widget(i)))
            w.inspector_choice.setCurrentIndex(w.inspector.currentIndex())

    def choose_inspector(self, index):
        key = self.window.inspector_choice.itemData(index)
        if key in self.panels:
            self.panels[key].home.select(self.panels[key])

    def owns_widget(self, widget):
        if widget is None:
            return False
        top = widget.window()
        return top is self.window or any(p.host is top for p in self.panels.values())

    def detach(self, key):
        panel = self.panels.get(key)
        if panel is None or self.stopping:
            return
        if panel.host is not None:
            self.reveal(key)
            return
        size = panel.widget.size().expandedTo(panel.widget.minimumSizeHint())
        default = QSize(720, 500) if key == 'editor' else (
            QSize(640, 400) if panel.home.group == 'output' else QSize(360, 480))
        size = size.expandedTo(default)
        if key == 'editor':
            self.window.assistance.dismiss()
        panel.home.remove(panel)
        host = panel.host = FloatingPanel(self, panel)
        host.resize(size)
        if panel.geometry:
            host.restoreGeometry(panel.geometry)
        else:
            host.move(self.window.pos())
        self.update_titles()
        host.show()
        fit_on_screen(host)
        self.refresh()
        if not self.restoring:
            self.reveal(key)

    def dock(self, key, *, reveal=True):
        panel = self.panels.get(key)
        if panel is None or self.stopping:
            return
        if panel.host is not None:
            host = panel.host
            panel.geometry = host.saveGeometry()
            if key == 'editor':
                self.window.assistance.dismiss()
            widget = host.takeCentralWidget()
            assert widget is panel.widget
            panel.host = None
            panel.home.insert(panel)
            host.hide()
            host.deleteLater()
            self.refresh()
        if reveal:
            self.reveal(key)

    def dock_all(self):
        for key in self.panels:
            self.dock(key, reveal=False)
        self.refresh()

    def show_main(self):
        self._activate(self.window)

    @staticmethod
    def _activate(window):
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()

    def reveal(self, key, *, focus=True):
        panel = self.panels.get(key)
        if panel is None or self.stopping:
            return
        w = self.window
        if panel.host is not None:
            self._activate(panel.host)
        else:
            if panel.home.group == 'editor' and w.current_workspace == 'Circuit':
                w.select_workspace('Code')
            elif panel.home.group == 'inspector':
                if w.current_workspace == 'Code':
                    w.select_workspace('Debug')
                w.inspector_action.setChecked(True)
            elif panel.home.group == 'output':
                w.output_action.setChecked(True)
            panel.home.select(panel)
            self.refresh()
            if focus:
                self.show_main()
        if focus:
            target = w.editor if key == 'editor' else panel.widget
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def refresh(self, *_):
        if not self.panels:
            return
        w = self.window
        floating_editor = self.panels['editor'].host is not None
        code = w.current_workspace == 'Code'
        editor_visible = w.current_workspace != 'Circuit'
        self.placeholder.setVisible(floating_editor and code)
        self.editor_home.slot.setVisible(editor_visible and (not floating_editor or code))
        if not floating_editor:
            w.editor_pane.setVisible(editor_visible)
        w.inspector_action.setEnabled(w.inspector.count() > 0)
        w.output_action.setEnabled(w.tabs.count() > 0)
        w.inspector_panel.setVisible(not code and w.inspector.count() > 0 and w.inspector_action.isChecked())
        output = w.tabs.count() > 0 and w.output_action.isChecked()
        w.tabs.setVisible(output)
        w.editor_area.setVisible(output or (editor_visible and (not floating_editor or code)))
        w.assistance.activate(editor_visible or floating_editor)
        self.sync_inspector()

    def update_titles(self):
        for panel in self.panels.values():
            if panel.host is not None:
                panel.host.setWindowTitle(panel.title + ' — ' + self.window.windowTitle())

    def save(self, settings):
        settings.setValue('panels/version', 1)
        settings.setValue('panels/floating', [p.key for p in self.panels.values() if p.host is not None])
        for panel in self.panels.values():
            geometry = panel.host.saveGeometry() if panel.host is not None else panel.geometry
            if geometry is not None:
                settings.setValue('panels/geometry/' + panel.key, geometry)
        for home in (self.inspector_home, self.output_home):
            settings.setValue('panels/selected/' + home.group, self.selected(home) or '')
        settings.setValue('panels/editor_splitter', self.window.editor_area.saveState())

    def restore(self, settings):
        if settings.value('panels/version') not in (1, '1'):
            return
        floating = settings.value('panels/floating', [])
        if isinstance(floating, str):
            floating = [floating]
        if not isinstance(floating, (tuple, list)):
            floating = []
        self.restoring = True
        try:
            for panel in self.panels.values():
                geometry = settings.value('panels/geometry/' + panel.key)
                if isinstance(geometry, QByteArray):
                    panel.geometry = geometry
                if panel.key in floating:
                    self.detach(panel.key)
            for home in (self.inspector_home, self.output_home):
                key = settings.value('panels/selected/' + home.group, '')
                if isinstance(key, str) and key in self.panels:
                    panel = self.panels[key]
                    if panel.home is home and panel.host is None:
                        home.select(panel)
            state = settings.value('panels/editor_splitter')
            if isinstance(state, QByteArray):
                self.window.editor_area.restoreState(state)
        finally:
            self.restoring = False
        self.refresh()

    def shutdown(self):
        # Do not redock or overwrite saved layout while the session is closing.
        self.stopping = True
        for panel in self.panels.values():
            if panel.host is not None:
                host = panel.host
                host.takeCentralWidget()
                panel.host = None
                panel.home.insert(panel)
                host.hide()
                host.deleteLater()
        # Definition previews and other owned dialogs must not outlive the session.
        for widget in self.window.findChildren(QWidget):
            if widget.isWindow():
                widget.hide()
