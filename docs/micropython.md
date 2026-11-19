# MicroPython 1.26.1 UART profile

The packaged MicroPython firmware uses the unmodified RP2 port from **1.26.1**
with the board configuration in `firmware/micropython`. It boots through the real
B2 ROM and boot2, formats/mounts its LittleFS filesystem, and exposes a UART0
REPL. The profile enables UART REPL and disables USB. It is a named, tested
configuration, not a claim that stock USB MicroPython or every module is supported.

## Use the desktop or CLI

First [prepare/select the B2 ROM](boot.md). A cross compiler is not needed to run
the bundled interpreter:

```sh
.venv/bin/rp2040-sim gui --project examples/micropython.json
.venv/bin/rp2040-sim run --project examples/micropython.json \
    --script firmware/micropython/blink.py --duration-ms 2000
```

Choose **File → Examples → New MicroPython project**. Edit the Python source and press
**F7 / Run Python**. The worker starts the interpreter, uploads source through
its UART raw REPL, runs it, returns to the friendly prompt and pauses. Output,
including Python tracebacks, appears in **Serial · UART0**. Single REPL commands
can also be sent through the serial input; press Run first if execution is paused.
**Interrupt Python · Ctrl+C** interrupts a running program and returns to the
friendly REPL. Upload cancellation waits only for the current bounded UART chunk.

Uploads are limited to 32 KiB of UTF-8 source. UART input is paced on virtual
time, with bounded queued chunks. The GUI stays responsive during upload and
execution; pause/reset/clock changes retain their normal meanings. Scripts that
run forever require interruption; CLI scripts must finish before `duration-ms`
and report Python exceptions as command failures. C projects retain the original
Build + Load behavior.

The default MicroPython project uses a 10 MHz functional instruction clock.
Firmware clock-register writes still do not change that configured clock.
Playback can slow the display without changing chip event timestamps.

Files survive engine reset. Export the simulated flash to preserve them between
sessions:

```sh
.venv/bin/rp2040-sim run --project examples/micropython.json \
    --script script.py --duration-ms 2000 --save-flash saved.bin
.venv/bin/rp2040-sim run saved.bin --startup rom --script another.py --duration-ms 2000
```

For GUI editing with a saved binary, open a MicroPython project and load it using
the ROM boot filter. Replay uses the original image plus timestamped input bytes,
including raw-REPL uploads recorded at their actual delivery boundaries.

## Dependency audit

| Startup/runtime dependency | Model and evidence |
| --- | --- |
| ROM reset, CRC, boot2, float/double/memory ABI | Real B2 ROM instructions; arithmetic and `math.sin` execute successfully |
| SDK startup/clocks/resets, SRAM vectors | Standard RP2 startup, selected Pico SDK 2.1.1 revision, functional control registers |
| Narrow SIO/PPB accesses | Byte/halfword reads, replicated I/O writes and byte-lane NVIC/system priority updates |
| UART0 REPL | Full interrupt-mask storage, RX delivery and clearable TX interrupt state avoid interrupt storms; byte-level timing |
| RTC/time | Calendar load/read/latching, virtual-second rollover, leap-year override, matching alarm and IRQ25 |
| Flash ID and LittleFS | Deterministic unique/JEDEC IDs, ROM SSI erase/program and SDK boot2 return; files verified after reset/export/reload |
| Core 1 synchronization | Existing launch/reset/FIFO/spinlock model supplies port initialization; general Python thread behavior is not part of the validated workload set |
| ROSC random bit | Deterministic seeded bit stream; debugger reads do not consume it; no physical entropy model |
| USB | Disabled in this profile; stock USB firmware was evaluated and stops at unsupported USB DPRAM/controller access |

The RTC advances in virtual one-second increments independently of the stored
divider. ACTIVE, LOAD and MATCH transitions are immediate; no RTC clock-domain
write latency is modeled. Day rollover follows the hardware's four-year leap
rule; FORCE_NOTLEAPYEAR handles century exceptions. Calendar alarm routing is
checked independently of MicroPython. Watchdog countdown/reset, USB transfers,
networking/radio, I2S, dormant clocks and arbitrary optional module paths remain
outside this compatibility claim and may stop with diagnostics.

## Rebuild and provenance

```sh
.venv/bin/python tools/prepare_micropython.py
```

The helper downloads hash-verified source archives into `.tools/downloads`, builds
mpy-cross and the UART profile with the local ARM compiler/Newlib, and packages
its UF2, editable example and notices. Existing valid archives are reused offline.
The profile has no vendor source patches. `MICROPY_LIB_DIR` points at the profile
because its frozen manifest only uses files shipped in MicroPython itself.
The C linker plus `libm` avoids requiring the C++ runtime; C++ allocation overrides
are disabled. Build date is pinned using `SOURCE_DATE_EPOCH`.

The revisions are:

- MicroPython `v1.26.1`, commit `647c8b96cae7e202c7a020395b7cfe65e5b8ce04`.
- Pico SDK `9a4113fbbae65ee82d8cd6537963bc3d3b14bcca` (the upstream pinned SDK).
- TinyUSB `5217cee5de4cd555018da90f9f1bcc87fb1c1d3a`.
- MbedTLS `107ea89daaefb9867ea9121002fbbdf926780e98`.

This SDK is separate from the SDK 2.2.0 C-example/editor profile. An attempted
MicroPython build with SDK 2.2.0 failed header-property gathering; the supported
build uses its pinned dependency instead. `assets/micropython-provenance.json`
records archive, board-profile and UF2 hashes plus the compiler. Packaged
`MICROPYTHON-NOTICES.txt` and `SDK-NOTICES.txt` retain dependency notices.

The official stock image `RPI_PICO-20250911-v1.26.1.uf2` was audited separately,
SHA256 `fc2a8096e0d999d3caca11b34381854599d68aee3e905d3cccbd5247ca78d07c`.
It is not substituted for the UART image or advertised as working USB firmware.

Automated workloads verify interpreter startup, raw upload, arithmetic/ROM math,
GPIO/sleep timing, ADC, RTC date rollover, Python error reporting, files across
reset and exported-flash reload, large uploads, deterministic input replay,
CLI execution/export, and GUI execution/interruption. Hardware comparison is
still outstanding; only Linux has execution evidence in this workspace.

Primary sources: [MicroPython 1.26.1 RP2 port](https://github.com/micropython/micropython/tree/v1.26.1/ports/rp2),
[RP2 startup](https://github.com/micropython/micropython/blob/v1.26.1/ports/rp2/main.c),
[UART REPL](https://github.com/micropython/micropython/blob/v1.26.1/ports/rp2/uart.c),
and [LittleFS boot](https://github.com/micropython/micropython/blob/v1.26.1/ports/rp2/modules/_boot.py).
