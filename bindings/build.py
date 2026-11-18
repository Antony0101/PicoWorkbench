"""Compile the C engine into the out-of-line CFFI extension."""
from pathlib import Path
import importlib.util
import sys

from cffi import FFI

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.find_spec("unicorn")
if spec is None:
    raise RuntimeError("Install unicorn>=2.1.4 before building the engine")
UNICORN = Path(spec.origin).parent
LOCAL_HEADERS = ROOT / ".tools/python/usr/include"
if sys.platform != "linux":
    raise RuntimeError("The native build currently supports Linux; see docs/compatibility.md")

ffibuilder = FFI()
header = (ROOT / "engine/include/rp2040_sim.h").read_text()
declarations = header.split("/* CFFI BEGIN */", 1)[1].split("/* CFFI END */", 1)[0]
ffibuilder.cdef(declarations)
ffibuilder.set_source(
    "rp2040_sim._native",
    '#include "rp2040_sim.h"',
    sources=[f"engine/src/{name}.c" for name in
             ("sim", "cpu", "bus", "scheduler", "gpio", "peripherals", "pio", "dma", "multicore", "system", "bootrom", "image", "flash", "rtc")],
    depends=["engine/include/rp2040_sim.h", "engine/src/sim_internal.h"],
    include_dirs=[str(ROOT / "engine/include"), str(UNICORN / "include"),
                  str(LOCAL_HEADERS / f"python{sys.version_info.major}.{sys.version_info.minor}"),
                  str(LOCAL_HEADERS)],
    extra_objects=[str(UNICORN / "lib/libunicorn.so.2")],
    extra_compile_args=["-std=c11", "-Wall", "-Wextra", "-Werror", "-O2"],
    runtime_library_dirs=["$ORIGIN/../unicorn/lib", str(UNICORN / "lib")],
)

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
