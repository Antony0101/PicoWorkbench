# Phase 1 implementation milestones

Complete each gate before broadening compatibility. The checkboxes below are
implementation status; broader compatibility remains governed by the status report.

## 0. Prove the execution foundation

- [x] Evaluate CPU backends for Cortex-M0+ instruction and exception support,
  bus callbacks, deterministic stepping, licensing, and timing visibility.
- [x] Run a tiny Thumb fixture through the best candidate and inspect its state.
- [x] Record the selected backend, missing features, and whether a custom
  interpreter is necessary. If custom, split decoder, instruction families,
  exceptions, and architectural validation into explicit tasks.
- [x] Define supported ELF/direct-load conventions and the initial compatibility matrix.
- [x] Establish the C build, Python environment, and CFFI packaging approach.

**Gate:** a repeatable headless proof executes the fixture and reports expected
register values. Do not assume a generic ARM emulator supplies a complete MCU.

## 1. Headless chip skeleton and LED firmware

- [x] Implement engine lifecycle, reset, bounded execution, and error reporting.
- [x] Add the memory bus, SRAM, mapped firmware storage, and basic access faults.
- [x] Validate ELF loading and implement the documented direct-load startup path.
- [x] Implement the SIO/GPIO, IO_BANK0, and PADS behavior needed by a fixture that
  explicitly configures and toggles one pin.
- [x] Connect a board LED model and collect timestamped GPIO changes.
- [x] Keep unsupported registers observable through diagnostics or strict failures.

**Gate:** a small bare-metal ELF blinks a virtual LED headlessly; reset and replay
produce the same trace. This does not yet claim normal Pico SDK boot support.

## 2. First desktop application

- [x] Wrap the engine through the API in [engine-api.md](engine-api.md).
- [x] Add the simulation worker, command queue, snapshots, and bounded trace buffers.
- [x] Create firmware loading, run/pause/reset/step, a Pico view, and register panels.
- [x] Display LED changes, virtual time, execution state, and unsupported-access errors.
- [x] Add PC breakpoints and side-effect-free memory inspection.

**Gate:** load the milestone 1 ELF in the GUI, run it, pause at a breakpoint, inspect
state, step it, and reset without freezing the interface.

## 3. Useful firmware debugging

- [x] Add Cortex-M exception entry/return, NVIC, SysTick, timer alarms, and required
  interrupt routing to the extent not already provided by the backend.
- [x] Add external GPIO input, GPIO interrupts, and a button model.
- [x] Add UART output/input, including interrupt and FIFO behavior required by fixtures.
- [x] Audit startup dependencies for selected Pico SDK examples: clocks, resets,
  synchronization, ROM calls, and flash behavior as applicable.
  Pico SDK 2.2.0 blink/UART use the [documented SDK ELF shortcut](../../docs/sdk-startup.md).
- [x] Implement dependencies or clearly document the selected boot shortcut.
- [x] Add serial and waveform panels, ELF symbols, and initial source location mapping.

**Gate:** selected, named firmware examples demonstrate button interrupts, timer
callbacks, and UART output. Each example has a documented supported startup path.

## 4. Circuit editing and external devices

- [x] Implement component descriptors, digital nets, pull-ups, and contention reporting.
- [x] Add a canvas for placing components and connecting pins.
- [x] Save/load project JSON with schema validation and relative firmware paths.
- [x] Add I2C master behavior and a simple register-based sensor.
- [x] Add SPI master behavior and one small display or loopback device.
- [x] Add PWM, a duty-cycle view, and manually controlled ADC inputs.
- [x] Add recorded, timestamped input events and deterministic replay.

**Gate:** save and reopen a project containing a button, LED, and bus device; run
firmware and obtain the same results through GUI control and headless replay.

## 5. PIO, DMA, and two cores

Treat these as separate increments with their own completion gates:

- [x] Extract private CPU, bus, scheduler, GPIO, and peripheral modules; preserve
  the public C API and existing behavioral tests before adding chip features.
- [x] PIO: state-machine instructions, FIFOs, pin mapping, clock dividers, stalls,
  IRQs, and inspection. Known waveforms and CPU interrupt wake-up pass within
  the [documented functional limits](../../docs/pio.md).
- [x] DMA: channels, transfer widths, pacing/DREQ, interrupts, and bus interactions.
  Paced transfers and both completion interrupt lines pass within the
  [documented functional limits](../../docs/dma.md).
- [x] Core 1: startup, shared memory, inter-core FIFOs, spinlocks, interrupt routing,
  and deterministic scheduling. Shared-memory/FIFO exchanges, both interrupt
  paths and reset replay pass within [documented limits](../../docs/multicore.md).
- [x] Validate an integrated DMA-to-PIO example after the individual gates pass.

**Gate:** supported examples pass with explicit timing guarantees. Do not infer
cycle accuracy from matching only final register or buffer values.

## 6. Broader compatibility and distribution

- [x] Extend normal boot and flash support; add UF2 loading with bounds validation.
  [B2 core-0 ROM/boot2 and flash profile](../../docs/boot.md).
- [x] Add watchdog, RTC, flash operations, or USB only as required by named workloads.
  RTC and serial flash support the named MicroPython UART workloads; USB and
  watchdog countdown/reset remain outside that profile.
- [x] Evaluate a pinned MicroPython image, inventory its hardware dependencies,
  and implement them before claiming interpreter compatibility.
  [MicroPython 1.26.1 UART profile and stock-image audit](../../docs/micropython.md).
- [x] Add a headless command-line runner and stable project-format migration policy.
- [x] Package the desktop application and document native library/toolchain setup.
- [ ] Validate additional operating systems individually before advertising support.
  External gate: Linux only is available; no additional OS validation is claimed.

Physical Pico comparison from the validation plan also remains open: the user
confirmed no Pico is attached. The local implementation gates above do not
substitute for those measurements. See [current status](implementation-status.md).

**Gate:** installation and example projects work on every advertised platform;
the compatibility matrix identifies partial implementations and timing limitations.
