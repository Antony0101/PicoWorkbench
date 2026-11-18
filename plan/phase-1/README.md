# Phase 1: RP2040 simulator implementation

Status: functional version 0.1.0 implemented through the initial desktop and
peripheral workflow, PIO, DMA, two-core synchronization and a pinned SDK 2.2.0
ELF startup profile, real core-0 B2 ROM/boot2, UF2/serial flash, RTC and a pinned
MicroPython UART interpreter workflow. Physical hardware comparison and additional
OS validation remain open; broader chip compatibility follows the documented limits. See
[implementation status](implementation-status.md) and the [running instructions](../../README.md).

This is the original implementation plan, retained with its completion evidence
and open external validation gates. Continue UI/UX development in
[Phase 2](../phase-2/README.md). See the [phase index](../README.md).

Build a desktop application that executes RP2040 firmware, displays the chip's
state, and lets users connect virtual components to a board. Use a C simulation
engine and a Python GUI, joined through CFFI.

## Read in this order

1. [Architecture](architecture.md): boundaries, timing, execution, and technology choices.
2. [Milestones](milestones.md): implementation sequence and completion gates.
3. [Engine API](engine-api.md): the contract between C, Python, and the debugger.
4. [Components and wiring](components.md): pins, connections, and external devices.
5. [Validation](validation.md): correctness, compatibility, and performance checks.

## Intended user experience

1. Create a project and select a Pico board.
2. Open or write firmware; build it or load an existing ELF image.
3. Add components and connect their pins on a wiring canvas.
4. Run, pause, reset, or step the simulation.
5. Inspect CPU registers, memory, peripheral state, UART output, and pin waveforms.
6. Save the project and reproduce a run using recorded inputs.

## Initial scope

- Desktop application, initially developed and verified on Linux.
- Actual ARM firmware execution, starting with a deliberately small bare-metal fixture.
- One active CPU core in the first useful version; an architecture that can later
  schedule both cores.
- Functional hardware behavior with documented timing approximations.
- Digital components and buses, with a manually supplied ADC input later.
- A headless engine usable independently of the GUI.

Full cycle accuracy, general analog circuit solving, USB enumeration, Pico W
radio emulation, reverse execution, and stock MicroPython compatibility are later
work. Unsupported features must be visible in the compatibility report.

## Decisions to settle before substantial implementation

- Evaluate CPU execution backends and choose one that can represent Cortex-M0+
  instructions, exceptions, and memory access semantics. Prefer a reusable C
  backend when it meets those needs; otherwise scope an interpreter explicitly.
- Confirm dependency licenses and distribution requirements.
- Define the exact startup path of the first ELF fixture and which reset/boot
  behavior is deliberately bypassed.

The default remains C + Python. A backend evaluation may propose an alternative,
but any resulting architecture change should be recorded before implementation.

## Reference material

- [RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf)
- [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk)
- [RP2040js reference implementation](https://github.com/wokwi/rp2040js)
- [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/)
- [CFFI](https://cffi.readthedocs.io/en/latest/overview.html)

Use the datasheet and applicable Arm architecture documentation as the behavioral
authority. Another emulator is a useful reference, not proof of correctness.
