# RP2040 simulator development phases

| Phase | Scope | Status |
| --- | --- | --- |
| [Phase 1](phase-1/README.md) | C/Python simulator, desktop, peripherals, PIO/DMA/two cores, SDK/ROM boot and MicroPython | Linux implementation delivered for documented workloads; physical Pico and additional OS validation remain open |
| [Phase 2](phase-2/README.md) | Desktop UI/UX: laptop layout, work protection, execution feedback, circuit editing, debugging, traces and accessibility | Linux implementation delivered; participant and screen-reader validation open |
| [Phase 3](phase-3/README.md) | SSD1306 OLED, UART plotting, bus inspection, MicroPython files and matching dashboards | Linux/WSLg delivered; 386 tests, real firmware and installed-wheel/desktop checks passed |

The original plan and its evidence now live in `phase-1/`. Existing right-click
menus are part of that delivered baseline. Their addition does not complete the
broader Phase 2 improvements.

Start new UI/UX work with the [Phase 2 milestones](phase-2/milestones.md).
The subsequent [single-file editor upgrade](editor-upgrade.md) records its
implementation and acceptance checks separately from the original phase evidence.
Check [Phase 1 status](phase-1/implementation-status.md) for simulator compatibility
and outstanding external checks, and [Phase 2 status](phase-2/implementation-status.md)
for UX delivery. Moving the plan does not close unfinished validation gates.

See the [project README](../README.md) for installation and running instructions.
