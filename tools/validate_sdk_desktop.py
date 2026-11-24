"""Real imported-project desktop checks; repeat with offscreen and WSLg Wayland.

Requires the prepared SDK/ARM/host toolchains. Writes only review artifacts.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from rp2040_sim.gui.app import STYLE, Window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/phase4-desktop')
    parser.add_argument('--project-root', type=Path, help='Reuse a prepared validation fixture')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = args.project_root.resolve() if args.project_root else output / 'project'
    if not root.exists():
        shutil.copytree(ROOT / 'firmware/sdk_project', root)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    settings = QSettings(str(output / 'settings.ini'), QSettings.IniFormat)
    settings.clear()
    window = Window(settings=settings)
    results = {'platform': app.platformName(), 'scale': app.primaryScreen().devicePixelRatio(), 'checks': {}}

    def wait(condition, timeout=120):
        end = time.monotonic() + timeout
        while not condition():
            app.processEvents()
            time.sleep(.01)
            if time.monotonic() > end:
                raise AssertionError(window.status.text() + '\n' + window.logs.toPlainText()[-4000:])

    def capture(name):
        app.processEvents()
        window.grab().save(str(output / (name + '.png')))
        assert window.width() <= 1366 and window.height() <= 768, (window.size(), window.minimumSizeHint())

    try:
        window.show()
        window.resize(1366, 768)
        wait(lambda: window.state is not None and not window.busy)
        window.open_sdk_project(root)
        window.configure_sdk()
        wait(lambda: window.build_thread is None)
        assert {t.name for t in window.sdk_targets} >= {'project_a', 'project_b'}, window.logs.toPlainText()
        window.sdk_target.setCurrentText('project_b')
        assert window.open_sdk_source(root / 'main.c')
        window.select_workspace('Code')
        capture('sdk-code')
        window.activateWindow()
        app.processEvents()
        QTest.qWait(20)
        window.editor.setFocus()
        QTest.keyClick(window.editor, Qt.Key.Key_F7)
        wait(lambda: window.build_thread is None and not window.busy and window.loaded_manifest is not None)
        assert window.loaded_manifest.verified
        for panel in ('serial', 'build', 'editor'):
            window.panels.detach(panel)
        window.send('run')
        wait(lambda: 'project:42' in window.serial.toPlainText())
        window.send('pause')
        wait(lambda: not window.running)
        results['checks']['configure_build_and_live_floating_serial'] = True
        for size in (11, 16, 24):
            window.set_editor_font(size)
            for workspace in ('Circuit', 'Code', 'Debug'):
                window.select_workspace(workspace)
                capture(f'{workspace.lower()}-{size}')
                assert window.minimumSizeHint().width() <= 1366 and window.minimumSizeHint().height() <= 768
                assert window.panels.panels['editor'].host.isVisible()
        results['checks']['floating_workspace_font_and_laptop_layout'] = True
        window.panels.dock_all()
        assert window.editor.toPlainText() == (root / 'main.c').read_text()
        assert window.source_matches(root / 'main.c')
        results['checks']['dock_all_preserves_source_and_manifest'] = True
        results['size'] = [window.width(), window.height()]
        results['minimum_size'] = [window.minimumSizeHint().width(), window.minimumSizeHint().height()]
        results['manifest'] = window.loaded_manifest.to_dict()
        (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({k: v for k, v in results.items() if k != 'manifest'}))
    finally:
        window._closing = True
        window.close()
        wait(lambda: not window.thread.isRunning(), timeout=15)


if __name__ == '__main__':
    main()
