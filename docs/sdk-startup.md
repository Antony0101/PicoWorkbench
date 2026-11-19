# Pico SDK ELF startup

The supported profile uses **Pico SDK 2.2.0**, board `pico`, Cortex-M0+, Release
builds, UART stdio, and USB stdio disabled. The named workloads are
`firmware/sdk/sdk_blink.c` and `sdk_uart.c`. They include SDK headers and link
the real SDK crt0, runtime initialization, GPIO, clocks, divider, timer and stdio
libraries. Neither workload replaces the reset handler or `runtime_init`.

## Run and edit

Bundled firmware needs no SDK or cross compiler:

```sh
.venv/bin/rp2040-sim gui --project examples/sdk_blink.json
.venv/bin/rp2040-sim run --project examples/sdk_blink.json --duration-ms 1100
.venv/bin/rp2040-sim run --project examples/sdk_uart.json --duration-ms 10
```

In the GUI, choose **File → Examples → New Pico SDK blink** or **New Pico SDK UART**.
Press F5 to run. The load dialog's **Pico SDK ELF — skip boot2** filter explicitly
selects SDK startup for external ELFs. Project JSON stores `"startup": "sdk"`;
old projects default to `"direct"`. Input recordings include the startup mode
in their identity and cannot replay with a different mode.

F7 builds an edited single C source using the packaged SDK CMake profile.
It uses a scratch project, preserves the user's source, and supports cancellation
of configure/build subprocesses. Set `PICO_SDK_PATH` to a **2.2.0** checkout;
the prepared workspace finds `.tools/pico-sdk-2.2.0` automatically. CMake 3.25+,
ARM GCC, and ARM Newlib headers/libraries are required for builds. For Ubuntu
toolchains, these are supplied by `gcc-arm-none-eabi`, `libnewlib-dev` and
`libnewlib-arm-none-eabi`. The SDK's optional git submodules are not needed for
these non-USB examples. `ARM_CC` selects the compiler; its parent installation
directory is passed to the SDK as `PICO_TOOLCHAIN_PATH`.

```sh
export PICO_SDK_PATH="$PWD/.tools/pico-sdk-2.2.0"
.venv/bin/rp2040-sim build firmware/sdk/sdk_blink.c \
    --startup sdk -o build/my-sdk.elf
.venv/bin/rp2040-sim run build/my-sdk.elf --startup sdk --duration-ms 1100

# Rebuild both named fixtures, compact packaged ELFs, and provenance manifest
.venv/bin/python tools/prepare_sdk.py
```

Float/double implementations are selected as `compiler`; ROM floating-point
tables are not implemented. `PICO_CXX_DISABLE_ALLOCATION_OVERRIDES=1` avoids
pulling C++ allocation overrides into this C-only profile, and the linker language
is C. The normal SDK ROM memory/bit-operation paths remain enabled.
This profile does not guarantee that arbitrary SDK applications run.

## Startup contract

`sim_load_elf_sdk` is an additive C API entry point; Python selects it with
`Engine.load(image, startup="sdk")`. The original direct-load API stays available.

1. Validate ELF32 ARM segments, bounds and overlaps, executable Thumb entry/reset
   addresses, and an aligned SRAM stack. Initialized physical load spans must not
   overlap. Initialized SRAM segments may have flash physical load addresses;
   invalid or unmapped LMAs fail before copying.
2. Load file bytes at ELF physical addresses. SRAM starts zeroed. The SDK's
   initialized `.data` payload remains in flash until crt0 copies it to SRAM.
3. Set core 0 SP/PC/VTOR from vectors at `0x10000100`, after the 256-byte boot2
   region. The ELF debugger entry can differ from its reset vector.
4. Execute SDK crt0 and runtime initialization. They copy data, zero BSS, install
   SRAM vectors, release/reset peripherals, configure clocks and install IRQs.
   SDK `sleep_ms` uses the modeled timer and its interrupt path.

In SDK shortcut mode, the boot2 bytes remain in flash but do not execute. There is no boot ROM image,
flash detection, boot2 CRC check, SSI transaction, XIP/cache setup or UF2 loading.
This is a supported SDK ELF shortcut, not authentic chip power-on boot.
The separate [ROM startup mode](boot.md) now executes B2 ROM/boot2 and loads UF2.

## Audited dependencies

| Dependency | Implemented behavior | Functional limits |
| --- | --- | --- |
| RESETS | RESET, WDSEL=0, RESET_DONE, atomic aliases; rising reset clears supported peripheral blocks; writes ignored while held | Reset release completes immediately; no watchdog-selected reset |
| XOSC | Enable magic/frequency fields, stable/enabled/BADWRITE status, startup storage | No crystal delay, dormant mode or frequency simulation |
| PLL_SYS/USB | Configuration, power fields, lock dependency on XOSC, feedback/refdiv and reset; postdivider power controls output readiness | Immediate lock, no physical frequency/lock timing |
| CLOCKS | Ten slice control/divider fields, REF/SYS one-hot SELECTED readiness, wake/sleep storage, resus disabled | **Does not change the configured simulator instruction clock**; no oscillator settling, phase/kill/duty/nudge modes, frequency counter or clock gating |
| WATCHDOG | Scratch registers and TICK configuration/RUNNING status | No countdown, reset, pause, or tick-derived timer frequency |
| SYSINFO/TBMAN/SYSCFG | Chip/platform identification and input-sync bypass storage used by runtime init | No synchronizer latency or general system configuration |
| USB main | SIE_CTRL transceiver power-down bit required by SDK runtime init | No USB controller, transfers or USB stdio |
| SIO divider | Core-local signed/unsigned operands, quotient/remainder, READY/DIRTY and software restore | Immediate result instead of eight-cycle latency; debugger peeks preserve DIRTY |
| ROM ABI | Synthetic header/table lookup; CLZ/CTZ/popcount/reverse, memcpy/memset and aligned variants | Native shims, one functional tick per call; no other ROM services or ROM image |
| Timer/UART/GPIO/IRQ | Existing functional peripherals support SDK GPIO, timer sleeps and UART stdio; UART DMA-enable storage | Instant UART TX; UART DREQ transfers remain unsupported |

RESETS preserves SIO, external net connections and manual ADC sources. UART
input delivered while its block is held in reset is discarded. PIO blocks reset
independently. Timer reset clears alarms/IRQ state; time reads continue to reflect
virtual microseconds. Reset register defaults release the peripherals for ELF
startup and are not the silicon power-on reset sequence.

Unknown ROM lookups and unsupported system modes stop with diagnostics.
ROM memory shims validate full ranges before allocation/copy and invalidate cached
executable SRAM translations after writes. Only SRAM destinations are allowed.
Aligned variants require word-aligned addresses and byte lengths.

## Evidence and provenance

At the default 1 MHz aggregate functional clock, bundled blink executes **4,999
CPU instructions over 1.1 virtual seconds**. GPIO25 transitions occur at
2.458, 252.635, 502.813, 752.990 and 1003.168 ms. The approximately 250 ms sleeps
include modeled instruction overhead; these are not physical-hardware timings.
The fixture checks initialized data and BSS before blinking. Tests independently
check flash LMAs, zeroed pre-start SRAM, copied initialized data, RAM VTOR, PLL
readiness, UART output, reset traces, and instruction budgets of 1, 37 and 10,000.

`sdk-provenance.json` in the packaged assets records compiler, SDK version,
profile/source hashes and compact ELF hashes. The SDK archive reference is
[`pico-sdk` tag 2.2.0](https://github.com/raspberrypi/pico-sdk/tree/2.2.0), SHA256
`dbd8db79015aced4ef35cbaa88051b0d451775010d20202e1c7d2bee69c25b35`.
Rebuilding expects an unmodified checkout of that tag. Compact ELFs retain all
loadable segments and LMAs but omit symbols/DWARF; full local build ELFs retain
debug information for the GUI.

The prepared toolchain is ARM GCC 13.2.1 and Ubuntu Newlib
`4.4.0.20231231-2`. Downloaded Newlib package SHA256 values:

- `libnewlib-dev_4.4.0.20231231-2_all.deb`:
  `f945b12c3abc844e76eebc28cf7d99e9aefd7caec39583116ef29556b3c52414`
- `libnewlib-arm-none-eabi_4.4.0.20231231-2_all.deb`:
  `9af508599193d07c9460897d01a36af46a2707765bd6941e27c6d0747859b0eb`

Locally extracted Newlib needs `usr/lib/arm-none-eabi/lib -> newlib` and
`usr/lib/arm-none-eabi/include -> ../../include/newlib`; system package installs
normally supply toolchain search paths. Optional TinyUSB absence produces a
CMake warning, with USB stdio disabled in this profile.

The packaged `SDK-NOTICES.txt` contains Pico SDK, pico_printf, Newlib and compiler
runtime license notices. Compiler and SDK installations themselves are not bundled.

Primary audit sources, all from tag 2.2.0:

- [crt0](https://github.com/raspberrypi/pico-sdk/blob/2.2.0/src/rp2_common/pico_crt0/crt0.S)
  and [linker layout](https://github.com/raspberrypi/pico-sdk/blob/2.2.0/src/rp2_common/pico_crt0/rp2040/memmap_default.ld)
- [Runtime initialization](https://github.com/raspberrypi/pico-sdk/blob/2.2.0/src/rp2_common/pico_runtime_init/runtime_init.c)
  and [clock startup](https://github.com/raspberrypi/pico-sdk/blob/2.2.0/src/rp2_common/pico_runtime_init/runtime_init_clocks.c)
- [ROM ABI](https://github.com/raspberrypi/pico-sdk/blob/2.2.0/src/rp2_common/pico_bootrom/include/pico/bootrom.h)
  and [register definitions](https://github.com/raspberrypi/pico-sdk/tree/2.2.0/src/rp2040/hardware_regs/include/hardware/regs)

The ROM/flash/UF2 increment is implemented separately; see [boot support](boot.md).
