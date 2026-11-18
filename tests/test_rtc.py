import struct

import pytest

from rp2040_sim.engine import SimulationError, Stop

RTC = 0x4005c000


def date(year, month, day):
    return year << 12 | month << 8 | day


def time(hour, minute, second, dow=0):
    return dow << 24 | hour << 16 | minute << 8 | second


def configure(thumb, year=2024, month=2, day=28, hour=23, minute=59, second=59, dow=3, extra=0):
    return thumb.write(RTC + 4, date(year, month, day)).write(RTC + 8, time(hour, minute, second, dow)).write(RTC + 12, 16).write(RTC + 12, 1 | extra)


@pytest.mark.parametrize('year,month,day,extra,expected', [
    (2024, 2, 28, 0, (2024, 2, 29)), (2023, 2, 28, 0, (2023, 3, 1)),
    (2100, 2, 28, 256, (2100, 3, 1)), (2024, 12, 31, 0, (2025, 1, 1)),
    (4095, 12, 31, 0, (0, 1, 1)),
])
def test_calendar_rollover_and_virtual_time(engine, thumb, year, month, day, extra, expected):
    configure(thumb, year, month, day, extra=extra).op(0xbf30)  # WFI
    engine.load(thumb.elf(loop=True))
    assert engine.run(1_100_000_000) == Stop.DEADLINE
    assert int.from_bytes(engine.peek(RTC + 24), 'little') == date(*expected)
    assert int.from_bytes(engine.peek(RTC + 28), 'little') == time(0, 0, 0, 4)
    assert int.from_bytes(engine.peek(RTC + 12), 'little') == 3 | extra
    engine.reset()
    assert engine.peek(RTC + 12) == bytes(4)


def test_calendar_latching_and_non_destructive_debug_peeks(engine, thumb):
    configure(thumb).write(0x40054010, 1_100_000).write(0x40054038, 1).write(0xe000e100, 1)
    thumb.op(0xb672).read(RTC + 28).op(0xbf30)  # PRIMASK: pending timer wakes WFI without exception entry.
    thumb.read(RTC + 24, 4).read(RTC + 28, 5).read(RTC + 24, 6)
    engine.load(thumb.elf())
    assert engine.run(1_050_000_000) == Stop.DEADLINE
    assert int.from_bytes(engine.peek(RTC + 24), 'little') == date(2024, 2, 29)
    assert engine.peek(RTC + 28) == struct.pack('<I', time(0, 0, 0, 4))
    assert engine.run(2_000_000_000) == Stop.BREAKPOINT
    registers = engine.snapshot().registers
    assert registers[4] == date(2024, 2, 28)
    assert registers[5] == time(0, 0, 0, 4)
    assert registers[6] == date(2024, 2, 29)


def test_alarm_second_match_wakes_wfi_and_gpio_route(engine, tmp_path):
    from test_interrupts import compile_source
    image = compile_source(tmp_path, '''
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 count;
void reset_handler(void); void rtc_irq(void);
__attribute__((section(".vectors"),used)) const u32 vectors[48] = {
 [0]=0x20042000, [1]=(u32)reset_handler, [16+25]=(u32)rtc_irq };
void rtc_irq(void) { count++; REG(0x4005f010)=1u<<28; }
void reset_handler(void) {
 REG(0x4005c004)=(2024u<<12)|(1u<<8)|1;
 REG(0x4005c008)=0; REG(0x4005c00c)=16; REG(0x4005c00c)=1;
 REG(0x4005c014)=(1u<<28)|1; REG(0x4005c010)=1u<<28;
 REG(0x4005c024)=1; REG(0xe000e100)=1u<<25;
 while (!count) __asm volatile("wfi");
 __asm volatile("bkpt 0"); for (;;) {}
}
''')
    engine.load(image)
    assert engine.run(2_000_000_000, 1000) == Stop.BREAKPOINT
    assert int.from_bytes(engine.peek(0x20000000), 'little') == 1
    assert 1_000_000_000 < engine.snapshot().time_ns < 1_001_000_000
    assert engine.peek(RTC + 32) == bytes(4)


def test_rtc_invalid_calendar_and_reset_gating(engine, thumb):
    engine.load(thumb.write(RTC + 12, 1).elf())
    with pytest.raises(SimulationError, match='valid loaded calendar'):
        engine.run(budget=20)
