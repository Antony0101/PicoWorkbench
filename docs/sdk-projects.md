# Build an existing Pico SDK project

Open a Pico SDK CMake project, choose a firmware target, build its ELF and run it
against the current circuit. The build can contain multiple C files and project
libraries; the editor still shows one source file at a time.

This profile targets **Pico SDK 2.2.0, RP2040 and `PICO_BOARD=pico`**, with Debug
or RelWithDebInfo builds. USB stdio, Pico W networking, Pico 2/RP2350, external-bus
DMA and general C++ application compatibility are outside this profile. Building
an ELF does not establish that its hardware behavior is supported; consult the
[compatibility guide](compatibility.md) and the current
[Phase 4 evidence](../plan/phase-4/implementation-status.md).

## Install or select the SDK

Open **Help → Pico SDK setup…**. Select an existing SDK/toolchain or press
**Install SDK 2.2.0**. Installation is explicit, uses a pinned SHA256-verified
archive, runs in the background and can be cancelled. It does not need sudo.
The destination is:

```text
$XDG_DATA_HOME/picoworkbench/tools/pico-sdk-2.2.0
# default: ~/.local/share/picoworkbench/tools/pico-sdk-2.2.0
```

**Remove managed SDK** removes only this application-owned copy, after confirming
its exact directory. Local changes inside that SDK are removed with it. External
SDKs, compilers, project source and simulated flash are not removed. Finish active
builds before removing the SDK. Source and firmware remain available after removal;
future builds/SDK analysis need a usable SDK again.

The command line provides the same installation and ownership checks:

```sh
.venv/bin/rp2040-sim sdk status
.venv/bin/rp2040-sim sdk install
.venv/bin/rp2040-sim sdk install --archive /path/to/pico-sdk-2.2.0.tar.gz
.venv/bin/rp2040-sim sdk remove --yes
```

An ARM GCC/Newlib toolchain, ARM C++ headers/libraries, CMake 3.25+, Make and host
C/C++ compilers are also required. Official C examples compile SDK C++ support
code; generated PIO headers require a host C++ compiler. On Ubuntu/Debian, run
the interactive dependency helper from a terminal:

```sh
.venv/bin/python tools/install_pico_sdk.py --linux-dependencies
```

It invokes `sudo apt-get install` and lets the package manager request confirmation
and your password. The GUI does not run privileged package installation. The
helper also accepts `--install`, optional `--archive`, and `--remove` for the SDK.

Use **Recheck** to inspect tool errors and **Apply** to save paths. Applying paths
invalidates build metadata and preserves the loaded chip. **Apply + Reconfigure**
explicitly runs CMake for the attached project. Discovery uses explicit paths,
environment (`PICO_SDK_PATH`, `ARM_CC`, `CMAKE`, `MAKE`, `CC`, `CXX`), repository-local
tools, then PATH. A managed SDK is also found in installed applications. Other
SDK versions are reported as outside the 2.2.0 profile. The MicroPython firmware's
separate SDK remains unchanged.

## Open, configure and build

1. Open a C circuit and choose **File → Open Pico SDK project…**. Select the
   directory containing its root `CMakeLists.txt`. Opening preserves the circuit,
   source buffer and loaded firmware; it does not execute CMake.
2. Press **Configure**. This explicitly executes the project's CMake scripts.
   Executable targets and ELF paths come from CMake's File API.
3. Choose a target and source file. Project C/header files open in the existing
   editor. SDK/generated definitions use read-only previews.
4. Press **F7 / Build + Load**. Save dirty project source when prompted. A current,
   verified successful build loads its ELF; failed, cancelled or superseded jobs
   preserve the previous program. Existing flash export/discard protection applies.
5. Press **F5** to run. Imported projects default to SDK shortcut startup. Select
   ROM boot explicitly and [prepare B2 ROM](boot.md#prepare-the-rom-once) for the
   real core-0 boot path. Use **Cancel build** to stop configuration/compilation.

Builds use application-owned caches below
`$XDG_CACHE_HOME/picoworkbench/sdk-projects/` (default `~/.cache/...`). They do not
reuse or clean a project's own build directory. Build output is separate from
UART/runtime output. The source tree may be read-only if you do not save edits.

## Source saving and debugging

**Save source** writes the active project file atomically, with an external-change
check. **Ctrl+S** saves the circuit JSON and embedded recovery buffer; it does not
write the CMake source tree. **Ctrl+Shift+S** writes a host copy without changing
the imported file binding. File switches offer Save/Discard/Cancel. A recovery
buffer that differs from disk must be resolved before overwriting disk source.

During builds, typing/undo remain available; GUI source-file writes are disabled.
Edits in the buffer belong to the next build. A build manifest records the ELF,
source/header/configuration hashes and toolchain identity. Input changes during
a build require a stable rebuild before automatic loading. Changed files after
loading invalidate mapped execution markers and F9 breakpoints. Full paths keep
same-named source files distinct. Address-level debugging remains available when
source provenance is missing.

Clangd uses the selected target's actual compilation commands and generated
headers. Configure/build the target before expecting generated-header analysis.
Target-specific metadata is kept in application-owned files. Project `.clangd`
and compilation databases are not overwritten. Compiler Problems and live
analysis remain separate. All controls work with the editor/output panels floating.

The circuit's optional `sdk_project` binding stores portable root/target/configuration
and CMake definitions under schema 1. Older application versions reject this new
field. Missing roots/artifacts leave the circuit open for reattachment/rebuilding;
an imported project without firmware does not run a bundled substitute. An ELF
reopened without a matching manifest requires rebuilding for mapped source debugging.

## Headless builds

```sh
.venv/bin/rp2040-sim build-project /path/to/project --list-targets
.venv/bin/rp2040-sim build-project /path/to/project --target my_app \
    --sdk-path /path/to/pico-sdk-2.2.0 --compiler /path/to/arm-none-eabi-gcc \
    --configuration Debug --define MY_FEATURE=ON -o build/my_app.elf
.venv/bin/rp2040-sim run build/my_app.elf --startup sdk --duration-ms 1100
```

Target listing emits JSON on stdout and configure logs on stderr. Builds export
an adjacent `.elf.manifest.json` sidecar and do not start simulation. Additional
tool options are `--cmake`, `--make`, `--host-cc` and `--host-cxx`. Board, platform,
toolchain, generator and configuration variables are managed by the application.
The existing single-file `build` command is retained.

## Pinned official examples

Prepare examples explicitly; tests and ordinary startup never download them:

```sh
.venv/bin/python tools/prepare_sdk_examples.py --download
# Offline: --archive /path/to/verified-archive.tar.gz
# Existing unchanged source: --checkout /path/to/pico-examples
```

**File → Official Pico SDK examples…** shows targets, required circuits, startup
and recorded status. Sources are pinned to `sdk-2.2.0`, commit
`4c3a3dc0196dd426fddd709616d0da984e027bab`; complete source-tree and workload hashes
are checked. Set `PICO_EXAMPLES_PATH` for an external prepared tree. The launcher
reports only the recorded startup/profile status; additional targets and startup
combinations require their own runtime validation. See the
[measured compatibility table](compatibility.md#measured-official-sdk-examples).

Run the acceptance harness with prepared tools and ROM:

```sh
.venv/bin/python tools/validate_sdk_projects.py
QT_QPA_PLATFORM=offscreen RP2040_TEST_SDK_PROJECTS=1 \
    RP2040_TEST_LANGUAGE_SERVERS=1 .venv/bin/python -m pytest -q
make native
```

The opt-in gate fails for missing prerequisites. Stock examples and project-authored
SPI/dashboard fixtures have separate provenance; compile success is never presented
as a passing runtime result. Physical Pico comparisons remain outstanding.
