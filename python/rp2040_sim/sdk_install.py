"""Explicit, cancellable installation/removal of an application-owned Pico SDK."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import threading
import urllib.request

SDK_VERSION = '2.2.0'
SDK_SHA256 = 'dbd8db79015aced4ef35cbaa88051b0d451775010d20202e1c7d2bee69c25b35'
SDK_URL = 'https://codeload.github.com/raspberrypi/pico-sdk/tar.gz/refs/tags/2.2.0'
MARKER = '.picoworkbench-install.json'


def tools_directory():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'picoworkbench/tools'


def managed_sdk():
    return tools_directory() / f'pico-sdk-{SDK_VERSION}'


def owned_sdk(path):
    path = Path(path)
    if path.is_symlink() or path.resolve() != managed_sdk().resolve():
        return False
    try:
        marker = json.loads((path / MARKER).read_text())
        return marker == {'owner': 'PicoWorkbench', 'kind': 'pico-sdk', 'version': SDK_VERSION, 'archive_sha256': SDK_SHA256}
    except (OSError, ValueError):
        return False


def install_sdk(*, archive=None, cancel=None, progress=None):
    cancel = cancel or threading.Event()
    progress = progress or (lambda text: None)
    destination = managed_sdk()
    if destination.exists():
        if owned_sdk(destination):
            return destination
        raise ValueError(f'Refusing to overwrite an existing unmanaged SDK: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    def check():
        if cancel.is_set():
            raise RuntimeError('SDK installation cancelled; incomplete files removed')
    with tempfile.TemporaryDirectory(prefix='.sdk-install-', dir=destination.parent) as temporary:
        staging = Path(temporary)
        if archive is None:
            archive = staging / 'sdk.tar.gz'
            progress('Downloading Pico SDK 2.2.0…')
            with urllib.request.urlopen(SDK_URL, timeout=15) as source, archive.open('wb') as output:
                total = 0
                while True:
                    check()
                    chunk = source.read(262144)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 64 * 1024 * 1024:
                        raise ValueError('SDK download exceeds the expected size limit')
                    output.write(chunk)
                    progress(f'Downloaded {total // 1024} KiB')
        archive = Path(archive)
        check()
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SDK_SHA256:
            raise ValueError('SDK archive SHA256 mismatch; nothing installed')
        progress('Verified archive. Extracting SDK…')
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                check()
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe SDK archive entry')
                bundle.extract(member, staging, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
        check()
        tree = staging / f'pico-sdk-{SDK_VERSION}'
        if not (tree / 'external/pico_sdk_import.cmake').is_file():
            raise ValueError('Downloaded archive is not the expected Pico SDK')
        (tree / MARKER).write_text(json.dumps({'owner': 'PicoWorkbench', 'kind': 'pico-sdk',
            'version': SDK_VERSION, 'archive_sha256': SDK_SHA256}))
        # rename never merges with an existing user directory.
        if destination.exists():
            raise ValueError('Another SDK installation already occupies the destination')
        tree.rename(destination)
    progress(f'Installed SDK: {destination}')
    return destination


def remove_sdk(path):
    if not owned_sdk(path):
        raise ValueError('Only the SDK installed by PicoWorkbench can be removed here; external SDKs are left untouched')
    shutil.rmtree(path)
