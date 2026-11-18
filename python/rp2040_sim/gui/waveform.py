from collections import deque

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .canvas import COLORS
from ..engine import Drive
from .menus import popup_menu


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.history = deque(maxlen=10_000)
        self.now = 0
        self.window_ns = 1_000_000_000
        self.pins = [25, 14, 0]
        self.latest_states = {}
        self.baseline = {}
        self.cleared_at = 0
        self.setMinimumHeight(160)

    def feed(self, snapshot, events, reset=False):
        if reset:
            self.history.clear()
            self.baseline = {}
            self.cleared_at = 0
        self.history.extend(e for e in events if e.kind == 1)
        self.latest_states = {pin: snapshot.pin(pin) for pin in self.pins}
        self.now = snapshot.time_ns
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101d25"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, top, width = 80, 35, max(20, self.width() - 100)
        span = self.window_ns
        start = max(0, self.now - span)
        painter.setPen(QColor("#89a7b6"))
        painter.drawText(15, 20, "DIGITAL TRACE · virtual time · wheel to zoom · last 10,000 transitions")
        for tick in range(6):
            x = left + width * tick / 5
            painter.setPen(QColor("#213742"))
            painter.drawLine(int(x), top, int(x), self.height() - 15)
            painter.setPen(QColor("#7894a5"))
            painter.drawText(QRectF(x - 25, self.height() - 16, 100, 16), f"{(start + span * tick / 5) / 1e6:.0f} ms")
        for row, pin in enumerate(self.pins):
            y = top + row * 37
            painter.setPen(QColor("#aec6d2"))
            painter.drawText(15, y + 15, f"GP{pin}")
            last_x = left + width * (max(start, self.cleared_at) - start) / span
            value = self.baseline.get(pin, Drive.LOW)
            records = [e for e in self.history if e.source == pin]
            for e in records:
                if e.time_ns < start:
                    value = Drive(e.value)
                    continue
                x = left + width * (e.time_ns - start) / span
                old_y = y + (2 if value == Drive.HIGH else 22)
                new_value = Drive(e.value)
                new_y = y + (2 if new_value == Drive.HIGH else 22)
                painter.setPen(QPen(QColor(COLORS[value]), 2))
                painter.drawLine(int(last_x), old_y, int(x), old_y)
                painter.drawLine(int(x), old_y, int(x), new_y)
                last_x, value = x, new_value
            painter.setPen(QPen(QColor(COLORS[value]), 2))
            line_y = y + (2 if value == Drive.HIGH else 22)
            end_x = left + width * (self.now - start) / span
            painter.drawLine(int(last_x), line_y, int(end_x), line_y)

    def wheelEvent(self, event):
        self.zoom(0.5 if event.angleDelta().y() > 0 else 2)
        event.accept()

    def zoom(self, factor):
        self.window_ns = max(10_000, min(60_000_000_000, int(self.window_ns * factor)))
        self.update()

    def context_menu(self):
        menu = QMenu(self)
        menu.addAction("Zoom in", lambda: self.zoom(0.5)).setEnabled(self.window_ns > 10_000)
        menu.addAction("Zoom out", lambda: self.zoom(2)).setEnabled(self.window_ns < 60_000_000_000)
        menu.addAction("Reset time scale", self.reset_zoom)
        menu.addSeparator()
        menu.addAction("Clear trace", self.clear_trace).setEnabled(bool(self.history))
        return menu

    def reset_zoom(self):
        self.window_ns = 1_000_000_000
        self.update()

    def clear_trace(self):
        self.history.clear()
        self.baseline = self.latest_states.copy()
        self.cleared_at = self.now
        self.update()

    def contextMenuEvent(self, event):
        popup_menu(self, self.context_menu(), event.globalPos())
        event.accept()
