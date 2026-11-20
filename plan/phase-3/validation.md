# Phase 3 validation

## Automated gates

- `tests/test_runtime_tools.py` executes the pinned MicroPython 1.26.1 firmware
  and real Pico SDK C dashboard. It checks the stock upstream OLED driver,
  exact matching C/Python display images and telemetry, SDK aliases, SPI frames,
  I2C NACKs, fault records, plot framing/bounds and deterministic replay.
- File gates use actual interpreter/UART/flash operations: binary and Unicode
  round trips, mkdir/rename/delete, staged-upload/download cancellation, owned
  helper timeout recovery, exporting/reloading flash and autorunning installed
  `/lib/ssd1306.py` plus `/main.py`.
- `tests/test_runtime_gui.py` checks laptop layout, component properties, bus
  filtering, idle-only access, playback/source identity, management-output
  exclusion and safe asynchronous opening into the single editor.
- Run all existing regressions, including opt-in real language servers:

  ```sh
  QT_QPA_PLATFORM=offscreen RP2040_TEST_LANGUAGE_SERVERS=1 .venv/bin/python -m pytest -q
  ```

- Build the native contract with `SIM_SANITIZE=ON` and run CTest with
  `ASAN_OPTIONS=detect_leaks=0` (Unicorn is a prebuilt JIT library). Both the normal
  and address/undefined-behavior sanitizer contract runs must pass.

## Desktop and package gates

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/validate_desktop.py --output build/phase3-review
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/validate_runtime_desktop.py
```

Repeat both against the local WSLg compositor, using `QT_QPA_PLATFORM=wayland`
and its runtime directory when needed. Runtime checks exercise the live OLED,
plotting, bus NACK filter, idle REPL file upload/listing, paused scene refresh,
workspace switching and editor font sizes 11/16/24 at 1366×768. General desktop
checks retain keyboard editing, undo routing, search, context menus and standard
widget accessibility interfaces. These checks do not replace a screen-reader
audit or participant study.

Build a wheel, install it into an isolated directory, and verify imports come
from that directory. Check bundled driver checksum/license/provenance, load and
run the SDK dashboard without compiling firmware, and construct native Qt views.

Saved local desktop results are linked from [implementation status](implementation-status.md).
Screenshots and ephemeral settings remain in ignored `build/` directories.

## Limits retained

Linux/WSLg is the available test environment; physical Pico comparison and other
operating systems remain external checks. Functional timing is retained. There
is no SPI OLED, hardware scrolling, external-bus DMA, recursive filesystem UI,
USB access or automatic indexing of device modules. Recovery from an interpreter
or engine failure can require reconnecting and removing the reported staging
file manually; cancellation during normal operation cleans it automatically.
