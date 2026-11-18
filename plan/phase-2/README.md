# Phase 2: desktop UI and UX

Status: planned. This phase turns the delivered simulator into a clearer,
comfortable desktop workflow for learning, wiring and firmware debugging.
It follows the interface review and research recorded in [research.md](research.md).

The baseline is [Phase 1](../phase-1/README.md): Linux version 0.1.0 with
333 passing automated tests, including thirteen GUI checks, and existing
right-click menus. These are baseline results, not evidence that Phase 2 is done.
Physical Pico and additional OS checks remain in [Phase 1 status](../phase-1/implementation-status.md).

## Read in this order

1. [Research and findings](research.md): observed problems and supporting sources.
2. [Milestones](milestones.md): ordered work and acceptance gates.
3. [Architecture](architecture.md): document state, history, worker commands and settings.
4. [Validation](validation.md): automated checks and task-based usability evaluation.
5. [Implementation status](implementation-status.md): delivered work and remaining gates.

## Intended workflow

1. Open an example, a saved project, or a C/MicroPython project template.
2. Add parts and wire them with clear endpoint and selection feedback.
3. Edit source and see whether it matches the loaded firmware or last uploaded script.
4. Build/load or upload/run through an obvious primary action.
5. Observe the circuit and serial output; slow playback, pause and inspect state.
6. Switch to a code/debug layout, set supported breakpoints and inspect traces.
7. Undo an accidental circuit edit, save changes and reopen the project safely.

## Scope and order

Deliver laptop layout, protection of work and execution feedback first. Follow
with workspace presets and onboarding, circuit properties and wiring feedback,
editor/debug integration, trace measurements and an accessibility pass. Finish
with the usability and regression evidence in the validation plan.

Keep frequent actions visible. Right-click menus remain additional access to
contextual commands. Preserve standard editing shortcuts and provide discoverable
keyboard alternatives for canvas operations.

Retain the C engine/Python GUI architecture, worker ownership, supported startup
profiles, deterministic execution and functional timing contract. New controls
must describe actual capabilities: instruction stepping is not source-line stepping,
and a transaction-level bus model is not a captured pin waveform.

## Boundaries

This phase does not expand USB, watchdog, analog or CPU emulation. It does not
add reverse execution, live save states, Python line debugging, source-level
Step Over, or transaction-derived protocol waveforms. Source breakpoints use
existing debug metadata where available and show unavailable states elsewhere.

UI preferences stay separate from portable circuit files. Keep existing project
JSON compatible; any necessary schema change requires an explicit migration and
validation. Linux is the delivery platform; other OS support needs its own evidence.

Complete each milestone's gate and update its status before claiming delivery.
