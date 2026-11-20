# OLED, plotting, bus inspection and MicroPython files

Launch `.venv/bin/rp2040-sim gui`. Open **File → Examples → New C temperature
dashboard** or **New MicroPython temperature dashboard**. The C demo is bundled;
running it needs no compiler. MicroPython requires the local B2 ROM described in
[MicroPython setup](micropython.md), then **F7 / Run Python** uploads the editor.
Nothing downloads at runtime.

The matching JSON examples are `examples/dashboard-c.json` and
`examples/dashboard-python.json`. Choose **Debug** to see code and the circuit,
then **Ctrl+J** for output. The existing I/O inspector's ADC0 / GP26 slider drives
the simulated temperature. Both examples show two digits and a bar on the OLED,
print `temperature:…,voltage:…`, read the register sensor, exercise SPI loopback,
and intentionally access missing address `0x3D` to demonstrate NACK handling.

## OLED

Add **ssd1306** from the component selector or right-click the circuit background
and choose **OLED SSD1306**. Wire GP4 → SDA and GP5 → SCL for I2C0, then Apply.
Other supported hardware-I2C pin routes work too. This is a logical circuit;
power wiring and analog bus timing are not modeled.

Properties and the component context menu choose address `0x3C` or `0x3D`.
The display has 128×64 pixels. The model supports command/data control bytes,
horizontal/vertical/page addressing, display enable, inversion, contrast, remap,
scan direction, start line, offset and standard driver initialization. Parameters
survive STOP boundaries. Reads are NACKed because this I2C display is write-only.
Hardware scrolling is reported as unsupported; an SPI OLED is not included.
Reset/applying the circuit resets external models and display RAM.

The example includes the upstream MicroPython driver inline so it works without
first installing device files. Its exact revision, source URL and checksum are
in `python/rp2040_sim/assets/ssd1306-provenance.json`; the accompanying upstream
license collection is retained beside it. The C SDK build profile links
`hardware_i2c`, `hardware_spi` and `hardware_adc` for edited dashboard builds.

## Serial plotter

Open **Serial plotter** in the output tabs. Print one sample per line:

```python
print('25,3.3')                         # ch1, ch2
print('temperature:25,voltage:3.3')    # named channels
```

Values accept finite signed decimal/scientific notation. A line must use either
numeric columns or named columns; duplicate names, invalid values, Unicode logs
and ordinary messages are ignored. Up to eight distinct channels can appear in
one capture. Changing UART clears the capture. UART0 MicroPython program stdout
is extracted from raw-REPL acknowledgements, prompts and stderr; file-management
traffic is excluded. The raw serial console remains available separately.

Timestamps use simulated nanoseconds at the newline. Each channel retains 10,000
samples; unfinished lines are limited to 4 KiB. Event loss or line overflow marks
a gap and breaks plotted lines. Clear and reset clear captures. CSV export uses
`time_ns,channel,value,segment`; a new segment identifies a capture gap.

Toggle channel checkboxes, wheel or press **+/-** to zoom, drag to pan, and use
**Home / Follow latest** to follow new data. The vertical range autoscales to the
visible values. **Freeze view** freezes rendering while capture continues;
export includes the worker's current capture. Plot UART/time scale are user
preferences, not circuit data.

## Bus inspector

Open **Bus inspector** for I2C/SPI exchanges. I2C rows group bytes through STOP,
including repeated starts and mixed read/write segments. SPI rows are individual
frames; the engine does not expose complete CS transaction boundaries.

Rows show virtual time, bus/controller, address, selected device, direction,
transmitted/received bytes, flags and ACK/NACK/fault status. Selecting a row shows
all bytes with hexadecimal/ASCII detail. Filter by strings such as `I2C0`,
`0x3c`, `oled`, `NACK` or `FAULT`. The filter controls the view; CSV/JSON exports
include all retained exchanges. Clear affects observation only. Capture can be
disabled without changing firmware behavior.

The buffer retains 10,000 exchanges. Its discarded count is visible; a retained
capture can begin in the middle of an I2C transaction. IDs restart on reset/reload
and are stable during deterministic replay. Missing SPI devices and conflicting
responders retain failure records. Bus register setup supports the documented SDK
and MicroPython CPU-driven workloads. Actual external-bus DMA and cycle-accurate
wire waveforms remain unsupported.

## Pico files

Open **Pico files** in a MicroPython project. Click **Boot/connect to REPL** for a
fresh interpreter. If a program or saved `main.py` is running, click **Interrupt
Python** explicitly. Pausing the chip does not make a running Python program idle.
File actions require a confirmed prompt and applied circuit; they never interrupt
a user program automatically.

Refresh lists a directory and available flash space. Activate a folder to enter
it; **Up** navigates to its parent. Upload/download arbitrary binary files, create
folders, rename entries, or delete files/empty folders. Recursive transfer/delete
is not included. Right-click actions mirror the visible controls.

**Open Python** loads a UTF-8 `.py` file into the existing editor and labels it
`Pico:/path.py`. Unsaved work is protected. If the buffer changes while a read is
pending, the downloaded source does not replace it. **Save to Pico** writes an
explicit device path; **Ctrl+S** still saves the circuit project and **Ctrl+Shift+S**
still saves a host source file. Opening or saving a device file does not change
the identity of the last uploaded/running program. Device paths are not host paths
and are not stored as project source paths.

Transfers execute isolated helpers through the actual interpreter and UART in
256-byte chunks. Writes use a temporary device file and replace the destination
only after completion; downloads use a temporary host file. Cancel stops between
helpers and removes staging files. Once the final rename begins, upload completes.
Timeout recovery interrupts only a helper that has acquired raw REPL; if recovery
fails, an error identifies the temporary file for manual removal after reconnect.
There is one REPL owner, so execution changes/manual UART are disabled or rejected
while a job runs. Virtual time advances independently of playback slowdown; the
previous playback state and speed are restored afterward.

Listings are limited to 1,000 entries and 64 KiB per reply; opening source is
limited to 1 MB. Larger files can still be downloaded in chunks. Flash capacity,
UART/interpreter speed and normal MicroPython filesystem errors apply.

To install the standalone dashboard, upload the bundled driver as
`/lib/ssd1306.py` and upload `assets/dashboard.py` as `/main.py`, adding
`from ssd1306 import SSD1306_I2C` at its top. Create `/lib` first. Reset runs
`main.py`; use Interrupt before further file operations. **Export flash image**
preserves device files for another session. Saving project JSON preserves source
and circuit, separately from runtime flash. Device modules are not automatically
indexed by the editor's language server.
