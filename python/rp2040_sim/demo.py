from importlib.resources import files

NAMES = ("blink", "workbench", "pio_blink", "dma_pio", "multicore", "sdk_blink", "sdk_uart", "sdk_flash", "sdk_dashboard", "micropython")


def dashboard_source():
    assets = files('rp2040_sim').joinpath('assets')
    return assets.joinpath('ssd1306.py').read_text() + '\n' + assets.joinpath('dashboard.py').read_text()


def image(name="blink") -> bytes:
    if name not in NAMES:
        raise ValueError("Unknown bundled demo")
    suffix = 'uf2' if name == 'micropython' else 'elf'
    return bytes.fromhex(files("rp2040_sim").joinpath(f"assets/{name}.{suffix}.hex").read_text())


def source(name="blink") -> str:
    if name not in NAMES:
        raise ValueError("Unknown bundled demo")
    suffix = 'py' if name == 'micropython' else 'c'
    return files("rp2040_sim").joinpath(f"assets/{name}.{suffix}").read_text()


def linker() -> str:
    return files("rp2040_sim").joinpath("assets/direct.ld").read_text()
