# Phase 4 implementation status

**Complete for the Linux/WSLg delivery scope — 2026-11-24.** Physical hardware
and previously outstanding external checks remain open below.

| Milestone | State | Evidence |
| --- | --- | --- |
| 1. Official baseline | Complete | Unchanged pinned blink/UART build from the upstream root; GPIO/UART, reset and replay pass in both SDK shortcut and B2 ROM modes |
| 2. Existing SDK projects | Complete | Shared File API builder and CLI; two targets, project library, same-basename sources and generated headers; GUI F7/F9, dirty buffers, external edits, recovery, diagnostics, cancellation and prior-image preservation |
| 3. SDK setup and assistance | Complete | Explicit/local tool discovery; asynchronous setup and owned SDK install/remove; actual target compilation context; real clangd completion/hover/definitions/diagnostics for project, SDK and generated PIO headers; standalone clangd/Pyright retained |
| 4. Compatibility and delivery | Complete | All nine stock targets / eleven startup combinations pass; SPI/dashboard fixtures; native and sanitizer checks; installed-wheel CLI/GUI; Linux/WSLg laptop, fonts and scaling checks |

The final regression run passed **464 tests, zero skipped**, in 680.54 seconds with
`RP2040_TEST_SDK_PROJECTS=1` and `RP2040_TEST_LANGUAGE_SERVERS=1`. Additional runtime
checks verify PWM period/duty and blink/PIO intervals. The machine-readable
[evidence summary](../../docs/benchmarks/phase4-sdk-linux.json) records implementation,
upstream, tool and ELF identities, runtime observations and desktop/package results.
See [delivery evidence](evidence.md) for commands and interpretation, and the
[user guide](../../docs/sdk-projects.md) for setup and controls.

The stock sources are `pico-examples` `sdk-2.2.0`, commit
`4c3a3dc0196dd426fddd709616d0da984e027bab`; the packaged
[catalog](../../python/rp2040_sim/assets/sdk-examples.json) retains archive, complete
source-tree and workload hashes. Compile success alone does not set tested status.
The SPI/dashboard programs are separately labelled project-authored fixtures.

System compiler installation was deferred at the user's request. Acceptance used
workspace-local compiler files and an explicit host C++ path, without installing
system packages. SDK/ARM/host compilers, examples and ROM are not shipped in the
wheel. The explicit installer manages SDK source only; the optional terminal
dependency helper remains available for later use.

## Remaining external checks

- Physical Pico comparison and real multi-monitor placement.
- Native desktop testing on other operating systems.
- Earlier participant usability, screen-reader and accessibility studies.

These checks are not completed by automated geometry, keyboard or WSLg tests.
USB/networking/RP2350, external-bus DMA, general C++ compatibility and a multi-tab
IDE remain excluded features, rather than uncompleted Phase 4 milestones.
