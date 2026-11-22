# Documentation

New to RP2040 Lab? Follow [Getting started](getting-started.md), run the bundled
LED/button example, then read [Desktop workflow](desktop.md). You do not need a
physical board or cross compiler for that first run.

## User guides

| Guide | What it covers |
| --- | --- |
| [Getting started](getting-started.md) | Fresh Linux installation, first run, optional tools and WSLg troubleshooting |
| [Desktop workflow](desktop.md) | Circuit/Code/Debug layouts, detachable panels, wiring, saving, debugging and traces |
| [Code editor](editor.md) | Editing, search, Problems, shortcuts and local C/MicroPython language services |
| [Examples and CLI](examples.md) | Bundled projects, expected results, custom firmware, traces and replay |
| [Runtime tools](runtime-tools.md) | SSD1306 OLED, serial plotter, bus inspector and MicroPython device files |
| [Boot modes](boot.md) | Direct load versus SDK shortcut/ROM boot, B2 ROM preparation, UF2 and flash |
| [MicroPython](micropython.md) | UART REPL, script upload, persistent files and the supported interpreter profile |

## Hardware support

The [compatibility matrix](compatibility.md) is the reference for implemented
behavior and limits. The project models supported digital interactions with
functional timing, rather than reproducing every silicon cycle or electrical
characteristic.

| Guide | What it covers |
| --- | --- |
| [Pico SDK startup](sdk-startup.md) | SDK 2.2.0 setup, supported initialization and fixture provenance |
| [PIO](pio.md) | State machines, instruction support, inspection and timing |
| [DMA](dma.md) | Channels, pacing, completion/error behavior and inspection |
| [Multicore](multicore.md) | Shared memory, core scheduling, launch protocol and synchronization |

## Development and verification

- [Contributing](../CONTRIBUTING.md): environment setup, repository structure,
  tests and useful bug reports.
- [Engine and backend](backend.md): the C/Python boundary, thread ownership,
  virtual-time contract and dependency choices.
- [Validation](validation.md): automated checks, recorded evidence and remaining
  hardware/platform/accessibility checks.
- [Third-party notices](../THIRD_PARTY_NOTICES.md): license scope, bundled assets
  and dependency provenance.
- [Development plans](../plan/README.md): design history and milestone records.
  Some pages describe proposals or evidence for earlier revisions; they are not
  a substitute for the current user guides.
- [Phase 4 SDK project plan](../plan/phase-4/README.md): planned support for
  existing CMake projects and an official-example compatibility suite; not yet
  implemented.

## Quick terminology

| Term | Meaning here |
| --- | --- |
| Firmware | The program running inside the simulated chip |
| GPIO | A logical general-purpose input/output pin, such as GP25 |
| ELF / UF2 | Compiled program/image formats; ELF can also carry debug information, while UF2 describes flash blocks |
| ROM / boot2 | The chip's built-in startup code and the small flash-resident second-stage loader |
| SDK | The Pico C/C++ software development kit; this project's editor builds one C source at a time |
| REPL | MicroPython's interactive read–evaluate–print prompt, reached through UART in the supported profile |
| Virtual time | Time inside the simulation; playback speed changes how quickly it advances relative to wall time |
| Language server | A local tool such as clangd/Pyright supplying editor completion and diagnostics |

Commands in these guides assume you are in the repository root and have installed
the `.venv` environment. Commands under `tools/` may download dependencies or
rebuild packaged assets when explicitly invoked; the application does not install
tools or download firmware while running a project.
