"""Digital GPIO traces and measurements on integer virtual timestamps."""
from collections import deque
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QMenu, QVBoxLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QCheckBox, QScrollArea)
from .canvas import COLORS
from .menus import popup_menu
from ..engine import Drive


def format_time(ns):
    for size, unit in [(1_000_000_000, 's'), (1_000_000, 'ms'), (1000, 'µs')]:
        if abs(ns) >= size:
            return f'{ns / size:.6g} {unit}'
    return f'{ns:g} ns'


def parse_time(text):
    import re
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*(ns|us|µs|ms|s)?\s*', text)
    if not match:
        raise ValueError('Use a nonnegative virtual time, for example 250 ms or 1000 ns')
    units = {'ns':1, 'us':1000, 'µs':1000, 'ms':1_000_000, 's':1_000_000_000}
    try:
        value = Decimal(match[1]) * units[match[2] or 'ns']
        if not value.is_finite() or value > 2**64 - 1 or value != int(value):
            raise ValueError('Time must fit an integer nanosecond timestamp')
        return int(value)
    except InvalidOperation as error:
        raise ValueError('Invalid time') from error


class TracePlot(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.history = deque(maxlen=10_000)
        self.now = 0
        self.window_ns = 1_000_000_000
        self.pins = [25, 14, 0]
        self.latest_states = {}
        self.baseline = {}
        self.cleared_at = 0
        self.follow = True
        self.view_start = 0
        self.cursor_a = None
        self.cursor_b = None
        self.loss = False
        self.drop_baseline = 0
        self._drag = None
        self.setMinimumSize(250, 165)
        self.setAccessibleName('Digital trace plot')
        self.setToolTip('Click: cursor A · Shift+click: cursor B · wheel: zoom · middle drag: pan. Keyboard alternatives are above.')

    @property
    def start(self):
        return max(0, self.now - self.window_ns) if self.follow else self.view_start

    def feed(self, snapshot, events, reset=False):
        if reset:
            self.history.clear()
            self.baseline = {}
            self.cleared_at = 0
            self.loss = False
            self.cursor_a = self.cursor_b = None
            self.drop_baseline = 0
            self.view_start = 0
        records = [event for event in events if event.kind == 1]
        if len(self.history) + len(records) > self.history.maxlen:
            self.loss = True
        self.history.extend(records)
        self.latest_states = {pin:snapshot.pin(pin) for pin in range(30)}
        if not self.baseline:
            self.baseline = self.latest_states.copy()
        self.loss |= snapshot.dropped_events > self.drop_baseline
        self.dropped = snapshot.dropped_events
        self.now = snapshot.time_ns
        self.update()
        self.changed.emit()

    def measurement(self, pin=None):
        pin = self.pins[0] if pin is None else pin
        if self.cursor_a is None or self.cursor_b is None:
            return {'error':'Set cursor A and B to measure virtual time'}
        a, b = sorted((self.cursor_a, self.cursor_b))
        result = {'delta_ns':b-a}
        if self.loss or a < self.cleared_at or b > self.now or b == a:
            return {**result, 'error':'Signal measurement unavailable: missing data or cursors outside captured time'}
        records = [event for event in self.history if event.source == pin]
        value = self.baseline.get(pin, Drive.FLOATING)
        rising = []
        valid = True
        for event in records:
            new = Drive(event.value)
            if a <= event.time_ns <= b:
                valid &= value in (Drive.LOW, Drive.HIGH) and new in (Drive.LOW, Drive.HIGH)
                if value == Drive.LOW and new == Drive.HIGH:
                    rising.append(event.time_ns)
            value = new
        if not valid or len(rising) < 2:
            return {**result, 'error':'Frequency/duty unavailable: need two rising edges with known digital states'}
        start, end = rising[0], rising[-1]
        high = 0
        value, previous = Drive.HIGH, start
        for event in records:
            if start < event.time_ns <= end:
                if value == Drive.HIGH:
                    high += event.time_ns - previous
                value, previous = Drive(event.value), event.time_ns
        return {**result, 'frequency_hz':(len(rising)-1)*1e9/(end-start), 'duty':high/(end-start)}

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#101d25'))
        left, width = 130, max(20, self.width()-150)
        start, span = self.start, self.window_ns
        painter.setPen(QColor('#c4d8e2'))
        painter.drawText(12, 18, 'Virtual time · HIGH 1 / LOW 0 / FLOAT Z / CONFLICT !')
        for tick in range(6):
            x = left + width*tick/5
            painter.setPen(QColor('#344c59'))
            painter.drawLine(int(x), 35, int(x), self.height()-22)
            painter.setPen(QColor('#b3c9d6'))
            painter.drawText(QRectF(x-35, self.height()-20, 100, 18), format_time(int(start+span*tick/5)))
        for row, pin in enumerate(self.pins):
            y = 35 + row*37
            painter.setPen(QColor('#d6e4ec'))
            state = self.latest_states.get(pin, Drive.FLOATING)
            label = {Drive.HIGH:'1',Drive.LOW:'0',Drive.FLOATING:'Z',Drive.CONFLICT:'!'}.get(state, '?')
            painter.drawText(10, y+17, f'GP{pin} {label} {state.name}')
            last_x = left + width*(max(start,self.cleared_at)-start)/span
            value = self.baseline.get(pin, Drive.FLOATING)
            for record in self.history:
                if record.source != pin:
                    continue
                if record.time_ns < start:
                    value = Drive(record.value)
                    continue
                if record.time_ns > start+span:
                    break
                x = left+width*(record.time_ns-start)/span
                old_y = y+(2 if value == Drive.HIGH else 22)
                new = Drive(record.value)
                new_y = y+(2 if new == Drive.HIGH else 22)
                pen = QPen(QColor(COLORS[value]), 2)
                if value not in (Drive.LOW, Drive.HIGH):
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(int(last_x),old_y,int(x),old_y)
                painter.drawLine(int(x),old_y,int(x),new_y)
                last_x,value=x,new
            end = left+width*(min(self.now,start+span)-start)/span
            if end >= last_x:
                painter.setPen(QPen(QColor(COLORS[value]),2))
                line_y = y+(2 if value == Drive.HIGH else 22)
                painter.drawLine(int(last_x),line_y,int(end),line_y)
        for title, value in [('A',self.cursor_a),('B',self.cursor_b)]:
            if value is not None and start <= value <= start+span:
                x = int(left+width*(value-start)/span)
                painter.setPen(QPen(QColor('#fcdd8a'),1,Qt.PenStyle.DashLine))
                painter.drawLine(x,25,x,self.height()-22)
                painter.drawText(x+3,32,title)

    def zoom(self, factor):
        self.window_ns = max(10, min(60_000_000_000,int(self.window_ns*factor)))
        self.update()
        self.changed.emit()

    def pan(self, direction):
        self.view_start=max(0,self.start+int(direction*self.window_ns/2))
        self.follow=False
        self.update()
        self.changed.emit()

    def reset_zoom(self):
        self.window_ns=1_000_000_000
        self.follow=True
        self.update()
        self.changed.emit()

    def fit_data(self):
        first = self.history[0].time_ns if self.history else self.cleared_at
        self.view_start=first
        self.window_ns=max(10,self.now-first)
        self.follow=False
        self.update()
        self.changed.emit()

    def clear_trace(self):
        self.history.clear()
        self.baseline=self.latest_states.copy()
        self.cleared_at=self.now
        self.drop_baseline=getattr(self,'dropped',0)
        self.loss=False
        self.cursor_a=self.cursor_b=None
        self.update()
        self.changed.emit()

    def context_menu(self):
        menu=QMenu(self)
        menu.addAction('Zoom in',lambda:self.zoom(0.5))
        menu.addAction('Zoom out',lambda:self.zoom(2))
        menu.addAction('Reset time scale',self.reset_zoom)
        menu.addAction('Fit captured data',self.fit_data)
        menu.addSeparator()
        menu.addAction('Clear trace',self.clear_trace).setEnabled(bool(self.history))
        return menu

    def contextMenuEvent(self,event):
        popup_menu(self,self.context_menu(),event.globalPos())
        event.accept()

    def wheelEvent(self,event):
        self.zoom(0.5 if event.angleDelta().y()>0 else 2)
        event.accept()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.MiddleButton:
            self._drag=(event.position().x(),self.start)
        elif event.button()==Qt.MouseButton.LeftButton:
            timestamp=max(0,int(self.start+(event.position().x()-130)/max(20,self.width()-150)*self.window_ns))
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.cursor_b=timestamp
            else:
                self.cursor_a=timestamp
            self.update()
            self.changed.emit()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._drag:
            x,start=self._drag
            self.view_start=max(0,int(start+(x-event.position().x())/max(20,self.width()-150)*self.window_ns))
            self.follow=False
            self.update()
            self.changed.emit()

    def mouseReleaseEvent(self,event):
        self._drag=None


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.plot=TracePlot()
        row=QHBoxLayout()
        row.addWidget(QLabel('GPIO channels'))
        self.channels=QLineEdit('25,14,0')
        self.channels.setMaximumWidth(130)
        self.channels.setAccessibleName('GPIO channels, comma separated numbers 0 through 29')
        self.channels.returnPressed.connect(self.select_channels)
        row.addWidget(self.channels)
        for title,callback in [('Set',self.select_channels),('−',lambda:self.plot.zoom(2)),('+',lambda:self.plot.zoom(.5)),
                               ('◀',lambda:self.plot.pan(-1)),('▶',lambda:self.plot.pan(1)),('Fit',self.plot.fit_data)]:
            button=QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.follow=QCheckBox('Follow latest')
        self.follow.setChecked(True)
        self.follow.toggled.connect(self.set_follow)
        row.addWidget(self.follow)
        row.addStretch()
        layout.addLayout(row)
        row=QHBoxLayout()
        self.cursor_a=QLineEdit()
        self.cursor_b=QLineEdit()
        for name,editor in [('A',self.cursor_a),('B',self.cursor_b)]:
            editor.setPlaceholderText('e.g. 250 ms')
            editor.setAccessibleName('Cursor '+name+' virtual time')
            editor.setMaximumWidth(160)
            row.addWidget(QLabel('Cursor '+name))
            row.addWidget(editor)
            editor.returnPressed.connect(self.set_cursors)
        measure=QPushButton('Measure first channel')
        measure.clicked.connect(self.set_cursors)
        row.addWidget(measure)
        self.summary=QLabel('Set two cursors to measure')
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName('Trace measurements and data loss')
        row.addWidget(self.summary,1)
        layout.addLayout(row)
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(75)
        scroll.setWidget(self.plot)
        layout.addWidget(scroll,1)
        self.plot.changed.connect(self.refresh)

    @property
    def history(self): return self.plot.history
    @property
    def window_ns(self): return self.plot.window_ns
    @property
    def pins(self): return self.plot.pins
    def feed(self,*args,**kwargs): self.plot.feed(*args,**kwargs)
    def context_menu(self): return self.plot.context_menu()
    def clear_trace(self): self.plot.clear_trace()
    def zoom(self,factor): self.plot.zoom(factor)
    def reset_zoom(self): self.plot.reset_zoom()

    def set_follow(self,value):
        if not value:
            self.plot.view_start=self.plot.start
        self.plot.follow=value
        self.plot.update()

    def select_channels(self):
        try:
            pins=list(dict.fromkeys(int(pin.strip()) for pin in self.channels.text().split(',')))
            if not pins or any(not 0<=pin<30 for pin in pins):
                raise ValueError
            self.plot.pins=pins
            self.plot.setMinimumHeight(max(165,65+37*len(pins)))
            self.plot.update()
            self.refresh()
        except ValueError:
            self.summary.setText('Enter GPIO numbers 0–29 separated by commas')

    def set_cursors(self):
        try:
            self.plot.cursor_a=parse_time(self.cursor_a.text())
            self.plot.cursor_b=parse_time(self.cursor_b.text())
            self.plot.update()
            self.refresh()
        except ValueError as error:
            self.summary.setText(str(error))

    def refresh(self):
        self.follow.blockSignals(True)
        self.follow.setChecked(self.plot.follow)
        self.follow.blockSignals(False)
        for editor,value in [(self.cursor_a,self.plot.cursor_a),(self.cursor_b,self.plot.cursor_b)]:
            if value is not None and not editor.hasFocus():
                editor.setText(f'{value} ns')
        result=self.plot.measurement()
        prefix='DATA LOSS / TRUNCATION · ' if self.plot.loss else ''
        delta='Δt '+format_time(result['delta_ns'])+' · ' if 'delta_ns' in result else ''
        self.summary.setText(prefix+delta+(result['error'] if 'error' in result else
            f"GP{self.pins[0]} · {result['frequency_hz']:.6g} Hz · {100*result['duty']:.3g}% duty"))
