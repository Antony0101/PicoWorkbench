#include "sim_internal.h"
#include <string.h>

static uint32_t uart_raw(uart_t *u) {
    return (u->cr & 1) ? (u->tx_irq ? 0x20u : 0) | (u->count ? 0x10u : 0) : 0;
}

static uint32_t i2c_raw(i2c_t *b) {
    return b->raw | (b->enabled ? 0x10u | (b->count > b->rx_tl ? 4u : 0) : 0);
}

static uint32_t spi_raw(spi_t *b) {
    return (b->cr1 & 2) ? 8u | (b->count >= 4 ? 4u : 0) : 0;
}

void sim_peripherals_advance(sim_t *s, uint64_t cycles) {
    uint32_t output = 0;
    for (unsigned i = 0; i < 8; i++) {
        pwm_t *p = &s->pwm[i];
        if (p->csr & 1) {
            p->phase += cycles * 16;
            uint64_t steps = p->phase / p->div;
            p->phase %= p->div;
            uint64_t counter = p->ctr + steps;
            if (counter > p->top) s->pwm_intr |= 1u << i;
            p->ctr = (uint32_t)(counter % (p->top + 1));
        }
        unsigned a = (p->ctr < (p->cc & 0xffff)) ^ !!(p->csr & 4);
        unsigned b = (p->ctr < (p->cc >> 16)) ^ !!(p->csr & 8);
        output |= (a | (b << 1)) << (2 * i);
    }
    output = (output | (output << 16)) & PIN_MASK;
    if (output != s->pwm_output) { s->pwm_output = output; sim_gpio_resolve(s); }
}

static uint32_t adc_raw(sim_t *s) {
    unsigned threshold = (s->adc_fcs >> 24) & 15;
    return s->adc_count >= threshold;
}

uint32_t sim_peripherals_read(sim_t *s, uint32_t a, bool peek, bool *available) {
    *available = true;
    uint32_t base = a & ~0x3fffu, off = a & 0xfffu;
    if (base == 0x40054000u) {
        uint64_t us = s->now / 1000;
        switch (off) {
        case 0x08: return peek ? (uint32_t)(us >> 32) : s->latched_high;
        case 0x0c:
            if (!peek) s->latched_high = (uint32_t)(us >> 32);
            return (uint32_t)us;
        case 0x10: case 0x14: case 0x18: case 0x1c: return s->alarm[(off - 0x10) / 4];
        case 0x20: return s->armed;
        case 0x24: return (uint32_t)(us >> 32);
        case 0x28: return (uint32_t)us;
        case 0x2c: case 0x30: return 0;
        case 0x34: return s->intr;
        case 0x38: return s->inte;
        case 0x3c: return s->intf;
        case 0x40: return (s->intr & s->inte) | s->intf;
        default: break;
        }
    } else if (base == 0x40034000u || base == 0x40038000u) {
        uart_t *u = &s->uart[base == 0x40038000u];
        switch (off) {
        case 0x00: {
            uint32_t value = u->count ? u->rx[u->head] : 0;
            if (u->count && !peek) { u->head = (u->head + 1) % 256; u->count--; }
            return value;
        }
        case 0x18: return 0x80 | (u->count ? 0 : 0x10); /* instant TX, RXFE */
        case 0x24: return u->ibrd;
        case 0x28: return u->fbrd;
        case 0x2c: return u->lcr;
        case 0x30: return u->cr;
        case 0x38: return u->imsc;
        case 0x3c: return uart_raw(u);
        case 0x40: return uart_raw(u) & u->imsc;
        case 0x44: return 0;
        case 0x48: return u->dmacr; /* UART DREQ transfers remain unsupported. */
        default: break;
        }
    } else if (base == 0x40050000u) {
        if (off < 0xa0) {
            pwm_t *p = &s->pwm[off / 20];
            switch (off % 20) {
            case 0: return p->csr;
            case 4: return p->div;
            case 8: return p->ctr;
            case 12: return p->cc;
            case 16: return p->top;
            }
        }
        switch (off) {
        case 0xa0: { uint32_t en = 0; for (unsigned i = 0; i < 8; i++) en |= (s->pwm[i].csr & 1) << i; return en; }
        case 0xa4: return s->pwm_intr;
        case 0xa8: return s->pwm_inte;
        case 0xac: return s->pwm_intf;
        case 0xb0: return (s->pwm_intr & s->pwm_inte) | s->pwm_intf;
        default: break;
        }
    } else if (base == 0x4004c000u) {
        switch (off) {
        case 0: return s->adc_cs | (s->adc_due ? 0 : 1u << 8);
        case 4: return s->adc_result;
        case 8: return s->adc_fcs | (!s->adc_count ? 1u << 8 : 0) |
            (s->adc_count == 4 ? 1u << 9 : 0) | (s->adc_count << 16);
        case 12: {
            uint32_t value = s->adc_count ? s->adc_fifo[s->adc_head] : 0;
            if (!peek) {
                if (s->adc_count) { s->adc_head = (s->adc_head + 1) % 4; s->adc_count--; }
                else s->adc_fcs |= 1u << 10;
            }
            return value;
        }
        case 16: return s->adc_div;
        case 20: return adc_raw(s);
        case 24: return s->adc_inte;
        case 28: return s->adc_intf;
        case 32: return (adc_raw(s) & s->adc_inte) | s->adc_intf;
        default: break;
        }
    } else if (base == 0x40044000u || base == 0x40048000u) {
        i2c_t *b = &s->i2c[base == 0x40048000u];
        switch (off) {
        case 0x00: return b->con;
        case 0x04: return b->tar;
        case 0x10: {
            uint32_t value = b->count ? b->rx[b->head] : 0;
            if (b->count && !peek) { b->head = (b->head + 1) % 16; b->count--; }
            return value;
        }
        case 0x14: case 0x18: case 0x1c: case 0x20: return b->timings[(off - 0x14) / 4];
        case 0x2c: return i2c_raw(b) & b->mask;
        case 0x30: return b->mask;
        case 0x34: return i2c_raw(b);
        case 0x38: return b->rx_tl;
        case 0x3c: return b->tx_tl;
        case 0x40: { uint32_t v = i2c_raw(b); if (!peek) { b->raw = 0; b->abort_source = 0; } return !!v; }
        case 0x54: { uint32_t v = b->raw & 0x40; if (!peek) { b->raw &= ~0x40u; b->abort_source = 0; } return !!v; }
        case 0x60: { uint32_t v = b->raw & 0x200; if (!peek) b->raw &= ~0x200u; return !!v; }
        case 0x6c: case 0x9c: return b->enabled;
        case 0x70: return 6u | (b->count ? 8u : 0) | (b->count == 16 ? 16u : 0);
        case 0x74: return 0;
        case 0x78: return b->count;
        case 0x7c: return b->sda_hold;
        case 0x80: return b->abort_source;
        case 0x88: return b->dma_cr;
        case 0xa0: return b->fs_spklen;
        default: break;
        }
    } else if (base == 0x4003c000u || base == 0x40040000u) {
        spi_t *b = &s->spi[base == 0x40040000u];
        switch (off) {
        case 0: return b->cr0;
        case 4: return b->cr1;
        case 8: {
            uint32_t value = b->count ? b->rx[b->head] : 0;
            if (b->count && !peek) { b->head = (b->head + 1) % 8; b->count--; }
            return value;
        }
        case 0x0c: return 3u | (b->count ? 4u : 0) | (b->count == 8 ? 8u : 0);
        case 0x10: return b->cpsr;
        case 0x14: return b->mask;
        case 0x18: return spi_raw(b);
        case 0x1c: return spi_raw(b) & b->mask;
        case 0x20: return 0;
        case 0x24: return b->dmacr;
        default: break;
        }

    }
    *available = false;
    return 0;
}

bool sim_peripherals_write(sim_t *s, uint32_t a, uint32_t value) {
    uint32_t base = a & ~0x3fffu, off = a & 0xfffu, alias = (a >> 12) & 3;
    if (base == 0x40054000u) {
        if (off >= 0x10 && off <= 0x1c) {
            unsigned i = (off - 0x10) / 4;
            s->alarm[i] = atomic_value(s->alarm[i], value, alias);
            uint64_t now = s->now / 1000;
            s->alarm_time[i] = now + (uint32_t)(s->alarm[i] - (uint32_t)now);
            s->armed |= 1u << i; return true;
        }
        if (off == 0x20) { s->armed &= ~value; return true; }
        if (off == 0x34) { s->intr &= ~value; return true; }
        if (off == 0x38) { s->inte = atomic_value(s->inte, value, alias) & 15; return true; }
        if (off == 0x3c) { s->intf = atomic_value(s->intf, value, alias) & 15; return true; }
        if ((off == 0x2c || off == 0x30) && value == 0) return true;
    } else if (base == 0x40034000u || base == 0x40038000u) {
        unsigned index = base == 0x40038000u;
        uart_t *u = &s->uart[index];
        switch (off) {
        case 0x00:
            if ((u->cr & 0x101) != 0x101) { sim_fail(s, "UART%u TX while disabled", index); return true; }
            u->tx_irq = true;
            sim_emit(s, SIM_EVENT_UART, index, value & 255); return true;
        case 0x24: u->ibrd = atomic_value(u->ibrd, value, alias) & 0xffff; return true;
        case 0x28: u->fbrd = atomic_value(u->fbrd, value, alias) & 63; return true;
        case 0x2c: u->lcr = atomic_value(u->lcr, value, alias) & 255; return true;
        case 0x30: u->cr = atomic_value(u->cr, value, alias) & 0xffff; return true;
        case 0x38:
            u->imsc = atomic_value(u->imsc, value, alias) & 0x7ff; return true;
        case 0x44: if (value & 0x20) u->tx_irq = false; return true;
        case 0x48: u->dmacr = atomic_value(u->dmacr, value, alias) & 7; return true;
        default: break;
        }
    } else if (base == 0x40050000u) {
        if (off < 0xa0) {
            pwm_t *p = &s->pwm[off / 20];
            uint32_t v;
            switch (off % 20) {
            case 0:
                v = atomic_value(p->csr, value, alias);
                if (v & ~13u) break;
                p->csr = v; sim_peripherals_advance(s, 0); return true;
            case 4:
                v = atomic_value(p->div, value, alias) & 0xfff;
                if (v < 16) break;
                p->div = v; p->phase = 0; return true;
            case 8: p->ctr = atomic_value(p->ctr, value, alias) & 0xffff; sim_peripherals_advance(s, 0); return true;
            case 12: p->cc = atomic_value(p->cc, value, alias); sim_peripherals_advance(s, 0); return true;
            case 16: p->top = atomic_value(p->top, value, alias) & 0xffff; return true;
            }
        } else if (off == 0xa0) {
            uint32_t old = 0;
            for (unsigned i = 0; i < 8; i++) old |= (s->pwm[i].csr & 1) << i;
            value = atomic_value(old, value, alias);
            for (unsigned i = 0; i < 8; i++) s->pwm[i].csr = (s->pwm[i].csr & ~1u) | ((value >> i) & 1);
            return true;
        } else if (off == 0xa4) { s->pwm_intr &= ~value; return true; }
        else if (off == 0xa8) { s->pwm_inte = atomic_value(s->pwm_inte, value, alias) & 255; return true; }
        else if (off == 0xac) { s->pwm_intf = atomic_value(s->pwm_intf, value, alias) & 255; return true; }
    } else if (base == 0x4004c000u) {
        switch (off) {
        case 0: {
            uint32_t v = atomic_value(s->adc_cs, value, alias);
            if ((v & ~0x700fu) || ((v >> 12) & 7) > 4) break;
            s->adc_cs = v & ~4u;
            if (!(v & 1)) s->adc_due = 0;
            else if ((v & 12) && !s->adc_due) s->adc_due = s->now + 2000;
            return true;
        }
        case 8:
            s->adc_fcs = (value & 0x0f00000fu) | (s->adc_fcs & (3u << 10) & ~value); return true;
        case 16: s->adc_div = atomic_value(s->adc_div, value, alias) & 0xffffffu; return true;
        case 24: s->adc_inte = value & 1; return true;
        case 28: s->adc_intf = value & 1; return true;
        default: break;
        }
    } else if (base == 0x40044000u || base == 0x40048000u) {
        unsigned index = base == 0x40048000u;
        i2c_t *b = &s->i2c[index];
        switch (off) {
        case 0:
            value = atomic_value(b->con, value, alias);
            if ((value & ~0x167u) || !(value & 1) || ((value >> 1) & 3) == 0 || ((value >> 1) & 3) == 3) break;
            b->con = value; return true;
        case 4:
            value = atomic_value(b->tar, value, alias);
            if (value > 127) break;
            b->tar = value; return true;
        case 0x10:
            if (!b->enabled || (value & ~0x7ffu) || s->request_pending) break;
            s->request = (sim_device_request_t){++s->request_id, s->now, 0, index, b->tar,
                                               value & 255, (value >> 8) & 7};
            s->request_pending = 1; return true;
        case 0x14: case 0x18: case 0x1c: case 0x20:
            b->timings[(off - 0x14) / 4] = atomic_value(b->timings[(off - 0x14) / 4], value, alias) & 0xffff; return true;
        case 0x30:
            if (value & ~0x254u) break;
            b->mask = atomic_value(b->mask, value, alias); return true;
        case 0x38: b->rx_tl = value & 15; return true;
        case 0x3c: b->tx_tl = value & 15; return true;
        case 0x6c:
            value = atomic_value(b->enabled, value, alias);
            if (value > 1) break;
            b->enabled = value; return true;
        case 0x7c: b->sda_hold = atomic_value(b->sda_hold, value, alias) & 0xffffffu; return true;
        case 0x88: b->dma_cr = atomic_value(b->dma_cr, value, alias) & 3; return true;
        case 0xa0: b->fs_spklen = atomic_value(b->fs_spklen, value, alias) & 255; return true;
        default: break;
        }
    } else if (base == 0x4003c000u || base == 0x40040000u) {
        unsigned index = base == 0x40040000u;
        spi_t *b = &s->spi[index];
        switch (off) {
        case 0:
            b->cr0 = atomic_value(b->cr0, value, alias) & 0xffff; return true;
        case 4:
            value = atomic_value(b->cr1, value, alias);
            if (value & ~2u) break;
            b->cr1 = value; return true;
        case 8:
            if (!(b->cr1 & 2) || s->request_pending) break;
            if ((b->cr0 & 0x3f) != 7) { sim_fail(s, "SPI supports 8-bit Motorola frames only"); return true; }
            s->request = (sim_device_request_t){++s->request_id, s->now, 1, index, 0, value & 255, 0};
            s->request_pending = 1; return true;
        case 0x10:
            value = atomic_value(b->cpsr, value, alias);
            if (value < 2 || value > 254 || (value & 1)) break;
            b->cpsr = value; return true;
        case 0x14:
            value = atomic_value(b->mask, value, alias);
            if (value & ~12u) break;
            b->mask = value; return true;
        case 0x20: return true;
        case 0x24: b->dmacr = atomic_value(b->dmacr, value, alias) & 3; return true;
        default: break;
        }

    }
    return false;
}

int sim_peripherals_input(sim_t *s, const input_t *input) {
    input_t e = *input;
    if (e.kind == SIM_EVENT_GPIO) {
        s->drive[e.pin] = e.value;
        sim_gpio_resolve(s);
    } else if (e.kind == SIM_EVENT_UART) {
        if (s->system.resets & (1u << (22 + e.pin))) return 0;
        uart_t *u = &s->uart[e.pin];
        if (u->count == 256) { sim_fail(s, "UART%u RX queue overflow", e.pin); return -1; }
        u->rx[(u->head + u->count++) % 256] = (uint8_t)e.value;
    } else {
        s->adc_input[e.pin] = e.value;
    }
    return 0;
}

void sim_peripherals_service(sim_t *s) {
    sim_rtc_service(s);
    for (unsigned i = 0; i < 4; i++) {
        if ((s->armed & (1u << i)) && s->now / 1000 >= s->alarm_time[i]) {
            s->armed &= ~(1u << i);
            s->intr |= 1u << i;
        }
    }
    if (s->adc_due && s->now >= s->adc_due) {
        s->adc_result = s->adc_input[(s->adc_cs >> 12) & 7];
        if (s->adc_fcs & 1) {
            if (s->adc_count == 4) s->adc_fcs |= 1u << 11;
            else s->adc_fifo[(s->adc_head + s->adc_count++) % 4] = (uint16_t)(
                s->adc_fcs & 2 ? s->adc_result >> 4 : s->adc_result);
        }
        uint64_t period = ((uint64_t)s->adc_div + 256) * 1000000000u / (48000000u * 256u);
        if (period < 2000) period = 2000;
        s->adc_due = s->adc_cs & 8 ? s->now + period : 0;
    }
    uint32_t levels = (s->intr & s->inte) | s->intf | sim_pio_irq_levels(s) | sim_dma_irq_levels(s);
    levels |= sim_rtc_irq(s);
    if ((s->pwm_intr & s->pwm_inte) | s->pwm_intf) levels |= 1u << 4;
    if ((adc_raw(s) & s->adc_inte) | s->adc_intf) levels |= 1u << 22;
    for (unsigned i = 0; i < 2; i++)
        if (uart_raw(&s->uart[i]) & s->uart[i].imsc) levels |= 1u << (20 + i);
    for (unsigned i = 0; i < 2; i++) {
        if (i2c_raw(&s->i2c[i]) & s->i2c[i].mask) levels |= 1u << (23 + i);
        if (spi_raw(&s->spi[i]) & s->spi[i].mask) levels |= 1u << (18 + i);
    }
    for (unsigned core = 0; core < 2; core++) {
        uint32_t local = levels | sim_multicore_irq(s, core);
        for (unsigned i = 0; i < 4; i++)
            if ((sim_gpio_raw(s, i) & s->gpio_inte[core][i]) | s->gpio_intf[core][i]) local |= 1u << 13;
        sim_cpu_irq_levels(s, core, local);
    }
}

uint64_t sim_peripherals_next_event(sim_t *s, uint64_t next) {
    next = sim_rtc_next_event(s, next);
    if (s->adc_due && s->adc_due < next) next = s->adc_due;
    for (unsigned i = 0; i < 8; i++) {
        pwm_t *p = &s->pwm[i];
        if (!(p->csr & 1)) continue;
        uint64_t steps = p->top + 1 - p->ctr;
        unsigned a = p->cc & 0xffff, b = p->cc >> 16;
        if (a > p->ctr && a <= p->top && a - p->ctr < steps) steps = a - p->ctr;
        if (b > p->ctr && b <= p->top && b - p->ctr < steps) steps = b - p->ctr;
        uint64_t cycles = (steps * p->div - p->phase + 15) / 16;
        uint64_t delta = (cycles * 1000000000u + s->hz - 1) / s->hz;
        if (delta && delta < UINT64_MAX - s->now && s->now + delta < next) next = s->now + delta;
    }
    for (unsigned i = 0; i < 4; i++)
        if ((s->armed & (1u << i)) && s->alarm_time[i] * 1000 < next) next = s->alarm_time[i] * 1000;
    return next;
}

void sim_peripherals_reset(sim_t *s) {
    s->armed = s->intr = s->inte = s->intf = s->latched_high = 0;
    memset(s->uart, 0, sizeof(s->uart)); memset(s->alarm, 0, sizeof(s->alarm));
    memset(s->i2c, 0, sizeof(s->i2c)); memset(s->spi, 0, sizeof(s->spi));
    memset(s->pwm, 0, sizeof(s->pwm));
    for (unsigned i = 0; i < 8; i++) { s->pwm[i].div = 16; s->pwm[i].top = 65535; }
    s->pwm_output = s->pwm_intr = s->pwm_inte = s->pwm_intf = 0;
    s->adc_cs = s->adc_result = s->adc_fcs = s->adc_div = s->adc_inte = s->adc_intf = 0;
    s->adc_due = 0; s->adc_head = s->adc_count = 0;
    memset(s->adc_input, 0, sizeof(s->adc_input));
    s->request_pending = 0;
    for (unsigned i = 0; i < 2; i++) { s->i2c[i].con = 0x65; s->i2c[i].tar = 0x55; }
    for (unsigned i = 0; i < 2; i++) s->uart[i].cr = 0x300;
}

int sim_get_device_request(sim_t *s, sim_device_request_t *out) {
    if (!s || !out) return -1;
    if (!s->request_pending) return 0;
    *out = s->request; return 1;
}

int sim_complete_device_request(sim_t *s, uint64_t id, uint32_t value, int ack) {
    if (!s || !s->request_pending || s->request.id != id || value > 255) return -1;
    unsigned index = s->request.controller;
    if (s->request.bus == 0) {
        i2c_t *b = &s->i2c[index];
        if (!ack) { b->raw |= 0x240; b->abort_source = 1; b->head = b->count = 0; }
        else if (s->request.flags & 1) {
            if (b->count == 16) return sim_fail(s, "I2C RX FIFO overflow");
            b->rx[(b->head + b->count++) % 16] = (uint8_t)value;
        }
        if (s->request.flags & 2) b->raw |= 0x200;
    } else {
        spi_t *b = &s->spi[index];
        if (!ack) return sim_fail(s, "SPI transfer has no selected external device");
        if (b->count == 8) return sim_fail(s, "SPI RX FIFO overflow");
        b->rx[(b->head + b->count++) % 8] = (uint8_t)value;
    }
    sim_emit(s, s->request.bus ? 5 : 4, index, s->request.value | (value << 8) |
         (s->request.flags << 16) | (ack ? 1u << 24 : 0));
    s->request_pending = 0; s->reason = SIM_BUDGET;
    sim_scheduler_service(s); return 0;
}

/* RESETS resets chip registers, while externally scheduled analog inputs remain. */
void sim_peripherals_reset_mask(sim_t *s, uint32_t mask) {
    if (mask & 1) {
        s->adc_cs = s->adc_result = s->adc_fcs = s->adc_div = s->adc_inte = s->adc_intf = 0;
        s->adc_due = 0; s->adc_head = s->adc_count = 0;
    }
    for (unsigned i = 0; i < 2; i++) {
        if (mask & (1u << (3 + i))) {
            memset(&s->i2c[i], 0, sizeof(i2c_t)); s->i2c[i].con = 0x65; s->i2c[i].tar = 0x55;
        }
        if (mask & (1u << (16 + i))) memset(&s->spi[i], 0, sizeof(spi_t));
        if (mask & (1u << (22 + i))) { memset(&s->uart[i], 0, sizeof(uart_t)); s->uart[i].cr = 0x300; }
    }
    if (mask & (1u << 14)) {
        memset(s->pwm, 0, sizeof(s->pwm));
        for (unsigned i = 0; i < 8; i++) { s->pwm[i].div = 16; s->pwm[i].top = 65535; }
        s->pwm_output = s->pwm_intr = s->pwm_inte = s->pwm_intf = 0;
    }
    if (mask & (1u << 21)) {
        s->armed = s->intr = s->inte = s->intf = s->latched_high = 0;
        memset(s->alarm, 0, sizeof(s->alarm));
    }
}
