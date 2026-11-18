# Two-core support

Both Cortex-M0 contexts execute on the simulation worker, sharing one Unicorn
memory map and one set of peripherals. Each core has its own registers,
MSP/PSP/CONTROL/PRIMASK, exception state, NVIC, vector table and SysTick.
This is functional simulation with a deterministic instruction schedule.

## Running the example

Open **File → New two-core demo** and press **F5**, or run:

```sh
.venv/bin/rp2040-sim gui --project examples/multicore.json
.venv/bin/rp2040-sim run --project examples/multicore.json \
    --duration-ms 1100 --trace build/multicore.jsonl
```

Core 0 launches core 1, then sends one work word every 250 ms using a timer
interrupt. Core 1 wakes from WFE, updates shared SRAM under spinlock 0, changes
GPIO25, and replies through the other FIFO. The reply wakes core 0 through its
FIFO interrupt and produces `Core 1 count: 00000001`, then increasing counts.
The default 1 MHz functional clock produces approximately 250 ms LED levels;
CPU work and exception handling add functional instruction latency.

The **Cores** tab shows both cores' instruction counts, PCs and sleep states.
Select either core to inspect registers, NVIC, vector table, SysTick, FIFO data,
and shared spinlocks. The **CPU0** tab retains the original debugger and symbol
view. **Step next / F10** advances one instruction on the next runnable core;
when both sleep it may first advance time to a wake event. Run N counts total
instructions across both cores. Pause, breakpoints, device requests and reset
apply to the whole chip. Breakpoint addresses apply to both cores.

The compiled demo and editable C source are bundled. Running it needs no compiler;
rebuilding `firmware/fixtures/multicore.c` requires the existing ARM toolchain.

## Startup and reset contract

The direct loader initializes core 0 from ELF vectors. Core 1 starts parked in a
modeled launch protocol, with no ROM instructions executed. It accepts the
six-word sequence `0, 0, 1, VTOR, SP, entry` over core 0's outgoing SIO FIFO,
echoing acknowledgments through core 0's incoming FIFO. Invalid initial command
words produce a zero response and restart the sequence. A zero during the
address portion returns to the initial handshake. Echoes issue a modeled SEV.

After a complete sequence, VTOR must be 256-byte aligned in mapped flash/SRAM,
SP must be word aligned inside SRAM, and entry must be a Thumb address inside
an ELF executable segment. Invalid launch parameters stop with a diagnostic.
The model initializes a fresh CPU context and starts the supplied entry. Stack
ownership and space for each core remain the firmware's responsibility. A
returning core 1 entry parks until a core reset; further interrupt execution after
that return is not modeled.

This follows the interface used by
[`multicore_launch_core1_raw`](https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2_common/pico_multicore/multicore.c)
and the [boot ROM holding/launch code](https://github.com/raspberrypi/pico-bootrom/blob/master/bootrom/bootrom_rt0.S).
The protocol shim does not supply the ROM image, SDK runtime initialization,
clock setup, boot2, flash setup, or general ROM calls. Arbitrary SDK firmware is
still outside the direct-load compatibility claim.

PSM FRCE_ON/FRCE_OFF supports PROC1 (bit 16), including atomic aliases. Asserting
FRCE_OFF parks and resets core 1's CPU state. Clearing it drains core 1's incoming
FIFO and queues a zero readiness response, as used by `multicore_reset_core1`.
A fresh launch can then follow. Shared SRAM, GPIO/peripherals and SIO spinlocks
are retained. SIO FIFO error flags are retained until firmware acknowledges them.
The initial direct-load reset starts with empty FIFOs; it does not queue an
unsolicited readiness word. PSM DONE reports the modeled processor ready bits;
other PSM blocks and processor-0 power/reset controls are unsupported.

## SIO synchronization and interrupt routing

The register behavior follows the
[official SIO register descriptions](https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2040/hardware_regs/include/hardware/regs/sio.h):

- CPUID reads 0 or 1 for the executing core.
- Two eight-word, 32-bit FIFOs, one in each direction. FIFO_ST is core-local:
  VLD indicates incoming data, RDY indicates outgoing space. A full write is
  ignored and sets WOF on the sender; an empty read returns zero and sets ROE on
  the receiver. WOF/ROE clear with write-one-to-clear. FIFO IRQ is the OR of
  incoming VLD, WOF and ROE: core 0 receives NVIC IRQ15; core 1 receives IRQ16.
- Reading an available spinlock atomically claims it and returns `1 << index`;
  reading a held lock returns zero. Any write releases it. All 32 locks are
  shared. The debugger records the last claiming core for inspection; it does
  not enforce ownership on release. SPINLOCK_ST reports the held bitmap.
- SEV sets both event flags. WFE consumes a pending event or sleeps until an
  event/enabled interrupt wakes it. FIFO firmware should use SEV when needed;
  an ordinary FIFO write does not itself issue a simulated SEV.

Common peripheral IRQ levels feed both cores' independent NVICs. GPIO has both
PROC0 and PROC1 INTE/INTF/INTS address groups, with shared raw edge status. SIO
FIFO IRQs route only to their receiving core. Core-local exception entry/return
uses that core's vector table, priorities, PRIMASK and stack selection.

DMA can access shared SRAM and modeled peripherals, but SIO and PPB are
processor-private interfaces and are rejected as DMA bus accesses. A Python
I2C/SPI device request from either core pauses the chip until its reply, preserving
the existing single-owner request/response boundary.

## Deterministic scheduling and limits

When both cores are runnable, they alternate single instructions. Scheduler
turn state persists across API calls, so changing an instruction batch budget
does not change the order. When one sleeps or remains in the launch protocol,
the other can execute bounded batches; the backend yields when an inter-core
wake or launch needs servicing. CPU context save/restore never occurs inside a
Unicorn MMIO callback. Both contexts share the same backing memory, so there is
no per-core SRAM copy to reconcile.

Each executed instruction on either core advances the existing functional root
clock by one tick. At 1 MHz there are one million aggregate instruction ticks
per simulated second: two busy cores each receive approximately half that rate.
This does not model both physical CPUs retiring concurrently at the hardware
clock. Peripherals, PIO and DMA advance once per root tick, and each launched
core's SysTick advances with that clock even while the other core executes.
When neither core is runnable, the scheduler advances to the next relevant
input/peripheral/SysTick/PIO/DMA deadline, bounded by the event-work budget.

Memory operations and SIO effects occur in this serialized order. There is no
hardware bus contention, SRAM bank arbitration, weak-memory model, simultaneous
spinlock claim timing, or sub-instruction interrupt latency. Boot-shim work has
no instruction or clock cost. Returned core-1 entries remain parked until reset.
SIO dividers now have a functional core-local implementation used by the
[SDK startup profile](sdk-startup.md). Interpolators, authentic power sequencing
and real core-1 ROM execution remain separate work. Core 0 can now execute the
[B2 ROM/boot2 profile](boot.md). Existing nanosecond rounding and
the selected ELF startup restrictions apply.

## API and verification

`Engine.cores()` returns immutable `CoresSnapshot`/`CoreSnapshot` copies. The
additive C query is `sim_cores_snapshot`; ABI version 1 and `sim_snapshot_t`
remain unchanged. The original snapshot keeps core 0 registers and aggregate
instruction count. Debug memory peeks use core 0's view for banked registers,
restore the execution context afterward, and never pop FIFO data or claim locks.
FIFO contents, interrupt banks, register banks and spinlock owners are available
without side effects in the copied two-core snapshot.

`tests/test_multicore.py` checks launch validation, shared memory, contended
spinlocks, FIFO capacity/order/errors, both FIFO interrupt paths, SEV/WFE,
core-1 SysTick/vector tables/PRIMASK/PSP/SVC, GPIO routing, reset/relaunch,
return parking, breakpoints, exact aggregate instruction budgets, external device
requests, core-1 DMA setup, and identical state across different batch budgets
and reset. The bundled example checks timer-driven shared-memory/FIFO exchange,
serial counts and GPIO pulses. GUI tests inspect both contexts, step once and
reset back to core 1's parked state.
