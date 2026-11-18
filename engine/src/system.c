/* SDK startup control plane. Ready transitions are functional and immediate;
 * the configured simulation instruction clock remains the timing source. */
#include "sim_internal.h"
#include <string.h>

#define RESET_BITS 0x01ffffffu
static const uint32_t ctrl_mask[10] = {0x00131de0,0x00131de0,0x00131de0,0x00131de0,
    0x63,0xe1,0xce0,0x130ce0,0x130ce0,0x130ce0};
static const uint32_t div_mask[10] = {0xffffffff,0xffffffff,0xffffffff,0xffffffff,
    0x300,0xffffffff,0,0x300,0x300,0xffffffff};

static void reset_pll(sim_t *s, unsigned i) {
    s->system.pll[i][0] = 1; s->system.pll[i][1] = 0x2d;
    s->system.pll[i][2] = 0; s->system.pll[i][3] = 0x77000;
}

void sim_system_reset(sim_t *s) {
    memset(&s->system, 0, sizeof(s->system));
    /* Direct-load fixtures enter with peripherals released, not power-on reset. */
    for (unsigned i = 0; i < 10; i++) s->system.clock_div[i] = 256;
    s->system.tick = 0x200;
    s->system.rosc_ctrl = 0xfabaa0; s->system.random_state = 0x2040b2;
    s->system.wake_sleep[0] = s->system.wake_sleep[2] = UINT32_MAX;
    s->system.wake_sleep[1] = s->system.wake_sleep[3] = 0x7fff;
    reset_pll(s, 0); reset_pll(s, 1);
}

static bool pll_locked(sim_t *s, unsigned i) {
    uint32_t *p = s->system.pll[i];
    return s->system.xosc_enabled && !(s->system.resets & (1u << (12 + i))) &&
        !(p[1] & 0x21) && (p[0] & 63) && p[2] >= 16 && p[2] <= 320;
}

static bool pll_output(sim_t *s, unsigned i) {
    return pll_locked(s, i) && !(s->system.pll[i][1] & 8);
}

static bool clock_ready(sim_t *s, unsigned index) {
    uint32_t c = s->system.clock_ctrl[index];
    unsigned source = c & (index == 4 ? 3 : 1), aux = (c >> 5) & 7;
    if (index == 4) return source == 0 || (source == 2 && s->system.xosc_enabled) ||
        (source == 1 && aux == 0 && pll_output(s, 1));
    if (index == 5) {
        if (!source) return clock_ready(s, 4);
        return aux == 0 ? pll_output(s, 0) : aux == 1 ? pll_output(s, 1) :
            aux == 2 || (aux == 3 && s->system.xosc_enabled);
    }
    return true; /* non-glitchless SELECTED registers are fixed at one */
}

static int reset_bit(uint32_t a) {
    switch (a & ~0x3fffu) {
    case 0x4004c000: return 0;
    case 0x50000000: return 2;
    case 0x40044000: return 3;
    case 0x40048000: return 4;
    case 0x40014000: return 5;
    case 0x40018000: return 6;
    case 0x4001c000: return 8;
    case 0x40020000: return 9;
    case 0x50200000: return 10;
    case 0x50300000: return 11;
    case 0x40028000: return 12;
    case 0x4002c000: return 13;
    case 0x40050000: return 14;
    case 0x4003c000: return 16;
    case 0x40040000: return 17;
    case 0x40054000: return 21;
    case 0x4005c000: return 15;
    case 0x40034000: return 22;
    case 0x40038000: return 23;
    default: return -1;
    }
}

bool sim_system_held(sim_t *s, uint32_t address) {
    int bit = reset_bit(address);
    return bit >= 0 && (s->system.resets & (1u << bit));
}

static void apply_reset(sim_t *s, uint32_t mask) {
    sim_peripherals_reset_mask(s, mask);
    sim_flash_reset_mask(s, mask);
    if (mask & (1u << 15)) sim_rtc_reset(s);
    if (mask & 4) sim_dma_reset(s);
    for (unsigned i = 0; i < 2; i++) {
        if (mask & (1u << (10 + i))) sim_pio_reset_block(s, i);
        if (mask & (1u << (12 + i))) reset_pll(s, i);
    }
    if (mask & (1u << 5)) {
        for (unsigned i = 0; i < 30; i++) s->ctrl[i] = 31;
        memset(s->gpio_edges, 0, sizeof(s->gpio_edges));
        memset(s->gpio_inte, 0, sizeof(s->gpio_inte)); memset(s->gpio_intf, 0, sizeof(s->gpio_intf));
    }
    if (mask & (1u << 8)) for (unsigned i = 0; i < 30; i++) s->pads[i] = 0x56;
    if (mask & (1u << 24)) s->system.usb_sie_ctrl = 0;
    sim_gpio_resolve(s);
}

uint32_t sim_system_read(sim_t *s, uint32_t a, bool peek, bool *found) {
    uint32_t base = a & ~0x3fffu, off = a & 0xfff;
    system_t *p = &s->system;
    *found = true;
    if (base == 0x4000c000u) {
        if (off == 0) return p->resets;
        if (off == 4) return p->wdsel;
        if (off == 8) return RESET_BITS & ~p->resets;
    }
    if (base == 0x40024000u) {
        if (off == 0) return p->xosc_ctrl;
        if (off == 4) return (p->xosc_enabled ? 0x80001000u : 0) | p->xosc_badwrite;
        if (off == 12) return p->xosc_startup;
    }
    if ((base == 0x40028000u || base == 0x4002c000u) && off < 16) {
        unsigned i = base == 0x4002c000u;
        return p->pll[i][off / 4] | (!off && pll_locked(s, i) ? 1u << 31 : 0);
    }
    if (base == 0x40008000u) {
        if (off < 0x78) {
            unsigned i = off / 12;
            if (off % 12 == 0) return p->clock_ctrl[i];
            if (off % 12 == 4) return i == 6 ? 0 : p->clock_div[i];
            if (off % 12 == 8) return clock_ready(s, i) ? 1u << (p->clock_ctrl[i] & (i == 4 ? 3 : i == 5 ? 1 : 0)) : 0;
        }
        if (off == 0x78) return p->resus;
        if (off == 0x7c) return 0;
        if (off >= 0xa0 && off <= 0xac) return p->wake_sleep[(off - 0xa0) / 4];
        if (off == 0xb0) return p->wake_sleep[0];
        if (off == 0xb4) return p->wake_sleep[1];
    }
    if (base == 0x40058000u) {
        if (!off) return 0x07000000;
        if (off == 8) return 0;
        if (off >= 12 && off <= 40) return p->scratch[(off - 12) / 4];
        if (off == 44) return p->tick | (p->tick & 0x200 ? 0x400 : 0);
    }
    if (base == 0x40060000u) {
        if (!off) return p->rosc_ctrl;
        if (off == 0x18) return 0x80011000;
        if (off == 0x1c) {
            uint32_t bit = p->random_state & 1;
            if (!peek) p->random_state = (p->random_state >> 1) ^ (bit ? 0x80200003u : 0);
            return bit;
        }
        if (off == 0x20) return 0;
    }
    if (base == 0x40000000u) {
        if (!off) return 0x20002927;
        if (off == 4) return 1;
    }
    if (base == 0x4006c000u && !off) return 1;
    if (base == 0x50110000u && off == 0x4c) return p->usb_sie_ctrl;
    if (base == 0x40004000u) {
        if (off == 0 || off == 4) return 0;
        if (off == 8) return 0x10000000;
        if (off == 12 || off == 16) return p->sync_bypass[(off - 12) / 4];
    }
    *found = false; return 0;
}

bool sim_system_write(sim_t *s, uint32_t a, uint32_t value) {
    uint32_t base = a & ~0x3fffu, off = a & 0xfff, alias = (a >> 12) & 3;
    system_t *p = &s->system;
    if (base == 0x4000c000u) {
        if (off == 0) {
            uint32_t reset = atomic_value(p->resets, value, alias) & RESET_BITS;
            apply_reset(s, reset & ~p->resets); p->resets = reset; return true;
        }
        if (off == 4) {
            if (value) { sim_fail(s, "Watchdog reset selection is not implemented"); return true; }
            p->wdsel = 0; return true;
        }
    }
    if (base == 0x40024000u) {
        if (off == 0) {
            uint32_t v = atomic_value(p->xosc_ctrl, value, alias) & 0xffffff;
            if ((v & 0xfff) != 0xaa0) { p->xosc_badwrite = 1u << 24; v = (v & ~0xfffu) | (p->xosc_ctrl & 0xfff); }
            unsigned en = v >> 12;
            if (en != 0xfab && en != 0xd1e) p->xosc_badwrite = 1u << 24;
            p->xosc_enabled = en != 0xd1e;
            p->xosc_ctrl = v; return true;
        }
        if (off == 4) { p->xosc_badwrite &= ~(value & (1u << 24)); return true; }
        if (off == 12) { p->xosc_startup = atomic_value(p->xosc_startup, value, alias) & 0x103fff; return true; }
    }
    if ((base == 0x40028000u || base == 0x4002c000u) && off < 16) {
        unsigned i = base == 0x4002c000u;
        static const uint32_t masks[] = {0x13f,0x2d,0xfff,0x77000};
        p->pll[i][off / 4] = atomic_value(p->pll[i][off / 4], value, alias) & masks[off / 4]; return true;
    }
    if (base == 0x40008000u) {
        if (off < 0x78) {
            unsigned i = off / 12;
            if (off % 12 == 0) {
                uint32_t v = atomic_value(p->clock_ctrl[i], value, alias) & ctrl_mask[i];
                if (v & 0x131500) { sim_fail(s, "Clock phase, kill, duty correction and nudge modes are not implemented"); return true; }
                p->clock_ctrl[i] = v; return true;
            }
            if (off % 12 == 4) {
                if (i == 6) return true; /* SDK accesses the reserved PERI divider slot. */
                p->clock_div[i] = atomic_value(p->clock_div[i], value, alias) & div_mask[i]; return true;
            }
        }
        if (off == 0x78) {
            if (value) { sim_fail(s, "Clock resuscitation is not implemented"); return true; }
            p->resus = 0; return true;
        }
        if (off >= 0xa0 && off <= 0xac) {
            unsigned i = (off - 0xa0) / 4;
            p->wake_sleep[i] = atomic_value(p->wake_sleep[i], value, alias) & (i & 1 ? 0x7fff : UINT32_MAX); return true;
        }
    }
    if (base == 0x40058000u) {
        if (off >= 12 && off <= 40) {
            unsigned i = (off - 12) / 4; p->scratch[i] = atomic_value(p->scratch[i], value, alias); return true;
        }
        if (off == 44) { p->tick = atomic_value(p->tick, value, alias) & 0x3ff; return true; }
        if (!off && !(value & 0xc0000000u)) return true;
    }
    if (base == 0x40060000u && !off) {
        uint32_t v = atomic_value(p->rosc_ctrl, value, alias);
        if ((v >> 12) != 0xfab) sim_fail(s, "Only enabled functional ROSC mode is implemented");
        else p->rosc_ctrl = v;
        return true;
    }
    if (base == 0x40004000u && (off == 12 || off == 16)) {
        unsigned i = (off - 12) / 4; p->sync_bypass[i] = atomic_value(p->sync_bypass[i], value, alias); return true;
    }
    if (base == 0x40004000u && (off == 0 || off == 4) && !value) return true;
    if (base == 0x50110000u && off == 0x4c) {
        uint32_t v = atomic_value(p->usb_sie_ctrl, value, alias);
        if (v & ~0x40000u) sim_fail(s, "Only USB transceiver power-down storage is implemented");
        else p->usb_sie_ctrl = v;
        return true;
    }
    return false;
}
