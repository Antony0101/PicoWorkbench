"""Local SDK/tool discovery, shared by the desktop and headless builders."""
from dataclasses import asdict, dataclass
from importlib.util import find_spec
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SDK_SOURCE_DIGEST = 'db9bedccc3e1052ff09ca381c124fd3264ffeb062af1897b08219bbebc6d9ce6'


def executable(selected, variable, local, names):
    candidate = selected or os.environ.get(variable, '')
    if candidate:
        found = shutil.which(candidate) or str(Path(candidate).expanduser().resolve())
        if not Path(found).is_file() or not os.access(found, os.X_OK):
            raise ValueError(f'{variable}: executable not found: {candidate}')
        return str(Path(found).resolve())
    for candidate in local:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    for name in names:
        if found := shutil.which(name):
            return str(Path(found).resolve())
    raise ValueError(f'{variable}: select an executable or install {names[0]}')


def probe(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f'Tool probe failed: {command[0]}: {error}') from error
    if result.returncode:
        raise ValueError(f'Tool probe failed: {command[0]}: {result.stderr.strip()}')
    return result.stdout.strip()


@dataclass(frozen=True)
class ToolchainProfile:
    sdk: str
    compiler: str
    cmake: tuple[str, ...]
    make: str
    host_cc: str
    host_cxx: str
    sdk_version: str
    compiler_version: str
    cmake_version: str
    newlib: str
    identity: str
    warnings: tuple[str, ...] = ()
    include_roots: tuple[str, ...] = ()

    @classmethod
    def discover(cls, *, sdk_path='', compiler='', cmake='', make='', host_cc='', host_cxx=''):
        from .sdk_install import managed_sdk
        local_sdk = ROOT / '.tools/pico-sdk-2.2.0'
        sdk = Path(sdk_path or os.environ.get('PICO_SDK_PATH') or (local_sdk if local_sdk.is_dir() else managed_sdk())).expanduser().resolve()
        if not (sdk / 'external/pico_sdk_import.cmake').is_file():
            raise ValueError('Select a local Pico SDK 2.2.0 checkout (PICO_SDK_PATH)')
        version_file = sdk / 'pico_sdk_version.cmake'
        text = version_file.read_text()
        parts = [re.search(rf'set\(PICO_SDK_VERSION_{key}\s+(\d+)\)', text) for key in ('MAJOR', 'MINOR', 'REVISION')]
        version = '.'.join(p[1] for p in parts if p)
        if version != '2.2.0':
            raise ValueError(f'SDK {version or "unknown"} is outside the supported 2.2.0 profile')
        cc = executable(compiler, 'ARM_CC', [ROOT / '.tools/arm/usr/bin/arm-none-eabi-gcc'], ['arm-none-eabi-gcc'])
        cxx = Path(cc).with_name(Path(cc).name.replace('gcc', 'g++'))
        if not cxx.is_file():
            raise ValueError(f'ARM C++ driver needed by SDK configuration: {cxx}')
        if cmake or os.environ.get('CMAKE'):
            cmake_cmd = (executable(cmake, 'CMAKE', [], ['cmake']),)
        elif find_spec('cmake'):
            cmake_cmd = (sys.executable, '-m', 'cmake')
        else:
            cmake_cmd = (executable('', 'CMAKE', [], ['cmake']),)
        cmake_version = probe([*cmake_cmd, '--version']).splitlines()[0]
        number = re.search(r'(\d+)\.(\d+)', cmake_version)
        if not number or tuple(map(int, number.groups())) < (3, 25):
            raise ValueError('CMake 3.25 or newer is required')
        make_path = executable(make, 'MAKE', [], ['make'])
        host_c = executable(host_cc, 'CC', [], ['cc', 'gcc', 'clang'])
        warnings = []
        try:
            host_cpp = executable(host_cxx, 'CXX', [], ['c++', 'g++', 'clang++'])
            probe([host_cpp, '--version'])
        except ValueError:
            if host_cxx or os.environ.get('CXX'):
                raise
            host_cpp = ''
            warnings.append('Host C++ compiler missing: install g++ for generated PIO headers')
        compiler_version = probe([cc, '--version']).splitlines()[0]
        if probe([cc, '-dumpmachine']) != 'arm-none-eabi':
            raise ValueError('Select an arm-none-eabi GCC cross compiler')
        newlib = probe([cc, '-mcpu=cortex-m0plus', '-mthumb', '-print-file-name=libc.a'])
        if not Path(newlib).is_file():
            raise ValueError('ARM Newlib libc.a was not found; install the ARM Newlib package')
        stdcpp = probe([str(cxx), '-mcpu=cortex-m0plus', '-mthumb', '-print-file-name=libstdc++.a'])
        if not Path(stdcpp).is_file():
            raise ValueError('ARM libstdc++ is missing. Install libstdc++-arm-none-eabi-dev and libstdc++-arm-none-eabi-newlib, or select a complete ARM toolchain.')
        try:
            headers = subprocess.run([str(cxx), '-E', '-Wp,-v', '-x', 'c++', '-include', 'cstdlib', '/dev/null'],
                                     capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError(f'ARM header probe failed: {error}') from error
        if headers.returncode:
            raise ValueError('ARM C++ headers are missing (cstdlib). Install libstdc++-arm-none-eabi-dev.\n' + headers.stderr[-1000:])
        includes = tuple(str(Path(line.strip()).resolve()) for line in headers.stderr.splitlines()
                         if line.startswith(' ') and Path(line.strip()).is_dir())
        for command in ([make_path, '--version'], [host_c, '--version'], [str(cxx), '--version']):
            probe(command)
        identity_data = [str(sdk), version, cc, compiler_version, cmake_cmd, cmake_version,
                         make_path, host_c, host_cpp, str(Path(newlib).resolve()),
                         hashlib.sha256(text.encode()).hexdigest(),
                         hashlib.sha256(Path(newlib).read_bytes()).hexdigest(),
                         hashlib.sha256(Path(stdcpp).read_bytes()).hexdigest()]
        # Include local modifications in the SDK identity without requiring git.
        sdk_files = sorted(p for p in sdk.rglob('*') if p.is_file() and p.suffix in ('.h', '.c', '.cmake', '.txt'))
        sdk_hash = hashlib.sha256()
        for path in sdk_files:
            sdk_hash.update(str(path.relative_to(sdk)).encode())
            sdk_hash.update(hashlib.sha256(path.read_bytes()).digest())
        identity_data.append(sdk_hash.hexdigest())
        if sdk_hash.hexdigest() != SDK_SOURCE_DIGEST:
            warnings.append('SDK sources differ from the pinned archive or contain additional sources; installation is modified/unverified')
        identity = hashlib.sha256(json.dumps(identity_data, sort_keys=True).encode()).hexdigest()
        return cls(str(sdk), cc, cmake_cmd, make_path, host_c, host_cpp, version,
                   compiler_version, cmake_version, str(Path(newlib).resolve()), identity, tuple(warnings), includes)

    def to_dict(self):
        return asdict(self)
