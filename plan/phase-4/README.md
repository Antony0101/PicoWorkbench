# Phase 4: official Pico SDK projects

Make PicoWorkbench useful for developers bringing existing Raspberry Pi Pico SDK
C projects: configure their CMake project, choose a firmware target, build its
ELF, run it against a circuit and inspect the result.

This extends the original SDK 2.2.0 single-source scratch workflow. Phase 4 adds
existing-project builds and a measured compatibility suite; it does not promise
that every SDK library, example or board works.

## Delivery order

| Milestone | Result | Acceptance gate |
| --- | --- | --- |
| 1. Official baseline | Pinned upstream examples, reproducible builds and a headless acceptance harness | Unmodified official blink and UART examples build and run through SDK shortcut and ROM startup |
| 2. Existing SDK projects | Headless build service, GUI project/target controls, one-file editing and safe ELF loading | Import a two-source CMake project, edit/build it, navigate diagnostics and debug the correct source file |
| 3. SDK setup and assistance | Shared tool discovery, setup dialog and clangd using the selected project's build metadata | A clean configured environment can build; completion/definitions/diagnostics work with real SDK and generated headers |
| 4. Compatibility and delivery | GPIO/ADC/PWM/I²C/SPI/PIO/DMA/multicore checks, necessary model fixes and public documentation | Every required catalog entry has passing real-firmware evidence, followed by desktop, regression and package checks |

See [milestones](milestones.md), [architecture](architecture.md),
[validation](validation.md) and [implementation status](implementation-status.md).

## Defaults and boundaries

- **Platform:** Linux x86-64 and WSLg; RP2040, `PICO_BOARD=pico` only.
- **SDK:** retain 2.2.0 as the acceptance baseline. Use the official
  [`pico-examples` `sdk-2.2.0` tag](https://github.com/raspberrypi/pico-examples/tree/sdk-2.2.0),
  resolving and recording its immutable commit/archive hashes before testing.
  Current upstream `master` requires a newer SDK; never combine floating example
  revisions with the pinned baseline.
- **Builds:** CMake 3.25+, ARM GCC/Newlib, Unix Makefiles, default Debug
  configuration. SDK source and compilers remain local dependencies.
- **Editing:** retain `Window.editor` and one source buffer. Imported projects
  can compile multiple files; this phase adds a target-source selector, not a
  multi-tab editor or a general filesystem/project tree.
- **Startup:** default imported projects to SDK shortcut. ROM boot is explicit
  and requires the existing prepared B2 ROM. Preserve standalone Direct/SDK/ROM
  builds and the MicroPython profile.
- **Loading:** a configure/build must never replace the running program until a
  successful, current build is explicitly loaded. Existing flash protections,
  source identity, F7/F9, worker ownership and detachable panels remain required.
- **Excluded:** USB/BOOTSEL/USB stdio, Pico W networking, RP2350/Pico 2, external
  I²C/SPI DMA, arbitrary C++ application compatibility, CMake presets, automatic
  tool installation and cycle-accurate timing. Host C++ tools such as `pioasm`
  remain legitimate build prerequisites.

Delivery evidence and current milestone states are recorded in
[implementation status](implementation-status.md). Physical Pico comparisons,
other OS validation and earlier participant/screen-reader checks remain open.
