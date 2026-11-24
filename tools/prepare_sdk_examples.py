"""Explicitly prepare the verified upstream SDK examples; never called by tests."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
COMMIT = '4c3a3dc0196dd426fddd709616d0da984e027bab'
ARCHIVE_SHA256 = '9873c546686ecd40fbb9d06af5e76e84eb30ec83494ef6b12bef1516f9140f36'
URL = f'https://codeload.github.com/raspberrypi/pico-examples/tar.gz/{COMMIT}'


def prepare(archive=None, checkout=None, download=False):
    destination = ROOT / '.tools/pico-examples-sdk-2.2.0'
    if checkout:
        source = Path(checkout).resolve()
        from rp2040_sim.sdk_examples import examples_directory
        import os
        previous = os.environ.get('PICO_EXAMPLES_PATH')
        try:
            os.environ['PICO_EXAMPLES_PATH'] = str(source)
            examples_directory()
        finally:
            if previous is None:
                os.environ.pop('PICO_EXAMPLES_PATH', None)
            else:
                os.environ['PICO_EXAMPLES_PATH'] = previous
        return source
    archive = Path(archive) if archive else ROOT / f'.tools/downloads/pico-examples-{COMMIT}.tar.gz'
    if not archive.exists():
        if not download:
            raise ValueError('Supply --archive, --checkout or explicitly request --download')
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(URL, timeout=60) as response:
            data = response.read(32 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
            raise ValueError('Downloaded examples archive hash mismatch')
        archive.write_bytes(data)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('Examples archive hash mismatch')
    if destination.exists():
        marker = destination / '.picoworkbench-examples.json'
        if not marker.exists() or json.loads(marker.read_text()).get('archive_sha256') != ARCHIVE_SHA256:
            raise ValueError(f'Refusing to overwrite an unverified directory: {destination}')
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                name = Path(member.name)
                if name.is_absolute() or '..' in name.parts or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe path in examples archive')
            bundle.extractall(staging, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
        tree = staging / f'pico-examples-{COMMIT}'
        (tree / '.picoworkbench-examples.json').write_text(json.dumps({
            'commit': COMMIT, 'archive_sha256': ARCHIVE_SHA256, 'upstream': URL}))
        shutil.move(str(tree), destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--checkout', type=Path)
    args = parser.parse_args()
    print(prepare(args.archive, args.checkout, args.download))


if __name__ == '__main__':
    main()
