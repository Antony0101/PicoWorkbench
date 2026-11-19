# Phase 2 implementation status

Status: Linux software implementation delivered. Participant usability sessions
and a screen-reader audit remain open. Existing right-click menus belong to the
Phase 1 baseline; the broader UX work is tracked here separately.

| Milestone | Status | Required evidence |
| --- | --- | --- |
| P2.1: laptop layout | Implemented | 1366×768 fits; collapsible panels, Circuit/Code/Debug presets, settings recovery and saved preferences |
| P2.2: work protection | Implemented | Validated history, source/circuit modified tracking, atomic saves and cancel/failure protection; runtime flash export guard |
| P2.3: execution feedback | Implemented | Source revisions, acknowledged loads, explicit circuit apply, last Python upload identity and transactional image replacement |
| P2.4: tasks and first run | Implemented | Example chooser/submenu, dismissible guide, hidden Advanced controls and contextual dependency help |
| P2.5: wiring and properties | Implemented | Wire preview, larger targets, net properties, keyboard controls, snapping, duplication and shared edit commands |
| P2.6: editor and debugger | Implemented for supported metadata | Line numbers, C/Python highlighting, mapped C breakpoints, execution markers, changed registers and compiler error navigation |
| P2.7: digital traces | Implemented | Selectable GPIO channels, zoom/pan/follow, exact virtual-time cursors, frequency/duty and explicit loss handling |
| P2.8: accessibility | Implemented; screen-reader validation open | Standard-widget alternatives to canvas gestures, focus styling, keyboard context menus, accessibility queries and contrast checks |
| P2.9: delivery validation | Local checks delivered; participant validation open | Regression suite, offscreen/scaled/WSLg checks, screenshots, benchmark and Linux package smoke checks |

## Delivered evidence

- The suite passes **346 tests**, including the Phase 1 checks and thirteen new
  document/desktop/trace checks in `tests/test_phase2.py`.
- Reference Qt checks pass on actual WSLg Wayland and offscreen rendering at
  100%, 125% and 150% scale. A requested 1366×768 window stays 1366×768;
  the Circuit preset minimum size is approximately 791×345, depending on fonts/style.
- Checks cover keyboard property editing/undo, keyboard context targets, wire
  preview/cancel, preference restoration, accessible widget interfaces and layout
  changes that preserve document/chip state. See the
  [saved desktop results](../../docs/benchmarks/phase2-wayland.json).
- Representative text contrast checks measured 13.17:1 for primary text and
  7.71:1 for secondary text against their backgrounds. LOW wires were brightened
  from 2.29:1 to 5.29:1 against the canvas; pin states also have text/symbol cues.
  These checks do not establish comprehensive accessibility conformance.
- The reference traced MicroPython loop acknowledged pause in approximately
  13.6–70.5 ms across five samples, meeting the 100 ms reference target.
  See [benchmark results](../../docs/benchmarks/phase2-linux-wsl2.json).
- MicroPython file write → circuit Apply → interpreter reboot → file read passes.
  C source mapping is checked against a compiled ELF; missing metadata and edits
  have explicit fallback behavior. The installed Linux wheel runs C and Python
  workflows with the new GUI modules and project persistence.

Reproduce desktop checks with `tools/validate_desktop.py` and the benchmark with
`tools/benchmark.py --gui`. See [desktop usage](../../docs/desktop.md).

## Remaining checks and limits

1. No participant usability sessions have been conducted. Task completion,
   assistance and confusion still need evaluation with intended users.
2. Orca is unavailable in this environment. Qt accessibility interfaces and keyboard
   alternatives were exercised; actual screen-reader operation remains unverified.
3. Source line debugging requires mapped C metadata. Python line debugging,
   source-level Step Over, synthesized bus waveforms and live CPU save states
   remain outside this phase. The standard Properties controls provide an
   accessible alternative to the custom graphics rather than a claim that every
   graphics item has been audited with assistive technology.
4. ROM circuit application preserves programmed flash. Modified SDK flash cannot
   be restored automatically and requires an explicit export/discard workflow.

These validation gates remain open. Linux feature delivery is not a claim of
validated usability for all users, platforms or assistive technologies.

## Available baseline evidence

- The interface review and published references are recorded in [research.md](research.md).
- [Phase 1 status](../phase-1/implementation-status.md) records 333 passing tests,
  including thirteen GUI checks. Those results predate Phase 2 implementation.
- Physical Pico comparison and additional OS validation remain open in Phase 1.

## Updating this report

For each delivered increment, record the application revision or identifiable
change, checks performed, observed results and remaining limitations. Update the
matching checkboxes in [milestones.md](milestones.md). Keep implemented behavior
separate from manual/user validation still awaiting evidence.

Do not mark the phase complete solely because planning is finished or automated
checks pass; use the gates in [validation.md](validation.md).
