# Phase 4 validation

## Real-firmware catalog

Before the first run, resolve `pico-examples` tag `sdk-2.2.0` to a full commit
and verify/archive its source hash. Retain SDK 2.2.0 and record ARM GCC/Newlib,
CMake, host tools, configure definitions, firmware hashes and startup mode.
Use the upstream root build files unchanged. Source paths below identify the
required stock workloads; target discovery must confirm their executable names.

| Required workload | Firmware / source category | Observable gate |
| --- | --- | --- |
| Blink | `blink` | GP25 high/low transitions and repeated/reset runs; test SDK shortcut and ROM boot |
| UART hello | `hello_world/serial` | Expected UART greeting, correct controller, no required USB; both startup modes |
| GPIO IRQ | `gpio/hello_gpio_irq` | Timestamped external input drives the example's configured IRQ callbacks without spurious repeats |
| ADC | `adc/hello_adc` | Drive documented ADC channels and supply required UART console input; verify known conversion values |
| PWM | `pwm/hello_pwm` | Check configured slice/divider/TOP/compare and observable pin behavior within the functional model |
| I²C scan | `i2c/bus_scan` | Known wired register-sensor address ACKs; missing addresses NACK; inspector records agree |
| PIO hello | `pio/hello_pio` | Build upstream `.pio` through the host generator; verify expected state-machine/GPIO behavior |
| DMA copy | `dma/hello_dma` | Correct memory copy and completion observation without external-bus DMA |
| Multicore hello | `multicore/hello_multicore` | Both core contexts execute, UART/FIFO/shared-memory behavior is correct and replay is deterministic |
| SPI API fixture | Project-authored C fixture using SDK `hardware_spi` | 8-bit master transfer to the existing loopback device; exact TX/RX and inspector/fault behavior |
| Existing dashboard | Bundled SDK temperature dashboard | ADC, OLED, plot telemetry, I²C NACK and SPI inspection remain correct |

The first nine are stock-example requirements; SPI and dashboard are labeled
SDK API fixtures. Do not claim compatibility with an unrelated stock sensor,
display, SPI slave or external-bus DMA example from passing those fixtures.
The additional stock entries after blink/UART require ROM-boot results;
SDK-shortcut compatibility is a separate, explicitly recorded result.
The harness may supply circuits, inputs, run limits and observer assertions;
it may not patch upstream source or replace SDK functions with host shims.

For every entry record initial circuit, input sequence, virtual-time/instruction
limits, expected observations and pass/fail reason. Compare external behavior,
not exact physical timing or incidental compiler-dependent instruction counts.
Repeat reset, different execution budgets and recorded-input replay; compare
relevant GPIO, OLED, plot and bus observations where present. Additional model
tests must independently establish fixes exposed by the firmware.

## Build and identity tests

- Configure/build a real two-source project with a project library, header and
  generated header. Exercise two executable targets and a nested CMake root.
- Test File API reply generations, no ELF/multiple artifacts, unsuccessful
  configure, stale replies, renamed/deleted targets and a missing compiler/SDK.
- Test roots/output paths with spaces and Unicode, read-only sources, invalid
  definitions, reserved overrides, unsupported boards and user-owned build trees.
- Cancel configure, compilation and `pioasm`/child processes. Confirm bounded
  termination, cache ownership and retention of the prior loaded program.
- Verify project Save As rebasing, old schema-1 documents, SDK-binding round trips,
  missing roots/artifacts, recovery buffers and no fallback to bundled firmware.
- Edit the active buffer while building; change another source/header on disk;
  change and restore a file during a build; switch target/project/configuration.
  Reject stale callbacks/unverified manifests without discarding newer edits.
- Give two files the same basename. Check exact file/line diagnostics, F9,
  execution highlights, stale-input invalidation, source changes after load and
  address-only fallback. Reset/apply must retain the correct loaded manifest.
- Compile success followed by an invalid ELF or unsupported runtime must not
  become a passing compatibility entry. A failed load retains the previous image.

## Setup and editor tests

- Test deterministic tool-selection precedence with explicit settings/options,
  environment, local tools and PATH; version mismatches, probe timeouts and missing
  Newlib/host tools must produce actionable errors without blocking the GUI.
- Run real clangd against project, SDK and generated PIO headers. Check target
  changes, duplicate compile contexts, completion, hover, definitions and analysis
  diagnostics. Reuse generated metadata; do not launch a competing scratch build.
- Exercise source save/conflict handling, Save/Discard/Cancel on navigation,
  atomic saves, embedded recovery, Ctrl+S/Ctrl+Shift+S and F7 behavior in both
  standalone and imported modes. Preserve standalone exact-buffer builds.
- Keep the same editor/document when floating. Verify source/Problems/log reveal
  routes, undo, local editor shortcuts, F7/F9, modal behavior, workspace changes,
  and code assistance with detached panels. Repeat MicroPython/Pyright checks.

## Delivery checks and evidence

Add a dedicated opt-in `RP2040_TEST_SDK_PROJECTS=1` gate. Ordinary tests may skip
unprepared external tools; this acceptance gate must fail clearly if SDK,
compiler, verified examples, ROM, Qt or required language services are unavailable.
No test downloads dependencies. Document the explicit preparation command first.
Use this final invocation once the new tests exist:

```sh
QT_QPA_PLATFORM=offscreen RP2040_TEST_SDK_PROJECTS=1 \
    RP2040_TEST_LANGUAGE_SERVERS=1 .venv/bin/python -m pytest -q
make native
```

Run native sanitizer checks after changes to native models. Record results
against the actual implementation digest, not this planning revision. Do not
reuse the older 411-test result as Phase 4 acceptance evidence.

Validate at 1366×768 on Linux/WSLg with keyboard access, context menus, editor
fonts 11/16/24, 125%/150% scaling and floating panels. Check cancel/close during
jobs and that source remains visible while viewing build errors. This does not
replace participant or screen-reader studies.

Build/install a wheel from a source snapshot without `.tools` or a prepared SDK
inside the package. With explicitly configured external SDK/compiler/examples,
run `build-project`, load the ELF and perform the GUI import/build workflow.
Verify imports originate in the installed package, license/notice inclusion and
that local SDKs, toolchains, build caches and ROM bytes are excluded.

Save a compact catalog/result summary under `docs/benchmarks/phase4-sdk-linux.json`
when generated, and link it from [implementation status](implementation-status.md).
Keep large logs/screenshots/temporary settings under ignored `build/phase4-review/`.
The delivered [JSON summary](../../docs/benchmarks/phase4-sdk-linux.json) and
[evidence notes](evidence.md) record the completed Linux/WSLg gates.

Physical Pico comparison, other operating systems, real multi-monitor placement
and earlier accessibility/user studies remain open external checks.
