"""Owned, single-threaded interface to the native hardware engine."""
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from ._native import ffi, lib


class Stop(IntEnum):
    BUDGET = 0
    DEADLINE = 1
    BREAKPOINT = 2
    IDLE = 3
    FAULT = 4
    NO_IMAGE = 5
    DEVICE_REQUEST = 6


class Drive(IntEnum):
    LOW = 0
    HIGH = 1
    FLOATING = 2
    CONFLICT = 3
    PULL_LOW = 4
    PULL_HIGH = 5


@dataclass(frozen=True)
class Event:
    time_ns: int
    sequence: int
    kind: int
    source: int
    value: int


@dataclass(frozen=True)
class DeviceRequest:
    id: int
    time_ns: int
    bus: int
    controller: int
    target: int
    value: int
    flags: int


@dataclass(frozen=True)
class Snapshot:
    time_ns: int
    instructions: int
    dropped_events: int
    registers: tuple[int, ...]
    xpsr: int
    gpio_out: int
    gpio_oe: int
    gpio_in: int
    gpio_floating: int
    gpio_conflict: int
    clock_hz: int
    stop: Stop
    loaded: bool
    pwm: tuple[tuple[int, ...], ...]

    def pin(self, pin: int) -> Drive:
        if self.gpio_conflict & (1 << pin):
            return Drive.CONFLICT
        if self.gpio_floating & (1 << pin):
            return Drive.FLOATING
        return Drive.HIGH if self.gpio_in & (1 << pin) else Drive.LOW


class SimulationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PioStateMachine:
    enabled: bool
    pc: int
    x: int
    y: int
    isr: int
    osr: int
    in_count: int
    out_count: int
    clkdiv: int
    execctrl: int
    shiftctrl: int
    pinctrl: int
    delay: int
    stalled: int
    exec_pending: bool
    tx: tuple[int, ...]
    rx: tuple[int, ...]


@dataclass(frozen=True)
class PioSnapshot:
    irq: int
    fdebug: int
    out: int
    oe: int
    instructions: tuple[int, ...]
    sm: tuple[PioStateMachine, ...]


@dataclass(frozen=True)
class DmaChannel:
    read_addr: int
    write_addr: int
    remaining: int
    reload: int
    ctrl: int
    waiting: bool

    @property
    def busy(self) -> bool:
        return bool(self.ctrl & (1 << 24))


@dataclass(frozen=True)
class DmaSnapshot:
    intr: int
    inte: tuple[int, ...]
    intf: tuple[int, ...]
    ints: tuple[int, ...]
    timers: tuple[int, ...]
    channels: tuple[DmaChannel, ...]


@dataclass(frozen=True)
class CoreSnapshot:
    instructions: int
    launched: bool
    sleeping: int
    registers: tuple[int, ...]
    xpsr: int
    msp: int
    psp: int
    control: int
    primask: int
    nvic_enable: int
    nvic_pending: int
    irq_levels: int
    vtor: int
    scr: int
    systick_ctrl: int
    systick_load: int
    systick_value: int
    event_flag: bool
    fifo_status: int
    rx: tuple[int, ...]


@dataclass(frozen=True)
class CoresSnapshot:
    core: tuple[CoreSnapshot, ...]
    spinlocks: int
    spinlock_owner: tuple[int, ...]
    boot_stage: int
    proc1_off: bool
    next_core: int
    active_core: int


class Engine:
    def __init__(self, clock_hz: int = 1_000_000):
        if lib.sim_abi_version() != 1:
            raise SimulationError("Native ABI version mismatch; rebuild the package")
        if not 1000 <= clock_hz <= 133_000_000:
            raise ValueError("clock_hz must be between 1,000 and 133,000,000")
        handle = lib.sim_create(clock_hz)
        if handle == ffi.NULL:
            raise SimulationError("Could not create the native Cortex-M0 engine")
        self._handle = ffi.gc(handle, lib.sim_destroy)

    @property
    def handle(self):
        if self._handle is None:
            raise SimulationError("Engine has been closed")
        return self._handle

    def close(self):
        if self._handle is not None:
            ffi.release(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _check(self, result):
        if result < 0:
            error = ffi.string(lib.sim_error(self.handle)).decode("utf-8", "replace")
            raise SimulationError(error or "Invalid operation, address, or queue capacity exceeded")
        return result

    def load(self, image: bytes | str | Path, *, startup: str = "direct", bootrom=None):
        if startup not in ("direct", "sdk", "rom"):
            raise ValueError("startup must be direct, sdk or rom")
        path = Path(image) if isinstance(image, (str, Path)) else None
        if path and path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Firmware image exceeds 64 MiB input limit')
        data = path.read_bytes() if path else image
        if not isinstance(data, bytes):
            raise TypeError("image must be bytes or a filesystem path")
        if startup == "rom":
            from .images import bootrom as get_rom, elf_flash
            rom = get_rom(bootrom)
            if data.startswith(b'\x7fELF'):
                data = elf_flash(data)
            loader = lib.sim_load_uf2 if data.startswith(b'UF2\n') or (path and path.suffix.lower() == '.uf2') else lib.sim_load_flash
            self._check(loader(self.handle, data, len(data), rom, len(rom)))
        else:
            loader = lib.sim_load_elf_sdk if startup == "sdk" else lib.sim_load_elf
            self._check(loader(self.handle, data, len(data)))

    def reset(self):
        self._check(lib.sim_reset(self.handle))

    def flash_image(self) -> bytes:
        return b''.join(self.peek(0x10000000 + offset, 65536) for offset in range(0, 2 * 1024 * 1024, 65536))

    def snapshot(self) -> Snapshot:
        out = ffi.new("sim_snapshot_t *")
        self._check(lib.sim_snapshot(self.handle, out))
        return Snapshot(
            int(out.time_ns), int(out.instructions), int(out.dropped_events),
            tuple(out.registers), int(out.xpsr), int(out.gpio_out), int(out.gpio_oe),
            int(out.gpio_in), int(out.gpio_floating), int(out.gpio_conflict),
            int(out.clock_hz), Stop(out.stop_reason), bool(out.loaded),
            tuple(tuple(row) for row in out.pwm),
        )

    def run(self, deadline_ns: int | None = None, budget: int = 10_000) -> Stop:
        if not 0 <= budget <= 1_000_000:
            raise ValueError("Instruction budget must be between 0 and 1,000,000")
        deadline = (1 << 64) - 1 if deadline_ns is None else deadline_ns
        if not 0 <= deadline < 1 << 64:
            raise ValueError("Deadline must fit in an unsigned 64-bit timestamp")
        reason = Stop(lib.sim_run_until(self.handle, deadline, budget))
        if reason == Stop.FAULT:
            raise SimulationError(ffi.string(lib.sim_error(self.handle)).decode())
        return reason

    def step(self) -> Stop:
        reason = Stop(lib.sim_step(self.handle))
        if reason == Stop.FAULT:
            raise SimulationError(ffi.string(lib.sim_error(self.handle)).decode())
        return reason

    def pio(self, block: int = 0) -> PioSnapshot:
        if block not in (0, 1):
            raise ValueError("PIO block must be 0 or 1")
        out = ffi.new("sim_pio_snapshot_t *")
        self._check(lib.sim_pio_snapshot(self.handle, block, out))
        machines = tuple(PioStateMachine(
            bool(sm.enabled), int(sm.pc), int(sm.x), int(sm.y), int(sm.isr), int(sm.osr),
            int(sm.in_count), int(sm.out_count), int(sm.clkdiv), int(sm.execctrl),
            int(sm.shiftctrl), int(sm.pinctrl), int(sm.delay), int(sm.stalled), bool(sm.exec_pending),
            tuple(sm.tx)[:sm.tx_count], tuple(sm.rx)[:sm.rx_count],
        ) for sm in out.sm)
        return PioSnapshot(int(out.irq), int(out.fdebug), int(out.out), int(out.oe),
                           tuple(out.instructions), machines)

    def dma(self) -> DmaSnapshot:
        out = ffi.new("sim_dma_snapshot_t *")
        self._check(lib.sim_dma_snapshot(self.handle, out))
        channels = tuple(DmaChannel(int(c.read_addr), int(c.write_addr), int(c.remaining),
                                    int(c.reload), int(c.ctrl), bool(c.waiting)) for c in out.channels)
        return DmaSnapshot(int(out.intr), tuple(out.inte), tuple(out.intf), tuple(out.ints),
                           tuple(out.timers), channels)

    def cores(self) -> CoresSnapshot:
        out = ffi.new("sim_cores_snapshot_t *")
        self._check(lib.sim_cores_snapshot(self.handle, out))
        cores = tuple(CoreSnapshot(
            int(c.instructions), bool(c.launched), int(c.sleeping), tuple(c.registers),
            int(c.xpsr), int(c.msp), int(c.psp), int(c.control), int(c.primask),
            int(c.nvic_enable), int(c.nvic_pending), int(c.irq_levels), int(c.vtor), int(c.scr),
            int(c.systick_ctrl), int(c.systick_load), int(c.systick_value), bool(c.event_flag),
            int(c.fifo_status), tuple(c.rx)[:c.rx_count],
        ) for c in out.core)
        return CoresSnapshot(cores, int(out.spinlocks), tuple(out.spinlock_owner), int(out.boot_stage),
                             bool(out.proc1_off), int(out.next_core), int(out.active_core))

    def peek(self, address: int, length: int = 4) -> bytes:
        if not 0 <= address <= 0xffffffff or not 1 <= length <= 65536:
            raise ValueError("Invalid memory address or length")
        buf = ffi.new("uint8_t[]", length)
        if lib.sim_peek_memory(self.handle, address, buf, length):
            raise SimulationError(f"Memory unavailable at 0x{address:08x} ({length} bytes)")
        return bytes(ffi.buffer(buf, length))

    def breakpoint(self, address: int, enabled: bool = True):
        if not 0 <= address <= 0xffffffff:
            raise ValueError("Invalid breakpoint address")
        self._check(lib.sim_breakpoint(self.handle, address, enabled))

    def connect_gpio(self, pin: int, net: int):
        self._check(lib.sim_connect_gpio(self.handle, pin, net))

    def device_request(self) -> DeviceRequest | None:
        out = ffi.new("sim_device_request_t *")
        if self._check(lib.sim_get_device_request(self.handle, out)) == 0:
            return None
        return DeviceRequest(int(out.id), int(out.time_ns), out.bus, out.controller,
                             out.target, out.value, out.flags)

    def complete_device(self, request: DeviceRequest, value: int, acknowledged: bool):
        self._check(lib.sim_complete_device_request(self.handle, request.id, value, acknowledged))

    def gpio(self, pin: int, drive: Drive, time_ns: int | None = None):
        when = self.snapshot().time_ns if time_ns is None else time_ns
        self._check(lib.sim_schedule_gpio(self.handle, when, pin, int(drive)))

    def uart(self, data: bytes, port: int = 0, time_ns: int | None = None):
        when = self.snapshot().time_ns if time_ns is None else time_ns
        for byte in data:
            self._check(lib.sim_schedule_uart(self.handle, when, port, byte))

    def adc(self, channel: int, value: int, time_ns: int | None = None):
        when = self.snapshot().time_ns if time_ns is None else time_ns
        self._check(lib.sim_schedule_adc(self.handle, when, channel, value))

    def events(self) -> list[Event]:
        buf = ffi.new("sim_event_t[]", 8192)
        count = lib.sim_drain_events(self.handle, buf, 8192)
        return [Event(int(e.time_ns), int(e.sequence), e.kind, e.source, e.value) for e in buf[0:count]]
