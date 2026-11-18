"""Cancelable, shell-free builds of freestanding Cortex-M0+ firmware."""
from pathlib import Path
import os
import shutil
import signal
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def compiler() -> str:
    configured = os.environ.get("ARM_CC")
    if configured:
        return configured
    found = shutil.which("arm-none-eabi-gcc")
    local = ROOT / ".tools/arm/usr/bin/arm-none-eabi-gcc"
    if found:
        return found
    if local.is_file():
        return str(local)
    raise RuntimeError("Install arm-none-eabi-gcc or set ARM_CC to its executable path")


def build_command(source: Path, output: Path, linker: Path) -> list[str]:
    return [compiler(), "-mcpu=cortex-m0plus", "-mthumb", "-std=c11", "-O1", "-g3",
            "-ffreestanding", "-fno-builtin", "-fno-unwind-tables", "-nostdlib",
            "-Wall", "-Wextra", "-Werror", "-Wl,--build-id=none",
            "-T", str(linker.resolve()), str(source.resolve()), "-o", str(output.resolve())]


class FirmwareBuild:
    def __init__(self, source: Path, output: Path, linker: Path):
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self.process = subprocess.Popen(build_command(source, output, linker),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, start_new_session=True)

    def finish(self, timeout: float = 60) -> str:
        try:
            log, _ = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.cancel()
            try:
                self.process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.communicate()
            raise RuntimeError("Firmware build timed out") from None
        if self.process.returncode:
            raise RuntimeError(log.strip() or "Firmware build failed")
        return log

    def cancel(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def build(source: Path, output: Path, linker: Path) -> str:
    return FirmwareBuild(source, output, linker).finish()


class SdkFirmwareBuild(FirmwareBuild):
    def __init__(self, source: Path, output: Path, linker: Path | None = None):
        from .sdk_build import sdk_path
        sdk = sdk_path()
        cc = compiler()
        toolchain = Path(shutil.which(cc) or cc).resolve().parent.parent
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("sdk_build.py")),
             str(source.resolve()), str(output.resolve()), str(sdk), str(toolchain)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)

    def finish(self, timeout: float = 180) -> str:
        return super().finish(timeout)
