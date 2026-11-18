#include "sim_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

int sim_fail(sim_t *s, const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(s->error, sizeof(s->error), fmt, args);
    va_end(args);
    s->reason = SIM_FAULT;
    sim_cpu_stop(s);
    return -1;
}

void sim_emit(sim_t *s, uint32_t kind, uint32_t source, uint32_t value) {
    sim_event_t e = {s->now, s->sequence++, kind, source, value};
    if (s->trace_count == TRACE_SIZE) { s->dropped++; return; }
    s->trace[(s->trace_head + s->trace_count++) % TRACE_SIZE] = e;
}

uint32_t sim_abi_version(void) { return SIM_ABI_VERSION; }

sim_t *sim_create(uint32_t hz) {
    if (hz < 1000 || hz > 133000000) return NULL;
    sim_t *s = calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->flash = calloc(1, FLASH_SIZE); s->ram = calloc(1, RAM_SIZE);
    if (!s->flash || !s->ram) { sim_destroy(s); return NULL; }
    s->hz = hz; s->stack = RAM_BASE + RAM_SIZE; s->entry = FLASH_BASE | 1; s->vector_base = FLASH_BASE;
    if (sim_reset(s)) { sim_destroy(s); return NULL; }
    return s;
}

void sim_destroy(sim_t *s) {
    if (!s) return;
    sim_cpu_close(s);
    free(s->flash); free(s->ram); free(s->rom); free(s);
}

const char *sim_error(const sim_t *s) { return s ? s->error : "Invalid simulator handle"; }

int sim_reset(sim_t *s) {
    if (!s) return -1;
    sim_scheduler_reset(s);
    sim_cpu_reset(s);
    sim_gpio_reset(s);
    sim_peripherals_reset(s);
    sim_pio_reset(s);
    sim_dma_reset(s);
    sim_multicore_reset(s);
    sim_system_reset(s);
    sim_flash_reset(s);
    sim_rtc_reset(s);
    s->sequence = s->dropped = s->trace_count = s->trace_head = 0;
    s->reason = s->loaded ? SIM_BUDGET : SIM_NO_IMAGE; s->error[0] = 0;
    return sim_cpu_open(s);
}

int sim_snapshot(sim_t *s, sim_snapshot_t *out) {
    if (!s || !out) return -1;
    memset(out, 0, sizeof(*out));
    out->time_ns = s->now; out->instructions = s->instructions; out->dropped_events = s->dropped;
    sim_cpu_snapshot(s, out);
    out->gpio_out = s->out; out->gpio_oe = s->oe; out->gpio_in = sim_gpio_sample(s);
    out->gpio_floating = s->floating; out->gpio_conflict = s->conflict;
    out->clock_hz = s->hz; out->stop_reason = s->reason; out->loaded = s->loaded;
    for (unsigned i = 0; i < 8; i++) {
        out->pwm[i][0] = s->pwm[i].csr; out->pwm[i][1] = s->pwm[i].div;
        out->pwm[i][2] = s->pwm[i].cc; out->pwm[i][3] = s->pwm[i].top;
    }
    return 0;
}

size_t sim_drain_events(sim_t *s, sim_event_t *out, size_t capacity) {
    if (!s || !out) return 0;
    size_t n = s->trace_count < capacity ? s->trace_count : capacity;
    for (size_t i = 0; i < n; i++) out[i] = s->trace[(s->trace_head + i) % TRACE_SIZE];
    s->trace_head = (s->trace_head + (unsigned)n) % TRACE_SIZE; s->trace_count -= (unsigned)n;
    return n;
}
