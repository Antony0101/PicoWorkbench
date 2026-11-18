#include "sim_internal.h"
#include <string.h>

static const int reg_ids[16] = {
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
};

void sim_cpu_systick_advance(sim_t *s, unsigned core, uint64_t ticks) {
    if (!(s->core[core].systick_ctrl & 1) || !ticks) return;
    uint64_t value = s->core[core].systick_value, load = s->core[core].systick_load;
    bool wrapped = false;
    if (!value) { value = load; ticks--; }
    if (ticks >= value && value) {
        ticks -= value; value = 0; wrapped = true;
        uint64_t rem = ticks % (load + 1);
        if (rem) value = load + 1 - rem;
    } else if (value) value -= ticks;
    s->core[core].systick_value = (uint32_t)value;
    if (wrapped) {
        s->core[core].systick_flag = 1;
        if (s->core[core].systick_ctrl & 2) s->core[core].systick_pending = 1;
    }
}

uint32_t sim_cpu_read(sim_t *s, uint32_t a, bool peek, bool *available) {
    *available = true;
    uint32_t off;
    if (a >= 0xe000e000u && a < 0xe000f000u) {
        off = a - 0xe000e000u;
        switch (off) {
        case 0x010: {
            uint32_t v = s->core[s->active_core].systick_ctrl | (s->core[s->active_core].systick_flag ? 0x10000u : 0);
            if (!peek) s->core[s->active_core].systick_flag = 0;
            return v;
        }
        case 0x014: return s->core[s->active_core].systick_load;
        case 0x018: return s->core[s->active_core].systick_value;
        case 0x01c: return 0; /* external reference is modeled at 1 MHz */
        case 0x100: case 0x180: return s->core[s->active_core].nvic_enable;
        case 0x200: case 0x280: return s->core[s->active_core].nvic_pending;
        case 0xd00: return 0x410cc601u;
        case 0xd04: {
            uint32_t xpsr = 0; uc_reg_read(s->cpu, UC_ARM_REG_XPSR, &xpsr);
            return (xpsr & 0x1ffu) | (s->core[s->active_core].pendsv_pending ? 1u << 28 : 0) |
                   (s->core[s->active_core].systick_pending ? 1u << 26 : 0);
        }
        case 0xd08: return s->core[s->active_core].vtor;
        case 0xd10: return s->core[s->active_core].scr;
        case 0xd1c: return (uint32_t)s->core[s->active_core].system_priority[11] << 24;
        case 0xd20: return (uint32_t)s->core[s->active_core].system_priority[14] << 16 | (uint32_t)s->core[s->active_core].system_priority[15] << 24;
        default: break;
        }
        if (off >= 0x400 && off < 0x420) {
            uint32_t v = 0;
            for (unsigned i = 0; i < 4; i++) v |= (uint32_t)s->core[s->active_core].priority[off - 0x400 + i] << (8 * i);
            return v;
        }
    }
    *available = false;
    return 0;
}

bool sim_cpu_write(sim_t *s, uint32_t a, uint32_t value) {
    uint32_t off;
    if (a >= 0xe000e000u && a < 0xe000f000u) {
        off = a - 0xe000e000u;
        switch (off) {
        case 0x010: s->core[s->active_core].systick_ctrl = value & 7; return true;
        case 0x014: s->core[s->active_core].systick_load = value & 0xffffffu; return true;
        case 0x018: s->core[s->active_core].systick_value = 0; s->core[s->active_core].systick_flag = 0; return true;
        case 0x100: s->core[s->active_core].nvic_enable |= value; return true;
        case 0x180: s->core[s->active_core].nvic_enable &= ~value; return true;
        case 0x200: s->core[s->active_core].nvic_pending |= value; return true;
        case 0x280: s->core[s->active_core].nvic_pending &= ~value; return true;
        case 0xd04:
            if (value & (1u << 28)) s->core[s->active_core].pendsv_pending = 1;
            if (value & (1u << 27)) s->core[s->active_core].pendsv_pending = 0;
            if (value & (1u << 26)) s->core[s->active_core].systick_pending = 1;
            if (value & (1u << 25)) s->core[s->active_core].systick_pending = 0;
            return true;
        case 0xd08: s->core[s->active_core].vtor = value & 0xffffff00u; return true;
        case 0xd10: s->core[s->active_core].scr = value & 0x16u; return true;
        case 0xd1c: s->core[s->active_core].system_priority[11] = (uint8_t)(value >> 24) & 0xc0; return true;
        case 0xd20:
            s->core[s->active_core].system_priority[14] = (uint8_t)(value >> 16) & 0xc0;
            s->core[s->active_core].system_priority[15] = (uint8_t)(value >> 24) & 0xc0; return true;
        default: break;
        }
        if (off >= 0x400 && off < 0x420) {
            for (unsigned i = 0; i < 4; i++) s->core[s->active_core].priority[off - 0x400 + i] = (value >> (8 * i)) & 0xc0;
            return true;
        }
    }
    return false;
}

static unsigned exception_priority(sim_t *s, unsigned exception) {
    if (exception >= 48) return 0;
    return !exception ? 256 : exception >= 16 ? s->core[s->active_core].priority[exception - 16] : s->core[s->active_core].system_priority[exception];
}

static unsigned pending_exception(sim_t *s) {
    uint32_t primask = 0, xpsr = 0;
    uc_reg_read(s->cpu, UC_ARM_REG_PRIMASK, &primask);
    if (primask & 1) return 0;
    uc_reg_read(s->cpu, UC_ARM_REG_XPSR, &xpsr);
    unsigned best = 0, priority = exception_priority(s, xpsr & 0x1ffu);
    if (s->core[s->active_core].pendsv_pending && s->core[s->active_core].system_priority[14] < priority) {
        best = 14; priority = s->core[s->active_core].system_priority[14];
    }
    if (s->core[s->active_core].systick_pending && s->core[s->active_core].system_priority[15] < priority) {
        best = 15; priority = s->core[s->active_core].system_priority[15];
    }
    uint32_t pending = s->core[s->active_core].nvic_pending & s->core[s->active_core].nvic_enable;
    for (unsigned i = 0; i < 32; i++) {
        if ((pending & (1u << i)) && (s->core[s->active_core].priority[i] < priority ||
            (s->core[s->active_core].priority[i] == priority && best && i + 16 < best))) {
            best = i + 16; priority = s->core[s->active_core].priority[i];
        }
    }
    return best;
}

static int exception_enter(sim_t *s, unsigned exception, uint32_t return_pc) {
    uint32_t frame[8], control = 0, sp = 0, xpsr = 0, handler = 0;
    uc_reg_read(s->cpu, UC_ARM_REG_CONTROL, &control);
    uc_reg_read(s->cpu, UC_ARM_REG_XPSR, &xpsr);
    bool thread = !(xpsr & 0x1ffu), use_psp = thread && (control & 2);
    uc_reg_read(s->cpu, use_psp ? UC_ARM_REG_PSP : UC_ARM_REG_MSP, &sp);
    uint32_t aligned = (sp - 32) & ~7u;
    if (sp < RAM_BASE + 32 || sp > RAM_BASE + RAM_SIZE || aligned < RAM_BASE)
        return sim_fail(s, "Exception stack outside SRAM: 0x%08x", sp);
    const int stacked[6] = {UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                            UC_ARM_REG_R3, UC_ARM_REG_R12, UC_ARM_REG_LR};
    for (unsigned i = 0; i < 6; i++) uc_reg_read(s->cpu, stacked[i], &frame[i]);
    frame[6] = return_pc;
    frame[7] = xpsr | ((sp & 4) ? 1u << 9 : 0);
    if (uc_mem_read(s->cpu, (uint64_t)s->core[s->active_core].vtor + 4 * exception, &handler, 4) || !(handler & 1))
        return sim_fail(s, "Missing Thumb vector for exception %u", exception);
    if (uc_mem_write(s->cpu, aligned, frame, sizeof(frame))) return sim_fail(s, "Cannot write exception frame");
    uc_reg_write(s->cpu, use_psp ? UC_ARM_REG_PSP : UC_ARM_REG_MSP, &aligned);
    uint32_t lr = !thread ? 0xfffffff1u : use_psp ? 0xfffffffdu : 0xfffffff9u;
    uint32_t next_xpsr = (xpsr & 0xf8000000u) | 0x01000000u | exception;
    uc_reg_write(s->cpu, UC_ARM_REG_XPSR, &next_xpsr);
    uint32_t msp = 0;
    uc_reg_read(s->cpu, UC_ARM_REG_MSP, &msp);
    uc_reg_write(s->cpu, UC_ARM_REG_SP, &msp);
    uc_reg_write(s->cpu, UC_ARM_REG_LR, &lr);
    uc_reg_write(s->cpu, UC_ARM_REG_PC, &handler);
    if (exception >= 16) s->core[s->active_core].nvic_pending &= ~(1u << (exception - 16));
    if (exception == 14) s->core[s->active_core].pendsv_pending = 0;
    if (exception == 15) s->core[s->active_core].systick_pending = 0;
    s->core[s->active_core].sleeping = 0; s->redirected = 1;
    uc_emu_stop(s->cpu);
    return 0;
}

static int exception_return(sim_t *s, uint32_t token) {
    if (token != 0xfffffff1u && token != 0xfffffff9u && token != 0xfffffffdu)
        return sim_fail(s, "Unsupported EXC_RETURN 0x%08x", token);
    bool use_psp = token & 4;
    uint32_t sp = 0, frame[8], control = 0;
    uc_reg_read(s->cpu, use_psp ? UC_ARM_REG_PSP : UC_ARM_REG_MSP, &sp);
    if (sp < RAM_BASE || (uint64_t)sp + sizeof(frame) > RAM_BASE + RAM_SIZE ||
        uc_mem_read(s->cpu, sp, frame, sizeof(frame))) return sim_fail(s, "Invalid exception return stack");
    if (!(frame[7] & 0x01000000u)) return sim_fail(s, "Exception return without Thumb state");
    const int stacked[6] = {UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                            UC_ARM_REG_R3, UC_ARM_REG_R12, UC_ARM_REG_LR};
    for (unsigned i = 0; i < 6; i++) uc_reg_write(s->cpu, stacked[i], &frame[i]);
    sp += 32 + ((frame[7] & (1u << 9)) ? 4 : 0);
    frame[7] &= ~(1u << 9);
    uc_reg_write(s->cpu, use_psp ? UC_ARM_REG_PSP : UC_ARM_REG_MSP, &sp);
    uc_reg_read(s->cpu, UC_ARM_REG_CONTROL, &control);
    if (token & 8) {
        control = (control & ~2u) | (use_psp ? 2u : 0);
        uc_reg_write(s->cpu, UC_ARM_REG_CONTROL, &control);
    }
    uc_reg_write(s->cpu, UC_ARM_REG_XPSR, &frame[7]);
    uc_reg_write(s->cpu, UC_ARM_REG_SP, &sp);
    uint32_t return_pc = frame[6] | 1u;
    uc_reg_write(s->cpu, UC_ARM_REG_PC, &return_pc);
    s->core[s->active_core].nvic_pending |= s->core[s->active_core].irq_levels;
    s->redirected = 1; uc_emu_stop(s->cpu);
    return 0;
}

static void instruction_hook(uc_engine *cpu, uint64_t address, uint32_t size, void *data) {
    sim_t *s = data;
    sim_scheduler_service(s);
    if (s->reason == SIM_FAULT) { uc_emu_stop(cpu); return; }
    if (s->request_pending) { s->reason = SIM_DEVICE_REQUEST; uc_emu_stop(cpu); return; }
    if (s->now >= s->deadline) { s->reason = SIM_DEADLINE; uc_emu_stop(cpu); return; }
    if (!s->core[s->active_core].launched ||
        (!s->core[1].launched && !s->proc1_off && s->fifo[1].count && s->fifo[0].count < 8) ||
        (s->boot_notify && s->fifo[0].count < 8)) {
        s->redirected = 1; uc_emu_stop(cpu); return;
    }
    if (s->boot_pending || s->core1_reset_pending ||
        (s->core[1 - s->active_core].launched &&
         (s->core[1 - s->active_core].sleeping == 1 || s->core[1 - s->active_core].sleeping == 2))) {
        /* Wake-up is resolved outside the backend; do not switch context here. */
        core_t *other = &s->core[1 - s->active_core];
        if (s->boot_pending || s->core1_reset_pending ||
            (other->nvic_pending & other->nvic_enable) || other->systick_pending ||
            other->pendsv_pending || (other->sleeping == 2 && other->event_flag)) {
            s->redirected = 1; uc_emu_stop(cpu); return;
        }
    }
    unsigned exception = pending_exception(s);
    if (exception) { exception_enter(s, exception, (uint32_t)address); return; }
    if (s->sdk_mode && sim_rom_call(s, (uint32_t)address)) return;
    bool executable = false;
    if (s->rom_mode && (address + size <= 0x4000 ||
        (address >= RAM_BASE && address + size <= RAM_BASE + RAM_SIZE) ||
        (address >= FLASH_BASE && address + size <= FLASH_BASE + FLASH_SIZE))) executable = true;
    for (unsigned i = 0; i < s->segment_count; i++) {
        segment_t seg = s->segments[i];
        if ((seg.flags & 1) && address >= seg.start && address + size <= (uint64_t)seg.start + seg.size)
            executable = true;
    }
    if (!executable) { sim_fail(s, "Execution outside ELF executable segments at 0x%08llx",
                            (unsigned long long)address); return; }
    if (!s->core[s->active_core].skip_break && !s->single_step) {
        for (unsigned i = 0; i < s->break_count; i++) {
            if (s->breaks[i] == address) { s->break_core = s->active_core; s->reason = SIM_BREAKPOINT; uc_emu_stop(cpu); return; }
        }
    }
    s->core[s->active_core].skip_break = 0;
    uint16_t opcode = 0;
    uc_mem_read(cpu, address, &opcode, 2);
    if ((opcode & 0xff00) == 0xbe00) {
        uint32_t next = ((uint32_t)address + 2) | 1u;
        uc_reg_write(cpu, UC_ARM_REG_PC, &next);
        s->break_core = s->active_core; s->reason = SIM_BREAKPOINT; uc_emu_stop(cpu); return;
    }
    uint32_t active = 0;
    uc_reg_read(cpu, UC_ARM_REG_IPSR, &active);
    uint32_t target = 0;
    if ((opcode & 0xff87) == 0x4700) {
        uc_reg_read(cpu, reg_ids[(opcode >> 3) & 15], &target);
    } else if ((opcode & 0xff00) == 0xbd00) {
        uint32_t sp = 0, count = 0;
        uc_reg_read(cpu, UC_ARM_REG_SP, &sp);
        for (unsigned i = 0; i < 8; i++) if (opcode & (1u << i)) count++;
        if (uc_mem_read(cpu, (uint64_t)sp + 4 * count, &target, 4)) target = 0;
        if ((active && target >= 0xfffffff0u) || (!active && s->active_core == 1 && target == CORE1_RETURN)) {
            for (unsigned i = 0; i < 8; i++) {
                if (opcode & (1u << i)) {
                    uint32_t v = 0; uc_mem_read(cpu, sp, &v, 4);
                    uc_reg_write(cpu, reg_ids[i], &v); sp += 4;
                }
            }
            sp += 4; uc_reg_write(cpu, UC_ARM_REG_SP, &sp);
        }
    }
    if (!active && s->active_core == 1 && target == CORE1_RETURN) {
        uint32_t pc = CORE1_RETURN;
        sim_scheduler_instruction(s);
        uc_reg_write(cpu, UC_ARM_REG_PC, &pc);
        s->core[1].sleeping = 3;
        s->redirected = 1; uc_emu_stop(cpu); return;
    }
    if (active && target >= 0xfffffff0u) { sim_scheduler_instruction(s); exception_return(s, target); return; }
    if (opcode == 0xbf40) s->core[0].event_flag = s->core[1].event_flag = 1;
    if (opcode == 0xbf20 || opcode == 0xbf30) {
        uint32_t next = ((uint32_t)address + 2) | 1u;
        uc_reg_write(cpu, UC_ARM_REG_PC, &next);
        sim_scheduler_instruction(s);
        s->core[s->active_core].sleeping = opcode == 0xbf20 && s->core[s->active_core].event_flag ? 0 : opcode == 0xbf20 ? 2 : 1;
        if (opcode == 0xbf20) s->core[s->active_core].event_flag = 0;
        s->redirected = 1; uc_emu_stop(cpu); return;
    }
    if ((opcode & 0xff00) == 0xdf00) {
        sim_scheduler_instruction(s);
        exception_enter(s, 11, (uint32_t)address + 2); return;
    }
    sim_scheduler_instruction(s);
}

int sim_cpu_open(sim_t *s) {
    sim_cpu_close(s);
    uc_err err = uc_open(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS, &s->cpu);
    if (err) return sim_fail(s, "Unicorn: %s", uc_strerror(err));
    err = uc_ctl_set_cpu_model(s->cpu, UC_CPU_ARM_CORTEX_M0);
    if (!err) err = sim_bus_map(s);
    uc_hook hook;
    if (!err) err = uc_hook_add(s->cpu, &hook, UC_HOOK_CODE, (void *)instruction_hook, s, 1, 0);
    if (err) return sim_fail(s, "Unicorn setup: %s", uc_strerror(err));
    uint32_t xpsr = 0x01000000u;
    uc_reg_write(s->cpu, UC_ARM_REG_XPSR, &xpsr);
    err = uc_context_alloc(s->cpu, &s->initial_context);
    if (!err) err = uc_context_save(s->cpu, s->initial_context);
    for (unsigned i = 0; i < 2 && !err; i++) {
        err = uc_context_alloc(s->cpu, &s->contexts[i]);
        if (!err) err = uc_context_save(s->cpu, s->contexts[i]);
    }
    if (err) return sim_fail(s, "CPU context setup: %s", uc_strerror(err));
    s->active_core = 0;
    return sim_cpu_init_core(s, 0, s->vector_base, s->stack, s->entry);
}

int sim_cpu_select(sim_t *s, unsigned core) {
    if (core == s->active_core) return 0;
    uc_err err = uc_context_save(s->cpu, s->contexts[s->active_core]);
    if (!err) err = uc_context_restore(s->cpu, s->contexts[core]);
    if (err) return sim_fail(s, "CPU context switch: %s", uc_strerror(err));
    s->active_core = core;
    return 0;
}

/* Only called outside uc_emu_start: callbacks enqueue startup/reset work. */
int sim_cpu_init_core(sim_t *s, unsigned core, uint32_t vtor, uint32_t sp, uint32_t entry) {
    uc_err err = uc_context_save(s->cpu, s->contexts[s->active_core]);
    if (!err) err = uc_context_restore(s->cpu, s->initial_context);
    uint32_t pc = entry | 1u;
    if (!err) err = uc_reg_write(s->cpu, UC_ARM_REG_MSP, &sp);
    if (!err) err = uc_reg_write(s->cpu, UC_ARM_REG_SP, &sp);
    if (!err) err = uc_reg_write(s->cpu, UC_ARM_REG_PC, &pc);
    uint32_t lr = core == 1 && entry ? CORE1_RETURN : 0;
    if (!err) err = uc_reg_write(s->cpu, UC_ARM_REG_LR, &lr);
    if (!err) err = uc_context_save(s->cpu, s->contexts[core]);
    if (!err) err = uc_context_restore(s->cpu, s->contexts[s->active_core]);
    if (err) return sim_fail(s, "CPU context initialization: %s", uc_strerror(err));
    uint64_t retired = s->core[core].instructions;
    memset(&s->core[core], 0, sizeof(core_t));
    s->core[core].instructions = retired;
    s->core[core].vtor = vtor;
    s->core[core].launched = entry != 0;
    return 0;
}

void sim_cpu_reset(sim_t *s) {
    memset(s->core, 0, sizeof(s->core));
    s->core[0].vtor = s->core[1].vtor = FLASH_BASE;
    s->active_core = s->next_core = s->break_core = s->redirected = 0;
    s->single_step = false;
}

void sim_cpu_irq_levels(sim_t *s, unsigned core, uint32_t levels) {
    core_t *c = &s->core[core];
    uint32_t rising = levels & ~c->irq_levels;
    c->nvic_pending |= rising;
    if (rising && (c->scr & 16)) c->event_flag = 1;
    c->irq_levels = levels;
}

uint64_t sim_cpu_next_event(sim_t *s, uint64_t next) {
    for (unsigned i = 0; i < 2; i++) {
        core_t *c = &s->core[i];
        if (c->launched && (c->systick_ctrl & 3) == 3 && c->systick_load) {
            uint64_t ticks = c->systick_value ? c->systick_value : c->systick_load + 1;
            uint64_t rate = c->systick_ctrl & 4 ? s->hz : 1000000u;
            uint64_t delta = (ticks * 1000000000u + rate - 1) / rate;
            if (delta < UINT64_MAX - s->now && s->now + delta < next) next = s->now + delta;
        }
    }
    return next;
}

void sim_cpu_execute(sim_t *s, uint32_t budget) {
    uint32_t pc = 0;
    uc_reg_read(s->cpu, UC_ARM_REG_PC, &pc);
    s->redirected = 0;
    uc_err err = uc_emu_start(s->cpu, pc | 1u, 0, 0, budget);
    if (err && s->reason != SIM_FAULT) {
        uc_reg_read(s->cpu, UC_ARM_REG_PC, &pc);
        sim_fail(s, "CPU fault at 0x%08x: %s", pc, uc_strerror(err));
    }
}

void sim_cpu_snapshot(sim_t *s, sim_snapshot_t *out) {
    uc_context_save(s->cpu, s->contexts[s->active_core]);
    for (unsigned i = 0; i < 16; i++) uc_context_reg_read(s->contexts[0], reg_ids[i], &out->registers[i]);
    uc_context_reg_read(s->contexts[0], UC_ARM_REG_XPSR, &out->xpsr);
}

void sim_cpu_core_snapshot(sim_t *s, unsigned core, sim_core_snapshot_t *out) {
    uc_context *ctx = s->contexts[core];
    for (unsigned i = 0; i < 16; i++) uc_context_reg_read(ctx, reg_ids[i], &out->registers[i]);
    uc_context_reg_read(ctx, UC_ARM_REG_XPSR, &out->xpsr);
    uc_context_reg_read(ctx, UC_ARM_REG_MSP, &out->msp);
    uc_context_reg_read(ctx, UC_ARM_REG_PSP, &out->psp);
    uc_context_reg_read(ctx, UC_ARM_REG_CONTROL, &out->control);
    uc_context_reg_read(ctx, UC_ARM_REG_PRIMASK, &out->primask);
}

void sim_cpu_close(sim_t *s) {
    for (unsigned i = 0; i < 2; i++) {
        if (s->contexts[i]) { uc_context_free(s->contexts[i]); s->contexts[i] = NULL; }
    }
    if (s->initial_context) { uc_context_free(s->initial_context); s->initial_context = NULL; }
    if (s->cpu) { uc_close(s->cpu); s->cpu = NULL; }
}

void sim_cpu_stop(sim_t *s) {
    if (s->cpu) uc_emu_stop(s->cpu);
}

int sim_breakpoint(sim_t *s, uint32_t address, int enabled) {
    if (!s || (address & 1)) return -1;
    for (unsigned i = 0; i < s->break_count; i++) {
        if (s->breaks[i] == address) {
            if (!enabled) s->breaks[i] = s->breaks[--s->break_count];
            return 0;
        }
    }
    if (!enabled) return 0;
    if (s->break_count == BREAK_SIZE) return -1;
    s->breaks[s->break_count++] = address; return 0;
}
