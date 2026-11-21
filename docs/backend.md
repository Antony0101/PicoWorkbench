# Engine and backend decisions

## Selected backend: Unicorn 2.1.4 in C

The native engine links to Unicorn's C library and selects its Cortex-M0 model in
Thumb/M-class mode. Cortex-M0 instruction execution supplies the initial CPU
foundation for this RP2040 model. Chip registers, GPIO, interrupts, SysTick,
exception frames, timing, and external interactions are implemented by this
project rather than assumed to exist in the CPU backend.

The execution proof cross-compiles freestanding Cortex-M0+ C firmware and runs
it through the native interface. Tests check instructions/flags, independent
instances, timer/GPIO IRQs, nested priorities, PRIMASK, SVC, MSP/PSP exception
return, memory validation, and real peripheral interactions.

Alternatives considered:

- A custom Thumb interpreter would give full control but require a separate ISA
  implementation and substantial architectural testing before useful firmware
  could run. It is unnecessary for the first release.
- [RP2040js](https://github.com/wokwi/rp2040js) offers an existing chip model, but
  embedding a JavaScript runtime would change the chosen C/Python boundary. It
  remains a useful behavioral reference; it was not embedded or benchmarked here.
- Unicorn supplies a reusable native CPU, but not full RP2040 compatibility or
  instruction cycle timings. The surrounding chip model must supply those pieces.

## Boundaries in the implementation

- `engine/include/rp2040_sim.h`: versioned opaque C API with copied results.
- `engine/src/sim.c`: lifecycle, reset coordination, copied snapshots, errors,
  and the trace ring.
- `engine/src/cpu.c`: Unicorn execution, instruction hooks, exceptions, NVIC,
  SysTick, per-core register contexts/snapshots, and breakpoints.
- `engine/src/bus.c`: flash/SRAM mapping, ELF validation/loading, MMIO routing,
  memory-access faults, and side-effect-free debug peeks.
- `engine/src/scheduler.c`: virtual time, ordered input delivery, bounded run/step,
  and sleeping-core event scheduling.
- `engine/src/gpio.c`: digital net resolution, GPIO mux/pads, SIO, and GPIO IRQ status.
- `engine/src/peripherals.c`: timer, UART, I2C, SPI, PWM, ADC, peripheral deadlines,
  IRQ aggregation, and external-device requests/replies.
- `engine/src/pio.c`: PIO0/PIO1 instruction execution, FIFO and shift state,
  clock divider phases, pin writes, IRQ sources, and copied PIO snapshots.
- `engine/src/dma.c`: channel transfers, arbitration, DREQ pacing, chaining,
  completion/error IRQs, and copied DMA snapshots.
- `engine/src/multicore.c`: SIO FIFOs/spinlocks, core-1 launch/reset protocol,
  synchronization IRQs, core-local hardware dividers and copied two-core state.
- `engine/src/system.c`: functional SDK clock/reset/startup control registers.
- `engine/src/bootrom.c`: explicit SDK-mode ROM lookup, bit and memory ABI shims.
- `engine/src/image.c`: bounded raw flash/UF2 validation and real-ROM image loading.
- `engine/src/flash.c`: functional NOR/SSI/QSPI, erase/program and XIP control storage.
- `engine/src/rtc.c`: deterministic calendar, latching, alarm and IRQ behavior.
- `engine/src/sim_internal.h`: private instance state and module interfaces.
- `bindings/build.py`: out-of-line CFFI extension; no Python callbacks in the CPU loop.
- `python/rp2040_sim/engine.py`: owned Python handles and immutable snapshots/events.
- `controller.py`, `project.py`, `components.py`: headless project and device models.
- `gui/worker.py`: the engine's sole owner in a Qt worker thread.
- `gui/`: presentation only; widgets receive copied state and submit commands.

All native modules operate on the same privately defined instance, owned by one
caller thread. The public handle remains opaque; module interfaces have hidden
symbol visibility on the supported Linux toolchain. ABI version 1 and the original
snapshot layout are preserved. PIO, DMA and the two-core inspector add independent
copied-state queries; SDK startup adds `sim_load_elf_sdk`, bringing the public API
to 22 functions. ROM/UF2 add two image loaders, bringing the API to 24 functions.
Both CMake and CFFI compile the same fourteen C translation units; the source package includes the
private header. The modules share private instance state.

Two saved Unicorn CPU contexts share one backend memory map. The scheduler
switches register contexts outside backend execution. Core-local exception,
NVIC, SysTick and sleep state live in separate private banks. Shared SRAM and
peripherals have one authoritative representation. See [two-core behavior](multicore.md).

The bus dispatches MMIO to GPIO, device, CPU-system, PIO, DMA, multicore and startup-system
register handlers. Unknown registers return an unhandled result to the bus.
Ordinary unsupported accesses fault there; debug peeks return an error without faulting the instance. A device
write that records a specific fault is still handled, preserving its diagnostic.

The scheduler delivers inputs in timestamp/submission order, services peripheral
events, then executes bounded CPU work. CPU instruction hooks advance virtual
time through the scheduler. During sleep, the scheduler chooses the earliest
input, device, SysTick, PIO clock, or ready DMA beat deadline. DMA uses the same
bus dispatcher as the CPU, with channel-level errors for invalid DMA accesses
and narrow peripheral transfer replication. See [DMA timing](dma.md#timing-and-limits).

An I2C/SPI transfer yields at the next instruction boundary, with the initiating
MMIO instruction already completed. The controller finds the wired Python
device, supplies its reply, and resumes execution. Stopping inside Unicorn's
MMIO callback would repeat the write on resume; the boundary protocol avoids that.

## Timing contract

One executed instruction on either core advances one aggregate timing tick.
Default clock: 1 MHz; configurable range: 1 kHz–133 MHz. An integer remainder prevents clock-conversion
drift during instruction execution. This is an explicit timing approximation,
not the RP2040's real instruction timing. When both cores are runnable they
alternate instructions; when one sleeps the other continues. Thus two busy
cores share the configured aggregate instruction rate, rather than both
retiring concurrently at that rate.

Timer values use virtual microseconds. SysTick and PWM advance with virtual
clock activity. Sleeping CPUs advance to scheduled inputs, timer/ADC deadlines,
SysTick events, or PWM edges, with a bounded event-work budget. Sleep transitions
round to representable nanosecond boundaries.

SDK oscillator/PLL readiness is immediate. SDK clock-register writes are stored
and affect mux readiness, but the configured aggregate functional clock remains
the timing source. ROM ABI shim calls consume one functional tick, independent
of memory length. See the [SDK startup contract](sdk-startup.md).
ROM startup instead executes the actual ROM instructions, including memory and
floating-point routines. RTC calendar time follows deterministic virtual seconds.
Flash programming updates the instance's retained flash bytes and invalidates
translated executable flash. See [ROM/flash behavior](boot.md).

UART, I2C, and SPI operate at byte/transaction level. Baud and SCL/SCK edge timing
are not simulated. ADC conversions have a minimum two-microsecond duration,
with longer continuous-conversion spacing derived from DIV.

Inputs at equal timestamps retain submission order. State changes and traces
are deterministic for the same firmware, configuration, engine, and input sequence.
Unrepresentable external timestamps are handled at the next instruction boundary;
controller recordings always use actual boundaries.

## Dependencies and distribution

The GUI uses Qt Essentials, avoiding unrelated Qt add-on modules. Unicorn is
pinned because its C ABI and behavior are part of execution reproducibility.
The extension looks for the runtime library beside the installed Unicorn package,
with a development-environment fallback path.

Unicorn's native engine is GPLv2, even though its Python wheel metadata describes
the bindings as BSD. Its [upstream license](https://github.com/unicorn-engine/unicorn/blob/2.1.4/COPYING)
and [release README](https://github.com/unicorn-engine/unicorn/blob/2.1.4/README.md)
are the relevant native dependency references. Qt Essentials reports LGPL-3.0-only,
GPL-2.0-only, or GPL-3.0-only alternatives. CFFI reports MIT-0; pyelftools reports
public domain. Original project code and documentation use
[`GPL-2.0-or-later`](../LICENSE). See the
[third-party inventory](../THIRD_PARTY_NOTICES.md) for dependency and bundled-asset
notices; their terms remain separate from the original project license.

The backend is dynamically linked, not copied into this repository. Packaging
produces a local Linux Python wheel. Additional platforms and a public package
release pipeline have not been validated.

The bundled SDK firmware contains compiled Pico SDK 2.2.0, pico_printf and
compiler/C runtime code. Packaged assets include `SDK-NOTICES.txt` and
`sdk-provenance.json`; the SDK and cross compiler themselves are build-only,
local dependencies. See [rebuilding the SDK profile](sdk-startup.md).
The packaged MicroPython UART UF2 has a separate pinned SDK/dependency manifest
and notices. The B2 ROM remains local and is hash-checked before Python loads it;
it is not included in distributions. See [MicroPython provenance](micropython.md).
