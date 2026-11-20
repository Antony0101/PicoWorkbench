# B2 ROM boot, UF2 and serial flash

ROM startup executes the official RP2040 B2 ROM on core 0. It reads the simulated
flash through SSI, performs the ROM's real boot2 CRC check, copies boot2 to SRAM,
executes boot2, and enters the firmware's flash vectors. ROM lookup, memory,
floating-point and flash routines execute ARM instructions in this mode.
Core 1 still uses the documented [launch-protocol shim](multicore.md).

## Prepare the ROM once

The ROM is a local runtime dependency; its bytes are not distributed in the wheel
or source archive. Upstream includes separate math-library licensing. Prepare the
pinned official release using the supplied helper:

```sh
.venv/bin/python tools/prepare_bootrom.py
# Or use an already downloaded official release ELF, without network access:
.venv/bin/python tools/prepare_bootrom.py --elf .tools/downloads/bootrom-b2.elf
```

The helper verifies the release ELF SHA256 before extracting its 16 KiB ROM
segment. The application also checks the binary SHA256 before using it:

- Official [B2 release](https://github.com/raspberrypi/pico-bootrom-rp2040/releases/tag/b2)
- ELF SHA256: `3a3c4c617d83bc9b910a6fa7bfdffa11ce26ff89370271d861507be821cc49aa`
- Binary SHA256: `9c19b46f068c21f90d200c514faad4a0d5cecfc978f155b8c9d25cb6bc2efd81`
- [Source and license](https://github.com/raspberrypi/pico-bootrom-rp2040/tree/b2)

The prepared workspace finds `.tools/bootrom-b2.bin` automatically. Installed
packages use `PICO_BOOTROM_PATH`, `--bootrom`, or a saved project's `bootrom` path.
The GUI's **File → Select B2 boot ROM** validates and saves that path. The
application does not download firmware or ROMs while loading a project.

## Run

The desktop's **Startup** selector, beside Playback speed, chooses the next C
editor build:

| Selection | Source style | Execution |
| --- | --- | --- |
| **Direct load** | Freestanding C with a vector table and `reset_handler()` | Starts at the application's reset vector; skips ROM |
| **ROM boot** | Pico SDK C with `int main(void)` | Runs the real B2 ROM → boot2 → SDK startup → `main()` |
| **SDK shortcut** | Pico SDK C with `int main(void)` | Starts at SDK firmware vectors; skips ROM and boot2 |

Choose a mode, press **F7 / Build + Load**, then **F5 / Run**. For a ready-to-edit
ROM example, choose **File → Examples → New ROM boot blink**. The SDK build
automatically supplies boot2, its checksum and the startup/vector code. The
earlier low-level blink's custom vectors and `reset_handler()` belong to Direct
load; selecting ROM boot does not rewrite that source into SDK `main()` code.

Changing the selection preserves the editor and the current simulated program.
Run/Step/Reset wait for a successful matching build or firmware load; returning
the selector to the loaded mode makes the current program runnable again.
A failed build leaves the loaded image and its source identity intact. The
status bar continues to show the actual loaded startup path, while the execution
row identifies a pending choice. Mode selection is disabled during execution,
builds, loads and file operations. The packaged MicroPython profile uses ROM boot.

Projects retain `startup` for the saved firmware. If a different next-build mode
is pending, an optional `build_startup` field saves that choice separately, so
reopening still loads the valid existing firmware. Existing schema-1 projects
without this field keep their original behavior. A successful build/load updates
`startup` and clears the pending override. **Load firmware** initially selects the
filter matching the chosen mode; the selected filter determines how that image
loads. External ROM images must already contain a valid boot2 page.

```sh
.venv/bin/rp2040-sim gui --project examples/rom_blink.json
.venv/bin/rp2040-sim run --project examples/rom_flash.json --duration-ms 200
.venv/bin/rp2040-sim run build/sdk/sdk_blink.elf --startup rom --duration-ms 1100
.venv/bin/rp2040-sim run firmware.uf2 --bootrom /path/to/bootrom-b2.bin
# Direct-load ELF (custom vectors/reset_handler):
.venv/bin/rp2040-sim run firmware.elf --startup direct --duration-ms 1000
```

Use **File → Examples → New ROM boot blink**, **New ROM flash test**, or the firmware
dialog's **RP2040 ROM boot** filter. Projects save `"startup": "rom"`. CLI input
files ending in `.uf2` or `.bin` select ROM mode when no project/startup override
is supplied. ELF ROM mode must be explicit; it uses physical flash load
addresses and ignores empty SRAM/BSS segments. ELF files with initialized
non-flash LMAs cannot use this boot path.

The bundled SDK flash example erases a 4 KiB sector at flash offset `0x1f0000`,
programs a 256-byte pattern, returns through SDK boot2, and checks the result over
XIP. It prints `SDK flash erase/program verified`. This modifies only the
simulated flash of the loaded instance.

## Container and API contract

The additive C entry points are `sim_load_flash` and `sim_load_uf2`; both take
caller-owned image/ROM buffers and copy them. ABI version 1 and snapshot layouts
remain unchanged. Python uses `Engine.load(image, startup="rom", bootrom=...)`.

UF2 loading validates the whole container before replacing flash:

- 512-byte blocks, all three magic values, and at most 8 MiB of input.
- RP2040 family ID `0xe48bff56`, flash-only flags `0x2000`, 256-byte payloads,
  page alignment, and the 2 MiB flash boundary.
- Consistent block counts and numbering, complete sets, and a boot2 page.
- Arbitrary block order and identical duplicate blocks are accepted.
  Conflicting duplicates, overlapping pages and missing blocks fail.

Gaps are erased (`0xff`). RAM-target UF2, metadata/file-container/extension-tag
blocks, and other families are explicitly outside this loader's profile. The
native parser is also available directly to callers with byte buffers; path-based
`.uf2` loading preserves format validation even if its first magic is corrupted.
Boot2 CRC validation is performed by ROM execution, not by a replacement host check.
An invalid CRC exhausts ROM retry attempts and reaches the unsupported USB boot
path, producing a diagnostic instead of executing the application.

## Functional flash behavior and limits

The 2 MiB NOR model supports read, write enable/disable, status/QE access,
page program with page wrapping and bit-clearing semantics, 4 KiB/64 KiB erase,
JEDEC ID and a deterministic unique ID. SFDP is absent, permitting the ROM's
JEDEC fallback. SSI RX data/FIFO status, QSPI CS overrides/pads, the boot2 dummy
quad read, and XIP cache control/flush storage support the named workloads.

Transfers, programming and erase complete immediately. There are no serial pin
waveforms, detailed SSI pipeline/baud timing, flash wear/protection behavior,
cache hit/miss timing, XIP stall timing, or power-on electrical sequencing.
Flash remains mapped for data access during command mode. Unsupported flash
commands stop explicitly. Only the modeled flash configuration and named boot2
workloads have validation; arbitrary boot2 implementations may require more registers.

Engine reset preserves flash bytes and resets controller/status state, giving
repeatable startup traces. Reloading an image restores its original flash bytes.
**File → Export flash image** or CLI `--save-flash saved.bin` preserves runtime
changes across application sessions. `Engine.flash_image()` returns the same
copied 2 MiB image. Recording replay reloads the original firmware before applying
inputs, so previous filesystem writes cannot contaminate replay.

Tests cover malformed containers, fake-ROM API execution, real boot2 execution
breakpoints/CRC rejection, ELF/binary/UF2 equivalence, reset replay, SDK flash
operations and persistent storage. At 1 MHz the bundled ROM blink retires
33,606 instructions over 1.1 virtual seconds; its first GPIO25 edge is at
31.065 ms. These are functional simulation measurements, not hardware timings.

Primary references: [UF2 specification](https://github.com/microsoft/uf2),
[B2 flash boot](https://github.com/raspberrypi/pico-bootrom-rp2040/blob/b2/bootrom/bootrom_main.c)
and [ROM flash routines](https://github.com/raspberrypi/pico-bootrom-rp2040/blob/b2/bootrom/program_flash_generic.c).
