# Implementation status

Version 0.1.0 implements a runnable C/Python desktop simulator. This document
records delivered behavior separately from the broader roadmap; it does not mark
the entire RP2040 compatibility plan complete.

| Milestone | Status | Evidence |
| --- | --- | --- |
| 0: execution foundation | Implemented | Unicorn Cortex-M0 backend selected, cross-compiled execution proof, CMake/CFFI packaging, documented direct-load contract |
| 1: headless chip and LED | Implemented | Validated ELF loading, mapped memory, GPIO/pads, LED traces, deterministic reset |
| 2: desktop application | Implemented | Worker-owned engine, wiring view, contextual right-click menus, registers/memory, breakpoints, run/pause/step/reset, offscreen GUI tests |
| 3: firmware debugging | Implemented for named direct/SDK/ROM workloads | Interrupts, debugging panels, symbols/DWARF; SDK crt0/runtime and functional clock/reset control; optional real B2 ROM/boot2 |
| 4: circuit and external devices | Implemented with documented functional limits | Saved workbench with button/LED, Python I2C sensor and SPI loopback, PWM, manual ADC, hash-checked deterministic input replay |
| 5: PIO/DMA/two cores | Implemented with documented functional limits | PIO0/1 state machines, FIFOs, shifts, clocking, side-set, IRQs and inspection; DMA pacing/chaining; two-core SRAM/FIFO/spinlock exchanges, reset/relaunch and deterministic scheduling |
| 6: broader compatibility/distribution | Linux implementation delivered; external validation open | Real core-0 B2 ROM/boot2, validated UF2, serial flash, RTC, pinned MicroPython UART profile, CLI/GUI/replay and Linux packaging; additional OS/hardware validation unavailable |

## Changes from the proposed layout

The desktop now includes 0.01×–4× playback targets, a CPU clock control that
resets the loaded firmware, and bounded instruction runs. Playback pacing is
separate from chip timing and is not stored in project files; the CPU clock is.
Controlled-clock tests compare event traces with headless execution and verify
exact instruction limits, pause/resume, and breakpoint stops.

Right-click menus provide component placement at the clicked location, component
editing/deletion, port connection/disconnection, canvas fitting and simulation
controls. Inspection tables copy values/selections/tables; editor menus preserve
standard editing actions and add Build/Run; output and trace menus offer display
clearing and trace zoom. Circuit mutations are guarded while running, and right
clicks do not press buttons or begin wiring.

Native modularization is implemented. The engine now has separate lifecycle,
CPU, memory/MMIO bus, scheduler, GPIO, and peripheral translation units, with
private state/interfaces in `engine/src/sim_internal.h`. PIO, DMA and multicore add three native modules and copied-state queries
without changing the existing snapshot layout
or ABI version 1. Internal helpers are hidden from the shared library's exports.
SDK startup adds system-control and ROM ABI modules; ROM/UF2/flash/RTC bring both
native builds to fourteen C files and 24 public functions, preserving ABI 1. See
[backend boundaries](../docs/backend.md#boundaries-in-the-implementation).

The PIO increment is implemented: both blocks, all instruction families, FIFO
and shift state, clock dividers, side-set/delays, IRQ routing, and a GUI inspector.
The bundled direct-load `pio_blink` demo produces a 2 Hz GPIO25 waveform while
the CPU sleeps. OUT_STICKY, INLINE_OUT_EN and input synchronizer latency
remain outside its support contract. See [PIO details](../docs/pio.md).

The DMA increment is implemented: twelve channels, widths, rings, chaining,
trigger aliases, pause/abort, error/completion IRQs, and PIO/ADC/timer pacing.
The bundled `dma_pio` demo generates six GPIO25 pulses while the CPU sleeps,
then reports completion through DMA IRQ0. A GUI inspector displays live copied
channel state. See [DMA details](../docs/dma.md) for supported DREQs and timing.

The core-1 increment is implemented: separate CPU/NVIC/SysTick contexts on a
shared memory map, FIFO launch/reset protocol, two eight-word FIFOs and IRQs,
32 spinlocks, both GPIO interrupt banks, and a Cores inspector. The bundled
`multicore` example exchanges timer-driven work and replies while updating
shared SRAM and GPIO25. Both runnable cores alternate instructions; their
aggregate functional clock is not physical concurrent-core timing. Startup uses
a ROM protocol shim. See [two-core details](../docs/multicore.md).

External bus models participate through explicit request/response boundaries.
They do not use arbitrary synchronous Python callbacks from the instruction loop.
The initial I2C/SPI models complete byte transactions immediately, so pin-level
protocol analysis is deliberately outside their compatibility claim.

The SDK startup increment is implemented for named Pico SDK 2.2.0 blink and UART
workloads. Explicit SDK ELF mode preserves flash LMAs and executes crt0, data/BSS
initialization, clock setup, SRAM vectors and timer IRQ-backed sleeps. Functional
clock/reset control, SIO dividers and ROM bit/memory shims supply the audited
dependencies. GUI F7 and the CLI build edited sources with the pinned SDK CMake
profile. Both demos are bundled and run without build tools. See the
[audit, profile and limits](../docs/sdk-startup.md).

The ROM/flash increment is implemented. Core 0 executes the official B2 ROM,
reads flash over modeled SSI, validates boot2 CRC, executes boot2 from SRAM and
starts firmware. UF2 ingestion validates bounds, family/flags, page sizes,
complete numbering, overlap and duplicates. SDK erase/program, flash export and
reset persistence pass. The ROM stays local and is hash-checked; it is not packaged.
See [boot and flash](../docs/boot.md).

The MicroPython increment is implemented for the pinned 1.26.1 UART profile.
Its unmodified RP2 port uses the exact upstream SDK/TinyUSB/MbedTLS revisions
and a board configuration that disables USB. RTC/calendar/alarms, narrow I/O and
UART IRQ fixes supply audited dependencies. The desktop runs editable Python
through the UART raw REPL; CLI scripts, interruption, errors, GPIO/time, ADC,
RTC, persistent LittleFS files and replay pass. See [MicroPython](../docs/micropython.md).

Validation: 333 automated tests, including thirteen GUI tests; 286 native/runtime
tests also pass under ASan/UBSan with leak detection disabled in this environment.
The native contract passes. The Linux reference benchmark measures approximately
1.40 million busy-loop instructions/second and 20–93 ms pause acknowledgement
across five traced MicroPython-loop samples; this is not full-speed RP2040 execution.

## Remaining external validation

1. Compare named workloads against a physical Pico. The user confirmed no Pico
   is attached, so no hardware measurements have been fabricated.
2. Validate additional operating systems before advertising support. The user
   confirmed Linux only is available; Windows/macOS remain unvalidated.

These gates remain open, so the full plan is not marked complete. Optional USB,
watchdog countdown/reset and other unvalidated hardware/module paths remain
outside the delivered named-workload profiles.

The precise implemented modes, approximations, and unsupported behavior are in
[the compatibility matrix](../docs/compatibility.md).
