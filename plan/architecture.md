# Architecture

## Technology choices

| Area | Proposed choice | Reason |
| --- | --- | --- |
| Hardware engine | C11, CMake | Explicit state and efficient execution |
| Bindings | CFFI, out-of-line API mode | Small C interface with compiled type checking |
| Application | Python, PySide6 Widgets | Desktop panels, editor, and a custom wiring scene |
| Project files | Versioned JSON | Portable board and connection descriptions |
| Firmware | ELF first; UF2 later | ELF supports executable segments and debug metadata |
| Verification | C engine tests and pytest | Hardware checks plus application integration |

Pin supported dependency versions during implementation. Keep the ARM firmware
toolchain separate from the host compiler used to build the simulator.

## Layer boundaries

The application contains five independent layers:

1. **CPU backend:** architectural registers, instruction execution, exceptions,
   and access to a memory bus.
2. **RP2040 chip:** address decoding, SRAM, flash mapping, peripheral registers,
   reset state, interrupt wiring, and event scheduling.
3. **Board:** chip instance, external flash configuration, board pin names, and
   board-mounted parts such as the Pico LED.
4. **Circuit:** external components, nets, bus devices, and input stimuli.
5. **Presentation:** editor, canvas, debugger, serial terminal, and waveform views.

The C engine owns authoritative chip state and net resolution. Python components
may own external device state, but all interactions occur at scheduler boundaries.
The GUI displays snapshots and submits commands; it does not mutate hardware
objects directly.

## Suggested repository structure

```text
engine/
  include/rp2040_sim.h
  src/cpu/
  src/bus/
  src/chip/
  src/peripherals/
  src/scheduler/
  src/circuit/
  tests/
bindings/
python/rp2040_sim/
  controller/
  components/
  project/
  build/
  gui/
firmware/fixtures/
tests/integration/
examples/
plan/
```

Create directories when their milestones begin; avoid empty module scaffolding.

## Simulation time and execution

- Use integer virtual timestamps and a stable ordering rule for simultaneous
  events. A fixed-point time representation with retained clock remainders is one
  option; select and test the representation before implementing clocks.
- Model clock domains explicitly. Converting CPU, peripheral, and PIO cycles to
  virtual time must avoid accumulating rounding drift.
- Schedule future events, such as timer alarms or UART completions, instead of
  polling every peripheral after every instruction.
- Start with instruction-boundary scheduling and document timing approximations.
  Precise bus contention and sub-instruction timing require a later fidelity level.
- When a core sleeps, advance to the next event that can change runnable state.
- Give execution both a virtual-time limit and an instruction/work limit so a
  stalled clock or zero-time loop cannot prevent control returning to the caller.
- Separate simulated speed from display refresh. Real-time pacing is optional and
  must never change event order or firmware results.

Both cores must eventually share one deterministic scheduling policy. Two host
threads executing cores independently would make ordering and replay harder;
begin with a single simulation owner that schedules both cores when supported.

## GUI responsiveness and ownership

Run simulation on a worker thread that owns the engine and Python device models.
The Qt main thread owns all widgets. Communicate through queued commands and
immutable snapshots. Bound each execution batch and check pause/stop commands
between batches.

CFFI execution calls must allow the GUI Python thread to run. Verify the selected
binding mode's GIL behavior. Python device callbacks can reacquire the GIL, so
high-frequency device behavior may need to move to C.

Publish display snapshots at roughly 30–60 Hz. Keep timestamped traces in bounded
buffers independently of rendering; report overflow rather than silently losing
events. A future process boundary should be possible through the controller API,
but is unnecessary for the first version.

## Firmware loading and builds

Begin with a controlled ELF fixture linked to known addresses. Validate target,
segment addresses, sizes, permissions, and entry assumptions. Provide a named
direct-load mode that defines the initial stack, PC, and vector-table handling.
Do not describe this mode as an authentic power-on boot.

Add normal boot behavior only when ROM, external flash/XIP, reset, and SDK startup
requirements have been identified. UF2 is an image container, not a substitute for
implementing the hardware expected by that firmware.

The build runner invokes the ARM toolchain as a separate process, captures
diagnostics, and supports cancellation. The engine accepts built images and has
no dependency on an editor or compiler installation.

## Debugging and persistence

Support run/pause, single-instruction step, PC breakpoints, register inspection,
memory inspection, and deterministic reset first. Add watchpoints and source
mapping after the execution model is stable. Keep debug metadata outside the
chip model.

Project JSON stores board configuration, component identifiers and parameters,
wires, firmware paths, and build settings. Resolve relative paths from the project
directory. Version the schema and reject unsupported versions clearly.

Record timestamped external inputs for replay. Replay from a known reset state
comes before serializing live snapshots. Live save states later need CPU, device,
clock, event-queue, and external component state together.
