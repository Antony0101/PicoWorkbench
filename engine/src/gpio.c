#include "sim_internal.h"
#include <string.h>

static uint32_t gpio_override(uint32_t value, uint32_t mode) {
    return mode == 1 ? !value : mode == 2 ? 0 : mode == 3 ? 1 : value;
}

static uint32_t output_value(sim_t *s, unsigned pin) {
    unsigned function = s->ctrl[pin] & 31;
    uint32_t output = function == 4 ? s->pwm_output :
        function == 6 || function == 7 ? s->pio[function - 6].out : s->out;
    return (output >> pin) & 1;
}

static uint32_t output_enable(sim_t *s, unsigned pin) {
    unsigned function = s->ctrl[pin] & 31;
    if (function == 6 || function == 7) return (s->pio[function - 6].oe >> pin) & 1;
    return function == 5 ? (s->oe >> pin) & 1 : function == 4;
}

void sim_gpio_resolve(sim_t *s) {
    uint32_t in = 0, floating = 0, conflict = 0;
    unsigned low_drive[30] = {0}, high_drive[30] = {0};
    unsigned low_pull[30] = {0}, high_pull[30] = {0};
    for (unsigned pin = 0; pin < 30; pin++) {
        uint32_t ctrl = s->ctrl[pin], pads = s->pads[pin];
        unsigned net = s->net[pin];
        unsigned oe = output_enable(s, pin);
        oe = gpio_override(oe, (ctrl >> 12) & 3) && !(pads & 128);
        if (oe) {
            unsigned val = gpio_override(output_value(s, pin), (ctrl >> 8) & 3);
            low_drive[net] |= !val; high_drive[net] |= val;
        }
        unsigned ext = s->drive[pin];
        low_drive[net] |= ext == SIM_LOW || ext == SIM_CONFLICT;
        high_drive[net] |= ext == SIM_HIGH || ext == SIM_CONFLICT;
        low_pull[net] |= !!(pads & 4) || ext == SIM_PULL_LOW;
        high_pull[net] |= !!(pads & 8) || ext == SIM_PULL_HIGH;
    }
    for (unsigned pin = 0; pin < 30; pin++) {
        uint32_t bit = 1u << pin;
        unsigned net = s->net[pin];
        unsigned low = low_drive[net], high = high_drive[net];
        unsigned state;
        if (low && high) state = SIM_CONFLICT;
        else if (low) state = SIM_LOW;
        else if (high) state = SIM_HIGH;
        else {
            low = low_pull[net]; high = high_pull[net];
            state = low && high ? SIM_CONFLICT : low ? SIM_LOW : high ? SIM_HIGH : SIM_FLOATING;
        }
        unsigned old = (s->conflict & bit) ? SIM_CONFLICT : (s->floating & bit) ?
            SIM_FLOATING : (s->input & bit) ? SIM_HIGH : SIM_LOW;
        if (state == SIM_HIGH) in |= bit;
        if (state == SIM_FLOATING) floating |= bit;
        if (state == SIM_CONFLICT) conflict |= bit;
        if (old != state) {
            sim_emit(s, SIM_EVENT_GPIO, pin, state);
            if (state == SIM_LOW || state == SIM_HIGH)
                s->gpio_edges[pin / 8] |= (state == SIM_HIGH ? 8u : 4u) << ((pin % 8) * 4);
        }
    }
    s->input = in; s->floating = floating; s->conflict = conflict;
}

uint32_t sim_gpio_raw(sim_t *s, unsigned group) {
    uint32_t raw = s->gpio_edges[group];
    for (unsigned p = group * 8; p < 30 && p < group * 8 + 8; p++) {
        unsigned level = (s->floating | s->conflict) & (1u << p) ? 0 :
                         (s->input & (1u << p)) ? 2 : 1;
        raw |= level << ((p % 8) * 4);
    }
    return raw;
}

uint32_t sim_gpio_sample(sim_t *s) {
    uint32_t result = 0;
    for (unsigned p = 0; p < 30; p++) {
        unsigned value = (s->pads[p] & 64) ? !!(s->input & (1u << p)) : 0;
        value = gpio_override(value, (s->ctrl[p] >> 16) & 3);
        result |= value << p;
    }
    return result;
}

uint32_t sim_gpio_read(sim_t *s, uint32_t a, bool *available) {
    *available = true;
    uint32_t base = a & ~0x3fffu, off = a & 0xfffu;
    if (a >= 0xd0000000u && a < 0xd0001000u) {
        switch (a - 0xd0000000u) {
        case 0x00: return s->active_core;
        case 0x04: return sim_gpio_sample(s);
        case 0x10: return s->out;
        case 0x20: return s->oe;
        default: break;
        }
    } else if (base == 0x40014000u && off >= 0xf0 && off < 0x160) {
        unsigned group = (off & 15) / 4;
        if (off < 0x100) return sim_gpio_raw(s, group);
        unsigned core = (off - 0x100) / 0x30, kind = ((off - 0x100) % 0x30) / 16;
        if (kind == 0) return s->gpio_inte[core][group];
        if (kind == 1) return s->gpio_intf[core][group];
        return (sim_gpio_raw(s, group) & s->gpio_inte[core][group]) | s->gpio_intf[core][group];
    } else if (base == 0x40014000u && off < 240) {
        unsigned p = off / 8;
        if (off & 4) return s->ctrl[p];
        unsigned out = gpio_override(output_value(s, p), (s->ctrl[p] >> 8) & 3);
        unsigned oe = gpio_override(output_enable(s, p), (s->ctrl[p] >> 12) & 3);
        unsigned in = !!(s->input & (1u << p));
        return (out << 8) | (out << 9) | (oe << 12) | (oe << 13) |
               (in << 17) | (((sim_gpio_sample(s) >> p) & 1) << 19);
    } else if (base == 0x4001c000u && off >= 4 && off <= 120) {
        return s->pads[(off - 4) / 4];

    }
    *available = false;
    return 0;
}

bool sim_gpio_write(sim_t *s, uint32_t a, uint32_t value) {
    uint32_t base = a & ~0x3fffu, off = a & 0xfffu, alias = (a >> 12) & 3;
    if (a >= 0xd0000000u && a < 0xd0001000u) {
        unsigned offset = a - 0xd0000000u;
        if (offset >= 0x10 && offset <= 0x1c) {
            s->out = atomic_value(s->out, value, (offset - 0x10) / 4 == 1 ? 2 :
                (offset - 0x10) / 4 == 2 ? 3 : (offset - 0x10) / 4 == 3 ? 1 : 0) & PIN_MASK;
            sim_gpio_resolve(s); return true;
        }
        if (offset >= 0x20 && offset <= 0x2c) {
            s->oe = atomic_value(s->oe, value, (offset - 0x20) / 4 == 1 ? 2 :
                (offset - 0x20) / 4 == 2 ? 3 : (offset - 0x20) / 4 == 3 ? 1 : 0) & PIN_MASK;
            sim_gpio_resolve(s); return true;
        }
    } else if (base == 0x40014000u && off >= 0xf0 && off < 0x160) {
        unsigned group = (off & 15) / 4;
        if (off < 0x100) s->gpio_edges[group] &= ~(value & 0xccccccccu);
        else {
            unsigned core = (off - 0x100) / 0x30, kind = ((off - 0x100) % 0x30) / 16;
            if (kind == 0) s->gpio_inte[core][group] = atomic_value(s->gpio_inte[core][group], value, alias);
            else if (kind == 1) s->gpio_intf[core][group] = atomic_value(s->gpio_intf[core][group], value, alias);
            else return false;
        }
        return true;
    } else if (base == 0x40014000u && off < 240 && (off & 7) == 4) {
        unsigned p = off / 8;
        uint32_t ctrl = atomic_value(s->ctrl[p], value, alias);
        if (ctrl & ~0x0003331fu) { sim_fail(s, "Unsupported GPIO CTRL bits 0x%08x", ctrl); return true; }
        if ((ctrl & 31) != 5 && (ctrl & 31) != 31 && (ctrl & 31) != 2 &&
            (ctrl & 31) != 1 && (ctrl & 31) != 3 && (ctrl & 31) != 4 &&
            (ctrl & 31) != 6 && (ctrl & 31) != 7) {
            sim_fail(s, "Unsupported GPIO function %u on GPIO%u", ctrl & 31, p); return true;
        }
        s->ctrl[p] = ctrl; sim_gpio_resolve(s); return true;
    } else if (base == 0x4001c000u && off >= 4 && off <= 120) {
        unsigned p = (off - 4) / 4;
        s->pads[p] = atomic_value(s->pads[p], value, alias) & 255;
        sim_gpio_resolve(s); return true;

    }
    return false;
}

void sim_gpio_reset(sim_t *s) {
    s->out = s->oe = s->input = s->floating = s->conflict = 0;
    memset(s->gpio_edges, 0, sizeof(s->gpio_edges));
    memset(s->gpio_inte, 0, sizeof(s->gpio_inte)); memset(s->gpio_intf, 0, sizeof(s->gpio_intf));
    for (unsigned i = 0; i < 30; i++) {
        s->ctrl[i] = 31; s->pads[i] = 0x56; s->drive[i] = SIM_FLOATING; s->net[i] = i;
    }
}

int sim_connect_gpio(sim_t *s, uint32_t pin, uint32_t net) {
    if (!s || pin >= 30 || net >= 30) return -1;
    s->net[pin] = net; sim_gpio_resolve(s); return 0;
}
