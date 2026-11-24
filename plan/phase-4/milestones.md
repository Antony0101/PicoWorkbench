# Phase 4 milestones

Delivered 2026-11-24 for Linux/WSLg. See [status](implementation-status.md) and
[recorded evidence](evidence.md); external checks remain listed separately.

Implement in order. A successful compile is not evidence that firmware executes
correctly. Each completed milestone needs real tools, expected runtime behavior,
focused regressions and recorded versions/hashes.

## 1. Establish the official baseline

- Add a user-invoked preparation helper for `pico-examples` at `sdk-2.2.0`.
  Record the full commit, archive SHA256, upstream license and selected source
  hashes in a checked-in catalog. Cache verified sources under ignored `.tools/`;
  accept an existing local checkout/archive for offline use. Tests and normal
  application startup must not initiate downloads.
- Build from the upstream root CMake project without editing its C sources or
  CMake files. Select only the named target, not the complete example collection.
  Record configuration arguments, compiler/SDK identity and output ELF hashes.
- Seed mandatory stock entries with `blink` and `hello_serial`. Use board `pico`,
  SDK 2.2.0, Debug, UART output and no USB stdio. Configure options are allowed
  and recorded; source/CMake patches create a different, explicitly labeled
  fixture and cannot satisfy the stock-example gate.
- Add deterministic circuit/input/oracle metadata: GP25/probe transitions for
  blink, UART text for serial, virtual-time and instruction budgets, and whether
  the firmware loops or finishes. End a looping workload with a bounded run.
  Expected observations must come from audited firmware/register behavior,
  rather than copying the simulator's current output.
- Run both entries through SDK shortcut and actual core-0 ROM/boot2 startup.
  Classify failures as tool/configuration, ELF/startup, missing hardware model or
  behavioral mismatch. Keep failure diagnostics and source attribution.

**Gate:** both unmodified examples build and produce verified GPIO/UART behavior
in both startup modes. Reset and repeated runs reproduce their observations.
Missing tools or ROM leave the gate open; they do not count as a pass.

## 2. Open, build and debug existing SDK projects

- Add **File → Open Pico SDK project…** and an SDK project control row with
  root, target, configuration, Configure, Build + Load, Cancel and Detach project.
  Opening a directory binds it to the current circuit after existing unsaved-work
  protection; it does not change wiring, run CMake or reset the loaded program.
- Configure explicitly in a managed, out-of-source cache. Discover executable
  targets and ELF artifacts using CMake File API. Populate target/source controls
  from the successful current reply; never scrape console output or guess an
  artifact by filename. Build only the selected target and its dependencies.
- Implement the shared headless project builder and `build-project` CLI described
  in [architecture](architecture.md). Stream logs asynchronously; support bounded
  cancellation of configure, build, generated-header tools and child processes.
  A failed/cancelled/superseded job leaves the current program and symbols intact.
- Keep the existing editor widget. A target-source selector opens one
  project-owned C/header file at a time, including project library dependencies.
  Switching files/projects protects edits with Save/Discard/Cancel. SDK and
  generated files open as read-only previews. Do not rewrite CMake automatically.
- In imported mode, F7 builds saved disk sources. If the active buffer is dirty,
  offer **Save source and build** or Cancel; do not silently compile the older
  disk text. Save atomically with an on-disk conflict check. During a build the
  buffer stays editable, but source-file writes from the GUI are disabled until
  the job finishes; subsequent buffer edits are not part of that build.
- Preserve Ctrl+S as circuit/project save with embedded buffer recovery.
  Provide an explicit **Save source** action for the active imported file.
  In imported mode, Ctrl+Shift+S saves a host copy and keeps the active project
  file/binding unchanged; standalone Save As behavior stays unchanged.
  Reopening a saved recovery buffer
  never overwrites a changed host file without resolving the conflict.
- Add file-aware build provenance and compiler diagnostic locations. Use complete
  resolved paths and recorded file contents for F9/execution mapping, replacing
  basename matching only for imported builds. Show changed/unverified inputs as
  requiring rebuild. Retain address breakpoints when source mapping is unavailable.
- Persist the optional SDK project binding with portable paths. A missing project
  root/target/artifact leaves the circuit available with a reattach/rebuild action.
  A fresh imported project must not silently load bundled blink as its firmware.

**Gate:** import an ordinary two-source CMake project without source/build-file
rewrites; select between two firmware targets; edit one source, build/load, run,
navigate an error in another source, and hit a correctly mapped C breakpoint.
Same-basename files, edits during builds, failed builds and cancellation must
preserve the correct artifact/source association and prior working program.

## 3. Make SDK setup and editor assistance usable

- Add **Help → Pico SDK setup…**, reusing the shared resolver rather than creating
  a second toolchain selection mechanism. Show selected paths, detected versions,
  SDK profile validation, ARM compiler, Newlib, CMake, Make and host compiler
  checks. Include host `pioasm` preparation/build failures in useful diagnostics.
- Detect explicitly selected paths, environment variables, repository-local tools
  and PATH as specified in the architecture. Pin acceptance to SDK 2.2.0; report
  other versions as outside this profile instead of claiming compatibility.
- Provide installation instructions and links, Recheck, Apply and Reconfigure.
  Do not run package managers or download SDKs/tools automatically. Applying setup
  invalidates affected build metadata and starts a new generation only on an
  explicit configure/build request; it does not alter the loaded chip.
- Export `compile_commands.json` during the actual configure. Point clangd at
  the selected project's metadata instead of invoking the single-file SDK scratch
  configurator again. Respect the selected ARM compiler for header discovery.
- Resolve duplicate source compilation entries using selected-target provenance.
  Where one header has several compile contexts, use the selected target's
  current source context and identify the context in the editor status.
- Navigate project definitions through the one-file editor with edit protection;
  retain read-only previews for SDK/generated headers. Separate live analysis,
  compiler results and runtime output. Discard responses/diagnostics from old
  file, target, configuration or SDK generations.
- Keep assistance, find/replace, execution markers, Problems and focus/undo routing
  correct when the editor or outputs are detached and across workspace switches.

**Gate:** configure from explicit user-selected tools and from supported local
discovery; build successfully; use real clangd completion/hover/definition and
diagnostics for `pico/stdlib.h`, a project header and a generated PIO header.
The standalone C and MicroPython/Pyright workflows must still pass.

## 4. Expand measured compatibility and deliver

- Add required stock catalog entries for GPIO interrupts, ADC console, PWM,
  I²C bus scan, PIO hello, memory-to-memory DMA and multicore hello. Resolve their
  target names from the pinned project's File API and retain their upstream paths
  and hashes. Use ROM boot for these additional stock gates; add SDK-shortcut
  results only where separately verified. This avoids implying that shortcut ROM
  ABI shims cover all SDK runtime services. See [validation](validation.md) for
  the required behavior per entry.
- Add a separately labeled **SDK API fixture** for SPI master exchange with the
  existing simulated SPI loopback device. Keep the SDK OLED/dashboard workload
  in the regression set. Do not label these fixtures as unmodified stock examples:
  stock peripheral examples may expect device models/slave modes we do not have.
- Repair model gaps exposed by this fixed catalog with documented register
  semantics and focused protocol tests. Preserve explicit faults for unsupported
  modes. Do not patch examples, bypass SDK calls or suppress faults to obtain a
  green result; a required mismatch keeps the milestone open.
- Add an official-examples launcher showing project, target, required circuit,
  startup mode and tested/untested/unsupported status. Never equate compile success
  with a supported runtime. Installation/download remains a separate explicit step.
- Maintain a machine-readable results catalog and a readable compatibility table
  with last tested source/tool versions, setup, observations, limits and failure
  reasons. A new SDK/example revision requires rerunning the complete catalog.
- Update SDK/install/editor/CLI/compatibility guides and contributor checks. Keep
  upstream BSD notices, GPL project terms and firmware/source provenance intact.
- Finish Linux/WSLg, package, native and regression checks. Record all evidence
  under Phase 4 and link larger generated reports from ignored review artifacts
  or small checked-in summaries; never commit local SDK/tool installations or ROMs.

**Gate:** every required catalog entry passes; desktop and installed-package
workflows work; legacy single-file/Direct/ROM/MicroPython workflows pass. Physical
Pico and earlier external accessibility/platform checks remain explicitly open.
