# Engine API contract

The following names describe the intended interface, not an implemented header.
Finalize structures and ownership rules in milestone 0 before building the GUI.

The delivered ABI is defined in `engine/include/rp2040_sim.h`. Its ELF loaders
are `sim_load_elf` (direct) and the additive `sim_load_elf_sdk` (SDK 2.2.0 profile).
Both retain ABI version 1; `sim_reset` repeats the previously selected startup
mode. See [SDK startup semantics](../docs/sdk-startup.md).
`sim_load_flash` and `sim_load_uf2` additionally accept an explicit B2 ROM and
execute its reset vectors; these retain ABI 1 as additive entry points.

## Core operations

| Operation | Meaning |
| --- | --- |
| `sim_create(config)` | Create an opaque instance with board/chip options |
| `sim_destroy(sim)` | Release the instance and its owned resources |
| `sim_load_image(sim, image)` | Load a validated image while stopped |
| `sim_reset(sim, mode)` | Reset using the selected boot/direct-load policy |
| `sim_run_until(sim, limits)` | Execute to a deadline, work budget, or stop condition |
| `sim_step(sim, core_id)` | Execute one selected-core instruction with defined event handling |
| `sim_get_snapshot(sim, request)` | Copy requested architectural and peripheral state |
| `sim_peek_memory(sim, address, buffer)` | Inspect memory without firmware read side effects |
| `sim_set_breakpoint(sim, address)` | Add an execution breakpoint |
| `sim_remove_breakpoint(sim, address)` | Remove an execution breakpoint |
| `sim_schedule_input(sim, event)` | Queue a timestamped external input |
| `sim_drain_events(sim, buffer)` | Copy trace and device-interaction events in batches |
| `sim_complete_device_request(sim, response)` | Resolve a paused external device interaction |

Use explicit-width integer types, an ABI version, structured error codes, and
opaque handles. Do not expose C internal layouts or pointers to mutable engine
state. Define buffer lengths, caller/callee ownership, string encoding, and error
lifetimes in the public header. No error may terminate the host process.

## Execution results

Each run result includes virtual time, work performed, and a reason for stopping:

- Deadline or instruction/work budget reached.
- Breakpoint or configured watchpoint.
- Pending external device request that must be answered before continuing.
- All cores waiting with no future event capable of waking them.
- Unsupported operation, invalid memory access, or another simulation fault.

Check pause requests between bounded runs in the worker. A halted CPU may still
have active timers or peripherals; do not treat it as an idle whole machine.

For multi-core stepping, define which other cores are frozen and how scheduled
peripheral events advance. Expose this debugger policy instead of leaving it to
host thread timing.

## External devices

Python devices must not receive their first notification after the engine has
already executed firmware that depends on their response. A synchronous bus read
or time-critical edge ends the execution batch at a defined boundary. The worker
handles the device request, schedules its result, and resumes execution.

Observers such as LED rendering or UART text display can consume buffered events
after a run. Active device models participate in scheduling. Include the earliest
external component deadline in each run limit.

Start with one outstanding device request per simulator. Avoid reentrant calls
from arbitrary GUI callbacks into a running engine. Batch bus transactions where
the modeled timing permits; use native models for high-frequency pin protocols.

## State and traces

A snapshot should include an immutable sequence number, virtual timestamp, core
registers, execution status, selected peripheral state, and resolved pin values.
Let the caller request relevant subsets so refreshing the GUI does not copy all
memory every frame.

Trace records carry a timestamp, stable ordering sequence, event kind, source,
and payload. Capture is configurable and bounded. Overflow is explicit; validation
runs that require a complete trace must fail if records were lost.

Reading the debugger view must not pop a FIFO, clear an interrupt, acknowledge a
status flag, or otherwise behave like a firmware MMIO read. Registers without a
meaningful inspection value should report unavailable.

## Thread and lifetime rules

- One worker owns and accesses an engine instance.
- The GUI exchanges copied commands, results, and snapshots with that worker.
- Circuit edits and firmware replacement occur while paused.
- Pending input/device requests are invalidated or rebuilt by a documented reset.
- Destroy the engine only after execution and component servicing have stopped.
- Test two independent instances to catch accidental global hardware state.
