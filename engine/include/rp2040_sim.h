#ifndef RP2040_SIM_H
#define RP2040_SIM_H
#include <stddef.h>
#include <stdint.h>

/* CFFI BEGIN */
typedef struct sim sim_t;
enum { SIM_ABI_VERSION = 1, SIM_GPIO_COUNT = 30 };
enum { SIM_BUDGET = 0, SIM_DEADLINE = 1, SIM_BREAKPOINT = 2,
       SIM_IDLE = 3, SIM_FAULT = 4, SIM_NO_IMAGE = 5, SIM_DEVICE_REQUEST = 6 };
enum { SIM_EVENT_GPIO = 1, SIM_EVENT_UART = 2, SIM_EVENT_INPUT = 3 };
enum { SIM_LOW = 0, SIM_HIGH = 1, SIM_FLOATING = 2, SIM_CONFLICT = 3,
       SIM_PULL_LOW = 4, SIM_PULL_HIGH = 5 };
typedef struct {
    uint64_t time_ns;
    uint64_t sequence;
    uint32_t kind;
    uint32_t source;
    uint32_t value;
} sim_event_t;
typedef struct {
    uint64_t time_ns;
    uint64_t instructions;
    uint64_t dropped_events;
    uint32_t registers[16];
    uint32_t xpsr;
    uint32_t gpio_out;
    uint32_t gpio_oe;
    uint32_t gpio_in;
    uint32_t gpio_floating;
    uint32_t gpio_conflict;
    uint32_t clock_hz;
    uint32_t stop_reason;
    uint32_t loaded;
    uint32_t pwm[8][4];
} sim_snapshot_t;
typedef struct {
    uint64_t id;
    uint64_t time_ns;
    uint32_t bus;
    uint32_t controller;
    uint32_t target;
    uint32_t value;
    uint32_t flags;
} sim_device_request_t;
enum { SIM_PIO_STALL_NONE = 0, SIM_PIO_STALL_WAIT = 1,
       SIM_PIO_STALL_TX = 2, SIM_PIO_STALL_RX = 3, SIM_PIO_STALL_IRQ = 4 };
typedef struct {
    uint32_t enabled, pc, x, y, isr, osr, in_count, out_count;
    uint32_t clkdiv, execctrl, shiftctrl, pinctrl, delay, stalled, exec_pending;
    uint32_t tx_count, rx_count, tx[8], rx[8];
} sim_pio_sm_snapshot_t;
typedef struct {
    uint32_t irq, fdebug, out, oe;
    uint32_t instructions[32];
    sim_pio_sm_snapshot_t sm[4];
} sim_pio_snapshot_t;
typedef struct {
    uint32_t read_addr, write_addr, remaining, reload, ctrl, waiting;
} sim_dma_channel_snapshot_t;
typedef struct {
    uint32_t intr, inte[2], intf[2], ints[2], timers[4];
    sim_dma_channel_snapshot_t channels[12];
} sim_dma_snapshot_t;
typedef struct {
    uint64_t instructions;
    uint32_t launched, sleeping, registers[16], xpsr, msp, psp, control, primask;
    uint32_t nvic_enable, nvic_pending, irq_levels, vtor, scr;
    uint32_t systick_ctrl, systick_load, systick_value, event_flag;
    uint32_t fifo_status, rx_count, rx[8];
} sim_core_snapshot_t;
typedef struct {
    sim_core_snapshot_t core[2];
    uint32_t spinlocks, spinlock_owner[32], boot_stage, proc1_off, next_core, active_core;
} sim_cores_snapshot_t;
uint32_t sim_abi_version(void);
sim_t *sim_create(uint32_t clock_hz);
void sim_destroy(sim_t *sim);
const char *sim_error(const sim_t *sim);
int sim_load_elf(sim_t *sim, const uint8_t *data, size_t length);
/* SDK 2.2.0 profile: flash+256 vectors, physical load addresses, ROM ABI shims.
 * Executes crt0/runtime, skips ROM boot/boot2. Reset retains the selected mode. */
int sim_load_elf_sdk(sim_t *sim, const uint8_t *data, size_t length);
/* Execute an externally supplied 16 KiB RP2040 ROM from its reset vectors. */
int sim_load_flash(sim_t *sim, const uint8_t *data, size_t length, const uint8_t *rom, size_t rom_length);
int sim_load_uf2(sim_t *sim, const uint8_t *data, size_t length, const uint8_t *rom, size_t rom_length);
int sim_reset(sim_t *sim);
int sim_run_until(sim_t *sim, uint64_t deadline_ns, uint32_t instruction_budget);
int sim_step(sim_t *sim);
int sim_snapshot(sim_t *sim, sim_snapshot_t *out);
int sim_peek_memory(sim_t *sim, uint32_t address, uint8_t *out, size_t length);
int sim_breakpoint(sim_t *sim, uint32_t address, int enabled);
int sim_connect_gpio(sim_t *sim, uint32_t pin, uint32_t net);
int sim_get_device_request(sim_t *sim, sim_device_request_t *out);
int sim_complete_device_request(sim_t *sim, uint64_t id, uint32_t value, int acknowledged);
int sim_schedule_gpio(sim_t *sim, uint64_t time_ns, uint32_t pin, uint32_t drive);
int sim_schedule_uart(sim_t *sim, uint64_t time_ns, uint32_t uart, uint32_t value);
int sim_schedule_adc(sim_t *sim, uint64_t time_ns, uint32_t channel, uint32_t value);
size_t sim_drain_events(sim_t *sim, sim_event_t *out, size_t capacity);
int sim_pio_snapshot(sim_t *sim, uint32_t block, sim_pio_snapshot_t *out);
int sim_dma_snapshot(sim_t *sim, sim_dma_snapshot_t *out);
int sim_cores_snapshot(sim_t *sim, sim_cores_snapshot_t *out);
/* CFFI END */
#endif
