# Contributing to RP2040 Lab

Start with the [README](README.md) and [documentation index](docs/README.md).
The project is experimental; the [compatibility matrix](docs/compatibility.md)
defines the supported behavior. A reproducible failing example, clearer
documentation or a focused implementation improvement is useful.

## Development setup

Use Linux x86-64. Follow [Getting started](docs/getting-started.md) for the host
compiler, Python headers and display packages, then install development extras:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[gui,dev]'
```

The [tested Python 3.12 dependency snapshot](requirements-dev.lock) is available
for reproducing the recorded environment. It pins versions; it is not a
cross-platform lockfile and contains no package hashes:

```sh
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation -e '.[gui,dev]'
```

For compiler tests, install ARM GCC and Newlib. SDK checks require the
[SDK 2.2.0 setup](docs/sdk-startup.md#install-the-build-tools). ROM/MicroPython
checks require the [prepared B2 ROM](docs/boot.md#prepare-the-rom-once). Real editor
assistance checks also need [clangd, Pyright and the pinned stubs](docs/editor.md).

## Run checks

```sh
# Python, native-engine, firmware and offscreen GUI tests
.venv/bin/python -m pytest -q

# Include the real language-server acceptance checks
RP2040_TEST_LANGUAGE_SERVERS=1 .venv/bin/python -m pytest -q

# Standalone C library and C API contract
make native
```

Tests skip unavailable optional prerequisites, so a green partial run does not
establish the full acceptance result. Check the test summary. GUI tests select
Qt's offscreen platform; interactive WSLg checks still need a working display.
See [Validation](docs/validation.md) for detailed evidence and outstanding checks.

After changing C engine sources or headers, rebuild before running Python tests:

```sh
.venv/bin/python setup.py build_ext --inplace
```

Run tests relevant to a change, then the full suite for changes spanning the
engine/controller/GUI boundary. Include regressions for behavioral fixes. Docs
changes should keep local links, paths, commands and feature claims accurate.

## Find the code

| Location | Responsibility |
| --- | --- |
| `engine/` | Native CPU, memory, peripherals, scheduling and public C API |
| `bindings/` | CFFI extension build |
| `python/rp2040_sim/` | Engine wrapper, projects, device models, REPL and CLI |
| `python/rp2040_sim/gui/` | Qt widgets, worker ownership and editor assistance |
| `firmware/`, `examples/` | Editable workloads, board profiles and circuit JSON |
| `python/rp2040_sim/assets/` | Packaged firmware, source copies, provenance and notices |
| `tests/`, `tools/` | Regression checks, asset preparation and validation helpers |
| `docs/`, `plan/` | Current guides and historical design/milestone evidence |

The [backend guide](docs/backend.md) explains ownership and timing. Keep the
simulation and capture models on their owner thread; GUI views receive copied
state. Preserve source-to-firmware identity and explicit unsupported-mode faults.

## Rebuild bundled assets deliberately

`make demo`, `make sdk-demo` and `make micropython-demo` regenerate packaged
assets and provenance. The MicroPython helper downloads pinned sources. These
commands are not required for everyday source edits or running bundled examples.
When changing an asset, include its editable source, matching hashes/provenance
and applicable notices. Never replace the tested UART firmware with an arbitrary
stock image, or commit a locally prepared boot ROM.

## Report a bug or propose a change

SDK project work also requires the pinned SDK/examples, a complete ARM C++/Newlib
toolchain, host C++ compiler and B2 ROM. Prepare dependencies explicitly, then run
the `RP2040_TEST_SDK_PROJECTS=1` and `RP2040_TEST_LANGUAGE_SERVERS=1` gates described
in [Pico SDK projects](docs/sdk-projects.md). Missing prerequisites fail acceptance;
ordinary-test skips do not establish SDK compatibility. Never modify stock
upstream sources to make a required runtime gate pass.

In the repository's issue tracker, include your OS/Python version, commit,
startup mode, smallest source/project that reproduces the issue, steps, expected
behavior and actual output. For UI issues, include workspace, font/scaling and
whether the panel is floating. Remove personal paths, device files and secrets
from attached projects or logs.

Keep pull requests focused. Explain the resulting behavior, relevant limits and
which checks ran, including skipped prerequisites. Update the user-facing guide
when controls, setup or compatibility change. New simulator faults should identify
the unsupported operation rather than silently accepting it.

Original project code and documentation use `GPL-2.0-or-later`; contributions to
those files use the same terms. Preserve third-party notices and provenance. See
[LICENSE](LICENSE) and [Third-party notices](THIRD_PARTY_NOTICES.md).
