# Single-file C and MicroPython editor

The accepted extension to the desktop plan keeps one editable source document,
uses the central area in Code mode, and provides smart assistance through locally
installed tools with guided setup. Implementation and Linux acceptance validation
are complete.

## Implementation

- [x] Keep `Window.editor` as the native `SourceEditor`; wrap it in an editor pane.
- [x] Add filename/source-save state, cursor position, indentation/pair preferences
  and language-service status, with separate collapsible output.
- [x] Add native editing commands, multiline highlighting, bracket matching,
  search/replace, line navigation, menu/context actions and keyboard shortcuts.
- [x] Add asynchronous LSP with UTF-16 positions, revision/caret safeguards,
  completion transactions, hover, signature help and definition previews.
- [x] Configure clangd from shared ARM compiler flags or actual SDK metadata;
  prepare SDK headers asynchronously without changing chip state.
- [x] Configure Pyright with MicroPython 1.26 Pico and standard-library stubs,
  isolated from the simulator's host Python project.
- [x] Add path detection, guided setup/restart and missing-tool fallback.
- [x] Present compiler and live problems separately, mark old build results and
  preserve exact-source identity for firmware/debug annotations.
- [x] Preserve project schema, save/upload/build semantics and per-user settings.

## Validation

`tests/test_editor.py` exercises editing, search, Unicode positions, completion
transactions, stale revisions/caret movement, delayed/fragmented LSP responses,
timeouts/crashes/shutdown, source previews, preferences and editing during builds.
Its opt-in real-tool cases exercise direct C, Pico SDK C and MicroPython.
`tools/validate_desktop.py` includes the full Code view, keyboard search,
independent output and standard-widget accessibility interfaces.

- [x] Full suite: **367 passed in 45.45 seconds**, with real language-server checks enabled.
- [x] Real clangd 23.1.0: direct ARM C and SDK 2.2.0 completion and diagnostics.
- [x] Real Pyright 1.1.414: `Pin`, `value`, `time.sleep_ms`, hover, stub definitions
  and Python diagnostics with MicroPython 1.26 stubs.
- [x] WSLg at 1366×768: Code layout, keyboard search, independent output, font
  preferences and standard accessibility interfaces; offscreen 125%/150% passed.
- [x] Rebuilt wheel: installed GUI startup and all three language-service contexts
  passed from outside the checkout using explicit local tool paths.

The [desktop result](../docs/benchmarks/editor-wayland.json) records the GUI source
digest. Participant usability, actual screen-reader testing and other platforms
remain separate validation work in the existing phases.

See the [editor guide](../docs/editor.md) for setup, shortcuts and behavior.
