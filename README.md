# RP2040 Lab

A desktop RP2040 firmware simulator with a C engine, Python controller, and
PySide6 GUI. Load real ARM ELF firmware, run or step it, inspect registers and
memory, wire digital components, and watch UART output and GPIO waveforms.

This is a working first release of [Phase 1](plan/phase-1/README.md).
[Phase 2](plan/phase-2/README.md) plans desktop UI/UX improvements; see the
[development phases](plan/README.md) for both plans and their status.
Real B2 ROM boot, UF2 loading and a pinned MicroPython UART profile are supported.
USB transfers and additional OS/hardware validation remain outside the delivered scope.
The current engine uses functional timing; it does
not claim complete chip compatibility or cycle accuracy.

## Run in this workspace

The virtual environment, native extension, GUI dependencies, and local ARM
compiler have been prepared:

```sh
.venv/bin/rp2040-sim gui
```

Press **Run**, then hold the button to change the LED's blink rate. Open
**File → New peripheral workbench** for the integrated I2C sensor, SPI loopback,
PWM LED, and ADC demonstration. **File → New PIO blink demo** runs a PIO-driven
LED while the CPU sleeps. **File → New DMA to PIO demo** feeds a PIO waveform
through DMA and reports its completion interrupt. The included demos run without
compiling firmware. **File → New two-core demo** demonstrates shared SRAM,
spinlocks and FIFO interrupts.
**File → New Pico SDK blink** and **New Pico SDK UART** run genuine SDK 2.2.0
C startup and runtime code using the supported SDK ELF shortcut.
**File → New ROM boot blink** executes the B2 ROM and boot2.
**File → New MicroPython project** opens a Python editor and UART REPL; press
**F7 / Run Python** to execute the source. These use the locally prepared B2 ROM.

Right-click the circuit background to add a component at that location. Component
menus offer editing and deletion; port menus connect or disconnect wires. The
circuit menu also offers Run/Pause/Step/Reset and canvas fitting. Circuit edits
are available while paused. Right-click inspection tables to copy values or a
table, the editor to build/run, serial or diagnostic output to copy/clear it,
and the logic analyzer to zoom or clear its displayed trace.

## Install on Linux

Requires Python 3.10+, a host C compiler, Python development headers, and the
usual Qt desktop libraries. An ARM cross compiler is needed only to build edited
firmware, not to run the bundled demos.

On Ubuntu/Debian, the host development packages are `build-essential`,
`python3-dev`, and `python3-venv`; the firmware compiler is `gcc-arm-none-eabi`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[gui,dev]'
.venv/bin/rp2040-sim gui
```

`ARM_CC` can name an ARM GCC executable outside `PATH`. In this prepared
workspace, `.tools/arm/usr/bin/arm-none-eabi-gcc` is found automatically. The local
tools and virtual environment are ignored by version control.

Linux x86-64 is the validated platform. Native library discovery currently uses
the shared library and headers shipped in the pinned Unicorn Python wheel.

### WSL display troubleshooting

The GUI requires a working desktop display, such as
[WSLg](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gui-apps).
On WSL, startup automatically uses the Wayland socket in
`/mnt/wslg/runtime-dir` if it is missing from `XDG_RUNTIME_DIR`.
An explicit `QT_QPA_PLATFORM` setting disables this automatic selection.
To select the WSLg socket manually:

```sh
WAYLAND_DISPLAY=/mnt/wslg/runtime-dir/wayland-0 QT_QPA_PLATFORM=wayland \
    .venv/bin/rp2040-sim gui
```

If you prefer X11 and Qt reports that its `xcb` platform plugin cannot load,
install the [Qt X11 runtime libraries](https://doc.qt.io/qt-6/linux-requirements.html).
On Ubuntu/Debian, these packages cover the dependencies missing from a minimal
WSL installation:

```sh
sudo apt update
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 \
    libxcb-util1 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-shape0 libxcb-xkb1
QT_QPA_PLATFORM=xcb .venv/bin/rp2040-sim gui
```

## Desktop workflow

- **F5** runs, **F6** pauses, **F10** steps, and **Ctrl+R** resets.
- **Playback** selects a target speed from **0.01× to 4×**. For example, 0.1×
  advances about one virtual second per ten real seconds. Change it while running
  without resetting the chip or changing firmware event timestamps. Actual speed
  depends on host performance and the configured CPU clock; long host stalls do
  not trigger unlimited catch-up. Playback is a session setting.
- **CPU clock** sets the functional instruction clock from **1 kHz to 133 MHz**.
  Pause, enter a frequency, then click **Apply + Reset** to reload the currently
  loaded firmware, reset chip state/input recording, and clear breakpoints.
  The frequency is saved with the project. This changes firmware timing.
- **Run N** executes the selected number of additional instructions, then pauses.
  It follows the playback speed and stops early at breakpoints or errors.
  Pause cancels the remaining count; Run resumes without an instruction limit.
- **File → Load firmware ELF** loads an existing executable. Choose its
  **Pico SDK ELF — skip boot2** filter for the supported SDK profile.
- Edit the C source and press **F7 / Build + Load** to compile a scratch copy.
  Build diagnostics appear in the bottom panel; Cancel build stops the compiler.
- Add a component, then click two signal ports to connect them. Drag parts while
  paused; select a part or wire and press Delete to remove it. Structural circuit
  changes reset execution. Double-click a sensor to change its address.
- Use **CPU** for registers and address/function breakpoints, **Memory** for
  side-effect-free inspection, and **I/O** for ADC sliders and PWM configuration.
- Use **PIO** to select a block/state machine and inspect PC, scratch/shift
  registers, FIFO contents, stalls, clock configuration, and decoded instruction
  memory. Inspection never consumes FIFO data.
- The bottom tabs contain source, UART0, the logic analyzer, and build output.
  Scroll over the analyzer to zoom its virtual-time window. Ctrl+wheel zooms the
  circuit canvas.
- Save circuit projects as JSON. Export timestamped input recordings from the
  File menu for headless replay. Saving a project also preserves the editor text;
  compiling it is a separate action.

The board drawing exposes logical chip GPIOs, including internal Pico signals;
it is not a physical header-placement diagram. LEDs and buttons are logical
models, with no electrical current or analog circuit solver.

## Headless execution

```sh
# Bundled blink firmware, one second of virtual time
.venv/bin/rp2040-sim run --duration-ms 1000 --trace build/blink.jsonl

# Integrated workbench: no cross compiler required
.venv/bin/rp2040-sim run --project examples/workbench.json --duration-ms 1000

# Replay an exported workbench input recording
.venv/bin/rp2040-sim run --project examples/workbench.json \
    --replay inputs.json --duration-ms 1000 --trace build/replay.jsonl
```

UART bytes go to stdout; the final execution summary goes to stderr. Replay
requires matching firmware, circuit configuration, engine version, and a duration
that includes every recorded input. It starts from reset.

## PIO demonstration

```sh
.venv/bin/rp2040-sim gui --project examples/pio_blink.json
.venv/bin/rp2040-sim run --project examples/pio_blink.json \
    --duration-ms 1100 --trace build/pio-blink.jsonl
```

Press **F5**. PIO0 SM0 drives GPIO25 at 2 Hz with the default 1 MHz clock:
250 ms high, 250 ms low. The CPU stays in WFI while PIO continues. The **PIO**
tab shows the two SET instructions, divider, PC, and delay counter; the logic
analyzer shows the GPIO25 waveform. Try **0.1× Playback** for slow motion.

The bundled ELF runs without a compiler or pioasm. Edit the included C source
and press **F7** to rebuild it when ARM GCC is installed. See
[PIO support and timing limits](docs/pio.md).

## DMA to PIO demonstration

```sh
.venv/bin/rp2040-sim gui --project examples/dma_pio.json
.venv/bin/rp2040-sim run --project examples/dma_pio.json --duration-ms 3100
```

Press **F5**. DMA feeds twelve words to PIO0 SM0, producing six GPIO25 pulses
while the CPU sleeps. The **DMA** tab shows transfer progress and FIFO pacing.
The completion interrupt prints `DMA complete` before PIO finishes draining its
FIFO. See [DMA behavior and timing limits](docs/dma.md).

## Two-core demonstration

```sh
.venv/bin/rp2040-sim gui --project examples/multicore.json
.venv/bin/rp2040-sim run --project examples/multicore.json --duration-ms 1100
```

Press **F5**. Core 0 sends work every 250 ms; core 1 updates shared SRAM under a
spinlock, drives GPIO25, and replies through its FIFO. Serial output shows
increasing `Core 1 count` values. The **Cores** tab inspects either register bank,
sleep state, NVIC, FIFO and spinlocks. **F10 / Step next** advances one instruction
on the next runnable core; Run N counts instructions across both cores.

Both cores share a deterministic instruction schedule with functional timing.
The launch handshake uses a ROM protocol shim. See [two-core support and limits](docs/multicore.md).

## Build custom firmware

```sh
.venv/bin/rp2040-sim build firmware/fixtures/blink.c -o build/custom.elf
.venv/bin/rp2040-sim run build/custom.elf --duration-ms 1000

.venv/bin/rp2040-sim build firmware/fixtures/interrupts.c -o build/interrupts.elf
.venv/bin/rp2040-sim build firmware/fixtures/buses.c -o build/buses.elf
.venv/bin/rp2040-sim gui --project examples/buses.json
```

The builder accepts a single freestanding C file and an optional `--linker` script.
The default linker places vectors and code in mapped flash and data/BSS in SRAM.
The loader requires an ELF32 little-endian ARM executable with a Thumb entry and
matching vectors at `0x10000000`. It initializes segments, stack, and PC directly.
SDK ELF mode additionally supports the pinned SDK 2.2.0 profile below.
ROM startup additionally executes the official B2 ROM and supported boot2 code,
with validated RP2040 flash UF2 ingestion. See [ROM/flash details](docs/boot.md).

## Pico SDK demonstration

```sh
.venv/bin/rp2040-sim gui --project examples/sdk_blink.json
.venv/bin/rp2040-sim run --project examples/sdk_uart.json --duration-ms 10

# Requires Pico SDK 2.2.0, CMake, ARM GCC and ARM Newlib
.venv/bin/rp2040-sim build firmware/sdk/sdk_blink.c --startup sdk -o build/sdk-edited.elf
.venv/bin/rp2040-sim run build/sdk-edited.elf --startup sdk --duration-ms 1100
```

The blink example uses SDK GPIO and `sleep_ms(250)`. SDK mode executes crt0,
data/BSS initialization, runtime clock setup, SRAM vectors and timer interrupts;
it skips boot ROM and boot2. Clock/reset registers and ROM ABI shims supply the
audited startup dependencies. Firmware clock-register writes do not change the
simulator's functional instruction clock. F7 builds SDK editor sources through
CMake; bundled examples need no build tools. See [SDK setup and limits](docs/sdk-startup.md).

## ROM boot and MicroPython

```sh
# One-time ROM preparation after a fresh install; no cross compiler required
.venv/bin/python tools/prepare_bootrom.py

.venv/bin/rp2040-sim gui --project examples/rom_blink.json
.venv/bin/rp2040-sim gui --project examples/micropython.json
.venv/bin/rp2040-sim run --project examples/micropython.json \
    --script firmware/micropython/blink.py --duration-ms 2000
```

The ROM remains a local dependency. Installed packages can use `PICO_BOOTROM_PATH`
or **File → Select B2 boot ROM**. ROM mode accepts flash ELF, binary and RP2040
UF2 images. **New ROM flash test** demonstrates SDK erase/program operations.

The MicroPython 1.26.1 UART profile supports editable scripts, REPL commands,
interruption, GPIO/time, RTC, ADC and a persistent LittleFS filesystem. Export
flash from the File menu or CLI `--save-flash` to preserve files between sessions.
USB is disabled in this profile. See [MicroPython workflow and audited limits](docs/micropython.md).

## Development and verification

```sh
make test                 # Python, firmware, replay, and offscreen GUI tests
make native               # CMake shared library and C API contract test
make demo                 # Rebuild packaged demo ELF hex and editable sources
make sdk-demo             # Rebuild pinned SDK C fixtures (requires SDK/toolchain)
make micropython-demo     # Fetch pinned sources and rebuild the UART interpreter
QT_QPA_PLATFORM=offscreen .venv/bin/rp2040-sim gui \
    --project examples/workbench.json --screenshot build/workbench.png
```

Firmware compiler tests skip when ARM GCC is unavailable; GUI tests skip when
PySide6 is absent. For the full checks, install the GUI/dev extras and ARM compiler.

See [compatibility](docs/compatibility.md), [engine/backend decisions](docs/backend.md),
[validation instructions](docs/validation.md), and [Phase 1 implementation status](plan/phase-1/implementation-status.md).
