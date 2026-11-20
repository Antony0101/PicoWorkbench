"""Native Qt views for worker-owned telemetry and bus captures."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget)

COLORS = ['#68e0b7', '#f4c56e', '#82b7ff', '#f189ac', '#bc9dff', '#58d6df', '#ffa184', '#c6dd83']


class PlotCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.channels = {}
        self.hidden = set()
        self.frozen = False
        self.follow = True
        self.window_ns = 5_000_000_000
        self.end_ns = 0
        self.drag = None
        self.setMinimumHeight(95)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Serial plot; wheel to zoom, drag to pan, Home to follow latest')

    def feed(self, channels):
        if self.frozen:
            return
        self.channels = channels
        if self.follow:
            self.end_ns = max((v[-1].time_ns for v in channels.values() if v), default=0)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#101d25'))
        rect = self.rect().adjusted(62, 10, -12, -25)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        start = max(0, self.end_ns - self.window_ns)
        end = max(start + 1, self.end_ns)
        visible = {k: [v for v in points if start <= v.time_ns <= end]
                   for k, points in self.channels.items() if k not in self.hidden}
        values = [v.value for points in visible.values() for v in points]
        low, high = (min(values), max(values)) if values else (0, 1)
        pad = (high-low) * .08 or max(abs(high)*.05, .5)
        low, high = low-pad, high+pad
        p.setPen(QColor('#607e8c'))
        for i in range(5):
            y = rect.top() + rect.height()*i/4
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.drawText(2, int(y)+4, f'{high-(high-low)*i/4:.3g}')
        p.drawText(rect.left(), self.height()-5, f'{start/1e9:.3f} s')
        p.drawText(max(rect.left(), rect.right()-100), self.height()-5, f'{end/1e9:.3f} s virtual')
        p.setClipRect(rect)
        for name, points in visible.items():
            color = COLORS[list(self.channels).index(name) % len(COLORS)]
            p.setPen(QPen(QColor(color), 1.5))
            # Preserve extrema in each screen column, bounded by viewport width.
            bins = {}
            for sample in points:
                x = rect.left() + (sample.time_ns-start)*rect.width()/(end-start)
                y = rect.bottom() - (sample.value-low)*rect.height()/(high-low)
                bins.setdefault((sample.segment, int(x)), []).append(y)
            previous = None
            previous_segment = None
            for (segment, x), ys in bins.items():
                point = QPointF(x, ys[-1])
                if previous is not None and segment == previous_segment:
                    p.drawLine(previous, point)
                p.drawLine(QPointF(x, min(ys)), QPointF(x, max(ys)))
                p.drawEllipse(point, 1.5, 1.5)
                previous = point
                previous_segment = segment

    def wheelEvent(self, event):
        self.window_ns = max(1_000, min(3_600_000_000_000, int(self.window_ns * (0.8 if event.angleDelta().y()>0 else 1.25))))
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = (event.position().x(), self.end_ns)
            self.follow = False

    def mouseMoveEvent(self, event):
        if self.drag:
            self.end_ns = max(0, self.drag[1] - int((event.position().x()-self.drag[0])*self.window_ns/max(1, self.width()-74)))
            self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Home:
            self.follow = True
            self.feed(self.channels)
        elif event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            self.window_ns = max(1000, min(3_600_000_000_000, int(self.window_ns * (1.25 if event.key()==Qt.Key.Key_Minus else .8))))
            self.update()
        else:
            super().keyPressEvent(event)


class SerialPlotter(QWidget):
    def __init__(self, send, parent=None):
        super().__init__(parent)
        self.send = send
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.uart = QComboBox()
        self.uart.addItems(['UART0', 'UART1'])
        self.uart.setAccessibleName('Plot UART')
        self.uart.currentIndexChanged.connect(lambda port: send('plot_port', port=port))
        row.addWidget(self.uart)
        self.freeze = QCheckBox('Freeze view')
        self.freeze.toggled.connect(self.freeze_view)
        row.addWidget(self.freeze)
        for name, fn in [('Follow latest', self.follow_latest), ('Clear', lambda: send('plot_clear')), ('Export CSV…', self.export)]:
            button = QPushButton(name)
            button.clicked.connect(fn)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.channel_row = QHBoxLayout()
        self.buttons = {}
        layout.addLayout(self.channel_row)
        self.canvas = PlotCanvas()
        layout.addWidget(self.canvas, 1)
        self.status = QLabel('Print numeric CSV or named values: temperature:25,voltage:3.3')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.latest = {}
        self.epoch = None

    def freeze_view(self, frozen):
        self.canvas.frozen = frozen
        if not frozen:
            self.canvas.feed(self.latest)

    def follow_latest(self):
        self.canvas.follow = True
        self.freeze.setChecked(False)
        self.canvas.feed(self.latest)

    def display(self, packet):
        if packet.get('reset') or packet.get('plot_epoch') != self.epoch:
            self.epoch = packet.get('plot_epoch')
            self.latest = {}
            self.canvas.channels = {}
            self.canvas.end_ns = 0
            self.canvas.hidden.clear()
            self.canvas.update()
            for button in self.buttons.values():
                self.channel_row.removeWidget(button)
                button.deleteLater()
            self.buttons.clear()
        snapshot = packet.get('plot')
        if snapshot is None:
            return
        self.latest = snapshot
        for name in snapshot:
            if name not in self.buttons:
                button = QCheckBox(name)
                button.setChecked(True)
                button.setStyleSheet('color: ' + COLORS[len(self.buttons)%8])
                button.toggled.connect(lambda checked, name=name: self.toggle(name, checked))
                self.buttons[name] = button
                self.channel_row.addWidget(button)
        self.canvas.feed(snapshot)
        self.status.setText(f'{sum(map(len, snapshot.values())):,} samples · {packet.get("plot_gaps", 0)} gaps · '
                            f'{packet.get("plot_dropped", 0):,} old samples discarded · wheel/+/- zoom · drag pan · Home follow')

    def toggle(self, name, checked):
        self.canvas.hidden.discard(name) if checked else self.canvas.hidden.add(name)
        self.canvas.update()

    def export(self):
        name, _ = QFileDialog.getSaveFileName(self, 'Export plot', 'plot.csv', 'CSV (*.csv)')
        if name:
            self.send('plot_export', path=name)


def bus_groups(records):
    groups, active = [], {}
    previous = None
    for record in records:
        key = record.controller
        if previous is not None and record.id != previous + 1:
            active.clear()
        previous = record.id
        if record.bus == 'SPI':
            groups.append([record])
        else:
            if key not in active:
                active[key] = []
                groups.append(active[key])
            active[key].append(record)
            if record.flags & 2 or not record.acknowledged or record.fault:
                active.pop(key, None)
    return groups


class BusTable(QAbstractTableModel):
    HEADERS = ('Time (ms)', 'Bus', 'Address', 'Device', 'Direction', 'TX', 'RX', 'Flags', 'Status')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.groups)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        group = self.groups[index.row()]
        first, last = group[0], group[-1]
        if role == Qt.ItemDataRole.ForegroundRole and any(r.status != 'ACK' for r in group):
            return QColor('#fc8b98')
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        columns = (f'{first.time_ns/1e6:.3f}', f'{first.bus}{first.controller}',
            ' / '.join(dict.fromkeys(f'0x{r.target:02X}' for r in group if r.target is not None)),
            ' / '.join(dict.fromkeys(r.device or '—' for r in group)),
            ' / '.join(dict.fromkeys('R' if r.flags&1 else 'W' for r in group)) if first.bus=='I2C' else 'duplex',
            ' '.join(f'{r.tx:02X}' for r in group[:32]) + (' …' if len(group)>32 else ''),
            ' '.join(f'{r.rx:02X}' for r in group[:32]) + (' …' if len(group)>32 else ''),
            ('RESTART ' if any(r.flags&4 for r in group) else '') + ('STOP' if last.flags&2 else 'partial' if first.bus=='I2C' else 'frame'),
            next((r.status for r in group if r.status != 'ACK'), 'ACK'))
        return columns[index.column()]


class BusInspector(QWidget):
    def __init__(self, send, parent=None):
        super().__init__(parent)
        self.send = send
        self.records = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.capture = QCheckBox('Capture')
        self.capture.setChecked(True)
        self.capture.toggled.connect(lambda enabled: send('bus_capture', enabled=enabled))
        row.addWidget(self.capture)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('Filter bus, controller, address, device, ACK/NACK/FAULT')
        self.filter.setAccessibleName('Bus filter')
        self.filter.textChanged.connect(self.refresh)
        row.addWidget(self.filter, 1)
        for label, fn in [('Clear', lambda: send('bus_clear')), ('Export…', self.export)]:
            button = QPushButton(label)
            button.clicked.connect(fn)
            row.addWidget(button)
        layout.addLayout(row)
        self.model = BusTable(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setAccessibleName('Bus transactions')
        self.table.horizontalHeader().setStretchLastSection(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('Selected transaction bytes and outcomes')
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setSizes([180, 65])
        layout.addWidget(splitter)
        self.table.selectionModel().currentChanged.connect(self.selected)
        self.status = QLabel('I²C grouped through STOP; SPI shows individual frames.')
        layout.addWidget(self.status)

    def display(self, packet):
        records = packet.get('bus')
        if records is None:
            return
        self.records = records
        self.refresh()
        self.status.setText(f'{len(records):,} exchanges · {packet.get("bus_dropped", 0):,} discarded · '
                            'I²C groups through STOP; SPI frames; truncated captures may start mid-transaction')

    def refresh(self, *_):
        query = self.filter.text().strip().lower()
        groups = bus_groups(self.records)
        if query:
            groups = [g for g in groups if any(query in f'{r.bus}{r.controller} {r.device} {r.target:#04x} {r.status}'.lower()
                      if r.target is not None else query in f'{r.bus}{r.controller} {r.device} {r.status}'.lower() for r in g)]
        self.model.beginResetModel()
        self.model.groups = groups
        self.model.endResetModel()

    def selected(self, index, previous=QModelIndex()):
        if not index.isValid() or index.row() >= len(self.model.groups):
            self.details.clear()
            return
        lines = []
        for r in self.model.groups[index.row()]:
            char = lambda v: chr(v) if 32 <= v < 127 else '.'
            lines.append(f'#{r.id} {r.time_ns} ns TX {r.tx:02X} {char(r.tx)} RX {r.rx:02X} {char(r.rx)} '
                         f'{"READ" if r.flags&1 else "WRITE"} {"RESTART" if r.flags&4 else ""} '
                         f'{"STOP" if r.flags&2 else ""} {r.status} {r.fault}')
        self.details.setPlainText('\n'.join(lines))

    def export(self):
        name, selected = QFileDialog.getSaveFileName(self, 'Export all bus exchanges', 'bus.json', 'JSON (*.json);;CSV (*.csv)')
        if name:
            self.send('bus_export', path=name, format='csv' if selected.startswith('CSV') or name.endswith('.csv') else 'json')
