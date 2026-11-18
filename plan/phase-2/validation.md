# Phase 2 validation and completion criteria

The baseline suite contains 333 tests, including thirteen GUI tests. Count and
record new results after implementation; baseline results do not validate planned
UX changes. Use [Phase 1 validation](../phase-1/validation.md) for unchanged engine
contracts and [current runtime instructions](../../docs/validation.md) to run checks.

## Automated behavioral checks

| Area | Required evidence |
| --- | --- |
| Layout | Primary controls and panel restoration work at 1366×768; presets fit the available area; off-screen/corrupt saved geometry recovers |
| Save lifecycle | Modified indicators track actual content; Save/Discard/Cancel on close and replacement; canceled/failed saves retain work; failed opens retain the current document |
| History | Add/delete/wire/move/parameter undo and redo restore configuration, IDs and attached wires; each drag/group edit forms one transaction; source undo stays independent |
| Pending circuit | GUI edits do not silently restart execution; Apply acknowledges the correct revision; failures retain the last valid document/session; runtime flash handling is verified |
| Program identity | C source/build/image revisions and Python source/upload revisions stay accurate across concurrent editing, failures, cancellation and project replacement |
| Shared commands | Mouse, menu and keyboard actions use the same handlers; stale menu actions cannot mutate a running circuit |
| Debugging | Available source mappings agree with executable addresses; absent metadata falls back explicitly; instruction/core stepping semantics remain unchanged |
| Traces | Channel changes and clear/zoom/pan leave the engine untouched; cursor results match known transitions; missing edges/dropped events prevent invalid measurements |
| Preferences | Layout/fonts restore from isolated settings; switching presets changes neither circuit configuration nor deterministic input recordings |
| Shutdown | Canceled close leaves workers operational; accepted close safely stops active simulation/build/upload work |

Use behavioral assertions and a small set of rendered checks. Avoid brittle
pixel-perfect screenshots or tests that only mirror widget construction. Compare
headless events and GUI/replay events when changes touch execution or recording.
Engine changes require the existing native contract and appropriate sanitizer checks.

## Manual reference-screen checks

1. Check a 1366×768 desktop and a larger desktop on Linux, accounting for window
   decorations and available screen geometry.
2. Check default and enlarged fonts and available desktop scaling settings;
   record the scale values actually tested.
3. Collapse/restore panels and change presets while a project is open; reopen
   the app with settings originating from a larger display.
4. Verify mouse and keyboard workflows, visible focus, menu shortcuts and readable
   pin labels. Inspect HIGH/LOW/FLOATING/CONFLICT without relying on color alone.
5. Check the custom canvas with an available Linux screen reader and record gaps.
   Do not infer screen-reader support from keyboard support alone.

Record screenshots, platform, display size, scaling, configuration and observed
issues. Offscreen Qt rendering is useful layout evidence; it is not a substitute
for all interactive desktop or assistive-technology checks.

## Task-based usability evaluation

Use the following tasks with beginners and embedded developers when participants
are available. An initial small round of 3–5 participants is a practical proposal,
not completed research or statistical proof. Record the tested application revision.

| Task | Successful outcome |
| --- | --- |
| Open blink and run it | The user finds a working example and observes GPIO25 without command-line setup beyond installation |
| Add an external LED | The user locates parts, connects GP25, understands pending changes and applies the circuit |
| Slow and inspect | The user changes playback, pauses, locates a register and copies its value |
| Edit C firmware | The user understands edited versus loaded code, rebuilds and identifies a build error when one is introduced |
| Run Python | The user opens the MicroPython template, executes edited source and finds UART output; missing ROM setup is explained |
| Recover a mistake | The user undoes a deleted connected component and cancels a close with unsaved edits |
| Inspect timing | The user selects GP25, places two cursors and obtains a correct interval from a known waveform |

Record completion, time, assistance, wrong turns, recovery and remaining confusion
for each task. Separate facilitator-assisted success from independent completion.
Prioritize lost work, inaccessible primary controls and misleading execution state
as delivery blockers. Re-evaluate affected tasks after fixes.

## Delivery gate

- Required automated checks pass and new behavior is documented.
- Manual laptop, file-lifecycle and keyboard workflows pass on the reference Linux desktop.
- Installed-package smoke checks cover C blink, MicroPython, saved projects and new preferences.
- User-session and accessibility results are recorded, including unperformed checks.
- Milestones are checked only when their evidence is available; pending external
  checks remain open in [implementation status](implementation-status.md).
- Phase 1 hardware/other-OS gates stay separate; Phase 2 does not establish broader compatibility.

Documentation-only plan changes require local link/content checks rather than a
runtime test run.
