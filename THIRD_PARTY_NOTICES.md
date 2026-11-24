# License scope and third-party notices

## Original project material

Original RP2040 Lab source code, tests, tools and documentation are licensed
under the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License or, at your option, any later
version. The SPDX identifier is **GPL-2.0-or-later**. The complete GPL version 2
text is in [LICENSE](LICENSE).

The project is distributed without any warranty, including the implied warranties
of merchantability or fitness for a particular purpose, as described in that
license. Third-party material retains its existing terms and attribution; the
project's license does not relicense those dependencies or bundled assets.

## Runtime dependencies

| Component | Use and license information |
| --- | --- |
| Unicorn 2.1.4 | Native CPU execution library, dynamically linked. Upstream describes the engine as GPLv2; see its [README](https://github.com/unicorn-engine/unicorn/blob/2.1.4/README.md) and [COPYING](https://github.com/unicorn-engine/unicorn/blob/2.1.4/COPYING). The Python wheel's BSD metadata refers to bindings and must not be treated as the native engine's license. |
| PySide6 Essentials / Shiboken6 | Optional GUI and Python bindings. The tested wheels report `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`; consult the installed package notices and [Qt for Python license documentation](https://doc.qt.io/qtforpython-6/licenses.html) for the components you distribute. |
| CFFI | Native Python binding layer. The tested 2.1.1 package reports `MIT-0`; retain its packaged notices. |
| pyelftools | ELF/DWARF inspection. The tested 0.33 package reports public domain; retain its packaged notices. |

These libraries are installed as dependencies, rather than copied into this
source tree. For redistributed application packages, keep their notices and
provide corresponding source/build information where their terms require it.
Do not assume dynamic linking removes the dependency's license requirements.

## Bundled firmware and drivers

These files contain upstream material in addition to this project's own example
code. Preserve the accompanying full notices and provenance when redistributing
them, including when firmware is encoded as text:

| Material | Notice and provenance |
| --- | --- |
| Compiled Pico SDK C workloads | [SDK-NOTICES.txt](python/rp2040_sim/assets/SDK-NOTICES.txt) and [sdk-provenance.json](python/rp2040_sim/assets/sdk-provenance.json): Pico SDK, pico_printf, Newlib and compiler runtime notices |
| MicroPython 1.26.1 UART firmware | [MICROPYTHON-NOTICES.txt](python/rp2040_sim/assets/MICROPYTHON-NOTICES.txt) and [micropython-provenance.json](python/rp2040_sim/assets/micropython-provenance.json): interpreter and pinned dependency notices |
| SSD1306 driver and inline dashboard driver | [ssd1306-LICENSE.txt](python/rp2040_sim/assets/ssd1306-LICENSE.txt) and [ssd1306-provenance.json](python/rp2040_sim/assets/ssd1306-provenance.json): upstream source, revision, checksum and license collection |

The source and build instructions for these profiles are in
[SDK startup](docs/sdk-startup.md), [MicroPython](docs/micropython.md) and
[Runtime tools](docs/runtime-tools.md). C examples use SDK 2.2.0; MicroPython uses
its own pinned SDK revision. The manifests record the exact upstream revisions
and source/archive hashes, rather than depending on an unspecified latest build.

## Locally prepared tools and ROM

The official RP2040 B2 ROM is **not bundled**. The user-directed preparation
helper downloads and verifies an upstream release, whose source and license
references are in the [boot guide](docs/boot.md#prepare-the-rom-once). Preserve
those terms if you separately redistribute a ROM.

ARM GCC/Newlib, Pico SDK source checkouts, clangd, Pyright and editor stubs are
local build/analysis tools. They are not bundled as executable tools in the
simulator package. Their own licenses still apply; the notices above cover
third-party code included in the packaged firmware, not an entire toolchain
distribution.

The explicit SDK installer downloads the verified official 2.2.0 archive into a
separate per-user directory and retains its upstream license files. The official
example preparation helper similarly retains the pinned `pico-examples` source
and notices. Its commit/archive/source hashes and BSD-3-Clause identification are
recorded in [the catalog](python/rp2040_sim/assets/sdk-examples.json). Neither
source tree is included in the simulator wheel. If you redistribute ELF files
you build, preserve the upstream example, SDK, Newlib and compiler-runtime notices
applicable to those binaries.
