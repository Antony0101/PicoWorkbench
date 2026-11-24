"""Cancelable CMake File API builds. This module deliberately has no Qt imports."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
import ctypes
import codecs
import fcntl
import hashlib
import json
import os
import re
import selectors
import shlex
import signal
import struct
import subprocess
import threading
import time
import uuid

from elftools.elf.elffile import ELFFile
from .sdk_tools import ToolchainProfile

RESERVED = {'PICO_BOARD', 'PICO_PLATFORM', 'PICO_SDK_PATH', 'PICO_TOOLCHAIN_PATH',
            'PICO_COMPILER', 'PICO_NO_PICOTOOL', 'CMAKE_BUILD_TYPE', 'CMAKE_GENERATOR',
            'CMAKE_TOOLCHAIN_FILE', 'CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER',
            'CMAKE_MAKE_PROGRAM', 'CMAKE_EXPORT_COMPILE_COMMANDS', 'CMAKE_HOME_DIRECTORY'}
SUFFIXES = {'.c', '.h', '.cc', '.cpp', '.hpp', '.S', '.s', '.cmake', '.txt', '.pio', '.ld', '.in'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class SdkProjectSpec:
    root: str
    target: str | None = None
    configuration: str = 'Debug'
    cache_variables: dict[str, str] = field(default_factory=dict)

    def validate(self):
        if not isinstance(self.root, str) or not self.root or '\0' in self.root:
            raise ValueError('SDK project root must be a nonempty path')
        if self.target is not None and (not isinstance(self.target, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*', self.target)):
            raise ValueError('Invalid SDK target name')
        if self.configuration not in ('Debug', 'RelWithDebInfo'):
            raise ValueError('SDK configuration must be Debug or RelWithDebInfo')
        if not isinstance(self.cache_variables, dict):
            raise ValueError('CMake definitions must be a string-valued object')
        for key, value in self.cache_variables.items():
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str) or '\0' in value:
                raise ValueError('Invalid CMake definition')
            if key in RESERVED or key.startswith(('CMAKE_GENERATOR_', 'CMAKE_SOURCE_', 'CMAKE_BINARY_')):
                raise ValueError(f'{key} is managed by PicoWorkbench')


@dataclass(frozen=True)
class ConfiguredTarget:
    name: str
    id: str
    artifacts: tuple[str, ...]
    sources: tuple[str, ...]
    generated: tuple[str, ...]
    compile_commands: tuple[dict, ...]

    @property
    def elf(self):
        candidates = [p for p in self.artifacts if Path(p).suffix == '.elf']
        if len(candidates) != 1:
            raise ValueError(f'{self.name}: expected one ELF artifact, found {len(candidates)}')
        return Path(candidates[0])


@dataclass(frozen=True)
class BuildManifest:
    session: str
    generation: int
    root: str
    target: str
    configuration: str
    toolchain: dict
    inputs: dict[str, str]
    elf_sha256: str
    verified: bool
    reasons: tuple[str, ...] = ()
    schema: int = 1
    text_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, elf):
        path = Path(str(elf) + '.manifest.json')
        try:
            if path.stat().st_size > 20_000_000:
                return None
            data = json.loads(path.read_text())
            result = cls(**data)
            hashes = (result.inputs, result.text_hashes)
            if (result.schema != 1 or result.verified is not True or
                not isinstance(result.root, str) or not Path(result.root).is_absolute() or
                not isinstance(result.target, str) or not result.target or
                not isinstance(result.session, str) or not result.session or
                type(result.generation) is not int or result.generation < 0 or
                result.configuration not in ('Debug', 'RelWithDebInfo') or
                not isinstance(result.toolchain, dict) or
                not isinstance(result.reasons, (list, tuple)) or not all(isinstance(r, str) for r in result.reasons) or
                any(not isinstance(group, dict) or any(not isinstance(p, str) or not Path(p).is_absolute() or
                    not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for p, h in group.items()) for group in hashes) or
                not result.text_hashes.keys() <= result.inputs.keys() or result.elf_sha256 != digest(elf)):
                return None
            return result
        except (ValueError, OSError, TypeError):
            return None

    def current(self):
        if not self.verified:
            return False
        try:
            return all(digest(path) == expected for path, expected in self.inputs.items())
        except OSError:
            return False

    def matches(self, filename, text):
        path = str(Path(filename).resolve())
        return self.verified and self.text_hashes.get(path, self.inputs.get(path)) == hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class BuildResult:
    path: Path
    manifest: BuildManifest
    target: ConfiguredTarget
    log: str


class Cancelled(RuntimeError):
    pass


class InputWatch:
    """Linux inotify records even changes restored before a final hash comparison."""
    def __init__(self, roots, excluded):
        libc = ctypes.CDLL(None, use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise RuntimeError('Cannot watch project inputs for build provenance')
        self.paths = {}
        self.changed = set()
        self.overflow = False
        try:
            for root in roots:
                root = Path(root)
                directories = [root, *(p for p in root.rglob('*') if p.is_dir() and not p.is_symlink())]
                for directory in directories:
                    if any(part in ('.git', '.tools', 'build', '__pycache__') for part in directory.relative_to(root).parts):
                        continue
                    if directory.is_relative_to(excluded):
                        continue
                    # MODIFY, ATTRIB, CLOSE_WRITE, MOVED_FROM/TO, CREATE/DELETE, DELETE_SELF/MOVE_SELF.
                    wd = libc.inotify_add_watch(self.fd, os.fsencode(directory), 0x00000fce)
                    if wd < 0:
                        raise RuntimeError(f'Cannot watch input directory: {directory}')
                    self.paths[wd] = directory
        except BaseException:
            os.close(self.fd)
            raise

    def drain(self):
        while True:
            try:
                data = os.read(self.fd, 262144)
            except BlockingIOError:
                break
            offset = 0
            while offset < len(data):
                wd, mask, cookie, length = struct.unpack_from('iIII', data, offset)
                name = os.fsdecode(data[offset + 16:offset + 16 + length].split(b'\0')[0])
                offset += 16 + length
                if mask & 0x4000:
                    self.overflow = True
                if wd in self.paths:
                    path = self.paths[wd] / name
                    if path.suffix in SUFFIXES or mask & 0x40000000:
                        self.changed.add(str(path.resolve()))

    def close(self):
        os.close(self.fd)


class ProjectBuilder:
    def __init__(self, spec, profile, *, session=None, generation=0, cache_root=None,
                 log=None, configure_timeout=120, build_timeout=300):
        spec.validate()
        self.spec, self.profile = spec, profile
        self.root = Path(spec.root).expanduser().resolve()
        if not (self.root / 'CMakeLists.txt').is_file():
            raise ValueError(f'No CMakeLists.txt in {self.root}')
        self.session = session or uuid.uuid4().hex
        self.generation = generation
        identity = [str(self.root), profile.identity, spec.configuration, spec.cache_variables, 'Unix Makefiles']
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
        base = Path(cache_root or Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'picoworkbench/sdk-projects')
        self.cache = base / key
        self.timeout, self.build_timeout = configure_timeout, build_timeout
        self.log_callback = log or (lambda text: None)
        self.logs = []
        self.cancelled = threading.Event()
        self.process = None
        self.targets = ()
        self.cmake_inputs = ()
        self.watcher = None

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise Cancelled('SDK build cancelled')

    @contextmanager
    def locked(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        marker = self.cache / '.picoworkbench-owned'
        if not marker.exists() and any(p.name != '.lock' for p in self.cache.iterdir()):
            raise ValueError(f'Refusing unowned cache directory: {self.cache}')
        with (self.cache / '.lock').open('a') as lock:
            start = time.monotonic()
            while True:
                self.check_cancelled()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - start > self.timeout:
                        raise RuntimeError('Timed out waiting for another SDK build in this cache')
                    self.cancelled.wait(.05)
            try:
                marker.write_text('PicoWorkbench SDK cache v1\n')
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def run(self, command, timeout):
        self.check_cancelled()
        self.log_callback('$ ' + shlex.join(map(str, command)) + '\n')
        env = os.environ.copy()
        env['CC'], env['CXX'] = self.profile.host_cc, self.profile.host_cxx or 'c++'
        env['LC_ALL'] = 'C.UTF-8'
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, env=env, cwd=self.root)
        process = self.process
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        start, termination = time.monotonic(), None
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        timed_out = False
        try:
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if self.watcher:
                    self.watcher.drain()
                if now - start > timeout:
                    timed_out = True
                if (timed_out or self.cancelled.is_set()) and termination is None:
                    termination = now
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if termination is not None and now - termination >= 2:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for key, _ in selector.select(.05):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    text = decoder.decode(data)
                    self.logs.append(text)
                    self.log_callback(text)
            code = process.wait()
            tail = decoder.decode(b'', final=True)
            if tail:
                self.logs.append(tail)
                self.log_callback(tail)
        except BaseException:
            # Ctrl+C and a failing log consumer must not leave CMake's children
            # running after the caller has released the cache lock.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                end = time.monotonic() + 2
                while time.monotonic() < end:
                    process.poll()
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.02)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        finally:
            selector.close()
            process.stdout.close()
            self.process = None
        self.check_cancelled()
        if timed_out:
            raise RuntimeError(f'SDK command timed out after {timeout}s')
        if code:
            raise RuntimeError(f'SDK command exited {code}\n' + ''.join(self.logs)[-12000:])

    def configure(self):
        with self.locked():
            return self._configure()

    def _configure(self):
        query = self.cache / '.cmake/api/v1/query/client-picoworkbench/query.json'
        atomic_write(query, json.dumps({'client': {'session': self.session, 'generation': self.generation},
            'requests': [{'kind': kind, 'version': version} for kind, version in
                         [('codemodel', 2), ('cache', 2), ('toolchains', 1), ('cmakeFiles', 1)]]}).encode())
        profile = self.profile
        command = [*profile.cmake, '-S', str(self.root), '-B', str(self.cache), '-G', 'Unix Makefiles',
            f'-DPICO_SDK_PATH={profile.sdk}', f'-DPICO_TOOLCHAIN_PATH={Path(profile.compiler).parent.parent}',
            f'-DCMAKE_C_COMPILER={profile.compiler}',
            f'-DCMAKE_CXX_COMPILER={str(Path(profile.compiler).with_name(Path(profile.compiler).name.replace("gcc", "g++")))}',
            f'-DCMAKE_MAKE_PROGRAM={profile.make}', '-DPICO_BOARD=pico', '-DPICO_PLATFORM=rp2040',
            '-DPICO_NO_PICOTOOL=1', f'-DCMAKE_BUILD_TYPE={self.spec.configuration}',
            '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON', '-DPICO_STDIO_UART=1', '-DPICO_STDIO_USB=0',
            '-DCMAKE_C_FLAGS_INIT=-fdiagnostics-color=never -fdiagnostics-column-unit=byte',
            '-DCMAKE_CXX_FLAGS_INIT=-fdiagnostics-color=never -fdiagnostics-column-unit=byte',
            *[f'-D{key}={value}' for key, value in sorted(self.spec.cache_variables.items())]]
        self.run(command, self.timeout)
        self.targets = self.read_reply()
        return self.targets

    def read_reply(self):
        reply = self.cache / '.cmake/api/v1/reply'
        indexes = sorted(reply.glob('index-*.json'))
        if not indexes:
            raise ValueError('CMake did not produce a File API reply')
        def read(name):
            path = (reply / name).resolve()
            if path.parent != reply.resolve():
                raise ValueError('Invalid File API reply path')
            return json.loads(path.read_text())
        index = read(indexes[-1].name)
        if index.get('cmake', {}).get('generator', {}).get('name', 'Unix Makefiles') != 'Unix Makefiles':
            raise ValueError('Unsupported CMake generator')
        answer = index['reply']['client-picoworkbench']['query.json']
        if answer.get('client') != {'session': self.session, 'generation': self.generation}:
            raise ValueError('Stale CMake File API reply')
        objects = {item['kind']: read(item['jsonFile']) for item in answer['responses'] if 'jsonFile' in item}
        if not {'codemodel', 'cache', 'toolchains', 'cmakeFiles'} <= objects.keys():
            raise ValueError('Incomplete CMake File API reply')
        cache = {entry['name']: entry['value'] for entry in objects['cache']['entries']}
        for key, expected in [('PICO_BOARD', 'pico'), ('PICO_PLATFORM', 'rp2040'), ('CMAKE_BUILD_TYPE', self.spec.configuration)]:
            if cache.get(key) != expected:
                raise ValueError(f'Unsupported configured {key}: {cache.get(key)}')
        for key, expected in [('CMAKE_C_COMPILER', self.profile.compiler), ('PICO_SDK_PATH', self.profile.sdk)]:
            if key in cache and Path(cache[key]).resolve() != Path(expected).resolve():
                raise ValueError(f'Configured {key} differs from the selected toolchain')
        model = objects['codemodel']
        if Path(model['paths']['source']).resolve() != self.root:
            raise ValueError('CMake reply belongs to another source root')
        self.cmake_inputs = tuple(str((self.root / p['path']).resolve()) for p in objects['cmakeFiles']['inputs'] if not p.get('isGenerated'))
        config = next((c for c in model['configurations'] if c['name'] == self.spec.configuration), None)
        if config is None:
            raise ValueError('CMake configuration is missing from reply')
        targets = {t['id']: read(t['jsonFile']) for t in config['targets']}
        database = json.loads((self.cache / 'compile_commands.json').read_text())
        commands_by_target = {}
        for entry in database:
            arguments = entry.get('arguments') or shlex.split(entry['command'])
            marker = next((re.search(r'CMakeFiles/([^/]+)\.dir/', arg) for arg in arguments if 'CMakeFiles/' in arg and '.dir/' in arg), None)
            if marker:
                commands_by_target.setdefault(marker[1], []).append({**entry, 'arguments': arguments})
        result = []
        for target in targets.values():
            if target['type'] != 'EXECUTABLE':
                continue
            if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*', target['name']):
                raise ValueError('Unsupported executable target name in CMake reply')
            members = {}
            def visit(t):
                if t['id'] in members:
                    return
                members[t['id']] = t
                for dependency in t.get('dependencies', []):
                    if dependency['id'] in targets:
                        visit(targets[dependency['id']])
            visit(target)
            sources, generated, commands = set(), set(), []
            for member in members.values():
                for source in member.get('sources', []):
                    path = (self.cache if source.get('isGenerated') else self.root) / source['path']
                    (generated if source.get('isGenerated') else sources).add(str(path.resolve()))
                commands.extend(commands_by_target.get(member['name'], []))
            result.append(ConfiguredTarget(target['name'], target['id'],
                tuple(str((self.cache / a['path']).resolve()) for a in target.get('artifacts', [])),
                tuple(sorted(sources)), tuple(sorted(generated)), tuple(commands)))
        return tuple(sorted(result, key=lambda t: t.name))

    def input_paths(self):
        paths = set(self.cmake_inputs)
        for target in self.targets:
            paths.update(target.sources)
        for root in (self.root, Path(self.profile.sdk)):
            for path in root.rglob('*'):
                if path.is_file() and path.suffix in SUFFIXES and not path.is_relative_to(self.cache) and not any(x in path.relative_to(root).parts for x in ('.git', '.tools', 'build')):
                    paths.add(str(path.resolve()))
        # Inputs discovered by previous compiler dependency files are observed before rebuilds.
        paths.update(self.dependencies())
        for root in self.profile.include_roots:
            paths.update(str(p.resolve()) for p in Path(root).rglob('*') if p.is_file())
        return {p for p in paths if Path(p).is_file() and not Path(p).is_relative_to(self.cache)}

    def dependencies(self, target=None):
        result = set()
        if target:
            depfiles = []
            for entry in target.compile_commands:
                args = entry['arguments']
                if '-o' in args:
                    output = args[args.index('-o') + 1]
                    depfiles.append(Path(entry['directory']) / (output + '.d'))
        else:
            depfiles = self.cache.rglob('*.o.d')
        for depfile in depfiles:
            if not depfile.is_file():
                continue
            text = depfile.read_text(errors='replace').replace('\\\n', '')
            if ':' not in text:
                continue
            for name in shlex.split(text.split(':', 1)[1]):
                path = (depfile.parent / name).resolve() if not Path(name).is_absolute() else Path(name).resolve()
                if path.is_file():
                    result.add(str(path))
        return result

    def build(self, output=None):
        with self.locked():
            self.watcher = InputWatch([self.root], self.cache)
            try:
                before = {p: digest(p) for p in self.input_paths()}
                before_stat = {p: (Path(p).stat().st_mtime_ns, Path(p).stat().st_ctime_ns) for p in before}
                started_ns = time.time_ns()
                self._configure()
                target = next((t for t in self.targets if t.name == self.spec.target), None)
                if target is None:
                    raise ValueError(f'Select a configured executable target: {self.spec.target!r}')
                artifact = target.elf
                reasons = []
                for path in self.input_paths() - before.keys():
                    stat = Path(path).stat()
                    if stat.st_ctime_ns >= started_ns:
                        reasons.append(f'Input modified while configuring: {path}')
                    before[path] = digest(path)
                    before_stat[path] = (stat.st_mtime_ns, stat.st_ctime_ns)
                self.input_baseline = dict(before)
                self.run([*self.profile.cmake, '--build', str(self.cache), '--target', target.name, '--parallel', '4'], self.build_timeout)
                self.watcher.drain()
                after = {}
                for path, expected in before.items():
                    try:
                        after[path] = digest(path)
                        stat = Path(path).stat()
                        if after[path] != expected or before_stat[path] != (stat.st_mtime_ns, stat.st_ctime_ns):
                            reasons.append(f'Input changed during build: {path}')
                    except OSError:
                        reasons.append(f'Input removed during build: {path}')
                generated = [Path(p) for p in target.generated]
                changed = {p for p in self.watcher.changed if Path(p) not in generated and not any(g.is_relative_to(p) for g in generated)}
                if changed or self.watcher.overflow:
                    reasons.append('Project files changed during build; rebuild from stable disk sources')
                target_dependencies = self.dependencies(target)
                for path in target_dependencies | set(target.generated):
                    if not Path(path).is_file():
                        continue
                    after[path] = digest(path)
                    if path not in before and not Path(path).is_relative_to(self.cache):
                        reasons.append(f'Unobserved dependency; rebuild to verify: {path}')
                data = artifact.read_bytes()
                elf = ELFFile(BytesIO(data))
                if elf.elfclass != 32 or elf['e_machine'] != 'EM_ARM' or elf['e_type'] != 'ET_EXEC':
                    raise ValueError('Selected artifact is not an executable 32-bit ARM ELF')
                segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
                if not segments or not any(s['p_paddr'] == 0x10000000 and s['p_filesz'] >= 264 for s in segments):
                    raise ValueError('Selected ELF lacks RP2040 flash/boot2 vectors')
                self.check_cancelled()
                required = set(self.cmake_inputs) | set(target.sources) | target_dependencies | set(target.generated)
                after = {path: value for path, value in after.items() if path in required}
                text_hashes = {}
                for path in after:
                    if Path(path).suffix in ('.c', '.h', '.cc', '.cpp', '.hpp', '.S'):
                        try:
                            text_hashes[path] = hashlib.sha256(Path(path).read_text(encoding='utf-8').encode()).hexdigest()
                        except UnicodeError:
                            pass
                manifest = BuildManifest(self.session, self.generation, str(self.root), target.name,
                    self.spec.configuration, self.profile.to_dict(), after, hashlib.sha256(data).hexdigest(), not reasons, tuple(reasons), text_hashes=text_hashes)
                result_dir = self.cache / 'results' / self.session / str(self.generation)
                published = result_dir / (target.name + '.elf')
                atomic_write(published, data)
                atomic_write(str(published) + '.manifest.json', json.dumps(manifest.to_dict(), indent=2).encode())
                if output is not None:
                    atomic_write(output, data)
                    atomic_write(str(output) + '.manifest.json', json.dumps(manifest.to_dict(), indent=2).encode())
                    published = Path(output)
                return BuildResult(published, manifest, target, ''.join(self.logs))
            finally:
                self.watcher.close()
                self.watcher = None

    def compilation_view(self, target, directory):
        """A target's commands take precedence over shared dependency contexts."""
        selected = {}
        for entry in target.compile_commands:
            selected.setdefault(str(Path(entry['file']).resolve()), entry)
        directory = Path(directory)
        atomic_write(directory / 'compile_commands.json', json.dumps(list(selected.values()), indent=2).encode())
        return directory
