"""Build the supported SDK profile using a locally installed SDK and toolchain.

The subprocess entry point keeps configure/build and their children in the
cancelable process group created by SdkFirmwareBuild. It never downloads tools.
"""
from importlib.util import find_spec
from pathlib import Path
import os
import shutil
import subprocess
import sys


def sdk_path(configured='') -> Path:
    root = Path(__file__).resolve().parents[2]
    # This module is also a subprocess script; avoid relative imports here.
    local = root / '.tools/pico-sdk-2.2.0'
    managed = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'picoworkbench/tools/pico-sdk-2.2.0'
    path = Path(configured or os.environ.get('PICO_SDK_PATH') or (local if local.is_dir() else managed)).expanduser().resolve()
    if not (path / "external/pico_sdk_import.cmake").is_file():
        raise RuntimeError("Set PICO_SDK_PATH to a Pico SDK 2.2.0 checkout; see docs/sdk-startup.md")
    return path


def cmake_command() -> list[str]:
    if os.environ.get('CMAKE'):
        return [os.environ['CMAKE']]
    if find_spec("cmake"):
        return [sys.executable, "-m", "cmake"]
    executable = shutil.which("cmake")
    if executable:
        return [executable]
    raise RuntimeError("Install CMake 3.25 or newer to build SDK firmware")


def main():
    configure_only = '--configure-only' in sys.argv
    arguments = [a for a in sys.argv[1:] if a != '--configure-only']
    source, output, sdk, toolchain = map(Path, arguments)
    project = output.parent / "sdk-editor"
    project.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, project / "editor.c")
    shutil.copyfile(Path(__file__).parent / "assets/sdk.CMakeLists.txt", project / "CMakeLists.txt")
    command = cmake_command()
    subprocess.run(command + ["-S", str(project), "-B", str(project / "out"),
                   f"-DPICO_SDK_PATH={sdk}", f"-DPICO_TOOLCHAIN_PATH={toolchain}",
                   "-DPICO_BOARD=pico", "-DPICO_NO_PICOTOOL=1", "-DCMAKE_BUILD_TYPE=Release",
                   "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                   *([f'-DCMAKE_MAKE_PROGRAM={os.environ["RP2040_SDK_MAKE"]}'] if os.environ.get('RP2040_SDK_MAKE') else [])], check=True)
    if configure_only:
        return
    subprocess.run(command + ["--build", str(project / "out"), "--parallel", "4"], check=True)
    shutil.copyfile(project / "out/editor.elf", output)


if __name__ == "__main__":
    main()
