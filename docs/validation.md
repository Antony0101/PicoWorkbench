# Validation

## Automated checks

Run `.venv/bin/python -m pytest -q`. The suite covers native instructions/flags,
ELF validation, alias semantics, GPIO nets, trace overflow, timer/interrupt/sleep
behavior, nested exceptions and process stacks, UART inspection, Python device
request ordering, I2C/SPI interactions, PWM, ADC, PIO, DMA, two-core synchronization, project persistence, replay,
firmware compilation/cancellation, and offscreen GUI interaction/shutdown.
Playback checks use a controlled host clock to verify speed changes, bounded
catch-up, pause/resume, and sub-instruction slow-motion intervals. They compare
firmware events against headless execution, check exact instruction limits across
batches and early breakpoint stops, and exercise clock reset/persistence in the GUI.

Firmware compiler tests skip if ARM GCC is absent. GUI tests skip if Qt is absent.
The fully prepared environment runs all checks.
The current Linux workspace passes 333 tests, including thirteen GUI checks.
Context-menu checks exercise viewport right clicks, component placement/editing,
selection-aware deletion, connecting/disconnecting ports, mutation guards while
running, clipboard output, standard editor actions, and trace display controls.
The new checks execute bundled SDK 2.2.0 blink/UART through crt0 and runtime init,
validate physical load addresses and RAM vector installation, compare reset and
batch-size traces, and exercise reset, PLL/mux readiness, SIO division and bounded
ROM memory helpers. A local SDK compiler check builds edited UART source through
the same CMake profile used by the editor. See [startup evidence](sdk-startup.md).
The SDK increment preserves ABI version 1 and exports 22 public functions, with
no private helpers exported. The native C contract also loads a synthetic SDK
ELF and inspects its ROM ABI header. An installed Linux wheel runs both SDK
demos and the CLI, and includes verified asset hashes, the SDK CMake profile and
third-party notices. Offscreen GUI checks additionally build, load and execute
edited SDK UART source through F7.
ROM/UF2 add two public loaders, for 24 exported functions and no exported private
helpers. New tests execute real ROM/boot2, reject malformed UF2/CRC, verify serial
flash persistence, and exercise RTC calendar/alarm behavior. MicroPython tests
run arithmetic/math, GPIO/sleep, ADC, RTC, files, raw uploads/errors, deterministic
replay, CLI/export and GUI execution/interruption. Its exact build dependencies
and asset hashes are recorded in [the profile](micropython.md).

After native source changes, rebuild the extension before running Python checks:

```sh
.venv/bin/python setup.py build_ext --inplace
.venv/bin/python -m pytest -q
```

`make native` separately builds a shared library and a C contract executable.
`engine_test path/to/image.elf` additionally exercises a firmware image through
the C interface, servicing bus requests with a deterministic dummy responder.
Python device-model tests provide the detailed sensor/loopback assertions.

The native module extraction was checked against all 74 existing tests and the
C contract. Before/after captures of blink, workbench, buses, and interrupts
fixtures matched byte for byte, including copied snapshots, timestamped events,
and sampled SRAM after queued GPIO/UART/ADC inputs. The public header was
unchanged, and dynamic-symbol inspection confirmed the same 18 exported C API
functions with no exported private helpers.

PIO adds an independent copied-state API query while retaining ABI version 1
and the original snapshot layout. Its tests cover all instruction families,
FIFO joins/overflow/stalls, automatic transfers, forced execution, divider/delay
waveforms, side-set during WAIT, and all four PIO interrupt lines waking WFI.
The bundled PIO demo executes only 27 CPU instructions over 1.1 virtual seconds,
with GPIO25 high/low widths of 250 ms. See [PIO validation](pio.md#verification).

DMA adds `sim_dma_snapshot` without changing ABI version 1 or the original
snapshot structure. DMA tests check all twelve channels and widths, aliases/reloads,
rings, chaining/control blocks, bus errors, executable SRAM replacement, both IRQ
wake paths, all sixteen PIO DREQs, ADC, and fractional timers. The integrated
`dma_pio` fixture emits six pulses with 250 ms high/low widths, handles a DMA
completion interrupt, and replays the same events after reset. It uses 128 CPU
instructions over 3.1 virtual seconds. See [DMA verification](dma.md#inspection-and-verification).

The two-core increment adds `sim_cores_snapshot` while retaining ABI version 1
and the original snapshot layout. Dynamic-symbol inspection confirms 21 public
functions and no private module exports. Tests cover CPU bank isolation, FIFO
interrupts/capacity/error flags, contended spinlocks, core-1 reset/relaunch,
SEV/WFE, private SysTick/VTOR/PRIMASK/PSP/SVC, both GPIO interrupt banks,
core-1 device requests and DMA setup, breakpoints and aggregate instruction
budgets. Different execution batch sizes produce identical state and traces.
The bundled two-core demo performs four timer-driven SRAM/FIFO exchanges in
1.1 virtual seconds using 1,129 aggregate CPU instructions. Its GUI test inspects
both banks, steps one instruction and resets core 1 to its launch wait state.
See [two-core verification](multicore.md#api-and-verification).

## Address and undefined-behavior sanitizers

```sh
.venv/bin/python -m cmake -S . -B build/sanitize \
    -DPython3_EXECUTABLE="$PWD/.venv/bin/python" \
    -DSIM_SANITIZE=ON -DCMAKE_BUILD_TYPE=Debug
.venv/bin/python -m cmake --build build/sanitize
.venv/bin/python -m cmake --build build/sanitize --target test
build/sanitize/engine_test build/workbench.elf
build/sanitize/engine_test build/pio_blink.elf
build/sanitize/engine_test build/dma_pio.elf
build/sanitize/engine_test build/multicore.elf
```

When running under `ptrace`, LeakSanitizer cannot inspect the process. In that
environment only, prefix the test commands with `ASAN_OPTIONS=detect_leaks=0`.
AddressSanitizer and UndefinedBehaviorSanitizer remain enabled. Leak detection is
not validated by those runs.

All 286 native/runtime tests also pass against the sanitized shared library
through CFFI's ABI mode. On this Ubuntu x86-64 workspace, that check can be reproduced after
building `build/sanitize`:

```sh
ASAN_OPTIONS=detect_leaks=0 LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libasan.so.8 \
    .venv/bin/python - <<'PY'
import os
from pathlib import Path
from cffi import FFI
import rp2040_sim.engine as engine
import pytest

# The parent has loaded ASan; compiler subprocesses use their normal libraries.
os.environ.pop("LD_PRELOAD", None)
ffi = FFI()
header = Path("engine/include/rp2040_sim.h").read_text()
ffi.cdef(header.split("/* CFFI BEGIN */")[1].split("/* CFFI END */")[0])
engine.ffi = ffi
engine.lib = ffi.dlopen(str(Path("build/sanitize/librp2040_sim.so").resolve()))
raise SystemExit(pytest.main([
    "tests/test_engine.py", "tests/test_interrupts.py", "tests/test_multicore.py",
    "tests/test_pio.py", "tests/test_dma.py", "tests/test_devices.py",
    "tests/test_pwm_adc.py", "tests/test_sdk.py", "tests/test_boot.py",
    "tests/test_rtc.py", "tests/test_micropython.py", "-q",
]))
PY
```

## Demos and display validation

```sh
.venv/bin/python tools/prepare_demo.py
.venv/bin/rp2040-sim run --project examples/workbench.json --duration-ms 1000
.venv/bin/rp2040-sim run --project examples/pio_blink.json --duration-ms 1100
.venv/bin/rp2040-sim run --project examples/dma_pio.json --duration-ms 3100
.venv/bin/rp2040-sim run --project examples/multicore.json --duration-ms 1100
.venv/bin/rp2040-sim run --project examples/sdk_blink.json --duration-ms 1100
.venv/bin/rp2040-sim run --project examples/sdk_uart.json --duration-ms 10
.venv/bin/rp2040-sim run --project examples/rom_flash.json --duration-ms 200
.venv/bin/rp2040-sim run --project examples/micropython.json \
    --script firmware/micropython/blink.py --duration-ms 2000
QT_QPA_PLATFORM=offscreen .venv/bin/rp2040-sim gui \
    --project examples/workbench.json --screenshot build/workbench.png
```

The workbench emits sensor ID/temperature, manual ADC readings, and SPI echoes;
holding the button changes GPIO25's blink period. GPIO0 runs PWM at 1 kHz and 25%
duty with the default 1 MHz instruction clock. The I/O inspector shows configured
PWM frequency/duty. Zoom the analyzer to resolve PWM edges.

The two-core increment also rebuilds the wheel and source distribution. An
isolated wheel install runs `examples/multicore.json` through 1.1 simulated
seconds and prints four FIFO reply counts. Package checks include the native
multicore source, Cores inspector and bundled firmware assets.

## Physical and timing limits

The automated suite does not replace an Arm conformance suite or physical Pico
measurements. Real hardware comparisons, accurate bus contention and full-rate
execution at 133 MHz remain unverified. ROM execution is validated against the
official B2 image and named firmware, not measured silicon behavior.

`tools/benchmark.py --gui --output build/benchmark.json` measures busy Thumb
throughput, the active workbench, MicroPython startup, peak process RSS and traced
GUI pause latency. On the Linux WSL2 x86-64/Python 3.12.3 reference environment:

- Busy loop: 1,000,000 instructions in 0.716 s, approximately 1.40 M instructions/s.
- Workbench: 1 virtual second in 0.803 s, 2,159 events and no trace loss.
- MicroPython startup/idle: 1 virtual second in 0.336 s, 393,487 instructions.
- Traced MicroPython `Pin.toggle()` loop: five pause acknowledgements of
  40.8, 21.5, 28.0, 20.1 and 92.2 ms; the 100 ms reference target passed this run.
- Peak RSS for the combined benchmark process, including Qt: 108.9 MiB.

These host-specific samples are not a universal latency guarantee. The broader
GUI regression checks retain a loose 500 ms CI ceiling. Physical Pico and other
OS validation remain open because neither is available in this workspace.
