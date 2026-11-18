# Simulator MicroPython board profile

This is a build configuration for the unmodified MicroPython 1.26.1 RP2 port.
It selects the Pico board, UART REPL and standard boot filesystem modules, with
USB disabled. The manifest freezes only files shipped in MicroPython itself;
it uses this directory as `MICROPY_LIB_DIR` and has no external `require()` entries.
Chip behavior belongs to the C engine, not this board configuration.
