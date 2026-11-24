#include "rp2040_sim.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void put32(unsigned char *p, unsigned v) {
    for (unsigned i = 0; i < 4; i++) p[i] = (unsigned char)(v >> (8 * i));
}
static int fixture_smoke(const char *path, int sdk) {
    FILE *file = fopen(path, "rb");
    if (!file) { perror(path); return 1; }
    assert(fseek(file, 0, SEEK_END) == 0);
    long length = ftell(file);
    assert(length > 0 && length < 16 * 1024 * 1024);
    rewind(file);
    unsigned char *data = malloc((size_t)length);
    assert(data && fread(data, 1, (size_t)length, file) == (size_t)length);
    fclose(file);
    sim_t *s = sim_create(1000000);
    assert(s && (sdk ? sim_load_elf_sdk(s, data, (size_t)length) : sim_load_elf(s, data, (size_t)length)) == 0);
    free(data);
    assert(sim_schedule_gpio(s, 3000000, 14, SIM_LOW) == 0);
    assert(sim_schedule_adc(s, 0, 0, 2321) == 0);
    sim_event_t *events = malloc(8192 * sizeof(*events));
    assert(events);
    sim_snapshot_t state;
    for (;;) {
        int reason = sim_run_until(s, 1000000000, 10000);
        if (reason == SIM_FAULT) { fprintf(stderr, "%s\n", sim_error(s)); free(events); sim_destroy(s); return 1; }
        sim_drain_events(s, events, 8192);
        if (reason == SIM_DEVICE_REQUEST) {
            sim_device_request_t r;
            assert(sim_get_device_request(s, &r) == 1);
            assert(sim_complete_device_request(s, r.id, r.bus ? r.value : 0x42, r.bus || r.target == 0x48) == 0);
        }
        assert(sim_snapshot(s, &state) == 0 && state.dropped_events == 0);
        if (reason == SIM_BREAKPOINT || reason == SIM_IDLE || state.time_ns >= 1000000000) break;
    }
    printf("fixture passed: %s (%llu instructions)\n", path, (unsigned long long)state.instructions);
    free(events); sim_destroy(s); return 0;
}

int main(int argc, char **argv) {
    if (argc == 2) return fixture_smoke(argv[1], 0);
    if (argc == 3 && strcmp(argv[1], "--sdk") == 0) return fixture_smoke(argv[2], 1);
    unsigned char elf[268] = {0};
    memcpy(elf, "\177ELF\1\1\1", 7);
    elf[16] = 2; elf[18] = 40; elf[20] = 1;
    put32(elf + 24, 0x10000009); put32(elf + 28, 52);
    elf[40] = 52; elf[42] = 32; elf[44] = 1;
    put32(elf + 52, 1); put32(elf + 56, 256);
    put32(elf + 60, 0x10000000); put32(elf + 64, 0x10000000);
    put32(elf + 68, 12); put32(elf + 72, 12); put32(elf + 76, 5);
    put32(elf + 256, 0x20042000); put32(elf + 260, 0x10000009);
    elf[264] = 42; elf[265] = 0x20; /* movs r0, #42 */
    elf[266] = 0xfe; elf[267] = 0xe7; /* b . */
    assert(sim_abi_version() == 1);
    assert(sim_create(0) == NULL);
    sim_t *s = sim_create(1000000), *other = sim_create(1000000);
    assert(s && other);
    sim_pio_snapshot_t pio;
    assert(sim_pio_snapshot(s, 0, &pio) == 0);
    assert(pio.irq == 0 && pio.out == 0 && pio.oe == 0);
    for (unsigned i = 0; i < 4; i++) {
        assert(pio.sm[i].clkdiv == 0x10000 && pio.sm[i].out_count == 32);
        assert(!pio.sm[i].enabled && !pio.sm[i].tx_count && !pio.sm[i].rx_count);
    }
    assert(sim_pio_snapshot(s, 1, &pio) == 0);
    assert(sim_pio_snapshot(s, 2, &pio) < 0);
    assert(sim_pio_snapshot(NULL, 0, &pio) < 0);
    assert(sim_pio_snapshot(s, 0, NULL) < 0);
    sim_dma_snapshot_t dma;
    assert(sim_dma_snapshot(s, &dma) == 0 && dma.intr == 0);
    for (unsigned i = 0; i < 12; i++) {
        assert(dma.channels[i].ctrl == 0 && dma.channels[i].reload == 0);
        assert(dma.channels[i].remaining == 0 && !dma.channels[i].waiting);
    }
    assert(sim_dma_snapshot(NULL, &dma) < 0);
    assert(sim_dma_snapshot(s, NULL) < 0);
    sim_cores_snapshot_t cores;
    assert(sim_cores_snapshot(s, &cores) == 0);
    assert(cores.core[0].launched && !cores.core[1].launched);
    assert(cores.core[0].registers[13] == 0x20042000 && !cores.spinlocks);
    assert(cores.core[0].fifo_status == 2 && cores.core[1].fifo_status == 2);
    assert(sim_cores_snapshot(NULL, &cores) < 0);
    assert(sim_cores_snapshot(s, NULL) < 0);
    assert(sim_run_until(s, 10000, 10) == SIM_NO_IMAGE);
    assert(sim_load_elf(s, elf, sizeof(elf)) == 0);
    assert(sim_step(s) == SIM_BUDGET);
    sim_snapshot_t state;
    assert(sim_snapshot(s, &state) == 0);
    assert(state.registers[0] == 42 && state.instructions == 1 && state.time_ns == 1000);
    assert(sim_snapshot(other, &state) == 0 && state.registers[0] == 0);
    unsigned char value[4];
    assert(sim_peek_memory(s, 0x40034000, value, 4) == 0);
    assert(sim_schedule_uart(s, 1000, 0, 'A') == 0);
    assert(sim_run_until(s, 1000, 0) == SIM_DEADLINE);
    assert(sim_peek_memory(s, 0x40034000, value, 4) == 0 && value[0] == 'A');
    assert(sim_peek_memory(s, 0x40034000, value, 4) == 0 && value[0] == 'A');
    assert(sim_peek_memory(s, 0x40034004, value, 4) < 0);
    assert(sim_breakpoint(s, 0x1000000a, 1) == 0);
    assert(sim_run_until(s, 10000, 10) == SIM_BREAKPOINT);
    assert(sim_reset(s) == 0);
    assert(sim_snapshot(s, &state) == 0 && state.instructions == 0);
    assert(sim_load_elf(s, elf, 20) < 0);
    assert(sim_reset(s) == 0);
    assert(sim_step(s) == SIM_BUDGET);
    unsigned char sdk[524] = {0};
    memcpy(sdk, elf, 256); memcpy(sdk + 512, elf + 256, 12);
    put32(sdk + 24, 0x10000109); put32(sdk + 516, 0x10000109);
    put32(sdk + 68, 268); put32(sdk + 72, 268);
    assert(sim_load_elf_sdk(NULL, sdk, sizeof(sdk)) < 0);
    assert(sim_load_elf_sdk(s, NULL, sizeof(sdk)) < 0);
    assert(sim_load_elf_sdk(s, sdk, sizeof(sdk)) == 0);
    assert(sim_step(s) == SIM_BUDGET);
    assert(sim_snapshot(s, &state) == 0 && state.registers[0] == 42);
    assert(sim_peek_memory(s, 0x10, value, 4) == 0 && value[0] == 'M' && value[1] == 'u');
    assert(sim_peek_memory(s, 0x7fe, value, 1) == 0 && value[0] == 32);
    assert(sim_peek_memory(s, 0x800, value, 4) == 0 && value[0] == 1 && value[1] == 4);
    assert(sim_peek_memory(s, 0x900, value, 4) == 0 && value[0] == 1 && value[1] == 5);
    sim_destroy(other); sim_destroy(s); sim_destroy(NULL);
    puts("engine contract passed");
    return 0;
}
