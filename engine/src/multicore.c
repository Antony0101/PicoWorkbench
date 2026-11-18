/* Shared SIO synchronization and a direct-load core-1 launch protocol shim. */
#include "sim_internal.h"
#include <string.h>

static uint32_t fifo_status(sim_t *s, unsigned core) {
    return s->fifo[core].errors | (s->fifo[core].count ? 1 : 0) |
        (s->fifo[1 - core].count < 8 ? 2 : 0);
}

static void push(core_fifo_t *f, uint32_t value) { f->words[(f->head + f->count++) % 8] = value; }
static uint32_t pop(core_fifo_t *f) {
    uint32_t value = f->words[f->head];
    f->head = (f->head + 1) % 8; f->count--;
    return value;
}

static void divide(core_t *c, bool signed_division) {
    if (signed_division) {
        int64_t a = (int32_t)c->dividend, b = (int32_t)c->divisor;
        c->quotient = b ? (uint32_t)(a / b) : a < 0 ? 1u : UINT32_MAX;
        c->div_remainder = b ? (uint32_t)(a % b) : c->dividend;
    } else {
        c->quotient = c->divisor ? c->dividend / c->divisor : UINT32_MAX;
        c->div_remainder = c->divisor ? c->dividend % c->divisor : c->dividend;
    }
    c->div_dirty = true;
}

void sim_multicore_reset(sim_t *s) {
    memset(s->fifo, 0, sizeof(s->fifo));
    memset(s->spinlock_owner, 0, sizeof(s->spinlock_owner));
    memset(s->boot_words, 0, sizeof(s->boot_words));
    s->spinlocks = s->proc1_on = s->proc1_off = s->boot_stage = 0;
    s->boot_pending = s->core1_reset_pending = s->boot_notify = false;
}

uint32_t sim_multicore_irq(sim_t *s, unsigned core) {
    return fifo_status(s, core) & 13 ? 1u << (15 + core) : 0;
}

static bool launch_valid(sim_t *s) {
    uint32_t vtor = s->boot_words[3], sp = s->boot_words[4], entry = s->boot_words[5];
    bool vectors = (vtor >= FLASH_BASE && (uint64_t)vtor + 4 <= FLASH_BASE + FLASH_SIZE) ||
                   (vtor >= RAM_BASE && (uint64_t)vtor + 4 <= RAM_BASE + RAM_SIZE);
    bool executable = false;
    for (unsigned i = 0; i < s->segment_count; i++) {
        segment_t seg = s->segments[i];
        if ((seg.flags & 1) && (entry & ~1u) >= seg.start &&
            (uint64_t)(entry & ~1u) + 2 <= (uint64_t)seg.start + seg.size) executable = true;
    }
    if (!vectors || (vtor & 255) || (sp & 3) || sp <= RAM_BASE || sp > RAM_BASE + RAM_SIZE ||
        !(entry & 1) || !executable) {
        sim_fail(s, "Invalid core 1 launch: VTOR=0x%08x SP=0x%08x entry=0x%08x", vtor, sp, entry);
        return false;
    }
    return true;
}

/* This is outside uc_emu_start: never restore a context in an MMIO callback. */
void sim_multicore_service(sim_t *s) {
    if (s->core1_reset_pending) {
        s->core1_reset_pending = false;
        if (sim_cpu_init_core(s, 1, FLASH_BASE, 0, 0)) return;
    }
    if (s->proc1_off || s->core[1].launched) return;
    if (s->boot_notify) {
        if (s->fifo[0].count == 8) return;
        push(&s->fifo[0], 0); s->boot_notify = false;
        s->core[0].event_flag = s->core[1].event_flag = 1;
    }
    while (s->fifo[1].count && s->fifo[0].count < 8) {
        uint32_t word = pop(&s->fifo[1]), reply = word;
        unsigned stage = s->boot_stage;
        if (stage < 3 && word != (stage == 2 ? 1u : 0u)) {
            s->boot_stage = word == 0 ? 1 : 0;
            reply = 0;
        } else if (stage >= 3 && !word) {
            s->boot_stage = 1;
        } else {
            s->boot_words[stage] = word;
            s->boot_stage++;
        }
        push(&s->fifo[0], reply);
        /* The ROM's echo helper executes SEV after each response. */
        s->core[0].event_flag = s->core[1].event_flag = 1;
        if (s->boot_stage == 6) {
            s->boot_pending = true;
            break;
        }
    }
    if (s->boot_pending) {
        s->boot_pending = false;
        if (launch_valid(s)) sim_cpu_init_core(s, 1, s->boot_words[3], s->boot_words[4], s->boot_words[5]);
    }
}

uint32_t sim_multicore_read(sim_t *s, uint32_t a, bool peek, bool *found) {
    *found = true;
    unsigned core = s->active_core;
    core_fifo_t *f = &s->fifo[core];
    if (a >= 0xd0000000u && a < 0xd0001000u) {
        unsigned off = a - 0xd0000000u;
        if (off == 0x50) return fifo_status(s, core);
        if (off == 0x54) return 0;
        if (off == 0x58) {
            if (!f->count) { if (!peek) f->errors |= 8; return 0; }
            return peek ? f->words[f->head] : pop(f);
        }
        if (off == 0x5c) return s->spinlocks;
        core_t *c = &s->core[core];
        if (off == 0x60 || off == 0x68) return c->dividend;
        if (off == 0x64 || off == 0x6c) return c->divisor;
        if (off == 0x70) { if (!peek) c->div_dirty = false; return c->quotient; }
        if (off == 0x74) return c->div_remainder;
        if (off == 0x78) return 1 | (c->div_dirty ? 2 : 0);
        if (off >= 0x100 && off < 0x180) {
            unsigned lock = (off - 0x100) / 4;
            uint32_t bit = 1u << lock;
            if (s->spinlocks & bit) return 0;
            if (!peek) { s->spinlocks |= bit; s->spinlock_owner[lock] = core + 1; }
            return bit;
        }
    }
    if ((a & ~0x3fffu) == 0x40010000u) {
        switch (a & 0xfff) {
        case 0: return s->proc1_on;
        case 4: return s->proc1_off;
        case 8: return 0;
        case 12: return 0x8000u | (s->proc1_off ? 0 : 0x10000u);
        }
    }
    *found = false; return 0;
}

bool sim_multicore_write(sim_t *s, uint32_t a, uint32_t value) {
    unsigned core = s->active_core;
    if (a >= 0xd0000000u && a < 0xd0001000u) {
        unsigned off = a - 0xd0000000u;
        if (off == 0x50) { s->fifo[core].errors &= ~(value & 12); return true; }
        if (off == 0x54) {
            core_fifo_t *f = &s->fifo[1 - core];
            if (f->count == 8) s->fifo[core].errors |= 4;
            else push(f, value);
            return true;
        }
        core_t *c = &s->core[core];
        if (off >= 0x60 && off <= 0x6c) {
            if (off & 4) c->divisor = value; else c->dividend = value;
            divide(c, off >= 0x68); return true;
        }
        if (off == 0x70) { c->quotient = value; c->div_dirty = true; return true; }
        if (off == 0x74) { c->div_remainder = value; c->div_dirty = true; return true; }
        if (off >= 0x100 && off < 0x180) {
            unsigned lock = (off - 0x100) / 4;
            s->spinlocks &= ~(1u << lock); s->spinlock_owner[lock] = 0; return true;
        }
    }
    if ((a & ~0x3fffu) == 0x40010000u) {
        unsigned off = a & 0xfff, alias = (a >> 12) & 3;
        if (off == 0 || off == 4) {
            uint32_t *reg = off ? &s->proc1_off : &s->proc1_on;
            uint32_t v = atomic_value(*reg, value, alias);
            if (v & ~0x10000u) { sim_fail(s, "Only PSM PROC1 force-on/off is implemented"); return true; }
            if (off == 4 && v != *reg) {
                s->core[1].launched = false;
                s->core1_reset_pending = true;
                s->boot_stage = 0; s->boot_pending = false;
                if (!v) {
                    /* The ROM drains its incoming mailbox, then announces readiness. */
                    s->fifo[1].head = s->fifo[1].count = 0;
                    s->boot_notify = true;
                } else s->boot_notify = false;
            }
            *reg = v; return true;
        }
        if (off == 8 && !value) return true;
    }
    return false;
}

int sim_cores_snapshot(sim_t *s, sim_cores_snapshot_t *out) {
    if (!s || !out) return -1;
    memset(out, 0, sizeof(*out));
    uc_context_save(s->cpu, s->contexts[s->active_core]);
    out->spinlocks = s->spinlocks; out->boot_stage = s->boot_stage;
    out->proc1_off = s->proc1_off; out->next_core = s->next_core; out->active_core = s->active_core;
    memcpy(out->spinlock_owner, s->spinlock_owner, sizeof(out->spinlock_owner));
    for (unsigned i = 0; i < 2; i++) {
        core_t *c = &s->core[i]; sim_core_snapshot_t *d = &out->core[i];
        sim_cpu_core_snapshot(s, i, d);
        d->instructions = c->instructions; d->launched = c->launched; d->sleeping = c->sleeping;
        d->nvic_enable = c->nvic_enable; d->nvic_pending = c->nvic_pending; d->irq_levels = c->irq_levels;
        d->vtor = c->vtor; d->scr = c->scr; d->event_flag = c->event_flag;
        d->systick_ctrl = c->systick_ctrl; d->systick_load = c->systick_load; d->systick_value = c->systick_value;
        d->fifo_status = fifo_status(s, i); d->rx_count = s->fifo[i].count;
        for (unsigned j = 0; j < s->fifo[i].count; j++) d->rx[j] = s->fifo[i].words[(s->fifo[i].head + j) % 8];
    }
    return 0;
}
