# PIO support

Both PIO blocks are modeled in `engine/src/pio.c`, using the instruction behavior
in [RP2040 datasheet chapter 3](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf)
and the [official PIO register descriptions](https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2040/hardware_regs/include/hardware/regs/pio.h).
This is functional simulation, validated against repository fixtures, rather than
a complete hardware conformance or cycle-accuracy claim.

## Running the example

Open **File → New PIO blink demo**, or launch:

```sh
.venv/bin/rp2040-sim gui --project examples/pio_blink.json
```

Press F5. The bundled firmware programs PIO0 SM0 to drive GPIO25 while the CPU
executes WFI. At the default functional clock the two SET instructions produce
250 ms high and 250 ms low. Select the PIO tab to inspect the running state or
pause and inspect the logic analyzer. Playback changes how quickly this appears
on screen; the CPU clock setting changes the modeled PIO timing too.

No compiler is needed to load the included example. Its editable source is
`firmware/fixtures/pio_blink.c`; it uses numeric PIO encodings and direct MMIO,
without depending on pioasm or normal Pico SDK startup.

## Implemented behavior

- Two blocks, four state machines each, and 32 instruction slots shared per block.
- JMP conditions, GPIO/PIN/IRQ WAIT, IN/OUT shifts, PUSH/PULL, MOV transforms and
  status source, IRQ set/clear/wait, SET, and JMP/MOV/OUT writes to the PIO PC.
- Injected SM_INSTR execution while disabled, including stalls; MOV/OUT EXEC
  execute their data on the following machine clock.
- Wrapping, delays, side-set (optional enable and pin-direction modes), circular
  pin mappings, and GPIO mux functions 6 and 7.
- Four-word TX/RX FIFOs, joined eight-word FIFOs, blocking and nonblocking
  transfers, automatic push/pull, sticky FIFO debug flags, and FIFO IRQ sources.
- Integer/fractional dividers, enable/restart controls, IRQ flags including
  relative indices, and both CPU interrupt lines on each block.

The instruction encodings follow the
[SDK encoding helpers](https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2_common/hardware_pio/include/hardware/pio_instructions.h).
Reserved instruction forms and unsupported modes stop with diagnostics instead
of silently acting as no-ops.

## Inspection and ownership

`Engine.pio(0)` and `Engine.pio(1)` return immutable copied state. The public C
query is `sim_pio_snapshot`; it does not enlarge the existing `sim_snapshot_t`.
Snapshots include each machine's PC, X/Y, ISR/OSR and shift counters, config,
stall/delay state, FIFO contents in read order, and instruction memory. The GUI
receives these copies from its simulation worker and never reads native state
from the widget thread.

Debugger peeks at RX FIFOs do not pop words or set underflow flags. Peeks at
instruction memory show the stored code; ordinary firmware reads of these
write-only locations return zero. CTRL restart clears transient machine state
while preserving its PC, scratch registers, OSR, and FIFO contents.

## Timing and current limits

PIO uses the engine's functional instruction clock as its system clock. Divider
phases advance even when a machine is disabled. The sleep scheduler stops at
PIO clock deadlines, keeping event work bounded between host commands. GPIO
inputs are sampled before the machine updates in each batch. State machines are
visited in block/SM order; simultaneous pin writes within a block favor higher
SM numbers, and side-set takes priority over that machine's ordinary pin write.

The model has no input synchronizer pipeline, metastability, or bus contention.
PIO FIFO DREQs connect to the [DMA model](dma.md). INPUT_SYNC_BYPASS is stored
but does not introduce or remove
latency. Nanosecond rounding and the engine's instruction-boundary ordering also
apply to PIO. Divider changes on the fly retain a bounded phase; this does not
claim exact hardware divider-transition timing. OUT_STICKY and INLINE_OUT_EN are
explicitly rejected. The [SDK startup increment](sdk-startup.md) adds functional
clock/reset registers. The separate [ROM startup profile](boot.md) now executes
core-0 ROM/boot2; the direct-load PIO fixture retains its original startup contract.

## Verification

`tests/test_pio.py` checks instruction families, shift directions/counts, FIFO
stalls and joins, automatic transfer ordering, side-set during WAIT, relative IRQ
acknowledgment, all four CPU interrupt lines, forced execution, and reset.
Known waveforms check fractional divider widths and the bundled 2 Hz example.
GUI tests inspect both blocks and verify reset without consuming device state.
