# Phase 2 UI architecture

Build on the [Phase 1 architecture](../phase-1/architecture.md). Preserve the
opaque engine API and single simulation-worker ownership. Widgets consume
copied state and submit queued commands; new panels never read live engine objects.

## Document, loaded program and presentation state

Keep these responsibilities separate:

| State | Contents | Persistence and authority |
| --- | --- | --- |
| Project document | Circuit configuration, source buffer, firmware/build references and existing project metadata | GUI document model; saved through existing project/source workflows |
| Loaded session | Acknowledged image identity, applied circuit revision, startup profile, execution/transfer state and breakpoints | Worker reports authoritative simulation status |
| Presentation | Workspace preset, panel sizes/visibility, window geometry, fonts and trace display options | Per-user settings; independent of portable project data |

Track saved content and current content, not just whether a change signal fired.
Undoing to saved content must clear the corresponding modified indicator.
Track source files and project files separately so saving one does not falsely
mark the other saved. An exported simulated flash image is a separate artifact.

Associate builds/uploads with the source revision that started them. If the user
edits during an operation, successful completion still leaves the newer buffer
marked different from the loaded program. C uses build/load language; MicroPython
uses upload/run language and reports interpreter transfer phases.

## Circuit history and application

Route additions, removal, wiring, movement and parameter edits through commands
that contain enough information to undo and redo. Use stable component IDs and
endpoint IDs. Coalesce a completed drag into one command; removing multiple parts
and their attached wires is one transaction. Keep source text history in the editor.

Validate a candidate document before committing it. A rejected edit must not
leave partially modified configuration or misleading undo history. Show staged
circuit changes separately from the worker's applied circuit revision.

Apply staged configuration through one explicit Apply + Reset or Apply + Run
operation while paused. Explain reset consequences before activation, then publish
the applied revision after worker acknowledgement. On failure retain the last
working session and expose retry/revert options. Layout and selection changes
must never trigger this operation.

Define runtime flash handling explicitly. A circuit-only reload using the same
firmware must avoid silently replacing modified flash with the original image.
For replacement workflows that discard runtime files, offer export/cancel before
replacement. This does not require a general live CPU save-state facility.

Disable circuit mutation while running and recheck guards when a command executes.
Menus, toolbar actions, keyboard commands and the properties panel share command
handlers and enabled-state rules. An action opened before a state transition
must not bypass those rules.

## Safe file lifecycle

Before closing or replacing unsaved documents, resolve Save/Discard/Cancel.
Only shut down workers or replace document references after that decision succeeds.
Canceled dialogs and failed writes leave work intact. Validate/read new input
before replacing the current document, and use atomic replacement for saved files
where supported. Show the resulting saved path and retain errors for diagnosis.

## Layout and navigation

Use Qt's existing splitters/panels, allowing collapse and restoration with View
actions. Introduce additional containers only where needed for independent sizing
and visibility. Avoid minimum sizes derived from the full contents of every
inspector. Keep infrequent controls in an expandable Advanced area.

Provide Circuit, Code and Debug presets as presentation changes. Store user
preferences with a version and safe defaults; clamp restored windows to available
screens and recover from invalid settings. Tests use isolated settings storage.
Changing a preset, font or trace window must not alter project hashes, input
recordings or simulation time.

## Debugging and trace boundaries

Reuse existing symbols/DWARF mapping outside the chip model. A source breakpoint
requires an actual executable address mapping; retain address/function fallback.
Do not imply Python source debugging or source-line stepping. The current
instruction scheduler and core-selection policy remain authoritative.

Trace views consume bounded timestamped GPIO events. Keep selected channels,
cursors and display ranges in presentation state. Measurements use virtual event
timestamps and account for trace drops and missing boundary transitions. Never
infer I2C/SPI pin activity from transaction-level request/response logs.

## Accessibility and feedback

Prefer standard widgets where they provide appropriate keyboard/accessibility
behavior. Custom canvas controls need focus, names, roles and state notifications
that can be verified through Qt's accessibility interfaces. Provide textual pin
state and properties access alongside colored graphics.

Centralize concise status messages and recovery actions while retaining detailed
engine/compiler diagnostics. Keep accurate distinctions between paused, finished,
faulted, building and uploading; elapsed virtual time is not wall-clock time.
