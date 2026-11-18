/* Calendar RTC on deterministic virtual seconds, independent of host timezone. */
#include "sim_internal.h"
#include <string.h>

void sim_rtc_reset(sim_t *s) { memset(&s->rtc, 0, sizeof(s->rtc)); s->rtc.div = 0xffff; }

static bool matches(rtc_t *r) {
    if (!(r->alarm[0] & 0x10000000)) return false;
    const unsigned dshift[] = {0,8,12}, dmask[] = {31,15,4095};
    const unsigned tshift[] = {0,8,16,24}, tmask[] = {63,63,31,7};
    for (unsigned i = 0; i < 3; i++) if ((r->alarm[0] & (1u << (24 + i))) &&
        ((r->date >> dshift[i]) & dmask[i]) != ((r->alarm[0] >> dshift[i]) & dmask[i])) return false;
    for (unsigned i = 0; i < 4; i++) if ((r->alarm[1] & (1u << (28 + i))) &&
        ((r->time >> tshift[i]) & tmask[i]) != ((r->alarm[1] >> tshift[i]) & tmask[i])) return false;
    return true;
}

void sim_rtc_service(sim_t *s) {
    rtc_t *r = &s->rtc;
    if (!(r->ctrl & 1)) return;
    uint64_t seconds = (s->now - r->tick_ns) / 1000000000u;
    if (!seconds) return;
    r->tick_ns += seconds * 1000000000u;
    unsigned year = (r->date >> 12) & 4095, month = (r->date >> 8) & 15, day = r->date & 31;
    unsigned dow = (r->time >> 24) & 7, hour = (r->time >> 16) & 31, minute = (r->time >> 8) & 63, sec = r->time & 63;
    uint64_t total = seconds + hour * 3600 + minute * 60 + sec;
    uint64_t days = total / 86400;
    sec = total % 60; minute = total / 60 % 60; hour = total / 3600 % 24;
    dow = (dow + days) % 7;
    while (days--) {
        static const unsigned lengths[] = {31,28,31,30,31,30,31,31,30,31,30,31};
        unsigned maxday = lengths[month - 1] + (month == 2 && !(year & 3) && !(r->ctrl & 256));
        if (++day > maxday) { day = 1; if (++month > 12) { month = 1; year = (year + 1) & 4095; } }
    }
    r->date = year << 12 | month << 8 | day;
    r->time = dow << 24 | hour << 16 | minute << 8 | sec;
}

uint64_t sim_rtc_next_event(sim_t *s, uint64_t next) {
    if ((s->rtc.ctrl & 1) && (s->rtc.alarm[0] & 0x10000000) &&
        s->rtc.tick_ns <= UINT64_MAX - 1000000000u && s->rtc.tick_ns + 1000000000u < next)
        return s->rtc.tick_ns + 1000000000u;
    return next;
}

uint32_t sim_rtc_irq(sim_t *s) { return ((matches(&s->rtc) & s->rtc.inte) | s->rtc.intf) ? 1u << 25 : 0; }

uint32_t sim_rtc_read(sim_t *s, uint32_t a, bool peek, bool *found) {
    *found = false;
    if ((a & ~0x3fffu) != 0x4005c000u) return 0;
    rtc_t *r = &s->rtc;
    uint32_t off = a & 0xfff;
    *found = true;
    switch (off) {
    case 0: return r->div;
    case 4: case 8: return r->setup[off / 4 - 1];
    case 12: return r->ctrl | ((r->ctrl & 1) ? 2 : 0);
    case 16: return r->alarm[0] | (r->alarm[0] & 0x10000000 ? 0x20000000 : 0);
    case 20: return r->alarm[1];
    case 24: return peek ? r->date : r->latched_date;
    case 28: if (!peek) r->latched_date = r->date; return r->time;
    case 32: return matches(r);
    case 36: return r->inte;
    case 40: return r->intf;
    case 44: return (matches(r) & r->inte) | r->intf;
    default: *found = false; return 0;
    }
}

bool sim_rtc_write(sim_t *s, uint32_t a, uint32_t value) {
    if ((a & ~0x3fffu) != 0x4005c000u) return false;
    rtc_t *r = &s->rtc;
    uint32_t off = a & 0xfff, alias = (a >> 12) & 3;
    if (!off) { r->div = atomic_value(r->div, value, alias) & 0xffff; return true; }
    if (off == 4 || off == 8) { unsigned i = off / 4 - 1; r->setup[i] = atomic_value(r->setup[i], value, alias) & (i ? 0x071f3f3f : 0x00ffff1f); return true; }
    if (off == 12) {
        uint32_t v = atomic_value(r->ctrl, value, alias) & 0x111;
        if (v & 16) { r->date = r->latched_date = r->setup[0]; r->time = r->setup[1]; }
        if (v & 1) {
            unsigned month = (r->date >> 8) & 15, day = r->date & 31;
            if (!month || month > 12 || !day || day > 31 || ((r->time >> 16) & 31) > 23 ||
                ((r->time >> 8) & 63) > 59 || (r->time & 63) > 59 || ((r->time >> 24) & 7) > 6) {
                sim_fail(s, "RTC requires a valid loaded calendar before enabling"); return true;
            }
        }
        if ((v & 1) && !(r->ctrl & 1)) r->tick_ns = s->now;
        r->ctrl = v & ~16u; return true;
    }
    if (off == 16 || off == 20) { unsigned i = (off - 16) / 4; r->alarm[i] = atomic_value(r->alarm[i], value, alias) & (i ? 0xf71f3f3f : 0x17ffff1f); return true; }
    if (off == 36) { r->inte = atomic_value(r->inte, value, alias) & 1; return true; }
    if (off == 40) { r->intf = atomic_value(r->intf, value, alias) & 1; return true; }
    return false;
}
