"""Fetch hash-pinned sources, build the UART profile, and package its UF2 asset."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from rp2040_sim.build import compiler
from rp2040_sim.images import elf_flash, uf2
from rp2040_sim.sdk_build import cmake_command

SOURCES = (
    ('micropython-1.26.1', 'micropython/micropython', 'refs/tags/v1.26.1',
     '10600e844f02a8b448cf176df71031d2eb3d731097a809a963ce9c35cef3d613', '.tools/micropython-1.26.1'),
    ('pico-sdk-micropython-1.26.1', 'raspberrypi/pico-sdk', '9a4113fbbae65ee82d8cd6537963bc3d3b14bcca',
     '6151b5b4541b83b38b3e4f91995a79d895cb689b3d3c2c8ebc2e19ea66a86854', '.tools/pico-sdk-micropython-1.26.1'),
    ('tinyusb-micropython-1.26.1', 'hathach/tinyusb', '5217cee5de4cd555018da90f9f1bcc87fb1c1d3a',
     'c1c9dca6664960b4707f3b100f0e037a5680902a47cb5c0858205bedeb179d3c', '.tools/micropython-1.26.1/lib/tinyusb'),
    ('mbedtls-micropython-1.26.1', 'Mbed-TLS/mbedtls', '107ea89daaefb9867ea9121002fbbdf926780e98',
     '7dc3219b00ab2d33d5108a6cfb1b6d11c1476f882493acdf0be5bcc9a181336b', '.tools/micropython-1.26.1/lib/mbedtls'),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_source(name, repo, revision, sha256, destination):
    archive = ROOT / f'.tools/downloads/{name}.tar.gz'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file() or digest(archive) != sha256:
        temporary = archive.with_suffix('.download')
        url = f'https://codeload.github.com/{repo}/tar.gz/{revision}'
        print('Downloading', url, flush=True)
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if digest(temporary) != sha256:
            raise RuntimeError(f'Unexpected source archive hash for {name}')
        temporary.replace(archive)
    target = ROOT / destination
    marker = target / '.sim-source-sha256'
    if marker.is_file() and marker.read_text() == sha256:
        return
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            # Strip the archive's single root folder; the data filter rejects
            # traversal, special files and links escaping the destination.
            member.name = member.name.partition('/')[2]
            if member.name:
                bundle.extract(member, target, filter='data')
    marker.write_text(sha256)


def main():
    for item in SOURCES:
        prepare_source(*item)
    mp = ROOT / '.tools/micropython-1.26.1'
    sdk = ROOT / '.tools/pico-sdk-micropython-1.26.1'
    env = dict(os.environ, SOURCE_DATE_EPOCH='1757548800')
    subprocess.run(['make', '-C', str(mp / 'mpy-cross'), '-j4'], env=env, check=True)
    cc = compiler()
    cc = str(Path(shutil.which(cc) or cc).resolve())
    output = ROOT / 'build/micropython-uart'
    command = cmake_command()
    subprocess.run(command + ['-S', str(mp / 'ports/rp2'), '-B', str(output),
        f'-DPICO_SDK_PATH_OVERRIDE={sdk}', f'-DPICO_TOOLCHAIN_PATH={Path(cc).parent.parent}',
        '-DPICO_NO_PICOTOOL=1', '-DMICROPY_BOARD=SIM_PICO',
        f'-DMICROPY_BOARD_DIR={ROOT / "firmware/micropython"}',
        f'-DMICROPY_LIB_DIR={ROOT / "firmware/micropython"}', f'-DPython3_EXECUTABLE={sys.executable}'], env=env, check=True)
    subprocess.run(command + ['--build', str(output), '--parallel', '4'], env=env, check=True)
    image = uf2(elf_flash((output / 'firmware.elf').read_bytes()))
    (output / 'firmware.uf2').write_bytes(image)
    assets = ROOT / 'python/rp2040_sim/assets'
    (assets / 'micropython.uf2.hex').write_text(image.hex() + '\n')
    shutil.copyfile(ROOT / 'firmware/micropython/blink.py', assets / 'micropython.py')
    manifest = {'version': '1.26.1', 'startup': 'rom', 'profile': 'SIM_PICO UART; USB disabled',
        'compiler': subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
        'sources': {name: {'repository': repo, 'revision': revision, 'archive_sha256': sha} for name, repo, revision, sha, _ in SOURCES},
        'profile_sha256': {p.name: digest(p) for p in sorted((ROOT / 'firmware/micropython').iterdir()) if p.is_file()},
        'uf2_sha256': hashlib.sha256(image).hexdigest(), 'source_date_epoch': env['SOURCE_DATE_EPOCH']}
    (assets / 'micropython-provenance.json').write_text(json.dumps(manifest, indent=2) + '\n')
    notices = [('MicroPython 1.26.1', mp / 'LICENSE'),
               ('MbedTLS', mp / 'lib/mbedtls/LICENSE'), ('TinyUSB', mp / 'lib/tinyusb/LICENSE'),
               ('Pico SDK', sdk / 'LICENSE.TXT')]
    text = 'MICROPYTHON FIRMWARE NOTICES\n\nSee also SDK-NOTICES.txt for compiler and C runtime notices.\n'
    for name, path in notices:
        text += '\n===== ' + name + ' =====\n\n' + path.read_text() + '\n'
    cmsis = sdk / 'src/rp2_common/cmsis/stub/CMSIS/Core/Include/core_cm0plus.h'
    text += '\n===== CMSIS core header attribution (Apache-2.0, full text above) =====\n' + cmsis.read_text().split('*/', 1)[0] + '*/\n'
    (assets / 'MICROPYTHON-NOTICES.txt').write_text(text)
    print(f'Bundled MicroPython UART profile: {len(image)} UF2 bytes', flush=True)


if __name__ == '__main__':
    main()
