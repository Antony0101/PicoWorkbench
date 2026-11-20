# Phase 3 milestones

1. **OLED:** 128×64 I²C SSD1306, command/data framing, live canvas, C and stock
   MicroPython-driver execution; repair SDK I²C initialization and NACK handling.
2. **Plotter:** timestamped numeric/named UART lines, REPL stdout demultiplexing,
   bounded captures, native Qt navigation and CSV export.
3. **Bus inspector:** observational exchange records including failures, I²C
   grouping, SPI frames, filters/exports; repair SDK SPI initialization.
4. **Files:** idle-only queued raw-REPL helpers, binary transfers, staging/cancel,
   single-editor integration, flash persistence and complete dashboard examples.

Each gate requires real firmware, focused tests and regression checks. Complete
delivery includes Linux/WSLg at 1366×768, package assets and documented controls.
No external-bus DMA, SPI OLED, recursive file operations, USB, or multi-file IDE.
