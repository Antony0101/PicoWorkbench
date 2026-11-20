"""Rebuild the bundled ARM demo and its editable source assets."""
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rp2040_sim.build import build

def bundle(name, sdk=False):
    output = ROOT / f"build/{name}.elf"
    source = ROOT / f"firmware/{'sdk' if sdk else 'fixtures'}/{name}.c"
    linker = ROOT / "firmware/fixtures/direct.ld"
    if sdk:
        output = ROOT / f"build/sdk/{name}.elf"
    else:
        build(source, output, linker)
    data = output.read_bytes()
    phoff = struct.unpack_from("<I", data, 28)[0]
    phsize, phcount = struct.unpack_from("<HH", data, 42)
    segments = []
    for i in range(phcount):
        p = list(struct.unpack_from("<8I", data, phoff + i * phsize))
        if p[0] == 1 and p[5]:
            segments.append((p, data[p[1]:p[1] + p[4]]))
    header = bytearray(data[:52])
    struct.pack_into("<II", header, 28, 52, 0)
    struct.pack_into("<HHHHH", header, 40, 52, 32, len(segments), 0, 0)
    struct.pack_into("<H", header, 50, 0)
    image = bytearray(header + bytes(32 * len(segments)))
    for i, (p, content) in enumerate(segments):
        while len(image) % 4:
            image.append(0)
        p[1], p[7] = len(image), 4
        struct.pack_into("<8I", image, 52 + i * 32, *p)
        image.extend(content)
    assets = ROOT / "python/rp2040_sim/assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / f"{name}.elf.hex").write_text(image.hex() + "\n")
    shutil.copyfile(source, assets / f"{name}.c")
    shutil.copyfile(linker, assets / "direct.ld")
    print(f"Bundled {name}: {len(image)}-byte ARM ELF and editable source")


if __name__ == "__main__":
    names = sys.argv[1:] or ("blink", "workbench", "pio_blink", "dma_pio", "multicore")
    for name in names:
        if name not in ("blink", "workbench", "pio_blink", "dma_pio", "multicore", "sdk_blink", "sdk_uart", "sdk_flash", "sdk_dashboard"):
            raise SystemExit("Unknown demo: " + name)
        bundle(name, sdk=name.startswith("sdk_"))
