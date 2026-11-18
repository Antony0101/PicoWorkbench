from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsObject,
                               QGraphicsPathItem, QGraphicsScene, QGraphicsView, QMenu)

from ..engine import Drive
from ..project import PINS
from .menus import copy_action, popup_menu

COLORS = {Drive.LOW: "#425769", Drive.HIGH: "#57e3b0", Drive.FLOATING: "#e6b961", Drive.CONFLICT: "#fc6e7c"}
PART_NAMES = {"led": "LED", "button": "Button", "probe": "Probe", "pullup": "Pull-up",
              "pulldown": "Pull-down", "i2c_sensor": "I²C sensor", "spi_loopback": "SPI loopback"}


class Port(QGraphicsEllipseItem):
    def __init__(self, endpoint, position, scene, parent=None):
        super().__init__(-6, -6, 12, 12, parent)
        self.endpoint = endpoint
        self.canvas_scene = scene
        self.setPos(position)
        self.setBrush(QColor("#314957"))
        self.setPen(QPen(QColor("#8da7b8"), 1.5))
        self.setZValue(5)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(endpoint + " — click two ports to connect")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.canvas_scene.port_clicked(self.endpoint)
        event.accept()


class Part(QGraphicsObject):
    def __init__(self, component, scene):
        super().__init__()
        self.component = component
        self.canvas_scene = scene
        self.state = Drive.FLOATING
        self.device_text = "No transfers"
        self.ports = {}
        self.down = False
        self.setPos(component.x, component.y)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setData(0, component.id)
        self.ports = {name: Port(component.id + ":" + name,
                                QPointF(0, 50 if len(PINS[component.kind]) == 1 else 35 + i * 23), scene, self)
                      for i, name in enumerate(PINS[component.kind])}

    def boundingRect(self):
        return QRectF(-8, -3, 220, max(110, 48 + len(self.ports) * 23))

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#65ccb5" if self.isSelected() else "#2b4553"), 1.5))
        painter.setBrush(QColor("#172932"))
        height = max(100, 38 + len(self.ports) * 23)
        painter.drawRoundedRect(QRectF(8, 0, 200 if len(self.ports) > 1 else 170, height), 10, 10)
        painter.setPen(QColor("#d9e7ed"))
        painter.setFont(QFont("Sans Serif", 9, QFont.Weight.DemiBold))
        title = {"i2c_sensor": "I2C", "spi_loopback": "SPI"}.get(self.component.kind, self.component.kind.upper())
        painter.drawText(QRectF(22, 10, 175 if len(self.ports) > 1 else 145, 20), title + " / " + self.component.id)
        kind = self.component.kind
        if kind == "led":
            painter.setBrush(QColor(COLORS[self.state]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(46, 53), 13, 13)
            painter.setPen(QColor("#a5bdc9"))
            painter.drawText(QRectF(73, 39, 100, 30), self.state.name)
        elif kind == "button":
            painter.setBrush(QColor("#357c6d" if self.down else "#29434f"))
            painter.drawRoundedRect(QRectF(24, 34, 138, 35), 6, 6)
            painter.setPen(QColor("#e6f4f5"))
            painter.drawText(QRectF(24, 34, 138, 35), Qt.AlignmentFlag.AlignCenter,
                             "PRESSED" if self.down else "Hold button")
        elif len(self.ports) > 1:
            painter.setFont(QFont("Sans Serif", 8))
            for name, port in self.ports.items():
                painter.setPen(QColor("#9cbac5"))
                painter.drawText(QRectF(14, port.pos().y() - 8, 55, 20), name.upper())
            painter.setPen(QColor("#68e0b7"))
            painter.drawText(QRectF(65, 33, 136, 45), Qt.TextFlag.TextWordWrap,
                             (f"I²C @ 0x{self.component.address:02X}\n" if kind == "i2c_sensor" else "SPI loopback\n") + self.device_text)
        else:
            painter.setPen(QColor(COLORS[self.state]))
            painter.drawText(QRectF(24, 37, 135, 30), self.state.name)
        painter.setPen(QColor("#7897a6"))
        painter.setFont(QFont("Sans Serif", 8))
        painter.drawText(QRectF(22, height - 24, 170, 20), self.component.label[:25] or "Digital signal")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self.component.kind == "button" and QRectF(24, 34, 138, 35).contains(event.pos()):
            self.down = True
            self.canvas_scene.button.emit(self.component.id, True)
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self.down:
            self.down = False
            self.canvas_scene.button.emit(self.component.id, False)
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)
            self.canvas_scene.moved.emit(self.component.id, self.pos().x(), self.pos().y())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.canvas_scene.running:
            self.canvas_scene.edit_requested.emit(self.component.id)
        event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.canvas_scene.update_wires()
        return super().itemChange(change, value)


class CircuitScene(QGraphicsScene):
    connect_requested = Signal(str, str)
    button = Signal(str, bool)
    moved = Signal(str, float, float)
    edit_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ports = {}
        self.parts = {}
        self.wire_items = []
        self.pending = None
        self.running = False
        self.setBackgroundBrush(QColor("#101d25"))
        self.setSceneRect(0, 0, 1100, 850)

    def label(self, text, x, y, size=10, color="#9bb4c2"):
        item = self.addText(text, QFont("Sans Serif", size))
        item.setDefaultTextColor(QColor(color))
        item.setPos(x, y)
        return item

    def set_project(self, project):
        self.clear()
        self.ports, self.parts, self.wire_items = {}, {}, []
        self.pending = None
        self.project = project
        self.label("CIRCUIT WORKSPACE", 25, 10, 10, "#5dd7b5")
        self.label("Right-click for actions · click two ports to wire · drag parts", 25, 35, 9)
        board = self.addRect(175, 95, 250, 635, QPen(QColor("#387969"), 2), QColor("#153d37"))
        board.setZValue(-1)
        self.label("RP2040", 247, 115, 19, "#d8f8eb")
        self.label("PICO PROFILE", 250, 152, 9, "#79bea7")
        self.label("LOGICAL GPIO VIEW", 236, 690, 8, "#79bea7")
        chip = self.addRect(248, 355, 105, 100, QPen(QColor("#34575a"), 2), QColor("#18282e"))
        chip.setZValue(-1)
        self.label("RP2040", 267, 384, 12, "#d1e0df")
        self.label("Cortex-M0+", 260, 413, 8)
        self.onboard_led = self.addEllipse(291, 215, 14, 14, QPen(QColor("#72aa88")), QColor("#425769"))
        self.label("GP25 LED", 263, 237, 8)
        for p in range(30):
            left = p < 15
            row = p if left else 29 - p
            x, y = (175 if left else 425), 195 + row * 32
            port = Port(f"pico:GP{p}", QPointF(x, y), self)
            self.addItem(port)
            self.ports[port.endpoint] = port
            self.label(f"GP{p:02}", x + 12 if left else x - 52, y - 12, 9, "#c0dfd1")
        for c in project.components:
            part = Part(c, self)
            self.addItem(part)
            self.parts[c.id] = part
            for name, port in part.ports.items():
                self.ports[c.id + ":" + name] = port
        for index, (a, b) in enumerate(project.wires):
            item = QGraphicsPathItem()
            item.setPen(QPen(QColor("#4c8d87"), 2))
            item.setZValue(-0.5)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            item.setData(0, f"wire:{index}")
            item.setToolTip(a + " ↔ " + b)
            self.addItem(item)
            self.wire_items.append((a, b, item))
        self.update_wires()

    def port_clicked(self, endpoint):
        if self.running:
            return
        if self.pending is None:
            self.pending = endpoint
            self.ports[endpoint].setPen(QPen(QColor("#fcdd8a"), 3))
        else:
            first = self.pending
            self.pending = None
            self.ports[first].setPen(QPen(QColor("#8da7b8"), 1.5))
            if first != endpoint:
                self.connect_requested.emit(first, endpoint)

    def cancel_connection(self):
        if self.pending is not None:
            self.ports[self.pending].setPen(QPen(QColor("#8da7b8"), 1.5))
            self.pending = None

    def update_wires(self):
        for a, b, item in self.wire_items:
            start, end = self.ports[a].scenePos(), self.ports[b].scenePos()
            path = QPainterPath(start)
            bend = max(60, abs(end.x() - start.x()) * 0.45)
            path.cubicTo(start + QPointF(bend, 0), end - QPointF(bend, 0), end)
            item.setPath(path)

    def display(self, snapshot, pins, running, devices=None, endpoints=None):
        self.running = running
        for p in range(30):
            self.ports[f"pico:GP{p}"].setBrush(QColor(COLORS[snapshot.pin(p)]))
        self.onboard_led.setBrush(QColor(COLORS[snapshot.pin(25)]))
        for key, part in self.parts.items():
            part.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not running)
            part.state = snapshot.pin(pins[key]) if key in pins else Drive.FLOATING
            part.device_text = (devices or {}).get(key, "No transfers")
            for port in part.ports.values():
                pin = (endpoints or {}).get(port.endpoint)
                state = snapshot.pin(pin) if pin is not None else part.state
                port.setBrush(QColor(COLORS[state]))
            part.update()
        for a, b, wire in self.wire_items:
            endpoint = a if a.startswith("pico:GP") else b
            if endpoint.startswith("pico:GP"):
                pin = int(endpoint.split("GP")[1])
            else:
                pin = (endpoints or {}).get(endpoint, pins.get(endpoint.split(":")[0]))
            state = snapshot.pin(pin) if pin is not None else Drive.FLOATING
            wire.setPen(QPen(QColor(COLORS[state]), 3 if wire.isSelected() else 2))


class CircuitView(QGraphicsView):
    delete_requested = Signal()
    add_requested = Signal(str, float, float)
    disconnect_requested = Signal(str)

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMinimumWidth(550)
        self.simulation_actions = []

    def fit_canvas(self):
        self.fitInView(self.scene().itemsBoundingRect().adjusted(-20, -20, 20, 20),
                       Qt.AspectRatioMode.KeepAspectRatio)

    def context_menu(self, position):
        scene = self.scene()
        point = self.mapToScene(position)
        menu = QMenu(self)
        item = self.itemAt(position)
        # Ports take precedence over their containing component.
        part = item
        while part is not None and not isinstance(part, Part):
            part = part.parentItem()

        def edit_action(title, callback):
            action = menu.addAction(title)
            action.setEnabled(not scene.running)
            action.triggered.connect(lambda: callback() if not scene.running else None)
            return action

        if isinstance(item, Port):
            endpoint = item.endpoint
            menu.addSection(endpoint)
            title = f"Connect {scene.pending} to this port" if scene.pending else "Connect from this port"
            edit_action(title, lambda: scene.port_clicked(endpoint)).setEnabled(
                not scene.running and scene.pending != endpoint)
            action = edit_action("Disconnect wires", lambda: self.disconnect_requested.emit(endpoint))
            action.setEnabled(not scene.running and any(endpoint in wire for wire in scene.project.wires))
            copy_action(menu, "Copy port name", endpoint)
        elif part is not None or (isinstance(item, QGraphicsPathItem) and item.data(0)):
            if part is not None:
                item = part
            # A context click on an unselected object targets that object alone.
            if not item.isSelected():
                scene.clearSelection()
                item.setSelected(True)
            if part is not None:
                key = part.component.id
                menu.addSection(key)
                title = "Edit I²C address…" if part.component.kind == "i2c_sensor" else "Edit label…"
                edit_action(title, lambda: scene.edit_requested.emit(key))
            else:
                menu.addSection("Wire")
            edit_action("Delete selected", self.delete_requested.emit)
        else:
            add = menu.addMenu("Add component")
            add.setEnabled(not scene.running)
            for kind, title in PART_NAMES.items():
                action = add.addAction(title)
                action.triggered.connect(
                    lambda checked=False, kind=kind: self.add_requested.emit(kind, point.x(), point.y())
                    if not scene.running else None)
        if scene.pending is not None:
            menu.addAction("Cancel connection", scene.cancel_connection)
        menu.addSeparator()
        menu.addAction("Fit canvas", self.fit_canvas)
        menu.addAction("Reset zoom", self.resetTransform)
        if self.simulation_actions:
            menu.addSeparator()
            menu.addActions(self.simulation_actions)
        return menu

    def contextMenuEvent(self, event):
        popup_menu(self, self.context_menu(event.pos()), event.globalPos())
        event.accept()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            if 0.3 < self.transform().m11() * factor < 3:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit()
        elif event.key() == Qt.Key.Key_Escape:
            self.scene().cancel_connection()
        else:
            super().keyPressEvent(event)
