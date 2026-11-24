# Phase 4 delivery evidence

Recorded 2026-11-24 on Linux x86-64 / WSL2 and WSLg. The compact
[JSON report](../../docs/benchmarks/phase4-sdk-linux.json) contains exact source,
tool and firmware identities. Large logs, screenshots, settings and package
artifacts are kept under ignored `build/phase4-review/`.

## Firmware gates

Sources are unchanged `pico-examples` `sdk-2.2.0`, commit
`4c3a3dc0196dd426fddd709616d0da984e027bab`, configured from the upstream CMake root.
SDK 2.2.0, board `pico`, RP2040, Debug, Unix Makefiles and four build jobs are used.
The complete source tree and selected workload hashes match the catalog.

Blink and UART pass both SDK shortcut and real core0 B2 ROM/boot2 startup. GPIO
IRQ, ADC, PWM, I²C scan, PIO, DMA and multicore pass ROM startup. Every case is
reset and repeated with instruction budgets 257 and 4096, then recorded-input
replay. The [compatibility table](../../docs/compatibility.md#measured-official-sdk-examples)
states the observations and startup limits.

Returning SDK programs enter `_exit`'s breakpoint loop. Tests accept only that
audited PC range. The multicore check explicitly resumes until core1 prints its
FIFO result. PWM receives eight further periods to check a 4 µs period, with
1 µs and 3 µs high times on A/B at the test's functional 1 MHz clock. GPIO blink and PIO delays
allow up to 1 ms of SDK instruction overhead. Normal debugger and replay behavior
still stops on breakpoints; no faults or SDK calls are bypassed.

Stock SDK runtime initialization exposed a missing shortcut data lookup. SF/SD
initialization descriptors now support copying the SDK tables; arithmetic calls
through them still fault explicitly and require ROM startup. Focused Python and
native tests cover the descriptors, lookup/copy and unsupported calls. ABI 1 and
the 24 public native functions are preserved.

The separate multi-source fixture builds two targets, a project library,
`left/value.c` and `right/value.c`, a configured header and generated PIO header.
Tests verify full-path DWARF mapping, target flags, Unicode/spaced paths,
header invalidation and a real change-and-restore during a build. The SPI byte
loopback and existing dashboard fixtures retain their own provenance.

## Regression and editor gates

With prepared tools, examples and ROM:

```sh
QT_QPA_PLATFORM=offscreen RP2040_TEST_SDK_PROJECTS=1 \
    RP2040_TEST_LANGUAGE_SERVERS=1 .venv/bin/python -m pytest -q
.venv/bin/python tools/validate_sdk_projects.py
make native
```

The final full suite passed **464 tests with no skips**. The required real-tool
tests fail on missing prerequisites. CMake process tests cover cache ownership,
stale File API replies, timeouts, cancellation, child processes and Ctrl+C cleanup.
GUI checks cover imported configure/build/load, F7 with editing during a build,
F9 at a mapped instruction, reset, another file's compiler error, cancellation,
recovery conflicts and floating editor/output panels. Real clangd checks use the
selected target, ARM/Pico headers and generated PIO declarations; standalone
clangd and MicroPython/Pyright checks also pass.

The native C contract passes (1/1). Address/undefined-behavior sanitizer builds
pass the contract and unchanged official blink/UART through SDK startup.
LeakSanitizer cannot operate under the sandbox's ptrace environment, so these
runs use `ASAN_OPTIONS=detect_leaks=0`; leak checking is not claimed.

System package installation was deferred. Tests use the existing local SDK/ARM
toolchain, actual ARM C++ headers/libraries extracted into ignored tools, and a
workspace-local host GCC 13.3 C++ compiler selected through `CXX`. Package versions
and SHA256 values are retained in the JSON report. They are test dependencies,
not installed system packages or bundled simulator tools.

## Desktop and packaging

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/validate_sdk_desktop.py
# Repeat against the local WSLg Wayland display, and with:
# QT_SCALE_FACTOR=1.25 / QT_SCALE_FACTOR=1.5
```

The styled GUI passes at 1366×768 on WSLg/Wayland and offscreen at 100%, 125% and
150% scaling. Checks configure/build a real project, receive live UART in a
floating panel, switch Circuit/Code/Debug with a floating editor at 11/16/24-point
text, and dock back with source/manifest identity intact. Keyboard/context-menu,
two-session, undo, layout recovery and shutdown regressions remain in the suite.

A source snapshot excluding `.tools`, `.venv` and build caches produces a Linux
wheel. An isolated package directory verifies installed imports, adjacent Unicorn
library discovery, licenses/notices/catalog, JSON target listing, explicit-tool
project build, CLI execution and GUI configure/F7/load/run with floating panels.
The managed SDK install/status/remove commands also pass against a verified local
archive in isolated per-user data. External SDK removal is refused. No dependency
download occurs during ordinary startup or tests.

Physical Pico comparison, other OS desktops, real multi-monitor placement and
earlier participant/screen-reader studies remain open. See
[implementation status](implementation-status.md).
