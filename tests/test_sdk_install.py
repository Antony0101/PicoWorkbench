"""Managed SDK installation must never remove externally supplied directories."""
from pathlib import Path
import hashlib
import io
import tarfile
import threading

import pytest
from rp2040_sim import sdk_install


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    path = tmp_path / 'sdk.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        data = b'# sdk import\n'
        entry = tarfile.TarInfo('pico-sdk-2.2.0/external/pico_sdk_import.cmake')
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    monkeypatch.setattr(sdk_install, 'SDK_SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_install_remove_owned_sdk_and_preserve_projects(archive, tmp_path):
    project = tmp_path / 'main.c'
    project.write_text('user source')
    path = sdk_install.install_sdk(archive=archive)
    assert sdk_install.owned_sdk(path)
    assert sdk_install.install_sdk(archive=archive) == path
    sdk_install.remove_sdk(path)
    assert not path.exists()
    assert project.read_text() == 'user source'


def test_unmanaged_and_symlinked_sdk_cannot_be_removed(archive, tmp_path):
    external = tmp_path / 'external-sdk'
    external.mkdir()
    with pytest.raises(ValueError, match='Only the SDK'):
        sdk_install.remove_sdk(external)
    sdk_install.managed_sdk().parent.mkdir(parents=True)
    sdk_install.managed_sdk().symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='unmanaged'):
        sdk_install.install_sdk(archive=archive)
    assert external.exists()


def test_bad_hash_and_cancellation_leave_no_install(archive, monkeypatch):
    monkeypatch.setattr(sdk_install, 'SDK_SHA256', 'wrong')
    with pytest.raises(ValueError, match='SHA256'):
        sdk_install.install_sdk(archive=archive)
    assert not sdk_install.managed_sdk().exists()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match='cancelled'):
        sdk_install.install_sdk(archive=archive, cancel=cancel)
    assert not list(sdk_install.managed_sdk().parent.glob('.sdk-install-*'))
