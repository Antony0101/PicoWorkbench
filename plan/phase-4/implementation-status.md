# Phase 4 implementation status

**Status: planned; implementation has not started.** Created 2026-11-22.

| Milestone | State | Evidence required before completion |
| --- | --- | --- |
| 1. Official baseline | Not started | Pinned catalog; stock blink/UART build and verified behavior in SDK/ROM modes |
| 2. Existing SDK projects | Not started | File API discovery, async project build, persistence, one-file editing and correct multi-file source/debug identity |
| 3. SDK setup and assistance | Not started | Tool checks/settings, usable setup flow and real clangd with target/generated headers |
| 4. Compatibility and delivery | Not started | Complete required catalog, model regressions, Linux/WSLg, installed package, native and full-suite gates |

## Existing baseline, not Phase 4 delivery

The application already has a fixed SDK 2.2.0 single-source build profile,
SDK/ROM startup, supported peripherals, runtime tools and detachable panels.
Those capabilities establish the starting point; they do not demonstrate
unmodified upstream project import or broad SDK compatibility.

The previous recorded full suite passed 411 tests. That result predates Phase 4
implementation and must not be presented as evidence for these milestones.

## Recording progress

For each gate add the implementation commit/digest, upstream commit and source
hashes, tool versions, configuration, commands, test summary and observed runtime
behavior. Link the generated compatibility catalog and review artifacts. Identify
stock examples separately from project-authored SDK fixtures. A required failing
or skipped workload keeps its gate open; do not relabel it as complete because
the compile step succeeded.

See [validation](validation.md) for acceptance criteria. Physical Pico comparison,
additional OS testing, real multi-monitor placement and earlier participant/
screen-reader checks remain external work and are not closed by this plan.
