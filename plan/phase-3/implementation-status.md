# Phase 3 implementation status

Phase 3 is implemented and validated on Linux/WSLg (2026-11-19).

| Milestone | Status |
| --- | --- |
| OLED | Complete: stock MicroPython driver, SDK dashboard and identical C/Python framebuffer images |
| Serial plotter | Complete: parsing/bounds, raw-REPL stdout, matching telemetry, native GUI and export |
| Bus inspector | Complete: SDK/MicroPython I2C/SPI, NACK/fault capture, filtering, export and deterministic replay |
| MicroPython files | Complete: binary/Unicode operations, cancellation/timeout cleanup, flash startup and safe editor integration |

## Evidence

- **386 tests passed in 145.68 s**, including the existing regressions, 19 new
  runtime tests and all three real clangd/Pyright checks. Command:
  `QT_QPA_PLATFORM=offscreen RP2040_TEST_LANGUAGE_SERVERS=1 .venv/bin/python -m pytest -q`.
- Native engine contract passed normally and under address/undefined-behavior
  sanitizers. The native extension builds with warnings treated as errors.
- Actual WSLg/Wayland and offscreen runtime checks passed at **1366×768** with
  font sizes **11/16/24**, workspace changes, live OLED/plotting, NACK filtering,
  file upload/listing and a visible source editor. Observed WSLg pause latency
  was **25.95 ms**; this is a local observation, not a real-time guarantee.
- General WSLg desktop regression checks passed, including keyboard editing,
  context menus, search, undo routing and widget accessibility interfaces.
- An installed wheel ran the bundled 27,600-byte C dashboard, imported Qt
  runtime views and verified driver checksum/license/provenance. MicroPython's
  complete inline example is 6,251 UTF-8 bytes, within the upload limit.
- [Saved benchmark evidence](../../docs/benchmarks/phase3-runtime-linux.json)
  records the final implementation digest shared by both runtime desktop checks.
  Screenshots and ephemeral settings remain under ignored `build/` review folders.

See [validation instructions](validation.md) and the [runtime tools guide](../../docs/runtime-tools.md).

Physical Pico comparison, additional OS testing and earlier accessibility/user
studies remain external checks; local implementation does not close those gates.
