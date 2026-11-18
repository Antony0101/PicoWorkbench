"""Prepare the official B2 ROM locally; ROM bytes are not packaged with the app."""
from io import BytesIO
from pathlib import Path
import argparse
import hashlib
import urllib.request

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://github.com/raspberrypi/pico-bootrom-rp2040/releases/download/b2/b2.elf'
ELF_SHA256 = '3a3c4c617d83bc9b910a6fa7bfdffa11ce26ff89370271d861507be821cc49aa'
ROM_SHA256 = '9c19b46f068c21f90d200c514faad4a0d5cecfc978f155b8c9d25cb6bc2efd81'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf', type=Path, help='Use an already downloaded official b2.elf')
    parser.add_argument('--output', type=Path, default=ROOT / '.tools/bootrom-b2.bin')
    args = parser.parse_args()
    if args.elf:
        data = args.elf.read_bytes()
    else:
        with urllib.request.urlopen(URL, timeout=60) as response:
            data = response.read(1_000_001)
    if hashlib.sha256(data).hexdigest() != ELF_SHA256:
        raise SystemExit('Downloaded ELF does not match the pinned official B2 release')
    elf = ELFFile(BytesIO(data))
    rom = next(s.data() for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD' and s['p_paddr'] == 0)
    if hashlib.sha256(rom).hexdigest() != ROM_SHA256:
        raise SystemExit('Unexpected B2 ROM payload hash')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rom)
    print(f'Prepared {args.output} (16 KiB, SHA256 {ROM_SHA256})')


if __name__ == '__main__':
    main()
