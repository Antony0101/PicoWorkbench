/* Functional RP2040 DMA: one arbitrated beat per root clock, no bus pipeline. */
#include "sim_internal.h"
#include <string.h>

#define CHANNELS 0xfffu
#define BUSY (1u << 24)
#define READ_ERROR (1u << 30)
#define WRITE_ERROR (1u << 29)
#define QUIET (1u << 21)

static uint32_t control(dma_channel_t *c) {
    return c->ctrl | (c->busy ? BUSY : 0) | c->errors | (c->errors ? 1u << 31 : 0);
}

static unsigned request(dma_channel_t *c) { return (c->ctrl >> 15) & 63; }

static unsigned available(sim_t *s, dma_channel_t *c) {
    unsigned dreq = request(c);
    if (dreq < 16) return sim_pio_dreq(s, dreq);
    if (dreq == 36) {
        unsigned threshold = (s->adc_fcs >> 24) & 15;
        return (s->adc_fcs & 8) && s->adc_count >= threshold ? s->adc_count : 0;
    }
    if (dreq >= 59 && dreq <= 62) return c->credits;
    return dreq == 63;
}

static bool valid_config(sim_t *s, unsigned index) {
    dma_channel_t *c = &s->dma.channel[index];
    if (((c->ctrl >> 2) & 3) == 3 || (c->ctrl & (1u << 23))) {
        sim_fail(s, "DMA%u: reserved transfer size or checksum sniffing is not implemented", index);
        return false;
    }
    unsigned dreq = request(c);
    if (!(dreq < 16 || dreq == 36 || dreq >= 59)) {
        sim_fail(s, "DMA%u DREQ %u is not implemented (supported: PIO, ADC, DMA timers, permanent)", index, dreq);
        return false;
    }
    if (((c->ctrl >> 11) & 15) >= 12) {
        sim_fail(s, "DMA%u CHAIN_TO selects an unavailable channel", index);
        return false;
    }
    return true;
}

static void trigger(sim_t *s, unsigned index, uint32_t value) {
    if (index >= 12) { sim_fail(s, "DMA chain selects an unavailable channel"); return; }
    dma_channel_t *c = &s->dma.channel[index];
    if (!value) {
        if (c->ctrl & QUIET) s->dma.intr |= 1u << index;
        return;
    }
    if (!(c->ctrl & 1) || !valid_config(s, index) || c->busy) return;
    c->remaining = c->reload;
    c->credits = 0;
    c->busy = c->remaining != 0;
}

void sim_dma_reset(sim_t *s) { memset(&s->dma, 0, sizeof(s->dma)); s->dma.last = 11; }

uint32_t sim_dma_irq_levels(sim_t *s) {
    uint32_t levels = 0;
    for (unsigned line = 0; line < 2; line++)
        if ((s->dma.intr & s->dma.inte[line]) | s->dma.intf[line]) levels |= 1u << (11 + line);
    return levels;
}

static uint32_t increment(uint32_t address, unsigned size, uint32_t ctrl, bool write) {
    if (!(ctrl & (1u << (write ? 5 : 4)))) return address;
    unsigned ring = (ctrl >> 6) & 15;
    if (ring && !!(ctrl & (1u << 10)) == write) {
        uint32_t mask = (1u << ring) - 1;
        return (address & ~mask) | ((address + size) & mask);
    }
    return address + size;
}

static void bus_error(sim_t *s, unsigned index, uint32_t error) {
    dma_channel_t *c = &s->dma.channel[index];
    c->errors |= error; c->busy = false;
    s->dma.intr |= 1u << index;
}

static int choose(sim_t *s) {
    dma_t *d = &s->dma;
    uint32_t high = 0, low = 0;
    for (unsigned i = 0; i < 12; i++) {
        dma_channel_t *c = &d->channel[i];
        if (c->busy && (c->ctrl & 1) && available(s, c)) {
            if (c->ctrl & 2) high |= 1u << i; else low |= 1u << i;
        }
    }
    uint32_t candidates = high & ~d->served_high;
    if (!candidates) {
        d->served_high = 0;
        candidates = low ? low : high;
    }
    for (unsigned n = 1; n <= 12; n++) {
        unsigned i = (d->last + n) % 12;
        if (!(candidates & (1u << i))) continue;
        d->last = i;
        if (high & (1u << i)) d->served_high |= 1u << i;
        return (int)i;
    }
    return -1;
}

void sim_dma_advance(sim_t *s, uint64_t cycles) {
    if (!cycles || s->reason == SIM_FAULT) return;
    dma_t *d = &s->dma;
    for (unsigned t = 0; t < 4; t++) {
        unsigned x = d->timer[t] >> 16, y = d->timer[t] & 65535;
        if (!x || !y) continue;
        uint64_t part = d->phase[t] + (cycles % y) * x;
        uint64_t pulses = cycles / y > 63 ? 63 : (cycles / y) * x + part / y;
        d->phase[t] = part % y;
        for (unsigned i = 0; i < 12; i++) {
            dma_channel_t *c = &d->channel[i];
            if (!c->busy || request(c) != 59 + t) continue;
            uint64_t credits = c->credits + pulses;
            c->credits = credits > 63 ? 63 : (uint32_t)credits;
        }
    }
    int index = choose(s);
    if (index < 0) return;
    dma_channel_t *c = &d->channel[index];
    unsigned size = 1u << ((c->ctrl >> 2) & 3);
    uint32_t value;
    if (!sim_bus_dma_read(s, c->read, size, &value)) { bus_error(s, index, READ_ERROR); return; }
    if (c->ctrl & (1u << 22)) {
        uint32_t swapped = 0;
        for (unsigned i = 0; i < size; i++) swapped |= ((value >> (8 * i)) & 255) << (8 * (size - i - 1));
        value = swapped;
    }
    if (!sim_bus_dma_write(s, c->write, size, value)) { bus_error(s, index, WRITE_ERROR); return; }
    if (s->reason == SIM_FAULT || !c->busy) return;
    c->read = increment(c->read, size, c->ctrl, false);
    c->write = increment(c->write, size, c->ctrl, true);
    unsigned dreq = request(c);
    if (dreq >= 59 && dreq <= 62 && c->credits) c->credits--;
    if (--c->remaining == 0) {
        c->busy = false;
        if (!(c->ctrl & QUIET)) d->intr |= 1u << index;
        unsigned chain = (c->ctrl >> 11) & 15;
        if (chain != (unsigned)index) trigger(s, chain, 1);
    }
}

uint64_t sim_dma_next_event(sim_t *s, uint64_t next) {
    for (unsigned i = 0; i < 12; i++) {
        dma_channel_t *c = &s->dma.channel[i];
        if (!c->busy || !(c->ctrl & 1)) continue;
        uint64_t cycles = 1;
        if (!available(s, c)) {
            unsigned dreq = request(c);
            if (dreq < 59 || dreq > 62) continue;
            unsigned t = dreq - 59, x = s->dma.timer[t] >> 16, y = s->dma.timer[t] & 65535;
            if (!x || !y) continue;
            cycles = (y - s->dma.phase[t] + x - 1) / x;
        }
        uint64_t delta = (cycles * 1000000000u + s->hz - 1) / s->hz;
        if (delta <= UINT64_MAX - s->now && s->now + delta < next) next = s->now + delta;
    }
    return next;
}

/* Underlying channel register: READ, WRITE, COUNT, CTRL. */
static unsigned channel_register(unsigned offset) {
    static const unsigned order[16] = {0, 1, 2, 3, 3, 0, 1, 2, 3, 2, 0, 1, 3, 1, 2, 0};
    return order[offset / 4];
}

uint32_t sim_dma_read(sim_t *s, uint32_t address, bool *found) {
    *found = false;
    if ((address & ~0x3fffu) != 0x50000000u) return 0;
    unsigned off = address & 0xfff;
    dma_t *d = &s->dma;
    *found = true;
    if (off < 0x300) {
        dma_channel_t *c = &d->channel[off / 64];
        switch (channel_register(off % 64)) {
        case 0: return c->read;
        case 1: return c->write;
        case 2: return c->remaining;
        default: return control(c);
        }
    }
    if (off >= 0x800 && off < 0xb00 && (off % 64) <= 4) {
        dma_channel_t *c = &d->channel[(off - 0x800) / 64];
        unsigned credits = available(s, c);
        return off % 64 == 4 ? c->reload : (credits > 63 ? 63 : credits);
    }
    if (off == 0x400) return d->intr;
    if (off >= 0x420 && off <= 0x42c) return d->timer[(off - 0x420) / 4];
    if (off == 0x430 || off == 0x434 || off == 0x438 || off == 0x440 || off == 0x444) return 0;
    if (off == 0x448) return 12;
    if ((off >= 0x404 && off <= 0x40c) || (off >= 0x414 && off <= 0x41c)) {
        unsigned line = (off - 0x404) / 16;
        switch (off & 15) {
        case 4: return d->inte[line];
        case 8: return d->intf[line];
        default: return (d->intr & d->inte[line]) | d->intf[line];
        }
    }
    *found = false; return 0;
}

bool sim_dma_write(sim_t *s, uint32_t address, uint32_t value) {
    if ((address & ~0x3fffu) != 0x50000000u) return false;
    unsigned off = address & 0xfff, alias = (address >> 12) & 3;
    dma_t *d = &s->dma;
    if (off < 0x300) {
        unsigned i = off / 64, reg = channel_register(off % 64);
        dma_channel_t *c = &d->channel[i];
        switch (reg) {
        case 0: c->read = atomic_value(c->read, value, alias); break;
        case 1: c->write = atomic_value(c->write, value, alias); break;
        case 2: c->reload = atomic_value(c->reload, value, alias); break;
        case 3:
            c->ctrl = atomic_value(c->ctrl, value, alias) & 0x00ffffff;
            c->errors &= ~(value & (READ_ERROR | WRITE_ERROR));
            if (c->busy && (c->ctrl & 1) && !valid_config(s, i)) return true;
            break;
        }
        if (off % 16 == 12) trigger(s, i, value);
        return true;
    }
    if (off >= 0x800 && off < 0xb00 && off % 64 == 0) {
        d->channel[(off - 0x800) / 64].credits = 0; return true;
    }
    if (off == 0x400 || off == 0x40c || off == 0x41c) { d->intr &= ~(value & CHANNELS); return true; }
    if (off >= 0x420 && off <= 0x42c) {
        unsigned t = (off - 0x420) / 4;
        uint32_t v = atomic_value(d->timer[t], value, alias);
        if ((v >> 16) && (v >> 16) > (v & 65535)) {
            sim_fail(s, "DMA timer numerator must not exceed its denominator"); return true;
        }
        d->timer[t] = v; d->phase[t] = 0; return true;
    }
    if (off == 0x430) {
        for (unsigned i = 0; i < 12; i++) if (value & (1u << i)) trigger(s, i, 1);
        return true;
    }
    if (off == 0x444) {
        for (unsigned i = 0; i < 12; i++) if (value & (1u << i)) {
            d->channel[i].busy = false; d->channel[i].credits = 0;
        }
        return true;
    }
    if (off == 0x434 || off == 0x438) {
        if (value) sim_fail(s, "DMA checksum sniffing is not implemented");
        return true;
    }
    if (off == 0x404 || off == 0x408 || off == 0x414 || off == 0x418) {
        unsigned line = (off - 0x404) / 16;
        uint32_t *reg = (off & 15) == 4 ? &d->inte[line] : &d->intf[line];
        *reg = atomic_value(*reg, value, alias) & CHANNELS; return true;
    }
    return false;
}

int sim_dma_snapshot(sim_t *s, sim_dma_snapshot_t *out) {
    if (!s || !out) return -1;
    memset(out, 0, sizeof(*out));
    dma_t *d = &s->dma;
    out->intr = d->intr;
    for (unsigned line = 0; line < 2; line++) {
        out->inte[line] = d->inte[line]; out->intf[line] = d->intf[line];
        out->ints[line] = (d->intr & d->inte[line]) | d->intf[line];
    }
    for (unsigned t = 0; t < 4; t++) out->timers[t] = d->timer[t];
    for (unsigned i = 0; i < 12; i++) {
        dma_channel_t *c = &d->channel[i];
        out->channels[i] = (sim_dma_channel_snapshot_t){c->read, c->write, c->remaining,
            c->reload, control(c), c->busy && (c->ctrl & 1) && !available(s, c)};
    }
    return 0;
}
