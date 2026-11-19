import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint, QPointF, Qt, QSettings
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from rp2040_sim.gui.app import Window


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def gui_environment(app, tmp_path, monkeypatch):
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: QMessageBox.StandardButton.Discard)
    yield
    for window in app.topLevelWidgets():
        if isinstance(window, Window):
            window._closing = True
            window.close()
            app.processEvents()


def wait_for(app, condition, timeout=5):
    end = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise AssertionError("GUI condition timed out")
        QTest.qWait(5)


def menu_action(menu, text):
    return next(action for action in menu.actions() if action.text() == text)


def test_gui_context_clicks_do_not_wire_or_press_buttons(app):
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        QTest.qWait(120)  # Initial fit-to-canvas timer.
        view, scene = window.view, window.scene
        port = scene.ports['pico:GP25']
        point = view.mapFromScene(port.scenePos())
        before = list(window.project.wires)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.RightButton, pos=point)
        assert scene.pending is None
        assert window.project.wires == before

        # Exercise the actual viewport context event, not only menu construction.
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                  view.viewport().mapToGlobal(point))
        QApplication.sendEvent(view.viewport(), event)
        menu = view._context_menu
        assert menu.isVisible()
        menu.close()
        menu_action(menu, 'Connect from this port').trigger()
        assert scene.pending == 'pico:GP25'

        button = scene.parts['button1']
        pressed = []
        scene.button.connect(lambda key, down: pressed.append((key, down)))
        point = view.mapFromScene(button.mapToScene(QPointF(60, 50)))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.RightButton, pos=point)
        assert not button.down
        assert pressed == []
        assert scene.pending == 'pico:GP25'
        menu = view.context_menu(point)
        menu_action(menu, 'Cancel connection').trigger()
        assert scene.pending is None
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_context_add_edit_delete_and_disconnect(app, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        view, scene = window.view, window.scene
        point = view.mapFromScene(QPointF(490, 700))
        expected = view.mapToScene(point)
        menu = view.context_menu(point)
        add = menu_action(menu, 'Add component').menu()
        menu_action(add, 'LED').trigger()
        component = next(c for c in window.project.components if c.id == 'led2')
        assert (component.x, component.y) == (expected.x(), expected.y())

        monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('Status LED', True))
        part = scene.parts['led2']
        menu = view.context_menu(view.mapFromScene(part.mapToScene(QPointF(60, 20))))
        menu_action(menu, 'Edit label…').trigger()
        assert next(c for c in window.project.components if c.id == 'led2').label == 'Status LED'

        # Right-clicking a different object must not delete the old selection.
        scene.parts['led2'].setSelected(True)
        part = scene.parts['button1']
        menu = view.context_menu(view.mapFromScene(part.mapToScene(QPointF(60, 20))))
        menu_action(menu, 'Delete selected').trigger()
        assert 'button1' not in scene.parts and 'led2' in scene.parts
        assert all('button1:signal' not in wire for wire in window.project.wires)
        assert ('pico:GP25', 'led1:signal') in window.project.wires

        point = view.mapFromScene(scene.ports['led2:signal'].scenePos())
        menu_action(view.context_menu(point), 'Connect from this port').trigger()
        point = view.mapFromScene(scene.ports['pico:GP0'].scenePos())
        menu_action(view.context_menu(point), 'Connect led2:signal to this port').trigger()
        assert ('led2:signal', 'pico:GP0') in window.project.wires
        point = view.mapFromScene(scene.ports['pico:GP25'].scenePos())
        menu_action(view.context_menu(point), 'Disconnect wires').trigger()
        assert all('pico:GP25' not in wire for wire in window.project.wires)
        assert ('led2:signal', 'pico:GP0') in window.project.wires
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_context_menus_follow_execution_state(app):
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        view, scene = window.view, window.scene
        point = view.mapFromScene(scene.parts['led1'].mapToScene(QPointF(60, 20)))
        old_edit = menu_action(view.context_menu(point), 'Delete selected')
        window.send('run')
        wait_for(app, lambda: window.running)
        point = view.mapFromScene(scene.parts['led1'].mapToScene(QPointF(60, 20)))
        menu = view.context_menu(point)
        assert not menu_action(menu, 'Edit label…').isEnabled()
        assert not menu_action(menu, 'Delete selected').isEnabled()
        assert not window.run_action.isEnabled()
        assert window.pause_action in menu.actions() and window.pause_action.isEnabled()
        old_edit.trigger()  # An action opened before Run still cannot mutate the circuit.
        assert 'led1' in scene.parts
        menu = view.context_menu(view.mapFromScene(scene.ports['pico:GP25'].scenePos()))
        assert not menu_action(menu, 'Disconnect wires').isEnabled()
        menu = view.context_menu(view.mapFromScene(QPointF(490, 700)))
        assert not menu_action(menu, 'Add component').isEnabled()
        window.pause_action.trigger()
        wait_for(app, lambda: not window.running)
        point = view.mapFromScene(scene.parts['led1'].mapToScene(QPointF(60, 20)))
        menu = view.context_menu(point)
        assert menu_action(menu, 'Edit label…').isEnabled()
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_context_copy_editor_output_and_waveform(app):
    from rp2040_sim.gui.menus import table_menu

    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        table = window.registers
        position = table.visualItemRect(table.item(15, 1)).center()
        menu = table_menu(table, position)
        value = table.item(15, 1).text()
        menu_action(menu, 'Copy value').trigger()
        assert app.clipboard().text() == value
        menu_action(menu, 'Copy table').trigger()
        assert app.clipboard().text().startswith('Register\tValue\n')
        assert f'PC\t{value}' in app.clipboard().text()
        assert window.editor.toPlainText()
        menu = window.output_context_menu(window.editor, QPoint(5, 5))
        assert window.build_action in menu.actions()
        assert any('Paste' in action.text() for action in menu.actions())
        window.serial.setPlainText('UART output')
        menu = window.output_context_menu(window.serial, QPoint(5, 5))
        menu_action(menu, 'Copy all').trigger()
        assert app.clipboard().text() == 'UART output'
        assert not window.interrupt_action.isEnabled()
        menu_action(menu, 'Clear output').trigger()
        assert not window.serial.toPlainText()

        window.send('run')
        wait_for(app, lambda: len(window.waveform.history) > 0)
        window.send('pause')
        wait_for(app, lambda: not window.running)
        waveform = window.waveform
        menu = waveform.context_menu()
        menu_action(menu, 'Zoom in').trigger()
        assert waveform.window_ns == 500_000_000
        menu_action(menu, 'Reset time scale').trigger()
        assert waveform.window_ns == 1_000_000_000
        instructions = window.state.instructions
        menu_action(menu, 'Clear trace').trigger()
        assert not waveform.history
        assert window.state.instructions == instructions
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_execution_inspection_and_shutdown(app, tmp_path):
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        window.send("run")
        wait_for(app, lambda: window.state.time_ns >= 300_000_000)
        window.send("button", id="button1", pressed=True)
        wait_for(app, lambda: "Button pressed" in window.serial.toPlainText())
        before = time.monotonic()
        window.send("pause")
        wait_for(app, lambda: not window.running)
        assert time.monotonic() - before < 0.5
        window.send("memory", address=0x10000000, length=16)
        wait_for(app, lambda: "10000000" in window.memory_text.toPlainText())
        instructions = window.state.instructions
        window.send("step")
        wait_for(app, lambda: window.state.instructions > instructions)
        assert window.state.instructions == instructions + 1
        window.send("reset")
        wait_for(app, lambda: window.state.instructions == 0)
        window.grab().save(str(tmp_path / "gui.png"))
        assert (tmp_path / "gui.png").stat().st_size > 1000
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()
        assert not window.thread.isRunning()


def test_gui_sdk_workloads_loading_inspection_and_reset(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from rp2040_sim import demo
    from rp2040_sim.project import Project

    project_path = tmp_path / 'sdk.json'
    Project.sdk_blink().save(project_path)
    window = Window(project_path)
    try:
        window.show()
        wait_for(app, lambda: window.state is not None and window.project.startup == 'sdk'
                 and 'SDK ELF' in window.startup_label.text())
        assert 'pico/stdlib.h' in window.editor.toPlainText()
        window.send('run')
        wait_for(app, lambda: window.state.time_ns >= 300_000_000)
        window.send('pause')
        wait_for(app, lambda: not window.running)
        assert window.state.instructions < 6000
        window.send('reset')
        wait_for(app, lambda: window.state.instructions == 0)
        image = tmp_path / 'sdk_uart.elf'
        image.write_bytes(demo.image('sdk_uart'))
        monkeypatch.setattr(QFileDialog, 'getOpenFileName',
                            lambda *a: (str(image), 'Pico SDK ELF — skip boot2 (*.elf *.axf)'))
        window.open_firmware()
        window.send('run')
        wait_for(app, lambda: 'Pico SDK UART ready' in window.serial.toPlainText())
        window.new_sdk()
        wait_for(app, lambda: not window.busy and window.state.instructions == 0
                 and window.project.startup == 'sdk' and window.project.builtin == 'sdk_blink')
        assert window.project.builtin == 'sdk_blink'
        window.new_project()
        wait_for(app, lambda: not window.busy and window.project.startup == 'direct'
                 and 'direct load' in window.startup_label.text())
        assert window.project.startup == 'direct'
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_rom_boot_and_flash_demo(app):
    from rp2040_sim.images import bootrom
    try:
        bootrom()
    except RuntimeError:
        pytest.skip('Official B2 ROM not prepared')
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        window.new_rom()
        window.send('run')
        wait_for(app, lambda: window.state.time_ns > 100_000_000 and 'B2 ROM' in window.startup_label.text())
        assert window.state.instructions > 29000
        window.new_rom(True)
        window.send('run')
        wait_for(app, lambda: 'SDK flash erase/program verified' in window.serial.toPlainText())
        window.send('pause')
        wait_for(app, lambda: not window.running)
        window.send('reset')
        wait_for(app, lambda: window.state.instructions == 0)
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_micropython_editor_execution_and_interrupt(app):
    from rp2040_sim.images import bootrom
    try:
        bootrom()
    except RuntimeError:
        pytest.skip('Official B2 ROM not prepared')
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        window.new_micropython()
        wait_for(app, lambda: window.project.builtin == 'micropython' and not window.busy)
        window.editor.setPlainText('from machine import Pin\nPin(25, Pin.OUT).value(1)\nprint("GUI Python executed")\n')
        window.build_source()
        wait_for(app, lambda: getattr(window, 'python_status', '') == 'finished', timeout=10)
        assert 'GUI Python executed' in window.serial.toPlainText()
        assert window.state.pin(25).name == 'HIGH'
        assert not window.running
        assert window.build_action.text() == 'Run Python'
        assert window.editor_pane.title.text().endswith('MicroPython')
        window.editor.setPlainText('while True:\n    pass\n')
        window.build_source()
        wait_for(app, lambda: window.python_status == 'execute')
        window.send('interrupt_python')
        wait_for(app, lambda: 'KeyboardInterrupt' in window.serial.toPlainText())
        window.send('pause')
        wait_for(app, lambda: not window.running)
        window.send('reset')
        wait_for(app, lambda: window.state.instructions == 0)
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_circuit_edits_and_compiler_failure(app):
    window = Window()
    try:
        wait_for(app, lambda: window.state is not None)
        previous = len(window.project.components)
        window.add_part()
        assert len(window.project.components) == previous + 1
        added = window.project.components[-1]
        window.add_wire("pico:GP0", added.id + ":signal")
        assert ("pico:GP0", added.id + ":signal") in window.project.wires
        window.scene.parts[added.id].setSelected(True)
        window.delete_selected()
        assert len(window.project.components) == previous
        window.editor.setPlainText("this is not valid C;")
        window.build_source()
        wait_for(app, lambda: window.build_thread is None)
        assert "error" in window.logs.toPlainText().lower() or "Install arm-none-eabi-gcc" in window.logs.toPlainText()
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_execution_controls_and_saved_clock(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from rp2040_sim.project import Project

    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        window.speed_combo.setCurrentIndex(window.speed_combo.findData(0.1))
        wait_for(app, lambda: window.playback_speed == 0.1)
        window.advanced_action.setChecked(True)
        window.clock_spin.setValue(100_000)
        window.clock_button.click()
        wait_for(app, lambda: window.state.clock_hz == 100_000)
        assert window.project.clock_hz == 100_000
        assert window.state.instructions == 0
        window.instruction_spin.setValue(123)
        window.run_n_button.click()
        wait_for(app, lambda: window.state.instructions == 123 and not window.running)
        assert window.run_n_button.isEnabled()
        assert window.clock_button.isEnabled()
        assert "0.1× playback" in window.status.text()
        destination = tmp_path / "slow.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(destination), ""))
        window.save_project()
        assert Project.load(destination).clock_hz == 100_000
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_pio_demo_inspection_and_reset(app):
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        window.new_pio_blink()
        # The queued run follows project loading; firmware then configures PIO.
        window.send("run")
        wait_for(app, lambda: window.state.time_ns >= 300_000_000)
        window.send("pause")
        wait_for(app, lambda: not window.running)
        view = window.pio_inspector
        assert view.snapshots[0].sm[0].enabled
        assert "SET PINS, 1 [24]" in view.program.item(0, 2).text()
        assert "100 Hz" in view.registers.toPlainText()
        assert int(view.machines.item(0, 1).text()) in (0, 1)
        assert window.state.instructions < 100
        view.block.setCurrentIndex(1)
        assert view.snapshots[1].sm[0].pc == 0
        assert view.machines.item(0, 2).text() == "Disabled"
        window.send("reset")
        wait_for(app, lambda: window.state.instructions == 0)
        assert not view.snapshots[0].sm[0].enabled
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()


def test_gui_dma_demo_inspection_completion_and_reset(app):
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        window.new_dma_pio()
        window.send('run')
        wait_for(app, lambda: window.state.time_ns >= 400_000_000)
        window.send('pause')
        wait_for(app, lambda: not window.running)
        view = window.dma_inspector
        assert window.inspector.currentWidget() is view
        assert view.channels.rowCount() == 12
        c = view.snapshot.channels[0]
        assert c.busy and c.waiting and 0 < c.remaining < 12
        assert view.channels.item(0, 1).text() == 'Waiting'
        assert view.channels.item(0, 4).text() == 'PIO0 TX0'
        assert 'WRITE_ADDR 50200010' in view.details.toPlainText()
        frozen = view.snapshot
        window.send('memory', address=0x50000008, length=4)
        wait_for(app, lambda: '50000008' in window.memory_text.toPlainText())
        assert view.snapshot == frozen
        window.speed_combo.setCurrentIndex(window.speed_combo.findData(4.0))
        window.send('run')
        wait_for(app, lambda: 'DMA complete' in window.serial.toPlainText())
        window.send('pause')
        wait_for(app, lambda: not window.running)
        assert not view.snapshot.channels[0].busy
        assert view.snapshot.intr == 0
        window.send('reset')
        wait_for(app, lambda: window.state.instructions == 0)
        assert all(c.ctrl == c.reload == 0 for c in view.snapshot.channels)
        assert view.channels.item(0, 1).text() == 'Idle'
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()
        assert not window.thread.isRunning()


def test_gui_multicore_launch_inspection_step_and_reset(app):
    window = Window()
    try:
        window.show()
        wait_for(app, lambda: window.state is not None)
        window.new_multicore()
        window.send('run')
        wait_for(app, lambda: 'Core 1 count: 00000001' in window.serial.toPlainText())
        window.send('pause')
        wait_for(app, lambda: not window.running)
        view = window.cores_inspector
        assert window.inspector.currentWidget() is view
        assert all(c.launched for c in view.snapshot.core)
        assert all(c.instructions > 0 for c in view.snapshot.core)
        assert view.snapshot.boot_stage == 6
        assert view.cores.item(0, 1).text() == 'WFI'
        assert view.cores.item(1, 1).text() == 'WFE'
        view.selected.setCurrentIndex(1)
        assert view.registers.item(7, 3).text() == f'{view.snapshot.core[1].registers[15]:08x}'
        assert 'MSP=2004' in view.details.toPlainText()
        before = window.state.instructions
        window.send('step')
        wait_for(app, lambda: window.state.instructions > before)
        assert window.state.instructions == before + 1
        window.send('reset')
        wait_for(app, lambda: window.state.instructions == 0)
        assert not view.snapshot.core[1].launched
        assert view.cores.item(1, 1).text() == 'Boot wait'
        assert view.snapshot.spinlocks == 0
    finally:
        window.close()
        wait_for(app, lambda: not window.thread.isRunning())
        app.processEvents()
        assert not window.thread.isRunning()
