# RP2040 simulator development phases

| Phase | Scope | Status |
| --- | --- | --- |
| [Phase 1](phase-1/README.md) | C/Python simulator, desktop, peripherals, PIO/DMA/two cores, SDK/ROM boot and MicroPython | Linux implementation delivered for documented workloads; physical Pico and additional OS validation remain open |
| [Phase 2](phase-2/README.md) | Desktop UI/UX: laptop layout, work protection, execution feedback, circuit editing, debugging, traces and accessibility | Linux implementation delivered; participant and screen-reader validation open |
| [Phase 3](phase-3/README.md) | SSD1306 OLED, UART plotting, bus inspection, MicroPython files and matching dashboards | Linux/WSLg delivered; 386 tests, real firmware and installed-wheel/desktop checks passed |
| [Phase 4](phase-4/README.md) | Official Pico SDK example validation, existing CMake projects, SDK setup and measured compatibility expansion | Complete for Linux/WSLg; physical/external checks remain open |

These pages preserve design history and evidence for the revisions that delivered
each milestone. Use the [documentation index](../docs/README.md) and
[compatibility matrix](../docs/compatibility.md) for current user-facing behavior.

The subsequent [single-file editor upgrade](editor-upgrade.md) records its
implementation and acceptance checks separately from the original phase evidence.
Check [Phase 1 status](phase-1/implementation-status.md) for simulator compatibility
and outstanding external checks, and [Phase 2 status](phase-2/implementation-status.md)
for UX delivery. Moving the plan does not close unfinished validation gates.

See the [project README](../README.md) for installation and running instructions.
