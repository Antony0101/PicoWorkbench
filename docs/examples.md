# Examples and command-line workflows

Start with the [installation guide](getting-started.md). All commands here run
from the repository root. Bundled firmware is stored as text-encoded ELF/UF2
assets; you do not need to rebuild it before running a supplied project.

## Choose an example

Open **File → New project / examples…**, or launch a project directly:

```sh
.venv/bin/rp2040-sim gui --project examples/workbench.json
```

| Project in `examples/` | What to try | Startup / extra runtime requirement |
| --- | --- | --- |
| `blink.json` | Run; hold the button to speed up the GPIO25 LED | Direct; none |
| `workbench.json` | Inspect the I²C sensor, SPI echo, PWM and ADC slider | Direct; none |
| `buses.json` | Examine I²C/SPI exchanges in Bus inspector | Direct; none |
| `pio_blink.json` | Observe PIO0 SM0 blinking while the CPU sleeps | Direct; none |
| `dma_pio.json` | Watch DMA feed PIO and report completion on UART | Direct; none |
| `multicore.json` | Inspect both cores exchanging FIFO messages | Direct; none |
| `sdk_blink.json` | Run SDK GPIO and `sleep_ms(250)` | SDK shortcut; none |
| `sdk_uart.json` | Watch output from SDK UART stdio | SDK shortcut; none |
| `dashboard-c.json` | Change ADC0; compare OLED, plot values and bus traffic | SDK shortcut; none |
| `rom_blink.json` | Execute core-0 ROM, boot2 and the SDK application | ROM; [prepared B2 ROM](boot.md#prepare-the-rom-once) |
| `rom_flash.json` | Exercise supported SDK flash erase/program operations | ROM; prepared B2 ROM |
| `micropython.json` | Edit source and press F7 to use the UART interpreter | ROM; prepared B2 ROM |
| `dashboard-python.json` | Run the MicroPython equivalent of the C dashboard | ROM; prepared B2 ROM |

Editing and rebuilding C adds [ARM toolchain requirements](getting-started.md#build-your-own-c-code).
The examples use supported hardware paths; they do not establish compatibility
with arbitrary firmware. See [Compatibility](compatibility.md).

## Run firmware headlessly

```sh
# Bundled blink: one simulated second
.venv/bin/rp2040-sim run --duration-ms 1000

# Integrated sensor, SPI, PWM and ADC example
.venv/bin/rp2040-sim run --project examples/workbench.json --duration-ms 1000

# PIO blink: 250 ms high, 250 ms low at the default 1 MHz functional clock
.venv/bin/rp2040-sim run --project examples/pio_blink.json --duration-ms 1100

# DMA feeds twelve words to PIO, producing six GPIO25 pulses
.venv/bin/rp2040-sim run --project examples/dma_pio.json --duration-ms 3100

# Core 0 sends work; core 1 updates shared state and replies
.venv/bin/rp2040-sim run --project examples/multicore.json --duration-ms 1100
```

UART bytes go to stdout; the execution summary goes to stderr. `--duration-ms`
sets a virtual-time deadline, not a wall-clock delay. A firmware fault or
unsupported access produces an error instead of pretending execution succeeded.
Use `.venv/bin/rp2040-sim run --help` for all options.

The board canvas uses logical chip GPIO numbers. It is not a Pico header diagram
or an electrical model. The [PIO](pio.md), [DMA](dma.md) and
[multicore](multicore.md) guides explain these examples' scheduling limits.

## Export a trace and replay inputs

Create the output directory before writing a trace:

```sh
mkdir -p build
.venv/bin/rp2040-sim run --duration-ms 1000 --trace build/blink.jsonl
```

The JSONL trace contains timestamped engine events. To reproduce an interactive
session, export its input recording through **File → Export input recording…**,
then pass that file to the matching project:

```sh
.venv/bin/rp2040-sim run --project examples/workbench.json \
    --replay inputs.json --duration-ms 1000 --trace build/replay.jsonl
```

Here `inputs.json` is the file you exported. Use the same firmware, circuit,
startup mode and engine version, and a duration that includes all recorded
inputs. Replay starts from reset; it does not restore a live machine snapshot.

## Build a single freestanding C source

After [installing ARM GCC](getting-started.md#build-your-own-c-code):

```sh
.venv/bin/rp2040-sim build firmware/fixtures/blink.c -o build/custom.elf
.venv/bin/rp2040-sim run build/custom.elf --duration-ms 1000
```

The direct builder accepts one C source and an optional `--linker` script.
It uses Cortex-M0+ flags and the supplied direct-load linker layout. The source
must provide a vector table and reset handler; the loader initializes segments,
stack and PC from the validated ELF. See [Boot modes](boot.md) for entry points.

Other freestanding fixtures include `interrupts.c`, `workbench.c`, `buses.c`,
`pio_blink.c`, `dma_pio.c` and `multicore.c` under `firmware/fixtures/`.
In the editor, F7 builds a scratch copy and F5 runs the successfully loaded image.
Builds retain debug information; compact bundled assets omit DWARF.

## Build SDK source and choose startup

First [install Pico SDK 2.2.0 and the toolchain](sdk-startup.md#install-the-build-tools):

```sh
.venv/bin/rp2040-sim build firmware/sdk/sdk_blink.c --startup sdk -o build/sdk-edited.elf
.venv/bin/rp2040-sim run build/sdk-edited.elf --startup sdk --duration-ms 1100
```

The SDK shortcut runs genuine SDK startup but skips the ROM and boot2. To use
the same flash image with the real core-0 ROM path, first prepare the B2 ROM:

```sh
.venv/bin/python tools/prepare_bootrom.py
.venv/bin/rp2040-sim run build/sdk-edited.elf --startup rom --duration-ms 1100
```

The CLI builder exposes `direct` and `sdk`; `rom` is an execution mode. The GUI's
**ROM boot** build selection uses the SDK builder and then loads the ROM path.
See [SDK startup](sdk-startup.md) and [ROM/UF2 support](boot.md).

## Run a MicroPython script

After the one-time ROM preparation:

```sh
.venv/bin/rp2040-sim run --project examples/micropython.json \
    --script firmware/micropython/blink.py --duration-ms 2000
```

This uses the bundled MicroPython 1.26.1 UART profile, not host Python. CLI
scripts must finish within the virtual-time deadline; a Python exception or
unfinished upload/run causes a command failure. In the GUI, use Interrupt Python
for intentionally endless loops. Read [MicroPython](micropython.md) for file
persistence, flash exports and tested APIs.

## Compare the temperature dashboards

```sh
.venv/bin/rp2040-sim gui --project examples/dashboard-c.json
# Requires the prepared B2 ROM:
.venv/bin/rp2040-sim gui --project examples/dashboard-python.json
```

Run the C dashboard with F5; upload/run the Python dashboard with F7. In Debug,
move **I/O → ADC0 / GP26**. Both programs display temperature on the OLED and
print named serial samples for the plotter. The inspector shows display traffic,
a register-sensor read, SPI loopback and an intentional `0x3D` NACK. See
[Runtime tools](runtime-tools.md) for controls and standalone driver installation.
