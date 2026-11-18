# Phase 2 research and interface findings

## Method and baseline

Review of the existing GUI source and rendered C, peripheral workbench and
MicroPython interfaces, compared with primary product documentation and UX
guidance. This is an interface evaluation; no participant usability sessions have
been completed. The recommendations are design proposals, not measured user outcomes.

The current dark theme, circuit/inspector arrangement, working examples and
right-click menus provide a useful foundation. Keep those capabilities while
improving readability, recovery and the connection between editing and execution.

## Findings and proposed responses

| Finding | Evidence in the reviewed application | Planned response |
| --- | --- | --- |
| Laptop layout exceeds available height | Requesting 1366×768 produced a 1366×831 window in the Linux/offscreen review; inspector tabs and editor space were constrained | P2.1: flexible/collapsible panels, adaptive controls and saved layout |
| Accidental edits are difficult to recover | Circuit operations lack undo history; close/project replacement lack unsaved-work handling | P2.2: document state, undo/redo and safe file lifecycle |
| Edited and running programs are hard to distinguish | Editor changes have no loaded-program comparison; circuit edits currently reload the simulation | P2.3: program identity, pending circuit changes, explicit apply/reset feedback |
| Advanced controls compete with common tasks | Clock/instruction limits are always visible beside playback; the File menu contains many starting points | P2.4: workspace presets, advanced controls and example/template chooser |
| Wiring and properties need stronger feedback | First-port highlighting exists, but no cursor-following wire preview or consolidated properties view | P2.5: preview, target feedback, net inspection and properties panel |
| Source and debugger views are weakly connected | Breakpoints are entered through an address/function field; editor has no line-number/breakpoint gutter | P2.6: mapped source locations and breakpoint UI with explicit fallback |
| Trace inspection is limited | Channels are fixed to GP25/GP14/GP0; no timing cursors or measurements | P2.7: selectable channels, virtual-time cursors and truthful measurements |
| Custom canvas needs accessibility work | Parts/ports are custom graphics items; much pin/net state is conveyed by color and small labels | P2.8: keyboard access, accessibility exposure and non-color state cues |

## Research supporting the direction

- [Nielsen Norman Group: usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)
  supports visible status, recognizable actions, error prevention and recovery.
  Apply these to loaded code, reset consequences and undo/save behavior.
- [Nielsen Norman Group: progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/)
  describes presenting common actions first and exposing specialized controls on
  request. Apply this to functional clock settings and instruction limits.
- [Wokwi: diagram editor](https://docs.wokwi.com/guides/diagram-editor)
  documents pin-to-pin wiring, cancellation, grid snapping, duplication and
  keyboard editing/undo. These are useful interaction references for the canvas.
- [Microsoft: menus and context menus](https://learn.microsoft.com/en-us/windows/apps/develop/ui/controls/menus-and-context-menus)
  distinguishes contextual secondary commands from frequent commands that merit
  direct controls. Retain visible Add/Run/Inspect entry points beside right-click access.
- [VS Code: debugger interface](https://code.visualstudio.com/docs/debugtest/debugging)
  connects execution location, editor breakpoints, debug controls and inspection.
  Adapt those patterns only where this simulator has the necessary metadata.
- [Saleae: measurements and timing markers](https://www.saleae.com/support/logic-software/viewing-and-analyzing-data/measurements-timing-markers)
  documents waveform region measurements and single/paired timing markers.
  Use these as references for cursor interaction, not as evidence of simulator fidelity.
- [W3C: use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)
  and [contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
  explain why state needs supporting text/shape cues and readable contrast.
  Apply the design principles to this desktop app without claiming WCAG conformance.
- [Qt: QWidget accessibility](https://doc.qt.io/qt-6/accessible-qwidget.html)
  explains existing standard-widget support and the interfaces/events needed for
  custom controls. Verify the custom scene separately from ordinary widgets.

## First delivery slice

Implement P2.1–P2.3 first: fit the laptop screen, protect work, and make execution
state trustworthy. Then evaluate the remaining changes through the
[task-based validation plan](validation.md). Do not use a more elaborate theme
or additional menus as the sole evidence of better usability.
