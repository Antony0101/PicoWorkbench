"""Desktop adapter for the headless SDK service and single-buffer project editing."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import threading
import uuid

from PySide6.QtCore import QFileSystemWatcher, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..sdk_tools import ToolchainProfile
from ..sdk_projects import (BuildManifest, Cancelled, ProjectBuilder, SdkProjectSpec, atomic_write, digest)

TOOL_KEYS = ('sdk_path', 'compiler', 'cmake', 'make', 'host_cc', 'host_cxx')


def timeout_setting(settings, key, default):
    try:
        return max(15, min(3600, settings.value('sdk/' + key, default, int)))
    except (ValueError, TypeError, OverflowError):
        return default


class SdkTask(QObject):
    done = Signal(object)
    failed = Signal(object)
    log = Signal(str)
    finished = Signal()

    def __init__(self, spec, options, session, generation, build=False, timeouts=None):
        super().__init__()
        self.spec, self.options = spec, options
        self.session, self.generation, self.build = session, generation, build
        self.job = None
        self.cancelled = False
        self.timeouts = timeouts or {}

    def cancel(self):
        self.cancelled = True
        if self.job:
            self.job.cancel()

    @Slot()
    def run(self):
        try:
            profile = ToolchainProfile.discover(**self.options)
            if self.cancelled:
                return
            self.job = ProjectBuilder(self.spec, profile, session=self.session,
                generation=self.generation, log=self.log.emit, **self.timeouts)
            if self.cancelled:
                self.job.cancel()
                return
            result = self.job.build() if self.build else self.job.configure()
            if not self.cancelled:
                self.done.emit((self.generation, self.job, result))
        except Cancelled:
            pass
        except Exception as error:
            if not self.cancelled:
                self.failed.emit((self.generation, str(error), getattr(self.job, 'input_baseline', {})))
        finally:
            self.finished.emit()


class ProbeTask(QObject):
    done = Signal(object)
    finished = Signal()

    def __init__(self, options):
        super().__init__()
        self.options = options

    @Slot()
    def run(self):
        try:
            result = ToolchainProfile.discover(**self.options)
        except Exception as error:
            result = str(error)
        self.done.emit(result)
        self.finished.emit()


class InstallTask(QObject):
    done = Signal(object)
    progress = Signal(str)
    finished = Signal()

    def __init__(self, remove=False):
        super().__init__()
        self.remove = remove
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    @Slot()
    def run(self):
        from ..sdk_install import install_sdk, managed_sdk, remove_sdk
        try:
            if self.remove:
                remove_sdk(managed_sdk())
                result = ('removed', str(managed_sdk()))
            else:
                result = ('installed', str(install_sdk(cancel=self.cancelled, progress=self.progress.emit)))
        except Exception as error:
            result = ('error', str(error))
        self.done.emit(result)
        self.finished.emit()


class SetupDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Pico SDK setup')
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        info = QLabel('Select local tools for the SDK 2.2.0 / RP2040 Pico profile. Empty fields use environment, local tools and PATH. Applying settings preserves the running chip.')
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.fields = {}
        labels = ('Pico SDK root', 'ARM GCC', 'CMake', 'Make', 'Host C compiler', 'Host C++ compiler')
        for key, label in zip(TOOL_KEYS, labels):
            row = QWidget()
            controls = QHBoxLayout(row)
            controls.setContentsMargins(0, 0, 0, 0)
            field = QLineEdit(window.settings.value('sdk/' + key, '', str))
            field.setAccessibleName(label)
            browse = QPushButton('Browse…')
            def choose(checked=False, key=key, field=field):
                path = QFileDialog.getExistingDirectory(self, 'Select SDK root') if key == 'sdk_path' else QFileDialog.getOpenFileName(self, 'Select executable')[0]
                if path:
                    field.setText(path)
            browse.clicked.connect(choose)
            controls.addWidget(field, 1)
            controls.addWidget(browse)
            self.fields[key] = field
            form.addRow(label, row)
        layout.addLayout(form)
        self.timeouts = {}
        for key, label, default in [('configure_timeout', 'Configure timeout (seconds)', 120), ('build_timeout', 'Build timeout (seconds)', 300)]:
            value = QSpinBox()
            value.setRange(15, 3600)
            value.setValue(timeout_setting(window.settings, key, default))
            form.addRow(label, value)
            self.timeouts[key] = value
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setAccessibleName('SDK setup detection results')
        layout.addWidget(self.result, 1)
        instructions = QLabel('Ubuntu/Debian dependencies: gcc-arm-none-eabi, libnewlib-arm-none-eabi, libstdc++-arm-none-eabi-dev, libstdc++-arm-none-eabi-newlib, cmake, make and g++.\nRun tools/install_pico_sdk.py --linux-dependencies in a terminal for interactive installation. Install/remove below manages the SDK source only.')
        instructions.setWordWrap(True)
        instructions.setOpenExternalLinks(True)
        layout.addWidget(instructions)
        managed = QHBoxLayout()
        install = QPushButton('Install SDK 2.2.0')
        install.clicked.connect(lambda: self.install(False))
        remove = QPushButton('Remove managed SDK')
        remove.clicked.connect(lambda: self.install(True))
        self.cancel_install = QPushButton('Cancel installation')
        self.cancel_install.setEnabled(False)
        self.cancel_install.clicked.connect(lambda: self.task.cancel() if isinstance(self.task, InstallTask) else None)
        for button in (install, remove, self.cancel_install):
            managed.addWidget(button)
        layout.addLayout(managed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close)
        self.recheck = buttons.addButton('Recheck', QDialogButtonBox.ButtonRole.ActionRole)
        self.reconfigure = buttons.addButton('Apply + Reconfigure', QDialogButtonBox.ButtonRole.ActionRole)
        self.recheck.clicked.connect(self.check)
        self.reconfigure.clicked.connect(lambda: self.apply(True))
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.thread = None
        QTimer.singleShot(0, self.check)

    def options(self):
        return {key: field.text().strip() for key, field in self.fields.items()}

    def apply(self, reconfigure=False):
        if self.thread and reconfigure:
            self.result.appendPlainText('Finish or cancel the current setup operation before reconfiguring.')
            return
        for key, value in self.options().items():
            self.window.settings.setValue('sdk/' + key, value)
        for key, widget in self.timeouts.items():
            self.window.settings.setValue('sdk/' + key, widget.value())
        self.window.invalidate_sdk()
        self.result.appendPlainText('Settings applied. Configure/build again to refresh metadata.')
        if reconfigure:
            self.accept()
            self.window.configure_sdk()

    def check(self):
        if self.thread:
            return
        self.recheck.setEnabled(False)
        self.result.setPlainText('Checking local SDK, ARM/Newlib, CMake, Make and host compilers…')
        self.thread = QThread(self.window)
        self.task = ProbeTask(self.options())
        self.task.moveToThread(self.thread)
        self.thread.started.connect(self.task.run)
        self.task.done.connect(self.checked)
        self.task.finished.connect(self.thread.quit)
        self.task.finished.connect(self.task.deleteLater)
        self.thread.finished.connect(self.finished_check)
        self.thread.start()

    @Slot(object)
    def checked(self, result):
        self.result.setPlainText(json.dumps(result.to_dict(), indent=2) if isinstance(result, ToolchainProfile) else result)

    @Slot()
    def finished_check(self):
        self.thread.deleteLater()
        self.thread = None
        self.recheck.setEnabled(True)
        self.cancel_install.setEnabled(False)

    def install(self, remove):
        if self.thread or self.window.build_thread is not None:
            return
        from ..sdk_install import managed_sdk, owned_sdk
        destination = managed_sdk()
        if remove:
            if not owned_sdk(destination):
                self.result.setPlainText('No application-managed SDK is installed. External SDKs cannot be removed here.')
                return
            answer = QMessageBox.question(self, 'Remove managed SDK',
                f'Remove {destination}, including any local changes inside this managed SDK? Project files are outside this directory.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.thread = QThread(self.window)
        self.task = InstallTask(remove)
        self.task.moveToThread(self.thread)
        self.thread.started.connect(self.task.run)
        self.task.done.connect(self.installed)
        self.task.progress.connect(self.result.setPlainText)
        self.task.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.task.finished.connect(self.task.deleteLater)
        self.thread.finished.connect(self.finished_check)
        self.recheck.setEnabled(False)
        self.cancel_install.setEnabled(not remove)
        self.thread.start()

    @Slot(object)
    def installed(self, result):
        kind, message = result
        self.result.setPlainText(f'{kind}: {message}')
        if kind == 'installed':
            self.fields['sdk_path'].setText(message)
            self.apply()
        elif kind == 'removed':
            if self.fields['sdk_path'].text() == message:
                self.fields['sdk_path'].clear()
            self.apply()

    def reject(self):
        if self.thread:
            self.result.appendPlainText('Tool checks are still running; each probe has an eight-second timeout.')
            return
        super().reject()


class SdkWorkflow:
    def setup_sdk(self):
        self.sdk_session = uuid.uuid4().hex
        self.sdk_generation = 0
        self.sdk_builder = None
        self.sdk_targets = ()
        self.loaded_manifest = None
        self.sdk_watched_manifest = None
        self.sdk_inputs_current = False
        self.sdk_input_watcher = QFileSystemWatcher(self)
        self.sdk_input_watcher.fileChanged.connect(self.sdk_input_changed)
        self.sdk_input_watcher.directoryChanged.connect(self.sdk_input_changed)
        self.sdk_manifest_timer = QTimer(self)
        self.sdk_manifest_timer.setSingleShot(True)
        self.sdk_manifest_timer.setInterval(150)
        self.sdk_manifest_timer.timeout.connect(self.recheck_sdk_manifest)
        self.sdk_disk_hash = digest(self.source_path) if self.source_path and self.source_path.is_file() else None
        if self.source_saved != self.editor.toPlainText():
            self.sdk_disk_hash = None
        self.sdk_controls = QWidget()
        layout = QVBoxLayout(self.sdk_controls)
        layout.setContentsMargins(4, 2, 4, 2)
        first = QHBoxLayout()
        self.sdk_root_label = QLabel()
        self.sdk_root_label.setWordWrap(True)
        first.addWidget(self.sdk_root_label, 1)
        self.sdk_configure_button = QPushButton('Configure')
        self.sdk_configure_button.clicked.connect(self.configure_sdk)
        first.addWidget(self.sdk_configure_button)
        self.sdk_detach_button = QPushButton('Detach project')
        self.sdk_detach_button.clicked.connect(self.detach_sdk)
        first.addWidget(self.sdk_detach_button)
        layout.addLayout(first)
        second = QHBoxLayout()
        self.sdk_target = QComboBox()
        self.sdk_target.setAccessibleName('SDK firmware target')
        self.sdk_target.setMinimumContentsLength(12)
        self.sdk_target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sdk_target.currentTextChanged.connect(self.select_sdk_target)
        self.sdk_configuration = QComboBox()
        self.sdk_configuration.setAccessibleName('SDK build configuration')
        self.sdk_configuration.addItems(['Debug', 'RelWithDebInfo'])
        self.sdk_configuration.currentTextChanged.connect(self.select_sdk_configuration)
        self.sdk_source = QComboBox()
        self.sdk_source.setAccessibleName('SDK target source file')
        self.sdk_source.setMinimumContentsLength(16)
        self.sdk_source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sdk_source.activated.connect(self.select_sdk_source)
        for label, widget in [('Target', self.sdk_target), ('Configuration', self.sdk_configuration), ('Source', self.sdk_source)]:
            second.addWidget(QLabel(label))
            second.addWidget(widget, 1 if widget is self.sdk_source else 0)
        layout.addLayout(second)
        self.editor_pane.layout().insertWidget(1, self.sdk_controls)
        self.refresh_sdk_controls()

    def watch_sdk_manifest(self, manifest):
        if manifest is self.sdk_watched_manifest:
            return
        self.sdk_watched_manifest = manifest
        old = self.sdk_input_watcher.files() + self.sdk_input_watcher.directories()
        if old:
            self.sdk_input_watcher.removePaths(old)
        self.sdk_inputs_current = bool(manifest and manifest.current())
        if manifest:
            paths = list(manifest.inputs)
            self.sdk_input_watcher.addPaths([p for p in paths if Path(p).is_file()])

    def sdk_input_changed(self, path):
        self.sdk_inputs_current = False
        self.editor.set_execution(0)
        self.refresh_breakpoints()
        self.update_actions()
        self.assistance.update_build_diagnostics()
        self.sdk_manifest_timer.start()

    def recheck_sdk_manifest(self):
        manifest = self.sdk_watched_manifest
        self.sdk_inputs_current = bool(manifest and manifest.current())
        if manifest:
            watched = set(self.sdk_input_watcher.files())
            missing = [path for path in manifest.inputs if path not in watched and Path(path).is_file()]
            if missing:
                self.sdk_input_watcher.addPaths(missing)
        self.refresh_breakpoints()
        self.assistance.update_build_diagnostics()
        self.update_actions()

    def tool_options(self):
        return {key: self.settings.value('sdk/' + key, '', str) for key in TOOL_KEYS}

    def invalidate_sdk(self):
        self.sdk_generation += 1
        self.sdk_builder = None
        self.sdk_targets = ()
        if isinstance(self.build_task, SdkTask):
            self.build_task.cancel()
        self.refresh_sdk_controls()
        had_context = self.assistance.project_context is not None
        self.assistance.set_project_context(None)
        if not had_context:
            self.assistance.restart()

    def refresh_sdk_controls(self):
        if not hasattr(self, 'sdk_controls'):
            return
        spec = self.project.sdk_project
        self.sdk_controls.setVisible(spec is not None)
        if spec is None:
            return
        self.sdk_root_label.setText(str(self.project.resolve(spec.root)))
        self.update_sdk_enabled()
        self.sdk_configuration.blockSignals(True)
        self.sdk_configuration.setCurrentText(spec.configuration)
        self.sdk_configuration.blockSignals(False)
        self.sdk_target.blockSignals(True)
        self.sdk_target.clear()
        self.sdk_target.addItems([t.name for t in self.sdk_targets])
        self.sdk_target.setCurrentIndex(self.sdk_target.findText(spec.target or ''))
        self.sdk_target.blockSignals(False)
        self.sdk_source.clear()
        target = next((t for t in self.sdk_targets if t.name == spec.target), None)
        if target:
            root = self.project.resolve(spec.root).resolve()
            own = sorted(p for p in target.sources if self.is_sdk_source(Path(p)) and Path(p).suffix in ('.c', '.h', '.S', '.hpp'))
            # Project headers not explicitly listed by CMake remain accessible.
            own = sorted(set(own) | {str(p.resolve()) for p in root.rglob('*.h') if self.is_sdk_source(p) and not any(x in p.relative_to(root).parts for x in ('build', '.git', '.tools'))})
            for path in own:
                import os
                self.sdk_source.addItem(os.path.relpath(path, root), path)
            self.sdk_source.setCurrentIndex(self.sdk_source.findData(str(self.source_path) if self.source_path else ''))
        if self.sdk_builder and target:
            self.assistance.set_project_context((self.sdk_builder, target, self.sdk_generation))

    def update_sdk_enabled(self):
        if not hasattr(self, 'sdk_controls'):
            return
        enabled = self.build_thread is None and not self.busy
        for widget in (self.sdk_configure_button, self.sdk_detach_button, self.sdk_target, self.sdk_configuration, self.sdk_source):
            widget.setEnabled(enabled)

    def is_sdk_source(self, path):
        path = Path(path).resolve()
        spec = self.project.sdk_project
        if spec is None or path.suffix not in ('.c', '.h', '.S', '.hpp'):
            return False
        target = next((t for t in self.sdk_targets if t.name == spec.target), None)
        if target and str(path) in target.generated:
            return False
        if self.sdk_builder:
            excluded = [Path(self.sdk_builder.profile.sdk), self.sdk_builder.cache,
                        *map(Path, self.sdk_builder.profile.include_roots)]
            if any(path.is_relative_to(directory) for directory in excluded):
                return False
        if path.is_relative_to(self.project.resolve(spec.root).resolve()):
            return True
        return bool(target and any(path.is_relative_to(Path(source).parent)
            for source in target.sources if self.is_project_library_source(Path(source))))

    def is_project_library_source(self, path):
        """External CMake library sources establish their editable source dirs."""
        if self.sdk_builder:
            excluded = [Path(self.sdk_builder.profile.sdk), self.sdk_builder.cache,
                        *map(Path, self.sdk_builder.profile.include_roots)]
            return not any(path.is_relative_to(directory) for directory in excluded)
        return False

    def protect_sdk_source(self):
        if not self.project.sdk_project or not self.source_path or self.editor.toPlainText() == self.source_saved:
            return True
        answer = QMessageBox.warning(self, 'Unsaved source', 'Save source changes before switching files?',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return self.save_sdk_source() if answer == QMessageBox.StandardButton.Save else answer == QMessageBox.StandardButton.Discard

    def open_sdk_project(self, root=None):
        if self.busy or self.build_thread is not None:
            return
        if not root or isinstance(root, bool):
            root = QFileDialog.getExistingDirectory(self, 'Open Pico SDK CMake project')
        if not root:
            return
        root = Path(root).resolve()
        if not (root / 'CMakeLists.txt').is_file():
            self.error('Choose a directory containing CMakeLists.txt')
            return
        if not self.protect_sdk_source() or not self.confirm_unsaved():
            return
        if self.project.builtin == 'micropython':
            self.error('Open a C circuit before attaching a Pico SDK project.')
            return
        self.project.sdk_project = SdkProjectSpec(str(root))
        if self.project.build_mode == 'direct':
            self.project.build_startup = 'sdk'
        self.invalidate_sdk()
        self.logs.appendPlainText('SDK project attached. Configure to discover targets; loaded firmware is preserved.')
        self.document_changed()
        self.panels.reveal('editor')

    def detach_sdk(self):
        if not self.project.sdk_project or self.build_thread is not None or not self.protect_sdk_source():
            return
        self.project.sdk_project = None
        self.invalidate_sdk()
        self.document_changed()

    def select_sdk_target(self, name):
        spec = self.project.sdk_project
        if not spec or not name or name == spec.target:
            return
        if not self.protect_sdk_source():
            self.refresh_sdk_controls()
            return
        spec.target = name
        self.sdk_generation += 1
        self.refresh_sdk_controls()
        if self.sdk_source.count() and self.sdk_source.currentIndex() < 0:
            first = next((self.sdk_source.itemData(i) for i in range(self.sdk_source.count()) if Path(self.sdk_source.itemData(i)).suffix == '.c'), None)
            if first:
                self.open_sdk_source(Path(first))
        self.document_changed()

    def select_sdk_configuration(self, name):
        if self.project.sdk_project and name != self.project.sdk_project.configuration:
            self.project.sdk_project.configuration = name
            self.invalidate_sdk()
            self.document_changed()

    def select_sdk_source(self, index):
        path = self.sdk_source.itemData(index)
        if path and not self.open_sdk_source(Path(path)):
            self.refresh_sdk_controls()

    def open_sdk_source(self, path, line=1, column=1):
        path = path.resolve()
        spec = self.project.sdk_project
        if not spec or not self.is_sdk_source(path):
            return False
        if path != self.source_path:
            if self.build_thread is not None or not self.protect_sdk_source():
                return False
            try:
                data = path.read_bytes()
                text = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            except (OSError, UnicodeError) as error:
                self.error(str(error))
                return False
            self._loading_document = True
            self.source_path = path
            self.project.source = str(path)
            self.device_source_path = self.device_source_saved = None
            self.source_saved = text
            self.sdk_disk_hash = hashlib.sha256(data).hexdigest()
            self.editor.setPlainText(text)
            self._loading_document = False
            self.assistance.set_context('c', self.project.build_mode, path)
            self.document_changed()
        self.sdk_source.setCurrentIndex(self.sdk_source.findData(str(path)))
        self.panels.reveal('editor')
        self.editor.go_to_line(line, column)
        return True

    def save_sdk_source(self):
        if self.build_thread is not None or not self.source_path or not self.is_sdk_source(self.source_path):
            self.error('Finish the build and select a project source before saving.')
            return False
        try:
            if digest(self.source_path) != self.sdk_disk_hash:
                self.error('The source changed on disk. Reopen it or save a copy before resolving this conflict.')
                return False
            data = self.editor.toPlainText().encode('utf-8')
            atomic_write(self.source_path, data)
            self.sdk_disk_hash = hashlib.sha256(data).hexdigest()
            self.source_saved = self.editor.toPlainText()
            self.document_changed()
            return True
        except OSError as error:
            self.error(str(error))
            return False

    def configure_sdk(self):
        self.start_sdk_job(False)

    def build_sdk_project(self):
        if self.source_path and self.editor.toPlainText() != self.source_saved:
            answer = QMessageBox.question(self, 'Save source before building', 'Build the imported project from saved disk files?',
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if answer != QMessageBox.StandardButton.Save or not self.save_sdk_source():
                return
        if not self.project.sdk_project.target:
            self.error('Configure the project and select an executable target before building.')
            return
        self.start_sdk_job(True)

    def start_sdk_job(self, build):
        if not self.project.sdk_project or self.busy or self.build_thread is not None:
            return
        spec = deepcopy(self.project.sdk_project)
        spec.root = str(self.project.resolve(spec.root).resolve())
        self.sdk_generation += 1
        self.build_text = self.editor.toPlainText()
        self.sdk_job_log = ''
        timeouts = {key: timeout_setting(self.settings, key, default)
                    for key, default in [('configure_timeout', 120), ('build_timeout', 300)]}
        self.build_task = SdkTask(spec, self.tool_options(), self.sdk_session, self.sdk_generation, build, timeouts)
        self.build_thread = QThread(self)
        self.build_task.moveToThread(self.build_thread)
        self.build_thread.started.connect(self.build_task.run)
        self.build_task.done.connect(self.sdk_done)
        self.build_task.failed.connect(self.sdk_failed)
        self.build_task.log.connect(self.sdk_log)
        self.build_task.finished.connect(self.build_thread.quit, Qt.ConnectionType.DirectConnection)
        self.build_task.finished.connect(self.build_task.deleteLater)
        self.build_thread.finished.connect(self.build_finished)
        self.cancel_action.setEnabled(True)
        self.build_thread.start()
        self.panels.reveal('build')
        self.update_actions()
        self.refresh_sdk_controls()

    def sdk_log(self, text):
        self.sdk_job_log += text
        self.logs.moveCursor(QTextCursor.MoveOperation.End)
        self.logs.insertPlainText(text)

    def sdk_done(self, packet):
        generation, builder, result = packet
        if (generation != self.sdk_generation or self._closing or not self.project.sdk_project or
                getattr(self.build_task, 'cancelled', False)):
            return
        self.sdk_builder = builder
        self.sdk_targets = builder.targets
        self.refresh_sdk_controls()
        if not hasattr(result, 'manifest'):
            self.logs.appendPlainText(f'Configured {len(builder.targets)} executable targets. Select a target, then Build + Load.')
            return
        self.assistance.compiled_project(self.sdk_job_log, result.manifest)
        if not result.manifest.verified or not result.manifest.current():
            self.logs.appendPlainText('Build inputs changed or could not be verified. Rebuild before loading.\n' + '\n'.join(result.manifest.reasons))
            return
        startup = self.project.build_mode
        def load():
            if generation != self.sdk_generation:
                return
            def loaded(ok):
                if ok:
                    self.project.firmware = str(result.path.resolve())
                    self.project.startup = startup
                    self.project.build_startup = None
                    self.loaded_manifest = result.manifest
                    self._read_symbols(result.path)
                    self.clear_breakpoints()
                    self.document_changed()
            self.request('load', loaded, path=result.path, startup=startup, source=None,
                manifest=result.manifest, discard_flash=self.discard_flash)
            self.discard_flash = False
        self.confirm_flash(load)

    def sdk_failed(self, packet):
        generation, message, baseline = packet
        if generation != self.sdk_generation or self._closing or not self.project.sdk_project:
            return
        self.assistance.compiled_project(self.sdk_job_log + '\n' + message, None, baseline)
        self.error(message)

    def show_sdk_setup(self):
        self.sdk_setup_dialog = SetupDialog(self)
        self.sdk_setup_dialog.exec()

    def official_sdk_examples(self):
        from ..sdk_examples import examples_directory, load_catalog
        catalog = load_catalog()
        dialog = QDialog(self)
        dialog.setWindowTitle('Official Pico SDK examples')
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        info = QLabel('Pinned sdk-2.2.0 examples. Opening attaches their unchanged upstream project. Configure and build the selected target to run.\nPrepare sources explicitly: python tools/prepare_sdk_examples.py --download')
        info.setWordWrap(True)
        layout.addWidget(info)
        choice = QComboBox()
        for entry in catalog['workloads']:
            choice.addItem(f'{entry["target"]} · {entry["startup"]} · {entry.get("status", "untested")}', entry)
        layout.addWidget(choice)
        details = QLabel()
        details.setWordWrap(True)
        def changed():
            entry = choice.currentData()
            details.setText(entry['description'] + '\nCircuit: ' + entry['circuit'])
        choice.currentIndexChanged.connect(changed)
        changed()
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                root = examples_directory()
            except (ValueError, OSError) as error:
                self.error(str(error))
                return
            self.open_sdk_project(root)
            if self.project.sdk_project and self.project.resolve(self.project.sdk_project.root).resolve() == root:
                self.project.sdk_project.target = choice.currentData()['target']
                self.project.build_startup = choice.currentData()['startup']
                self.document_changed()
