# Phase 1 validation and delivery criteria

## Correctness strategy

Test architectural behavior and observable firmware results. Avoid tests that
only repeat the implementation's internal structure.

| Area | Required evidence |
| --- | --- |
| CPU | Instruction/flag corner cases, branches, stack operations, exception entry/return |
| Memory bus | Access widths, alignment policy, aliasing, invalid addresses, MMIO side effects |
| Registers | Reset values, writable masks, write-one-to-clear behavior, atomic aliases where applicable |
| Scheduler | Stable ordering, deadline boundaries, clock conversion drift, idle wake-up |
| Interrupts | Enable/mask/pending behavior, priority, nesting, return, peripheral acknowledgement |
| GPIO/nets | Output enable, input changes, pulls, floating states, contention, reset |
| Bindings | Error propagation, ownership, repeated create/destroy, independent instances |
| Projects | Save/load round trip, invalid connections, schema rejection, relative paths |

Choose tests relevant to each implemented feature; do not block the first
milestone on coverage of hardware that has not yet been implemented.

## Firmware fixtures

Keep small, reproducible fixtures with pinned toolchain/build settings and
expected observable behavior:

1. Arithmetic, branches, memory access, and a known final register signature.
2. Explicit GPIO setup and LED transitions under direct-load startup.
3. Timer-driven LED and GPIO interrupt from recorded button presses.
4. UART output and input with known byte sequences.
5. I2C register read and SPI transfer against deterministic external devices.
6. PIO waveform, DMA transfer, and inter-core exchange as those features arrive.

Use virtual-time limits and expected outputs; a test that only observes no crash
does not establish compatibility. Verify that diagnostics identify the first
unsupported access instead of letting a fixture appear to pass accidentally.

## Comparison against hardware and references

For behaviors that matter to supported examples, run the same firmware on a real
Pico when available and compare UART output, interrupt outcomes, and logic traces.
Record hardware setup, firmware revision, clock configuration, and tolerances.

Use independent emulators as secondary comparisons. Resolve disagreement against
the RP2040 datasheet and applicable Arm specifications. If physical hardware is
unavailable, identify unverified behavior rather than inventing measurements.

## Determinism

Repeat a fixed firmware/circuit/input recording from reset and compare event
sequences and final state. Include a simultaneous-events case. Project configuration,
engine version, firmware hash, startup policy, input timestamps, and any random
seed must be recorded. GUI refresh rate must not affect the result.

## Performance and responsiveness

Measure headless instruction throughput, active-device overhead, trace volume,
memory consumption, and pause latency on a named host and workload. Do not claim
full-speed RP2040 execution before measuring it.

Initial GUI acceptance target: pause/stop is normally acknowledged within 100 ms
on the reference machine, with large firmware loops and tracing enabled. Bound
execution batches and component servicing to make that target achievable. Treat
this as a target to verify, not an existing guarantee.

Stress bounded trace buffers and report loss. Keep display downsampling separate
from hardware timing and from complete traces used for correctness checks.

## Compatibility matrix

Maintain the following fields for each implemented subsystem:

- Implemented registers, operating modes, and interrupt behavior.
- Supported firmware fixtures and tested startup paths.
- Timing level: functional, approximate scheduled timing, or measured cycle behavior.
- Unsupported behavior and strict-mode diagnostics.
- Evidence and known deviations.

Start strict development mode with explicit unsupported-access stops. If a later
permissive mode returns defaults, display that choice and log every affected
region so successful execution is not mistaken for hardware fidelity.

## Delivery checks

- Build the C engine with compiler warnings enabled and run relevant tests.
- Use address/undefined-behavior sanitizers for engine changes where supported.
- Exercise the Python bindings and the milestone's headless firmware fixtures.
- Smoke-test load, run, pause, step, reset, close, and error display in the GUI.
- Verify project reopening and documented examples when those features exist.
- Ship dependency/license information and clearly documented limitations.

Documentation-only planning changes need link/content checks, not runtime tests.
