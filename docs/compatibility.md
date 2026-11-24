# Compatibility matrix

All implemented hardware uses functional or approximate scheduled timing. No
subsystem is advertised as cycle accurate. Validation is against the included
fixtures and documented register semantics; no physical Pico comparison has been
performed. See [Validation](validation.md) for the tested environments and evidence.

| Subsystem | Implemented | Limits |
| --- | --- | --- |
| CPU | Two Unicorn Cortex-M0 Thumb/M-class contexts, shared memory, separate register/interrupt banks | Serialized functional instruction schedule; no exhaustive ISA conformance claim |
| Multicore | Core-1 FIFO launch handshake, PSM PROC1 reset, two eight-word FIFOs/IRQs, 32 spinlocks, CPUID and SEV/WFE; copied GUI inspection | Protocol shim instead of ROM execution; no simultaneous physical CPU cycles, bus contention, or full PSM; see [details](multicore.md) |
| Memory | 2 MiB flash, 264 KiB SRAM, validated ELF load segments/LMAs, optional local B2 ROM | No SRAM aliases, detailed XIP/cache stalls or flash wear |
| Startup | Direct fixtures, SDK 2.2.0 ELF shortcut, or real core-0 B2 ROM/CRC/boot2 execution from ELF/binary/UF2 | Functional startup conditions; core 1 retains protocol shim; see [boot](boot.md) and [SDK audit](sdk-startup.md) |
| Flash/UF2 | Strict RP2040 256-byte flash UF2 validation; SSI/QSPI read, program, erase, ID, status/QE and boot2 return; export/persistent bytes | Immediate transfers, no pin waveform, wear/protection or cache timing; RAM/metadata/extended UF2 unsupported |
| Clock/reset control | RESETS aliases and supported peripheral reset; XOSC/PLL readiness, clock mux/divider fields, watchdog scratch/TICK, startup identification | Immediate readiness; clock writes do not change simulator Hz; no oscillator latency, clock gating, watchdog countdown or full power/reset model |
| SIO divider | Per-core signed/unsigned division, quotient/remainder, READY/DIRTY and software restore | Instant completion, no eight-cycle divider latency |
| ROM ABI | SDK-mode bit/memory shims and SF/SD initialization descriptors; ROM mode executes actual B2 lookup, memory, math and flash routines | Shortcut floating-point table calls fault with a ROM-startup instruction; B2 ROM must be prepared locally; USB boot remains unsupported |
| RTC | Calendar setup/load/read/latching, second ticks, day/leap rollover, match alarm/IRQ25 and reset | Fixed virtual seconds, immediate control transitions; no RTC divider timing/clock-domain latency |
| MicroPython | Pinned 1.26.1 UART profile, raw script upload/interrupt, arithmetic/math, GPIO/sleep, ADC, RTC, LittleFS files, CLI/GUI and replay | USB disabled; general threading/optional module paths unvalidated; [profile details](micropython.md) |
| Debugger | Run/pause/reset/step, bounded instruction runs, PC breakpoints, BKPT, memory/register inspection, ELF symbols and DWARF locations | No GDB server, watchpoints, reverse execution, or source-level stepping |
| GUI execution controls | 0.01×–4× playback targets; editable 1 kHz–133 MHz functional CPU clock | Playback is host-limited and may overshoot a time target by one instruction; applying a clock change resets the chip |
| Interrupts | Per-core NVIC enable/pending/priority and VTOR, timer/GPIO/UART/PWM/ADC/I2C/SPI routing, SVC/PendSV/SysTick, nested priority tests, PRIMASK, MSP/PSP frames | No architectural HardFault/NMI escalation or tail-chain timing; exception return handles BX and POP PC |
| Sleep | WFI, WFE, SEV, virtual-time event wake-up | Approximate timing; no low-power electrical model |
| GPIO | SIO output/OE aliases, input sampling, mux selections, input/output/OE overrides, pad pulls/IE/OD | Supported mux functions: SPI=1, UART=2, I2C=3, PWM=4, SIO=5, PIO0=6, PIO1=7, null=31; no drive-strength physics |
| GPIO IRQs | Level and edge status, edge W1C, processor-0/1 enable/force/status | GPIO IRQ override fields unsupported |
| Digital nets | Connected GPIOs, strong/weak drives, high impedance, pulls and contention | External-only circuits without a GPIO endpoint have no active engine net; opposing external weak pulls are rejected |
| Timer | Virtual time reads, four wrapping 32-bit alarms, arm/disarm, IRQ masks/force/W1C | No timer writes, debug pause, or clock-source configuration |
| SysTick | Per-core enable, reload/current, count flag, CPU/reference clock, IRQ | Reference clock fixed at 1 MHz; no detailed interrupt latency |
| UART0/1 | Byte TX/RX, RX queue, FR, configuration/full IRQ-mask storage, clearable TX interrupt and RX IRQ | Instant TX, simplified FIFO/threshold behavior; non-RX/TX error/modem sources stay inactive; no baud waveform or pin routing |
| I2C0/1 | 7-bit master, TAR/ENABLE/CON, DATA_CMD, RX FIFO, STOP/abort status and IRQs | Immediate transfers; no slave mode, bit-banging, arbitration, stretching, or detailed FIFO timing |
| I2C sensor | Python 256-byte register device, pointer writes, reads, auto-increment, repeated start/stop | Simulated generic device: register 0 ID=0x42, register 1 temperature=25; not a specific sensor chip |
| SSD1306 OLED | 128×64 I²C display, framebuffer RAM, addressing modes, initialization, inversion/contrast/orientation and live snapshots | Addresses 0x3C/0x3D; no SPI OLED or hardware scrolling; see [runtime tools](runtime-tools.md) |
| SPI0/1 | 8-bit Motorola master frames, configuration, RX FIFO, threshold IRQs | Byte transfers only; no SCK waveform, slave mode, DMA or automatic chip select |
| SPI loopback | Python byte echo, validates wired TX/RX/SCK mux and active-low SIO chip select | Not an electrical short or display controller |
| PWM | Eight free-running slices, A/B compare/invert, DIV, TOP, counter, wrap IRQ, GPIO output | No phase-correct/gated modes, phase advance/retard, or double-buffered compare updates |
| PIO0/1 | Four SMs per block, 32 shared instructions, JMP/WAIT/IN/OUT/PUSH/PULL/MOV/IRQ/SET, forced and EXEC instructions, side-set/delay/wrap, fractional dividers, FIFO joins/autopush/autopull, IRQ routing and copied debug state | Functional shared-clock timing; no input synchronizer latency, bus contention, OUT_STICKY or INLINE_OUT_EN; see [PIO details](pio.md) |
| DMA | Twelve channels, transfer widths, rings, byte swap, alias/reload triggers, chaining, pause/abort, bus errors, two IRQ lines, PIO/ADC/fractional-timer pacing and copied inspection | One functional beat per root tick; FIFO-level pacing, instant abort; no other DREQs, checksum sniffing, bus contention or silicon pipeline timing; see [DMA details](dma.md) |
| ADC | Manual 12-bit channels, single/continuous conversions, result, FIFO, threshold IRQ, overflow and DMA DREQ | No analog solver, noise, round-robin, voltage-to-sample physics or temperature calibration |
| Projects | Versioned JSON, stable endpoints, geometry, component addresses, relative path rebasing | Pico logical board profile only; no live machine save states |
| SDK projects | Local CMake project/target selection, multiple C sources and libraries, generated headers, verified ELF manifests and target-specific clangd | SDK 2.2.0, RP2040, board `pico`, Unix Makefiles, Debug/RelWithDebInfo; one editor buffer; no general C++/USB/networking support |
| Runtime tools | UART serial plots, I²C/SPI transaction capture, MicroPython file operations and detachable GUI panels | Bounded captures; file actions require a confirmed idle UART REPL; [details](runtime-tools.md) |
| Replay | Timestamped button, strong/weak GPIO, UART and ADC inputs with firmware/circuit hashes | Starts from reset; unexpected breakpoints interrupt replay; no random devices or host-time-dependent models |

Unsupported registers, instructions, and modes stop with an address/operation
diagnostic. Debug peeks at unavailable registers return an error without changing
hardware state. MMIO supports aligned byte/halfword/word reads, replicated I/O
writes, and lane-specific narrow PPB priority writes; other narrow PPB writes
fail explicitly. Debugger memory
peeks support arbitrary bytes in SRAM/flash. Banked debug peeks and the original
CPU snapshot use core 0; the Cores inspector exposes both banks. DMA rejects
processor-private SIO/PPB accesses.

The trace ring holds 8,192 events and counts overflow explicitly. The GUI retains
10,000 GPIO transitions for display and up to 5,000 serial text blocks. Headless
validation rejects trace overflow.

## Measured official SDK examples

The Phase 4 baseline uses unchanged `pico-examples` at `sdk-2.2.0`, commit
`4c3a3dc0196dd426fddd709616d0da984e027bab`, SDK 2.2.0, ARM GCC 13.2.Rel1,
board `pico` and Debug. Each runtime check is repeated after reset with instruction
budgets 257 and 4096, then recorded-input replay. The
[results](benchmarks/phase4-sdk-linux.json) record hashes and observations;
[setup and controls](sdk-projects.md) explain how to run your own project.

| Stock target | Verified startup | Observed behavior |
| --- | --- | --- |
| `blink` | SDK shortcut, B2 ROM | GP25 alternates every 250 ms |
| `hello_serial` | SDK shortcut, B2 ROM | Repeated UART0 `Hello, world!` |
| `hello_gpio_irq` | B2 ROM | GP2 rising and falling callbacks, once per injected edge |
| `hello_adc` | B2 ROM | ADC0=2048 prints raw `0x800` and `1.650000 V` |
| `hello_pwm` | B2 ROM | TOP=3, A/B levels=1/3 and GP0/GP1 transitions |
| `i2c_bus_scan` | B2 ROM | 112 address exchanges; only the GP4/GP5 sensor at 0x48 ACKs |
| `hello_pio` | B2 ROM | Generated PIO program drives GP25 through PIO1 |
| `hello_dma` | B2 ROM | Complete source/destination copy and the expected UART greeting |
| `hello_multicore` | B2 ROM | FIFO flag 123 exchange; both cores report success |

Several stock examples return from `main()`. The SDK then executes a repeated
`BKPT` in `_exit`, so a paused simulator is expected. In the multicore example,
core0 reaches that breakpoint before core1 finishes printing. Explicit resumes
of this verified exit loop are part of its gate; assertions, faults and unrelated
breakpoints are never skipped. The normal replay command remains strict.

The SPI byte-loopback and OLED/ADC/plot dashboard checks are **project-authored SDK
fixtures**, with separate source provenance. They are not stock upstream examples.
Additional SDK targets and startup combinations remain unvalidated. Functional
timing, byte-level buses and the core1 launch shim retain the limits above.

## Not implemented

Watchdog countdown/reset, USB transfers/BOOTSEL USB, general Pico SDK/Arduino
compatibility, stock USB MicroPython/CircuitPython compatibility, and native
desktop validation for macOS/Windows. Physical Pico comparison is outstanding;
Linux/WSLg is the validated desktop environment.

## Reference sources

- [RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf)
- [Pico SDK register descriptions](https://github.com/raspberrypi/pico-sdk/tree/master/src/rp2040/hardware_regs/include/hardware/regs)
- [Unicorn C API and backend](https://github.com/unicorn-engine/unicorn/tree/2.1.4)
