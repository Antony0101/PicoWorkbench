# Phase 4 architecture and compatibility

## Project profile and persistence

Keep the existing standalone-source and MicroPython paths. Introduce an optional
`SdkProjectSpec` on the circuit `Project`, serialized as `sdk_project` under schema
1 with these fields:

| Field | Meaning |
| --- | --- |
| `root` | CMake source root, rebased relative to the circuit JSON on Save As |
| `target` | Selected executable target name, or null before target selection |
| `configuration` | `Debug` by default; `RelWithDebInfo` also supported |
| `cache_variables` | Explicit string-valued project CMake definitions, empty by default |

The existing `source`, `source_text`, `startup`, `build_startup` and `firmware`
continue to describe the active file/recovery buffer and last loaded artifact.
An SDK binding is valid only for C projects and SDK/ROM startup, with board
`pico`/platform RP2040. Tool executable paths and cache directories are user
settings, not portable project fields.

New versions load existing schema-1 files with no binding. Older versions reject
the new field; document this forward-compatibility limit. Validate field types,
but allow missing external paths to be repaired in the UI rather than preventing
the circuit from opening. Imported projects without a usable artifact show
**Build required**; do not substitute `builtin` firmware. Attaching a different
build target does not invalidate or reset an already loaded program.

## Shared build service

Implement a Qt-independent project build module with typed records:

- `ToolchainProfile`: resolved SDK/compiler/CMake/Make/host-tool paths and versions.
- `SdkProjectSpec`: portable user intent described above.
- `ConfiguredTarget`: stable target name, application source paths, target-scoped
  compile context and discovered ELF artifact path.
- `BuildResult`/`BuildManifest`: request/generation identity, project/target/config,
  SDK/toolchain identity, input hashes, ELF SHA256 and verified source mapping.

GUI jobs adapt this service through a worker thread and queued result signals;
the CLI uses it without importing Qt. Give every job a session ID and monotonically
increasing generation. Changing project, target, toolchain or configuration
invalidates old callbacks; only a matching successful result can offer/load its
artifact. Serialize jobs per managed build directory and isolate separate windows.

Use one Qt-independent Linux cache resolver for both GUI and CLI:
`<XDG_CACHE_HOME>/picoworkbench/sdk-projects/<profile-hash>/`, falling back to
`~/.cache/picoworkbench/sdk-projects/<profile-hash>/`. The hash includes
resolved root, SDK/toolchain identity, generator, configuration and definitions.
Do not adopt or clean a user's existing build directory. Cleanup only directories
owned/marked by this application. Project source directories may be read-only
when no editor saves are requested; app-generated files belong in the cache.

Configure with explicit `-S`/`-B`, Unix Makefiles, board `pico`, Debug or
RelWithDebInfo, compile-command export and the resolved SDK/toolchain paths.
Disable optional picotool acquisition for the ELF-only profile. Request CMake
File API `codemodel-v2`, `cache-v2`, `toolchains-v1` and `cmakeFiles-v1` through a
client query before configure. Follow the successful reply index to executable
targets/artifacts; require the selected profile/board in the resulting cache.
Do not depend on newer API fields absent from CMake 3.25.

Reserve app-managed CMake variables (source/build/generator/toolchain, board,
platform, configuration and compile-command export). Pass other saved definitions
as individual `-D` arguments, never through a shell. Defaults request UART stdio
and no USB; an upstream target's own explicit stdio settings are not rewritten.
Unsupported target behavior remains a reported limitation/fault.

Build with `cmake --build <cache> --target <selected> --parallel 4`. Validate the
reported ELF and copy the successful artifact plus manifest atomically to an
application-owned result location. Do not load an old file merely because it
already exists. Keep configure/build logs separate from UART/runtime output.
Default timeouts: 120 seconds for configure and 300 for build; allow an explicit
per-user increase for slower machines. Cancel sends TERM to the complete process
group and KILL after a two-second grace period. GUI shutdown joins these jobs
without blocking normal event delivery indefinitely.

Opening a project only reads its selected root. **Configure** and **Build** are
explicit actions that execute that project's build scripts. The application
does not install tools or promise that arbitrary CMake scripts are sandboxed.

## Source, artifact and debugging identity

Imported projects build saved files at their original paths. Keep one editor,
with a target-source selector and project-file definition/diagnostic navigation.
Use Save/Discard/Cancel on file switches. Save atomically only after checking
the on-disk version still matches the opened file. Preserve the original project
save/recovery buffer behavior; saving circuit JSON alone does not update the CMake
source tree. Headers and project library sources can be selected without adding
a general filesystem tree or changing the user's CMake lists.

At build start record hashes for project-local sources, headers and CMake inputs;
watch those inputs/directories during the job and verify hashes/stat identities
again before accepting its result. Include compiler dependency-file inputs and
generated-header hashes in the final manifest. Distinguish generated build outputs
from project inputs: a declared generator creating its output during the build is
not itself an external edit. Treat any observed external edit,
unresolved input provenance or configuration change as unverified: retain logs
but do not auto-load or enable mapped source debugging until a stable rebuild.
Unsaved editor changes during a build can remain in the buffer; never overwrite
them with the built disk version. Disable GUI source-file writes while that job
is active, but preserve normal text editing and undo.

Carry the verified manifest alongside the loaded program through Python worker
messages, immutable state packets, reset and circuit reapplication. Keep it out
of the native ABI and out of runtime capture buffers. For imported projects,
replace basename/`editor.c` alias matching with canonical full-path plus recorded
content matching. Per-file status must account for header/config changes affecting
the target, not just the currently displayed C file. Missing or unverifiable
metadata disables mapped markers, not address-level debugging or valid firmware
execution. Reopening a saved ELF without its matching manifest must not invent
source identity; inspect it with symbols/address breakpoints and rebuild for maps.

Compiler diagnostics carry original path/line/column and build generation.
Navigate project files in the existing editor with edit protection; use read-only
previews for SDK/generated/external files. Label stale results and do not underline
newer buffers. Keep LSP document identity/revision checks when switching files.

## Tool selection and clangd

GUI precedence: explicit setup selection, environment, repository-local tools,
then PATH for executable discovery. CLI precedence: explicit command option,
environment, repository-local tools, then PATH. SDK discovery uses an explicit
root, `PICO_SDK_PATH`, or the existing `.tools/pico-sdk-2.2.0` convention; it must
not guess an arbitrary SDK from PATH. Store setup choices in existing per-user
settings. Check actual SDK version and report modified/unverified installations.

Reuse the current ARM compiler/header-discovery rules. Check CMake >=3.25, Make,
ARM GCC/Newlib, required C/C++ compiler drivers and host tools used for generated
PIO headers. A probe/configure failure must say which step failed. Tool probes
run asynchronously with bounded timeouts, and applying settings cannot reset the
simulation. Keep the pinned MicroPython SDK/profile separate.

Use the real selected project's `compile_commands.json` plus File API target
membership for clangd. Keep a managed target-specific view when one source has
different flags in multiple targets; never overwrite a user-owned compilation
database or `.clangd`. Read SDK/generated definitions in a preview, project
definitions in the one-file editor. The existing standalone scratch configurator
remains for standalone profiles only.

## CLI and existing desktop behavior

Add these commands; retain existing `build`, `run` and `gui` interfaces:

```sh
rp2040-sim build-project /path/to/project --list-targets
rp2040-sim build-project /path/to/project --target blink -o build/blink.elf
rp2040-sim run build/blink.elf --startup sdk --duration-ms 1100
```

`--list-targets` configures and returns machine-readable target metadata without
building. A normal project build requires a target, writes an adjacent manifest
with the exported ELF, and never starts simulation. Support explicit `--sdk-path`,
`--compiler`, `--configuration` and repeatable `--define KEY=VALUE` arguments.
ROM execution remains a `run --startup rom` choice requiring the existing ROM.

Use the panel manager's reveal routing for logs, Problems and source navigation.
F7 dispatches to the active build profile; F9 retains supported C breakpoints.
Preserve all save/flash prompts, circuit/text undo ownership, runtime capture
ownership and floating-panel state. No additional engine or GUI-thread device
execution is introduced. Native ABI 1 and existing timing approximations remain.

## Primary references

- [Pinned official example project](https://github.com/raspberrypi/pico-examples/tree/sdk-2.2.0)
- [SDK 2.2.0 source](https://github.com/raspberrypi/pico-sdk/tree/2.2.0)
- [CMake 3.25 File API](https://cmake.org/cmake/help/v3.25/manual/cmake-file-api.7.html)
- [Compile-command export](https://cmake.org/cmake/help/v3.25/variable/CMAKE_EXPORT_COMPILE_COMMANDS.html)
- [Existing SDK profile](../../docs/sdk-startup.md) and [editor contracts](../../docs/editor.md)
