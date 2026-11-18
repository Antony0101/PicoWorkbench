"""Rebuild and bundle the pinned SDK workloads from an existing SDK checkout."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from rp2040_sim.build import compiler
from rp2040_sim.sdk_build import cmake_command, sdk_path
from prepare_demo import bundle


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    import shutil
    cc = compiler()
    cc = str(Path(shutil.which(cc) or cc).resolve())
    command = cmake_command()
    sdk = sdk_path()
    subprocess.run(command + ['-S', str(ROOT / 'firmware/sdk'), '-B', str(ROOT / 'build/sdk'),
                   f'-DPICO_SDK_PATH={sdk}', f'-DPICO_TOOLCHAIN_PATH={Path(cc).parent.parent}',
                   '-DPICO_BOARD=pico', '-DPICO_NO_PICOTOOL=1', '-DCMAKE_BUILD_TYPE=Release'], check=True)
    subprocess.run(command + ['--build', str(ROOT / 'build/sdk'), '--parallel', '4'], check=True)
    manifest = {
        'pico_sdk': '2.2.0', 'board': 'pico', 'startup': 'sdk',
        'sdk_archive_sha256': 'dbd8db79015aced4ef35cbaa88051b0d451775010d20202e1c7d2bee69c25b35',
        'compiler': subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
        'profile_sha256': digest(ROOT / 'firmware/sdk/CMakeLists.txt'),
        'newlib': '4.4.0.20231231-2 (Ubuntu)', 'workloads': {},
    }
    for name in ('sdk_blink', 'sdk_uart', 'sdk_flash'):
        bundle(name, sdk=True)
        manifest['workloads'][name] = {
            'startup': 'rom' if name == 'sdk_flash' else 'sdk',
            'source_sha256': digest(ROOT / f'firmware/sdk/{name}.c'),
            'bundled_elf_sha256': hashlib.sha256(bytes.fromhex(
                (ROOT / f'python/rp2040_sim/assets/{name}.elf.hex').read_text())).hexdigest(),
        }
    (ROOT / 'python/rp2040_sim/assets/sdk-provenance.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
