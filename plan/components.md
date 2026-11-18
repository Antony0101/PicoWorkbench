# Components and wiring

## Distinguish internal and external peripherals

On-chip UART, I2C, SPI, GPIO, and PIO are register-level models in the C engine.
External LEDs, buttons, sensors, and displays attach through board pins and buses.
Their GUI widgets are separate from their behavioral models.

## Connection model

Each component declares stable pin identifiers, direction/capability, and default
drive state. Board pin labels map to chip signals without making those labels
part of the chip implementation.

Wires join pins into nets. Resolve digital nets using:

- Strong low or high drive.
- High impedance, including an open-drain output that has released the line.
- Weak pull-up or pull-down.
- Floating and unknown/conflicting results.

A strong drive overrides a weak pull; opposing strong drives produce contention.
Define conflicting pulls and floating-input sampling explicitly. Show diagnostics
when firmware relies on an undefined electrical state instead of choosing a
silent arbitrary value.

Apply simultaneous drive updates in a defined order and resolve affected nets
until stable. Detect a bounded delta-event limit for zero-time feedback loops and
report the involved components.

Initially model logical behavior rather than resistor networks or physical
current. An LED's visual brightness can use PWM duty cycle; it does not imply an
electrical current calculation. ADC inputs can use user-supplied scalar voltages
without introducing a general analog solver.

## Component interface

Each component provides:

- A type identifier, schema version, parameter schema, and pin descriptors.
- Reset and explicit state ownership.
- Timestamped input handling and scheduled future outputs.
- A read-only state view for presentation.
- Configuration serialization; full live-state serialization is later work.

Python models run on the simulation worker, not the Qt main thread. Timed outputs
use virtual deadlines. Wall-clock timers must not drive hardware behavior.

## Bus fidelity

Begin with transaction-level device models for I2C and SPI where this can support
the selected firmware. The controller still needs the register, FIFO, status,
interrupt, and timing behavior used by that firmware.

Transaction-level devices do not automatically support software bit-banging,
PIO-driven traffic, I2C clock stretching, arbitration, or arbitrary logic-analyzer
decoding. Record these limitations. Add a pin-level protocol model when a workload
needs it, and route both model types through the same board/net identities.

For I2C, represent open-drain lines and pull-ups. For SPI, specify chip select,
clock polarity/phase, transfer width, and supported modes. Avoid claiming full
bus coverage after implementing only a single happy-path sensor exchange.

## Initial component catalog

| Component | Purpose | First milestone |
| --- | --- | --- |
| LED | Observe GPIO and later PWM output | 1 |
| Button | Supply scheduled GPIO input, later optional bounce | 3 |
| UART terminal | Observe and inject serial data | 3 |
| Logic analyzer | Inspect timestamped digital transitions | 3 |
| I2C sensor | Exercise addressing and register transactions | 4 |
| SPI display or loopback | Exercise transfers and chip select | 4 |
| Adjustable analog source | Supply an explicit ADC input | 4 |

## Project files and canvas

Persist component instance IDs, type versions, parameters, positions, and net
membership. Store endpoint identifiers rather than deriving connectivity from
screen coordinates. Reject missing pins, duplicate IDs, and incompatible schema
versions with actionable diagnostics.

Compile canvas edits into a validated connection graph while paused, then apply
it as one update. Keep camera position and panel layout as optional presentation
settings. A headless run must reproduce the circuit without loading any widgets.
