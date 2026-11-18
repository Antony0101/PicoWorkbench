#include "sim_internal.h"
#include <string.h>

void sim_scheduler_reset(sim_t *s) {
    s->now = s->instructions = 0;
    s->remainder = s->input_count = 0;
}

void sim_scheduler_service(sim_t *s) {
    while (s->input_count && s->inputs[0].time <= s->now) {
        input_t e = s->inputs[0];
        memmove(s->inputs, s->inputs + 1, --s->input_count * sizeof(input_t));
        sim_emit(s, SIM_EVENT_INPUT, e.kind == SIM_EVENT_GPIO ? e.pin :
             e.kind == SIM_EVENT_UART ? 30 + e.pin : 32 + e.pin, e.value);
        if (sim_peripherals_input(s, &e)) return;
    }
    sim_peripherals_service(s);
}

static void advance(sim_t *s, uint64_t cycles, uint64_t before) {
    for (unsigned i = 0; i < 2; i++) {
        if (!s->core[i].launched) continue;
        sim_cpu_systick_advance(s, i, s->core[i].systick_ctrl & 4 ? cycles :
                               s->now / 1000 - before / 1000);
    }
    sim_peripherals_advance(s, cycles);
    sim_pio_advance(s, cycles);
    sim_dma_advance(s, cycles);
}

void sim_scheduler_instruction(sim_t *s) {
    uint64_t before = s->now;
    uint64_t numerator = (uint64_t)s->remainder + 1000000000u;
    s->now += numerator / s->hz;
    s->remainder = (uint32_t)(numerator % s->hz);
    s->instructions++;
    s->core[s->active_core].instructions++;
    advance(s, 1, before);
}

static bool runnable(core_t *c) {
    if (!c->launched) return false;
    if ((c->sleeping == 1 || c->sleeping == 2) && ((c->nvic_pending & c->nvic_enable) || c->systick_pending ||
        c->pendsv_pending || (c->sleeping == 2 && c->event_flag))) {
        if (c->sleeping == 2) c->event_flag = 0;
        c->sleeping = 0;
    }
    return !c->sleeping;
}

int sim_run_until(sim_t *s, uint64_t deadline, uint32_t budget) {
    if (!s) return SIM_FAULT;
    if (!s->loaded) return s->reason = SIM_NO_IMAGE;
    if (s->reason == SIM_FAULT) return SIM_FAULT;
    if (s->request_pending) return SIM_DEVICE_REQUEST;
    if (deadline < s->now) { sim_fail(s, "Run deadline is in the past"); return SIM_FAULT; }
    if (s->reason == SIM_BREAKPOINT) s->core[s->break_core].skip_break = 1;
    s->reason = SIM_BUDGET; s->deadline = deadline;
    sim_scheduler_service(s);
    if (s->reason == SIM_FAULT) return SIM_FAULT;
    if (deadline == s->now) return s->reason = SIM_DEADLINE;
    if (!budget) return SIM_BUDGET;
    uint64_t start = s->instructions;
    unsigned redirects = 0, event_work = 0;
    while (s->instructions - start < budget && s->reason == SIM_BUDGET) {
        sim_multicore_service(s); /* context changes are outside the CPU backend */
        if (s->reason == SIM_FAULT) break;
        sim_scheduler_service(s);
        bool ready[2] = {runnable(&s->core[0]), runnable(&s->core[1])};
        if (!ready[0] && !ready[1]) {
            uint64_t next = deadline;
            if (++event_work > budget) break;
            if (s->input_count && s->inputs[0].time < next) next = s->inputs[0].time;
            next = sim_peripherals_next_event(s, next);
            next = sim_cpu_next_event(s, next);
            next = sim_pio_next_event(s, next);
            next = sim_dma_next_event(s, next);
            if (next == UINT64_MAX) { s->reason = SIM_IDLE; break; }
            uint64_t before = s->now, elapsed = next - before;
            uint64_t cycles = (elapsed / 1000000000u) * s->hz +
                (elapsed % 1000000000u) * s->hz / 1000000000u;
            s->now = next; s->remainder = 0;
            advance(s, cycles, before);
            if (s->reason == SIM_FAULT) break;
            sim_scheduler_service(s);
            if (s->now >= deadline) { s->reason = SIM_DEADLINE; break; }
            continue;
        }
        unsigned core = ready[s->next_core] ? s->next_core : 1 - s->next_core;
        if (sim_cpu_select(s, core)) break;
        uint64_t before = s->instructions;
        uint32_t remaining = budget - (s->instructions - start);
        sim_cpu_execute(s, ready[0] && ready[1] ? 1 : remaining);
        if (s->instructions != before) s->next_core = 1 - core;
        sim_scheduler_service(s);
        if (s->request_pending && s->reason == SIM_BUDGET) s->reason = SIM_DEVICE_REQUEST;
        if (s->reason != SIM_BUDGET) break;
        if (s->instructions == before) {
            if (!s->redirected) break;
            if (++redirects > budget * 2u + 32u) { sim_fail(s, "Exception redirect limit exceeded"); break; }
        }
    }
    if (s->request_pending && s->reason == SIM_BUDGET) s->reason = SIM_DEVICE_REQUEST;
    return s->reason;
}

int sim_step(sim_t *s) {
    if (!s) return SIM_FAULT;
    s->single_step = true;
    int reason = sim_run_until(s, UINT64_MAX, 1);
    s->single_step = false;
    return reason;
}

static int schedule(sim_t *s, uint64_t time, uint32_t kind, uint32_t pin, uint32_t value) {
    if (!s || time < s->now || s->input_count == INPUT_SIZE) return -1;
    unsigned i = s->input_count++;
    while (i && s->inputs[i - 1].time > time) { s->inputs[i] = s->inputs[i - 1]; i--; }
    s->inputs[i] = (input_t){time, kind, pin, value}; return 0;
}

int sim_schedule_gpio(sim_t *s, uint64_t time, uint32_t pin, uint32_t drive) {
    if (pin >= 30 || drive > SIM_PULL_HIGH) return -1;
    return schedule(s, time, SIM_EVENT_GPIO, pin, drive);
}

int sim_schedule_uart(sim_t *s, uint64_t time, uint32_t uart, uint32_t value) {
    if (uart > 1 || value > 255) return -1;
    return schedule(s, time, SIM_EVENT_UART, uart, value);
}

int sim_schedule_adc(sim_t *s, uint64_t time, uint32_t channel, uint32_t value) {
    if (channel > 4 || value > 4095) return -1;
    return schedule(s, time, 6, channel, value);
}
