"""Pinned official-example metadata. Reading it never installs or downloads tools."""
from pathlib import Path
import hashlib
import json
import os

ROOT = Path(__file__).resolve().parents[2]


def load_catalog():
    return json.loads((Path(__file__).parent / 'assets/sdk-examples.json').read_text())


def examples_directory():
    root = Path(os.environ.get('PICO_EXAMPLES_PATH', ROOT / '.tools/pico-examples-sdk-2.2.0')).resolve()
    catalog = load_catalog()
    tree = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.name == '.picoworkbench-examples.json' or any(p in ('.git', 'build', '__pycache__') for p in path.relative_to(root).parts):
            continue
        tree.update(str(path.relative_to(root)).encode())
        tree.update(hashlib.sha256(path.read_bytes()).digest())
    if tree.hexdigest() != catalog['source_tree_sha256']:
        raise ValueError('Official examples are missing or modified. Prepare the verified sdk-2.2.0 archive, or set PICO_EXAMPLES_PATH to its unchanged source tree.')
    for entry in catalog['workloads']:
        for name, expected in entry['source_hashes'].items():
            try:
                actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            except OSError as error:
                raise ValueError('Prepare pinned examples explicitly: python tools/prepare_sdk_examples.py --download; or set PICO_EXAMPLES_PATH') from error
            if actual != expected:
                raise ValueError(f'Pinned example changed: {root / name}')
    return root
