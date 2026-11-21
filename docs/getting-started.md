# Getting started

This guide takes a fresh Linux checkout to a running LED/button circuit. That
default example runs without a physical Pico, ARM compiler, Pico SDK or ROM.
Add those tools only when you need to edit C or run ROM/MicroPython projects.

## Requirements

The tested platform is Linux x86-64 with Python 3.12, including WSL2 with WSLg.
The package requires Python 3.10 or newer. Native Windows/macOS builds and other
CPU architectures have not been validated.

| Requirement | When you need it |
| --- | --- |
| Python, `venv`, a host C compiler and matching Python headers | Every installation; the engine is compiled locally |
| PySide6 and an X11/Wayland desktop display | GUI use; installed by the `gui` extra |
| ARM GCC | Building edited C firmware and C header discovery in the editor |
| Pico SDK 2.2.0, CMake and ARM Newlib | Building SDK/ROM C sources |
| Official B2 ROM, prepared locally | ROM boot and MicroPython, including their bundled examples |
| clangd or Pyright/stubs | Optional editor assistance; basic editing works without them |

## Install from source

Clone the repository, or download and extract its source archive. Open a terminal
in the directory containing `pyproject.toml`.

On Ubuntu/Debian:

```sh
sudo apt update
sudo apt install build-essential python3-dev python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[gui]'
```

These commands download Python dependencies and build the C extension. Use the
development package matching your interpreter: for example, a separately
installed Python 3.12 needs its corresponding development headers. The `-e`
installation runs the Python code from this checkout. Changes to the C engine
still require rebuilding the extension.

For a headless installation, replace `'.[gui]'` with `.`. To run development
checks, follow [Contributing](../CONTRIBUTING.md).

## First run

```sh
.venv/bin/rp2040-sim gui
```

Press **F5 / Run**. GPIO25's LED should blink; holding the button makes it blink
faster. Choose **Debug** and open **Logic analyzer** below the editor to inspect
the signal. **F6** pauses; **Ctrl+R** resets the loaded program. **F10** executes
one machine instruction, not one line of source.

Use the workspace tabs to change the layout. **↗** opens a panel in a separate
window without starting another chip. **View → Panels** finds detached tools;
**View → Reset layout** returns them all to the main window.

Save circuit/source work with **Ctrl+S**. **Ctrl+Shift+S** writes a separate host
source file. Runtime flash/files require a separate export. The
[desktop guide](desktop.md) explains circuit changes, save prompts and debugging.

You can also check the engine without a display:

```sh
.venv/bin/rp2040-sim run --duration-ms 1000
```

Expect a startup UART message and an execution summary. No toolchain or network
access is needed for this bundled run after installation.

## Build your own C code

For the freestanding examples, install ARM GCC:

```sh
sudo apt install gcc-arm-none-eabi
.venv/bin/rp2040-sim build firmware/fixtures/blink.c -o build/custom.elf
.venv/bin/rp2040-sim run build/custom.elf --duration-ms 1000
```

In the GUI, choose **Startup → Direct load**, edit the existing source, press
**F7 / Build + Load**, then **F5 / Run**. Direct sources supply vectors and a
`reset_handler()`. `ARM_CC` can select an ARM GCC executable outside PATH:

```sh
export ARM_CC=/path/to/arm-none-eabi-gcc
```

SDK sources instead provide `int main(void)` and need the
[SDK 2.2.0 toolchain setup](sdk-startup.md#install-the-build-tools). Use
**SDK shortcut** or **ROM boot** for them. Selecting a mode does not convert
freestanding source into SDK source. See [Boot modes](boot.md).

## Run MicroPython or ROM boot

Prepare the hash-checked official B2 ROM once, then open the bundled interpreter:

```sh
.venv/bin/python tools/prepare_bootrom.py
.venv/bin/rp2040-sim gui --project examples/micropython.json
```

The preparation command downloads a pinned upstream ROM release into `.tools/`;
it is not included in this repository. For offline preparation or an existing
binary, see the [boot guide](boot.md#prepare-the-rom-once).

Press **F7 / Run Python** to upload and execute the editor source. Use
**Interrupt Python** for a running loop. This profile uses UART and has USB
disabled. A cross compiler is not needed to run it. Read
[MicroPython](micropython.md) for its tested APIs and filesystem behavior.

For completion and documentation, follow the separate
[code assistance setup](editor.md#optional-local-code-assistance).

## Display troubleshooting on Linux and WSL

The GUI needs a working desktop session. On WSL, use
[WSLg](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gui-apps).
The application looks for WSLg's Wayland socket when the normal runtime directory
does not contain it. An explicit `QT_QPA_PLATFORM` setting overrides that choice.

If Qt reports missing shared libraries on a minimal Ubuntu/Debian install:

```sh
sudo apt install libegl1 libgl1 libopengl0 libdbus-1-3 libfontconfig1 \
    libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
    libxcb-util1 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-shape0 libxcb-xkb1
```

These are runtime libraries; reinstalling the Python package does not supply
them. See the [Qt Linux requirements](https://doc.qt.io/qt-6/linux-requirements.html).

To select WSLg's Wayland socket explicitly:

```sh
WAYLAND_DISPLAY=/mnt/wslg/runtime-dir/wayland-0 QT_QPA_PLATFORM=wayland \
    .venv/bin/rp2040-sim gui
```

To use a working X11 display instead:

```sh
QT_QPA_PLATFORM=xcb .venv/bin/rp2040-sim gui
```

`QT_QPA_PLATFORM=offscreen` is useful for automated screenshots and tests. It does
not open an interactive desktop window.

## Other common setup errors

| Symptom | What to check |
| --- | --- |
| `Python.h` is missing during installation | Install development headers matching the Python used to create `.venv` |
| Native extension cannot be imported after editing C | Run `.venv/bin/python setup.py build_ext --inplace` from the repository root |
| ARM compiler not found when pressing F7 | Install `gcc-arm-none-eabi`, or set `ARM_CC`; bundled demos can still run |
| SDK path or Newlib error | Follow the pinned [SDK build setup](sdk-startup.md#install-the-build-tools) |
| B2 ROM missing or hash mismatch | Run the ROM helper or select the official prepared binary; arbitrary ROM versions are rejected |
| Completion is unavailable | Configure the relevant local server/stubs through **Code assistance…**; see [editor setup](editor.md) |
| A detached panel is hard to find | Use **View → Panels → Show / Focus** or **View → Reset layout** |
