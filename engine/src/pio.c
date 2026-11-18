/* RP2040 PIO: functional instruction clocks and sampled digital inputs. */
#include "sim_internal.h"
#include <string.h>

static uint32_t mask_bits(unsigned count) {
    return count == 32 ? UINT32_MAX : (1u << count) - 1;
}

static unsigned threshold(uint32_t config, unsigned shift) {
    unsigned value = (config >> shift) & 31;
    return value ? value : 32;
}

static unsigned capacity(pio_sm_t *sm, bool rx) {
    unsigned join = sm->shiftctrl >> 30;
    if (join == 3) return 0;
    if (join == 0) return 4;
    return join == (rx ? 2u : 1u) ? 8 : 0;
}

unsigned sim_pio_dreq(sim_t *s, unsigned dreq) {
    pio_sm_t *sm = &s->pio[dreq / 8].sm[dreq & 3];
    return dreq & 4 ? sm->rx.count : capacity(sm, false) - sm->tx.count;
}

static uint32_t pop(pio_fifo_t *fifo) {
    uint32_t value = fifo->words[fifo->head];
    fifo->head = (fifo->head + 1) % 8;
    fifo->count--;
    return value;
}

static void push(pio_fifo_t *fifo, uint32_t value) {
    fifo->words[(fifo->head + fifo->count++) % 8] = value;
}

static uint32_t divider(pio_sm_t *sm) {
    uint32_t value = sm->clkdiv >> 8;
    return value ? value : 65536u * 256;
}

/* Disabled dividers free-run lazily; enabling does not restart their phase. */
static bool clock_to(pio_sm_t *sm, uint64_t cycle) {
    uint32_t div = divider(sm);
    uint64_t elapsed = cycle - sm->phase_cycle;
    bool tick = elapsed >= (div - sm->phase + 255u) / 256u;
    sm->phase = (sm->phase + (elapsed % div) * 256u) % div;
    sm->phase_cycle = cycle;
    return tick;
}

static bool active(pio_t *pio) {
    return pio->ctrl || pio->sm[0].exec_pending || pio->sm[1].exec_pending ||
        pio->sm[2].exec_pending || pio->sm[3].exec_pending;
}

static uint32_t rotate_pins(uint32_t pins, unsigned base) {
    return base ? (pins >> base) | (pins << (32 - base)) : pins;
}

static void write_pins(pio_t *pio, unsigned base, unsigned count, uint32_t value, bool directions) {
    uint32_t *destination = directions ? &pio->oe : &pio->out;
    for (unsigned i = 0; i < count; i++) {
        unsigned pin = (base + i) & 31;
        if (pin >= 30) continue;
        uint32_t bit = 1u << pin;
        *destination = (*destination & ~bit) | (((value >> i) & 1) << pin);
    }
}

static void autopull(pio_sm_t *sm) {
    if ((sm->shiftctrl & (1u << 17)) && sm->out_count >= threshold(sm->shiftctrl, 25) && sm->tx.count) {
        sm->osr = pop(&sm->tx);
        sm->out_count = 0;
    }
}

static unsigned irq_index(unsigned encoded, unsigned sm) {
    return (encoded & 4) | ((encoded + ((encoded & 16) ? sm : 0)) & 3);
}

static int invalid_instruction(sim_t *s, unsigned block, unsigned machine, unsigned instruction) {
    return sim_fail(s, "Unsupported PIO%u SM%u instruction 0x%04x at PC %u",
                    block, machine, instruction, s->pio[block].sm[machine].pc);
}

/* One attempt. Stalls retain the PC; side-set still takes effect during stalls. */
static void execute(sim_t *s, unsigned block, unsigned machine, uint16_t instruction,
                    bool injected, uint32_t pins) {
    pio_t *pio = &s->pio[block];
    pio_sm_t *sm = &pio->sm[machine];
    unsigned major = instruction >> 13, arg = (instruction >> 5) & 7, data = instruction & 31;
    unsigned count = data ? data : 32;
    unsigned sides = sm->pinctrl >> 29;
    unsigned delay = (instruction >> 8) & mask_bits(5 - sides);
    unsigned next_pc = sm->pc == ((sm->execctrl >> 12) & 31) ? (sm->execctrl >> 7) & 31 : (sm->pc + 1) & 31;
    bool jump = false, schedule = false;
    uint16_t next_instruction = 0;
    uint32_t value = 0;
    sm->stalled = SIM_PIO_STALL_NONE;
    switch (major) {
    case 0: { /* JMP */
        bool condition = false;
        switch (arg) {
        case 0: condition = true; break;
        case 1: condition = !sm->x; break;
        case 2: condition = sm->x-- != 0; break;
        case 3: condition = !sm->y; break;
        case 4: condition = sm->y-- != 0; break;
        case 5: condition = sm->x != sm->y; break;
        case 6: condition = (pins >> ((sm->execctrl >> 24) & 31)) & 1; break;
        case 7: condition = sm->out_count < threshold(sm->shiftctrl, 25); break;
        }
        if (condition) { sm->pc = data; jump = true; }
        break;
    }
    case 1: { /* WAIT */
        unsigned source = arg & 3, index = data;
        bool polarity = (arg & 4) != 0;
        if (source == 0) value = (pins >> index) & 1;
        else if (source == 1) value = (pins >> ((index + (sm->pinctrl >> 15)) & 31)) & 1;
        else if (source == 2 && !(index & 8)) {
            index = irq_index(index, machine);
            value = (pio->irq >> index) & 1;
        } else { invalid_instruction(s, block, machine, instruction); return; }
        if (value != polarity) sm->stalled = SIM_PIO_STALL_WAIT;
        else if (source == 2 && polarity) pio->irq &= ~(1u << index);
        break;
    }
    case 2: /* IN */
        if (!sm->autopush_pending) {
            switch (arg) {
            case 0: value = rotate_pins(pins, (sm->pinctrl >> 15) & 31); break;
            case 1: value = sm->x; break;
            case 2: value = sm->y; break;
            case 3: value = 0; break;
            case 6: value = sm->isr; break;
            case 7: value = sm->osr; break;
            default: invalid_instruction(s, block, machine, instruction); return;
            }
            value &= mask_bits(count);
            if (count == 32) sm->isr = value;
            else if (sm->shiftctrl & (1u << 18)) sm->isr = (sm->isr >> count) | (value << (32 - count));
            else sm->isr = (sm->isr << count) | value;
            sm->in_count = sm->in_count + count > 32 ? 32 : sm->in_count + count;
        }
        if ((sm->shiftctrl & (1u << 16)) && sm->in_count >= threshold(sm->shiftctrl, 20)) {
            if (sm->rx.count >= capacity(sm, true)) {
                pio->fdebug |= 1u << machine;
                sm->stalled = SIM_PIO_STALL_RX;
                sm->autopush_pending = true;
            } else {
                push(&sm->rx, sm->isr);
                sm->isr = sm->in_count = 0;
                sm->autopush_pending = false;
            }
        }
        break;
    case 3: /* OUT */
        if ((sm->shiftctrl & (1u << 17)) && sm->out_count >= threshold(sm->shiftctrl, 25)) {
            if (!sm->tx.count) pio->fdebug |= 1u << (24 + machine);
            autopull(sm); /* A refill of an empty OSR consumes this cycle. */
            sm->stalled = SIM_PIO_STALL_TX;
            break;
        }
        value = sm->shiftctrl & (1u << 19) ? sm->osr & mask_bits(count) : sm->osr >> (32 - count);
        sm->osr = count == 32 ? 0 : sm->shiftctrl & (1u << 19) ? sm->osr >> count : sm->osr << count;
        sm->out_count = sm->out_count + count > 32 ? 32 : sm->out_count + count;
        switch (arg) {
        case 0: case 4: write_pins(pio, sm->pinctrl & 31, (sm->pinctrl >> 20) & 63, value, arg == 4); break;
        case 1: sm->x = value; break;
        case 2: sm->y = value; break;
        case 3: break;
        case 5: sm->pc = value & 31; jump = true; break;
        case 6: sm->isr = value; sm->in_count = count; break;
        case 7: next_instruction = (uint16_t)value; schedule = true; break;
        }
        autopull(sm);
        break;
    case 4: /* PUSH / PULL */
        if (data) { invalid_instruction(s, block, machine, instruction); return; }
        if (arg & 4) {
            if (((arg & 2) || (sm->shiftctrl & (1u << 17))) && sm->out_count < threshold(sm->shiftctrl, 25)) break;
            if (sm->tx.count) sm->osr = pop(&sm->tx);
            else if (arg & 1) { pio->fdebug |= 1u << (24 + machine); sm->stalled = SIM_PIO_STALL_TX; break; }
            else sm->osr = sm->x;
            sm->out_count = 0;
        } else {
            if ((arg & 2) && sm->in_count < threshold(sm->shiftctrl, 20)) break;
            if (sm->rx.count < capacity(sm, true)) push(&sm->rx, sm->isr);
            else {
                pio->fdebug |= 1u << machine;
                if (arg & 1) { sm->stalled = SIM_PIO_STALL_RX; break; }
            }
            sm->isr = sm->in_count = 0;
        }
        break;
    case 5: { /* MOV */
        unsigned source = data & 7, operation = data >> 3;
        switch (source) {
        case 0: value = rotate_pins(pins, (sm->pinctrl >> 15) & 31); break;
        case 1: value = sm->x; break;
        case 2: value = sm->y; break;
        case 3: value = 0; break;
        case 5: value = ((sm->execctrl & 16) ? sm->rx.count : sm->tx.count) < (sm->execctrl & 15) ? UINT32_MAX : 0; break;
        case 6: value = sm->isr; break;
        case 7: value = sm->osr; break;
        default: invalid_instruction(s, block, machine, instruction); return;
        }
        if (operation == 1) value = ~value;
        else if (operation == 2) {
            uint32_t reversed = 0;
            for (unsigned bit = 0; bit < 32; bit++) reversed |= ((value >> bit) & 1) << (31 - bit);
            value = reversed;
        } else if (operation == 3) { invalid_instruction(s, block, machine, instruction); return; }
        switch (arg) {
        case 0: write_pins(pio, sm->pinctrl & 31, (sm->pinctrl >> 20) & 63, value, false); break;
        case 1: sm->x = value; break;
        case 2: sm->y = value; break;
        case 4: next_instruction = (uint16_t)value; schedule = true; break;
        case 5: sm->pc = value & 31; jump = true; break;
        case 6: sm->isr = value; sm->in_count = 0; break;
        case 7: sm->osr = value; sm->out_count = 0; break;
        default: invalid_instruction(s, block, machine, instruction); return;
        }
        break;
    }
    case 6: { /* IRQ */
        if ((arg & 4) || (data & 8)) { invalid_instruction(s, block, machine, instruction); return; }
        unsigned index = irq_index(data, machine);
        if (arg & 2) pio->irq &= ~(1u << index);
        else if (arg & 1) {
            if (sm->irq_wait < 0) { pio->irq |= 1u << index; sm->irq_wait = (int)index; }
            if (pio->irq & (1u << sm->irq_wait)) sm->stalled = SIM_PIO_STALL_IRQ;
            else sm->irq_wait = -1;
        } else pio->irq |= 1u << index;
        break;
    }
    case 7: /* SET */
        switch (arg) {
        case 0: case 4: write_pins(pio, (sm->pinctrl >> 5) & 31, (sm->pinctrl >> 26) & 7, data, arg == 4); break;
        case 1: sm->x = data; break;
        case 2: sm->y = data; break;
        default: invalid_instruction(s, block, machine, instruction); return;
        }
        break;
    }
    /* Side-set has priority over an instruction's main pin write. */
    if (sides) {
        unsigned bits = sides, side = (instruction >> (13 - sides)) & mask_bits(sides);
        bool optional = (sm->execctrl & (1u << 30)) != 0;
        if (!optional || (side & (1u << (sides - 1)))) {
            if (optional) bits--;
            write_pins(pio, (sm->pinctrl >> 10) & 31, bits, side, (sm->execctrl & (1u << 29)) != 0);
        }
    }
    if (major != 3) autopull(sm);
    if (sm->stalled) return;
    if (!injected && !jump) sm->pc = next_pc;
    sm->exec_pending = schedule;
    if (schedule) sm->exec_instr = next_instruction;
    sm->delay = schedule ? 0 : delay;
}

static void restart(pio_sm_t *sm) {
    sm->isr = sm->in_count = sm->delay = sm->stalled = 0;
    sm->out_count = 32;
    sm->exec_pending = sm->autopush_pending = false;
    sm->irq_wait = -1;
}

void sim_pio_reset_block(sim_t *s, unsigned block) {
    memset(&s->pio[block], 0, sizeof(s->pio[block]));
    for (unsigned i = 0; i < 4; i++) {
        pio_sm_t *sm = &s->pio[block].sm[i];
        sm->clkdiv = 0x10000; sm->execctrl = 0x1f000;
        sm->shiftctrl = 0xc0000; sm->pinctrl = 0x14000000;
        restart(sm);
        sm->phase_cycle = s->pio_cycles;
    }
}

void sim_pio_reset(sim_t *s) {
    s->pio_cycles = 0;
    sim_pio_reset_block(s, 0); sim_pio_reset_block(s, 1);
}

void sim_pio_advance(sim_t *s, uint64_t cycles) {
    if (!cycles) return;
    s->pio_cycles += cycles;
    if (!active(&s->pio[0]) && !active(&s->pio[1])) return;
    uint32_t pins = sim_gpio_sample(s);
    bool changed = false;
    for (unsigned b = 0; b < 2; b++) {
        pio_t *pio = &s->pio[b];
        uint32_t old_out = pio->out, old_oe = pio->oe;
        for (unsigned i = 0; i < 4; i++) {
            pio_sm_t *sm = &pio->sm[i];
            if (!(pio->ctrl & (1u << i)) && !sm->exec_pending) continue;
            if (!clock_to(sm, s->pio_cycles)) continue;
            if (sm->delay && !sm->exec_pending) { sm->delay--; autopull(sm); continue; }
            bool injected = sm->exec_pending;
            execute(s, b, i, injected ? sm->exec_instr : pio->memory[sm->pc], injected, pins);
            if (s->reason == SIM_FAULT) return;
        }
        changed |= old_out != pio->out || old_oe != pio->oe;
    }
    if (changed) sim_gpio_resolve(s);
}

uint64_t sim_pio_next_event(sim_t *s, uint64_t next) {
    for (unsigned b = 0; b < 2; b++) for (unsigned i = 0; i < 4; i++) {
        pio_sm_t *sm = &s->pio[b].sm[i];
        if (!(s->pio[b].ctrl & (1u << i)) && !sm->exec_pending) continue;
        uint64_t cycles = (divider(sm) - sm->phase + 255u) / 256u;
        uint64_t delta = (cycles * 1000000000u + s->hz - 1) / s->hz;
        if (delta < UINT64_MAX - s->now && s->now + delta < next) next = s->now + delta;
    }
    return next;
}

static uint32_t raw_irq(pio_t *pio) {
    uint32_t value = (pio->irq & 15) << 8;
    for (unsigned i = 0; i < 4; i++) {
        pio_sm_t *sm = &pio->sm[i];
        if (sm->rx.count) value |= 1u << i;
        if (sm->tx.count < capacity(sm, false)) value |= 1u << (4 + i);
    }
    return value;
}

uint32_t sim_pio_irq_levels(sim_t *s) {
    uint32_t levels = 0;
    for (unsigned b = 0; b < 2; b++) for (unsigned line = 0; line < 2; line++)
        if ((raw_irq(&s->pio[b]) & s->pio[b].inte[line]) | s->pio[b].intf[line]) levels |= 1u << (7 + b * 2 + line);
    return levels;
}

static pio_t *decode(sim_t *s, uint32_t address) {
    uint32_t base = address & ~0x3fffu;
    return base == 0x50200000u ? &s->pio[0] : base == 0x50300000u ? &s->pio[1] : NULL;
}

uint32_t sim_pio_read(sim_t *s, uint32_t address, bool peek, bool *available) {
    pio_t *pio = decode(s, address);
    *available = pio != NULL;
    if (!pio) return 0;
    unsigned off = address & 0xfff;
    if (off == 0) return pio->ctrl;
    if (off == 8) return pio->fdebug;
    if (off == 4 || off == 12) {
        uint32_t value = 0;
        for (unsigned i = 0; i < 4; i++) {
            pio_sm_t *sm = &pio->sm[i];
            if (off == 12) value |= (sm->tx.count | (sm->rx.count << 4)) << (8 * i);
            else value |= ((sm->rx.count >= capacity(sm, true)) | (!sm->rx.count << 8) |
                          ((sm->tx.count >= capacity(sm, false)) << 16) | (!sm->tx.count << 24)) << i;
        }
        return value;
    }
    if (off >= 0x10 && off < 0x20) return 0; /* write-only TX FIFO */
    if (off >= 0x20 && off < 0x30) {
        unsigned i = (off - 0x20) / 4;
        pio_fifo_t *rx = &pio->sm[i].rx;
        if (peek) return rx->count ? rx->words[rx->head] : 0;
        if (rx->count) return pop(rx);
        pio->fdebug |= 1u << (8 + i);
        return 0;
    }
    if (off == 0x30) return pio->irq;
    if (off == 0x34) return 0;
    if (off == 0x38) return pio->input_sync_bypass;
    if (off == 0x3c) return pio->out;
    if (off == 0x40) return pio->oe;
    if (off == 0x44) return (32u << 16) | (4u << 8) | 4;
    if (off >= 0x48 && off < 0xc8) return peek ? pio->memory[(off - 0x48) / 4] : 0;
    if (off >= 0xc8 && off < 0x128) {
        pio_sm_t *sm = &pio->sm[(off - 0xc8) / 24];
        switch ((off - 0xc8) % 24) {
        case 0: return sm->clkdiv;
        case 4: return sm->execctrl | (sm->exec_pending && sm->stalled ? 1u << 31 : 0);
        case 8: return sm->shiftctrl;
        case 12: return sm->pc;
        case 16: return pio->memory[sm->pc];
        case 20: return sm->pinctrl;
        }
    }
    if (off == 0x128) return raw_irq(pio);
    if (off >= 0x12c && off <= 0x140) {
        unsigned line = (off - 0x12c) / 12;
        switch ((off - 0x12c) % 12) {
        case 0: return pio->inte[line];
        case 4: return pio->intf[line];
        case 8: return (raw_irq(pio) & pio->inte[line]) | pio->intf[line];
        }
    }
    *available = false;
    return 0;
}

bool sim_pio_write(sim_t *s, uint32_t address, uint32_t value) {
    pio_t *pio = decode(s, address);
    if (!pio) return false;
    unsigned off = address & 0xfff, alias = (address >> 12) & 3;
    if (off == 0) {
        uint32_t control = atomic_value(pio->ctrl, value, alias);
        pio->ctrl = control & 15;
        for (unsigned i = 0; i < 4; i++) {
            clock_to(&pio->sm[i], s->pio_cycles);
            if (control & (1u << (4 + i))) restart(&pio->sm[i]);
            if (control & (1u << (8 + i))) pio->sm[i].phase = 0;
        }
        return true;
    }
    if (off == 8) { pio->fdebug &= ~value; return true; }
    if (off >= 0x10 && off < 0x20) {
        unsigned i = (off - 0x10) / 4;
        pio_sm_t *sm = &pio->sm[i];
        if (sm->tx.count >= capacity(sm, false)) pio->fdebug |= 1u << (16 + i);
        else push(&sm->tx, value);
        return true;
    }
    if (off == 0x30) { pio->irq &= ~value; return true; }
    if (off == 0x34) { pio->irq |= value & 255; return true; }
    if (off == 0x38) { pio->input_sync_bypass = atomic_value(pio->input_sync_bypass, value, alias) & PIN_MASK; return true; }
    if (off >= 0x48 && off < 0xc8) { pio->memory[(off - 0x48) / 4] = value & 0xffff; return true; }
    if (off >= 0xc8 && off < 0x128) {
        unsigned i = (off - 0xc8) / 24;
        pio_sm_t *sm = &pio->sm[i];
        uint32_t v;
        switch ((off - 0xc8) % 24) {
        case 0:
            v = atomic_value(sm->clkdiv, value, alias) & 0xffffff00;
            if (!(v >> 16) && (v & 0xff00)) break;
            clock_to(sm, s->pio_cycles);
            sm->clkdiv = v; sm->phase %= divider(sm); return true;
        case 4:
            v = atomic_value(sm->execctrl, value, alias) & 0x7fffff9f;
            if (v & (3u << 17)) { sim_fail(s, "PIO OUT_STICKY and INLINE_OUT_EN are not implemented"); return true; }
            sm->execctrl = v; return true;
        case 8:
            v = atomic_value(sm->shiftctrl, value, alias) & 0xffff0000;
            if ((v ^ sm->shiftctrl) & 0xc0000000u) {
                memset(&sm->tx, 0, sizeof(sm->tx)); memset(&sm->rx, 0, sizeof(sm->rx));
            }
            sm->shiftctrl = v; return true;
        case 16:
            clock_to(sm, s->pio_cycles);
            sm->exec_instr = value & 0xffff; sm->exec_pending = true;
            sm->autopush_pending = false; sm->irq_wait = -1;
            execute(s, (unsigned)(pio - s->pio), i, sm->exec_instr, true, sim_gpio_sample(s));
            sim_gpio_resolve(s); return true;
        case 20:
            v = atomic_value(sm->pinctrl, value, alias);
            if ((v >> 29) > 5 || ((v >> 26) & 7) > 5 || ((v >> 20) & 63) > 32) break;
            sm->pinctrl = v; return true;
        default: break;
        }
    }
    if (off >= 0x12c && off <= 0x140) {
        unsigned line = (off - 0x12c) / 12;
        if ((off - 0x12c) % 12 == 0) { pio->inte[line] = atomic_value(pio->inte[line], value, alias) & 0xfff; return true; }
        if ((off - 0x12c) % 12 == 4) { pio->intf[line] = atomic_value(pio->intf[line], value, alias) & 0xfff; return true; }
    }
    return false;
}

int sim_pio_snapshot(sim_t *s, uint32_t block, sim_pio_snapshot_t *out) {
    if (!s || !out || block >= 2) return -1;
    pio_t *pio = &s->pio[block];
    memset(out, 0, sizeof(*out));
    out->irq = pio->irq; out->fdebug = pio->fdebug; out->out = pio->out; out->oe = pio->oe;
    for (unsigned i = 0; i < 32; i++) out->instructions[i] = pio->memory[i];
    for (unsigned i = 0; i < 4; i++) {
        pio_sm_t *sm = &pio->sm[i];
        sim_pio_sm_snapshot_t *dest = &out->sm[i];
        dest->enabled = !!(pio->ctrl & (1u << i));
        dest->pc = sm->pc; dest->x = sm->x; dest->y = sm->y; dest->isr = sm->isr; dest->osr = sm->osr;
        dest->in_count = sm->in_count; dest->out_count = sm->out_count;
        dest->clkdiv = sm->clkdiv; dest->execctrl = sm->execctrl; dest->shiftctrl = sm->shiftctrl; dest->pinctrl = sm->pinctrl;
        dest->delay = sm->delay; dest->stalled = sm->stalled; dest->exec_pending = sm->exec_pending;
        dest->tx_count = sm->tx.count; dest->rx_count = sm->rx.count;
        for (unsigned n = 0; n < sm->tx.count; n++) dest->tx[n] = sm->tx.words[(sm->tx.head + n) % 8];
        for (unsigned n = 0; n < sm->rx.count; n++) dest->rx[n] = sm->rx.words[(sm->rx.head + n) % 8];
    }
    return 0;
}
