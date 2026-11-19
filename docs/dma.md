# DMA support

The native DMA model follows the
[RP2040 DMA register descriptions](https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2040/hardware_regs/include/hardware/regs/dma.h),
[official DREQ assignments](https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2040/hardware_regs/include/hardware/regs/dreq.h),
and [datasheet section 2.5](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf).
It is a functional model with repository fixtures, not a cycle-accuracy or full
hardware conformance claim.

## Run the example

Open **File → Examples → New DMA to PIO demo**, then press **F5**, or run:

```sh
.venv/bin/rp2040-sim gui --project examples/dma_pio.json
.venv/bin/rp2040-sim run --project examples/dma_pio.json \
    --duration-ms 3100 --trace build/dma-pio.jsonl
```

The bundled direct-load firmware configures DMA0 to copy twelve flash words into
PIO0 SM0's TX FIFO. PIO emits six GPIO25 pulses, with 250 ms per level at the
1 MHz functional clock. FIFO backpressure pauses DMA automatically. The CPU
sleeps, wakes on DMA IRQ0, acknowledges it, prints `DMA complete`, and sleeps
again. Completion means the last word entered the FIFO: PIO still has queued
output to emit at that point. After the final low level, PIO stalls awaiting
more data.

The **DMA** tab shows all twelve channels, remaining/reload counts, pacing,
addresses, and interrupt status. Select a channel to inspect its width, ring,
chain, and timer settings. Pause to inspect it, or select **0.1× Playback** for
slow motion. Reset restores the entire transfer and waveform deterministically.
The demo ELF and editable source are bundled; running it needs no ARM compiler.
The source is `firmware/fixtures/dma_pio.c`.

## Implemented behavior

- Twelve channels with live read/write addresses, transfer counts, and BUSY.
- Byte, halfword, and word widths, independent address increment controls,
  natural power-of-two read/write rings, and byte swapping.
- CTRL_TRIG and all three alias layouts, reload counts, multi-channel triggers,
  completion chaining, paused enabled state, and channel aborts.
- Completion interrupts, quiet/null-trigger interrupt signaling, read/write bus
  error flags with write-one-to-clear, and both DMA IRQ lines (NVIC 11 and 12).
- All sixteen PIO TX/RX DREQs, including joined FIFO capacities; ADC FIFO pacing
  when FCS.DREQ_EN is set; all four fractional DMA timers; permanent requests.
- Reads from mapped flash/SRAM, writes into SRAM, and transfers to/from supported
  MMIO. Processor-private SIO/PPB accesses are rejected as bus errors.
  Narrow MMIO writes replicate their byte/halfword across the 32-bit bus;
  narrow reads select the addressed byte/halfword. CPU MMIO accesses retain the
  existing aligned-word restriction.
- DMA control blocks can write another channel's configuration and trigger
  aliases through the same bus.

TRANS_COUNT writes update the reload count; reads report the remaining live
count. Triggering an idle enabled channel copies reload into remaining. A trigger
while BUSY does not restart the current sequence. Clearing EN pauses a channel
without clearing BUSY; setting it resumes. CHAN_ABORT terminates without a
completion interrupt. A zero write to a trigger register sets the raw interrupt
when IRQ_QUIET is enabled. Chaining to the same channel disables chaining.

Unmapped or misaligned DMA accesses and attempts to write flash stop that
channel, set its bus-error flag and raw interrupt, and leave the CPU running.
The model reports the precise failing address and leaves the failing beat in
the remaining count; hardware's pipelined error-address offsets are not modeled.
Unsupported simulator features report engine diagnostics rather than pretending
to be hardware bus errors.

## Timing and limits

CPU, PIO and DMA use the same functional root clock. After each CPU timing tick,
PIO advances and DMA executes at most one arbitrated read/write beat. During
WFI/WFE the event scheduler stops at the next ready DMA beat, timer request,
PIO clock, or other peripheral/input deadline. Each sleep event counts against
the execution work budget. Waiting on an empty/full FIFO does not make DMA
walk every root-clock tick.

High-priority channels are considered once per arbitration round before one
low-priority channel; channels of each class use deterministic round-robin
selection. This does not simulate separate read/write bus masters, SRAM banks,
flash latency, DMA read/write pipelines, or CPU bus contention.

PIO and ADC pacing uses FIFO availability at the time of a beat, rather than the
hardware DREQ credit wires. Fractional timer requests accrue per-channel credits
(up to 63), with timer phases driven by the functional root clock. Numerators
must not exceed denominators. A zero numerator disables pacing; reset uses 0/0.
DBG_TCR exposes the reload count; DBG_CTDREQ exposes current modeled readiness
or timer credits, not the silicon's full credit-handshake state. Writing it
clears timer credits. FIFO_LEVELS reads zero because there is no bus pipeline.
Aborts finish immediately, so silicon in-flight abort behavior is not modeled.
Nanosecond rounding and existing instruction-boundary ordering apply.

SPI, UART, PWM-wrap, I2C, and XIP DREQs are not implemented. Selecting them when
starting/resuming a channel stops with a diagnostic. DMA writes to the external
I2C/SPI controllers are rejected as write bus errors; their current Python device
handshake remains CPU-owned. Checksum sniffing and reserved sizes/channels are
explicitly rejected. Functional clock/reset registers are part of the
[SDK startup profile](sdk-startup.md). The separate [ROM boot profile](boot.md)
now executes core-0 ROM/boot2; this DMA fixture retains direct-load startup.

## Inspection and verification

`Engine.dma()` returns an immutable copied `DmaSnapshot`, including twelve
`DmaChannel` objects. The additive C query `sim_dma_snapshot` preserves ABI
version 1 and the original `sim_snapshot_t` layout. GUI widgets receive snapshots
from the worker and never access a live native instance. Inspection does not
consume FIFO data, clear interrupts, or advance DMA.

`tests/test_dma.py` checks widths, channel selection, byte swapping, fixed/ring
addresses, trigger aliases, reload/retrigger behavior, chaining/control blocks,
pause/resume/abort, bus errors, executable SRAM replacement, quiet interrupts,
both CPU interrupt wake paths,
all sixteen PIO requests, FIFO backpressure, narrow MMIO replication, ADC and
fractional timer pacing, and reset replay. The integrated fixture checks exact
250 ms pin transitions and the completion message while using fewer than 200 CPU
instructions over 3.1 simulated seconds. GUI tests inspect a waiting transfer,
observe completion, and reset all channels.
