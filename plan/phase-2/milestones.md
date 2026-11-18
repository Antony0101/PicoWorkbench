# Phase 2 implementation milestones

All implementation tasks start unchecked. Existing right-click menus and the
research review are baseline work. Follow the shared [architecture](architecture.md)
and [validation](validation.md) contracts throughout.

## P2.1 — Fit ordinary laptop screens

- [ ] Remove the minimum-height constraints that forced a requested 1366×768
  window to 1366×831 in the reviewed Linux environment.
- [ ] Make inspectors and output panels collapsible and restore them through View actions.
- [ ] Adapt toolbars, legends and inspector navigation when width is limited.
- [ ] Keep canvas labels readable when fitting; provide visible zoom and fit controls.
- [ ] Persist window geometry, panel sizes and font preferences; provide Reset layout.

**Gate:** at 1366×768 on the reference Linux desktop, application chrome fits the
available area, primary controls remain reachable and the editor can be expanded
without restarting. Restoring saved geometry on a smaller display stays on-screen.

## P2.2 — Protect source and circuit work

- [ ] Track saved versus modified source and circuit configuration, and show a modified marker.
- [ ] Add Save/Discard/Cancel handling before closing, replacing a project or replacing edited source.
- [ ] Cancel the operation on canceled/failed saves; preserve the current document on failed opens.
- [ ] Add circuit Undo/Redo for add/delete, wiring/disconnection, movement, labels and device settings.
- [ ] Treat a drag or multi-object deletion as one history operation and restore attached wires.
- [ ] Preserve the editor's own text undo/redo; make action routing depend on focus.
- [ ] Define handling of modified simulated flash before operations that replace it,
  including the existing export option. Distinguish runtime files from saved project configuration.

**Gate:** accidental deletion can be fully undone; undoing back to the saved
configuration clears its modified state. Canceling a close or failed save leaves
the project, editor, history and simulation available.

## P2.3 — Explain loaded code and execution state

- [ ] Show firmware/script identity and distinct paused, running, building,
  uploading, finished and fault states near execution controls.
- [ ] Track C editor changes against the loaded build; show Edited — rebuild required.
- [ ] Track MicroPython edits against the last uploaded source; use upload/run wording.
- [ ] Make circuit changes pending until an explicit Apply + Reset or Apply + Run,
  with clear feedback about restarted execution and breakpoint handling.
- [ ] Publish loaded/applied status only after worker acknowledgement; preserve
  truthful previous state on validation, build, upload or load failure.
- [ ] Keep Playback speed distinct from the functional CPU clock, and label
  Step instruction and the existing next-runnable-core behavior accurately.
- [ ] Add concise fault explanations and relevant recovery actions with expandable technical details.

**Gate:** edited source cannot appear to be the code currently executing. A failed
build never reports a successful load, and a wiring edit does not silently reset
the chip. Changing playback alone preserves virtual event ordering and timing.

## P2.4 — Organize tasks and the first run

- [ ] Add Circuit, Code and Debug workspace presets with visible switching and reset.
- [ ] Keep Run/Pause, Reset, Build/Run and Playback prominent; move clock and
  bounded instruction controls into an expandable Advanced area.
- [ ] Organize new-project/examples actions into a discoverable chooser with
  C blink, MicroPython and peripheral workbench starting points.
- [ ] Provide a short dismissible guide to Run, wiring, playback and inspection.
- [ ] Explain missing ROM/toolchain requirements in the relevant workflow and
  offer existing setup instructions without an automatic network download.
- [ ] Expose shortcuts and retain visible alternatives to right-click commands.

**Gate:** each starting point leads to the correct source language, startup profile
and primary action. Switching layouts does not modify the circuit or chip state.

## P2.5 — Improve wiring and component properties

- [ ] Add a temporary wire preview, destination feedback and explicit cancellation.
- [ ] Increase port hit areas without changing circuit connectivity or obscuring adjacent ports.
- [ ] Highlight the selected net and show its connected endpoints and resolved state.
- [ ] Add a selection properties panel for labels, supported device parameters and connections.
- [ ] Offer optional grid snapping, keyboard movement and component duplication through history commands.
- [ ] Label the existing drawing as a logical GPIO view; distinguish GPIO identifiers
  from physical Pico header numbers without implying unmodeled power/electrical behavior.
- [ ] Keep pending edit, running-state guards, right-click and keyboard behavior consistent.

**Gate:** an LED can be added, wired to GP25, edited, duplicated and removed with
undo/redo. Escape cancels the preview without changing the net graph. Invalid
edits retain the last valid circuit and report the reason.

## P2.6 — Connect source editing and debugging

- [ ] Add line numbers and C/Python-appropriate syntax highlighting.
- [ ] Highlight the current C source location when valid debug metadata maps it.
- [ ] Add gutter breakpoints and a manageable breakpoint list with address/function fallback.
- [ ] Show disassembly/address information when source mapping is unavailable;
  do not present firmware C addresses as Python source breakpoints.
- [ ] Highlight changed register values on meaningful snapshot updates and allow copying them.
- [ ] Link compiler diagnostics to an editor line when a reliable source mapping exists.

**Gate:** supported C source breakpoints resolve to instruction addresses, halt at
the expected location and remain consistent with the breakpoint list. Missing
metadata has an explicit fallback; instruction-step semantics stay unchanged.

## P2.7 — Make digital traces useful for measurement

- [ ] Replace fixed GP25/GP14/GP0 channels with selectable, named GPIO channels.
- [ ] Add horizontal pan/zoom, fit-to-data, follow-latest and readable time units at every scale.
- [ ] Add cursor pairs and Δt, with frequency/duty measurements only where enough transitions exist.
- [ ] Distinguish HIGH, LOW, FLOATING and CONFLICT through text/shape cues as well as color.
- [ ] Show trace drops/truncation, and mark measurements unavailable across missing data.
- [ ] Keep clear-display, channel selection and zoom independent of hardware state and recording.

**Gate:** known blink and PIO fixtures produce expected virtual-time cursor
measurements. Display changes leave headless/replay results unchanged, and
incomplete traces never show unsupported precision or protocol claims.

## P2.8 — Finish keyboard access and readability

- [ ] Verify focus order, visible focus and keyboard access to primary controls and menus.
- [ ] Provide keyboard selection, connection/cancellation, inspection and manipulation of canvas elements.
- [ ] Add accessible names, roles/state exposure and change notifications for custom controls as needed.
- [ ] Check text and control contrast, font scaling and non-color state cues.
- [ ] Verify keyboard-triggered context menus target the focused object.

**Gate:** the core workflow is possible without a mouse. Record screen-reader
checks actually performed, unresolved gaps and tested scaling configurations.

## P2.9 — Validate workflows and package the changes

- [ ] Complete the automated and manual scenarios in [validation.md](validation.md).
- [ ] Evaluate representative tasks with intended users when participants are available;
  record assistance and confusion separately from automated results.
- [ ] Resolve blocking layout, data-loss and misleading-state findings before delivery.
- [ ] Verify the installed Linux package, example reopening, existing menus and documented shortcuts.
- [ ] Update screenshots, usage documentation, status and remaining limitations.

**Gate:** evidence supports every completed milestone. Unperformed user studies,
accessibility checks or platform checks remain explicitly open rather than being
inferred from a passing automated suite.
