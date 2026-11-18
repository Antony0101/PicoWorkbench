#ifndef RP2040_SIM_INTERNAL_H
#define RP2040_SIM_INTERNAL_H

/* Private state belongs to one simulation owner; no host threads are added. */
#include "rp2040_sim.h"
#include <unicorn/unicorn.h>
#include <unicorn/arm.h>
#include <stdbool.h>

#define FLASH_BASE 0x10000000u
#define FLASH_SIZE 0x00200000u
#define RAM_BASE 0x20000000u
#define RAM_SIZE 0x00042000u
#define PIN_MASK 0x3fffffffu
#define TRACE_SIZE 8192u
#define INPUT_SIZE 512u
#define BREAK_SIZE 64u
#define MAX_SEGMENTS 32u
#define CORE1_RETURN 0xffffffe1u

typedef struct { uint32_t start, size, flags; } segment_t;
typedef struct { uint64_t time; uint32_t kind, pin, value; } input_t;
typedef struct { struct sim *sim; uint32_t base; } region_t;
typedef struct {
    uint32_t cr, ibrd, fbrd, lcr, imsc, dmacr;
    bool tx_irq;
    uint8_t rx[256];
    unsigned head, count;
} uart_t;
typedef struct {
    uint32_t con, tar, enabled, mask, raw, abort_source, rx_tl, tx_tl;
    uint32_t timings[4], sda_hold;
    uint8_t rx[16];
    unsigned head, count;
} i2c_t;
typedef struct {
    uint32_t cr0, cr1, cpsr, mask;
    uint8_t rx[8];
    unsigned head, count;
} spi_t;
typedef struct { uint32_t csr, div, ctr, cc, top; uint64_t phase; } pwm_t;
typedef struct { uint32_t words[8]; unsigned head, count; } pio_fifo_t;
typedef struct {
    uint32_t clkdiv, execctrl, shiftctrl, pinctrl;
    uint32_t pc, x, y, isr, osr, in_count, out_count, phase, delay;
    uint64_t phase_cycle;
    uint16_t exec_instr;
    bool exec_pending, autopush_pending;
    unsigned stalled;
    int irq_wait;
    pio_fifo_t tx, rx;
} pio_sm_t;
typedef struct {
    uint32_t ctrl, irq, fdebug, input_sync_bypass, out, oe, inte[2], intf[2];
    uint16_t memory[32];
    pio_sm_t sm[4];
} pio_t;
typedef struct {
    uint32_t read, write, reload, remaining, ctrl, errors, credits;
    bool busy;
} dma_channel_t;
typedef struct {
    dma_channel_t channel[12];
    uint32_t intr, inte[2], intf[2], timer[4], phase[4], served_high;
    unsigned last;
} dma_t;
typedef struct {
    uint64_t instructions;
    uint32_t nvic_enable, nvic_pending, irq_levels, vtor, scr;
    uint8_t priority[32], system_priority[16];
    uint32_t systick_ctrl, systick_load, systick_value;
    int systick_flag, systick_pending, pendsv_pending, sleeping, event_flag, skip_break;
    bool launched;
    uint32_t dividend, divisor, quotient, div_remainder;
    bool div_dirty;
} core_t;
typedef struct {
    uint32_t resets, wdsel, xosc_ctrl, xosc_startup, xosc_badwrite;
    bool xosc_enabled;
    uint32_t pll[2][4], clock_ctrl[10], clock_div[10], resus, tick, scratch[8];
    uint32_t wake_sleep[4], sync_bypass[2], usb_sie_ctrl;
    uint32_t rosc_ctrl, random_state;
} system_t;
typedef struct {
    uint32_t div, setup[2], ctrl, alarm[2], date, time, latched_date, inte, intf;
    uint64_t tick_ns;
} rtc_t;
typedef struct { uint32_t words[8]; unsigned head, count, errors; } core_fifo_t;
typedef struct {
    uint32_t io[6], pads[6], ssi[64], xip_ctrl;
    uint32_t address, position, command;
    uint32_t rx[16];
    unsigned head, count, force_cs;
    bool active, wel;
    uint8_t sr1, sr2;
} flash_t;

struct sim {
    uc_engine *cpu;
    uc_context *contexts[2], *initial_context;
    core_t core[2];
    unsigned active_core, next_core, break_core;
    core_fifo_t fifo[2]; /* indexed by receiving core */
    uint32_t spinlocks, spinlock_owner[32], proc1_off, proc1_on;
    uint32_t boot_words[6];
    unsigned boot_stage;
    bool boot_pending, core1_reset_pending, boot_notify, single_step;
    uint8_t *flash, *ram, *rom;
    segment_t segments[MAX_SEGMENTS];
    unsigned segment_count;
    uint32_t entry, stack, vector_base, hz, remainder;
    bool sdk_mode, rom_mode;
    uint64_t now, instructions, deadline, sequence, dropped;
    int loaded, reason, redirected;
    char error[256];
    uint32_t out, oe, input, floating, conflict;
    uint32_t ctrl[30], pads[30], drive[30], net[30];
    uint32_t alarm[4], armed, intr, inte, intf;
    uint64_t alarm_time[4];
    uint32_t latched_high;
    uint32_t gpio_edges[4], gpio_inte[2][4], gpio_intf[2][4];
    uart_t uart[2];
    i2c_t i2c[2];
    spi_t spi[2];
    pwm_t pwm[8];
    pio_t pio[2];
    uint64_t pio_cycles;
    dma_t dma;
    system_t system;
    flash_t flash_hw;
    rtc_t rtc;
    uint32_t pwm_output, pwm_intr, pwm_inte, pwm_intf;
    uint32_t adc_cs, adc_result, adc_fcs, adc_div, adc_inte, adc_intf, adc_input[5];
    uint64_t adc_due;
    uint16_t adc_fifo[4];
    unsigned adc_head, adc_count;
    sim_device_request_t request;
    uint64_t request_id;
    int request_pending;
    region_t regions[34];
    uint32_t breaks[BREAK_SIZE];
    unsigned break_count;
    input_t inputs[INPUT_SIZE];
    unsigned input_count;
    sim_event_t trace[TRACE_SIZE];
    unsigned trace_head, trace_count;
};


/* Private module interfaces. Only rp2040_sim.h is the public C ABI. */
#if defined(__GNUC__)
#define SIM_INTERNAL __attribute__((visibility("hidden")))
#else
#define SIM_INTERNAL
#endif

SIM_INTERNAL int sim_fail(sim_t *s, const char *fmt, ...);
SIM_INTERNAL void sim_emit(sim_t *s, uint32_t kind, uint32_t source, uint32_t value);

/* CPU backend, exceptions, NVIC and SysTick. */
SIM_INTERNAL int sim_cpu_open(sim_t *s);
SIM_INTERNAL void sim_cpu_close(sim_t *s);
SIM_INTERNAL void sim_cpu_stop(sim_t *s);
SIM_INTERNAL void sim_cpu_reset(sim_t *s);
SIM_INTERNAL void sim_cpu_execute(sim_t *s, uint32_t budget);
SIM_INTERNAL void sim_cpu_snapshot(sim_t *s, sim_snapshot_t *out);
SIM_INTERNAL void sim_cpu_core_snapshot(sim_t *s, unsigned core, sim_core_snapshot_t *out);
SIM_INTERNAL void sim_cpu_systick_advance(sim_t *s, unsigned core, uint64_t ticks);
SIM_INTERNAL uint64_t sim_cpu_next_event(sim_t *s, uint64_t next);
SIM_INTERNAL void sim_cpu_irq_levels(sim_t *s, unsigned core, uint32_t levels);
SIM_INTERNAL int sim_cpu_select(sim_t *s, unsigned core);
SIM_INTERNAL int sim_cpu_init_core(sim_t *s, unsigned core, uint32_t vtor, uint32_t sp, uint32_t entry);
SIM_INTERNAL uint32_t sim_cpu_read(sim_t *s, uint32_t address, bool peek, bool *available);
SIM_INTERNAL bool sim_cpu_write(sim_t *s, uint32_t address, uint32_t value);

/* Memory mapping, ELF loading and MMIO routing. */
SIM_INTERNAL uc_err sim_bus_map(sim_t *s);
SIM_INTERNAL bool sim_bus_dma_read(sim_t *s, uint32_t address, unsigned size, uint32_t *value);
SIM_INTERNAL bool sim_bus_dma_write(sim_t *s, uint32_t address, unsigned size, uint32_t value);
SIM_INTERNAL bool sim_bus_write_ram(sim_t *s, uint32_t address, const uint8_t *data, size_t length);
SIM_INTERNAL uc_err sim_rom_map(sim_t *s);
SIM_INTERNAL bool sim_rom_call(sim_t *s, uint32_t address);
SIM_INTERNAL void sim_flash_reset(sim_t *s);
SIM_INTERNAL void sim_flash_reset_mask(sim_t *s, uint32_t mask);
SIM_INTERNAL uint32_t sim_flash_read(sim_t *s, uint32_t address, bool peek, bool *found);
SIM_INTERNAL bool sim_flash_write(sim_t *s, uint32_t address, uint32_t value);
SIM_INTERNAL void sim_rtc_reset(sim_t *s);
SIM_INTERNAL void sim_rtc_service(sim_t *s);
SIM_INTERNAL uint64_t sim_rtc_next_event(sim_t *s, uint64_t next);
SIM_INTERNAL uint32_t sim_rtc_irq(sim_t *s);
SIM_INTERNAL uint32_t sim_rtc_read(sim_t *s, uint32_t address, bool peek, bool *found);
SIM_INTERNAL bool sim_rtc_write(sim_t *s, uint32_t address, uint32_t value);

/* Virtual time, queued input delivery and bounded execution. */
SIM_INTERNAL void sim_scheduler_reset(sim_t *s);
SIM_INTERNAL void sim_scheduler_service(sim_t *s);
SIM_INTERNAL void sim_scheduler_instruction(sim_t *s);

/* Digital nets, GPIO mux/pads and GPIO interrupt status. */
SIM_INTERNAL void sim_gpio_reset(sim_t *s);
SIM_INTERNAL void sim_gpio_resolve(sim_t *s);
SIM_INTERNAL uint32_t sim_gpio_raw(sim_t *s, unsigned group);
SIM_INTERNAL uint32_t sim_gpio_sample(sim_t *s);
SIM_INTERNAL uint32_t sim_gpio_read(sim_t *s, uint32_t address, bool *available);
SIM_INTERNAL bool sim_gpio_write(sim_t *s, uint32_t address, uint32_t value);

/* Timer, UART, PWM, ADC, I2C and SPI models and external device handshakes. */
SIM_INTERNAL void sim_peripherals_reset(sim_t *s);
SIM_INTERNAL int sim_peripherals_input(sim_t *s, const input_t *input);
SIM_INTERNAL void sim_peripherals_service(sim_t *s);
SIM_INTERNAL void sim_peripherals_advance(sim_t *s, uint64_t cycles);
SIM_INTERNAL uint64_t sim_peripherals_next_event(sim_t *s, uint64_t next);
SIM_INTERNAL uint32_t sim_peripherals_read(sim_t *s, uint32_t address, bool peek, bool *available);
SIM_INTERNAL bool sim_peripherals_write(sim_t *s, uint32_t address, uint32_t value);

/* PIO instruction execution, FIFOs, GPIO drivers and clock deadlines. */
SIM_INTERNAL void sim_pio_reset(sim_t *s);
SIM_INTERNAL void sim_pio_advance(sim_t *s, uint64_t cycles);
SIM_INTERNAL uint64_t sim_pio_next_event(sim_t *s, uint64_t next);
SIM_INTERNAL uint32_t sim_pio_irq_levels(sim_t *s);
SIM_INTERNAL uint32_t sim_pio_read(sim_t *s, uint32_t address, bool peek, bool *available);
SIM_INTERNAL bool sim_pio_write(sim_t *s, uint32_t address, uint32_t value);
SIM_INTERNAL unsigned sim_pio_dreq(sim_t *s, unsigned dreq);

/* DMA beats share the functional root clock with CPU and PIO. */
SIM_INTERNAL void sim_dma_reset(sim_t *s);
SIM_INTERNAL void sim_dma_advance(sim_t *s, uint64_t cycles);
SIM_INTERNAL uint64_t sim_dma_next_event(sim_t *s, uint64_t next);
SIM_INTERNAL uint32_t sim_dma_irq_levels(sim_t *s);
SIM_INTERNAL uint32_t sim_dma_read(sim_t *s, uint32_t address, bool *available);
SIM_INTERNAL bool sim_dma_write(sim_t *s, uint32_t address, uint32_t value);

/* SIO mailboxes/spinlocks and the direct-load core-1 boot handshake. */
SIM_INTERNAL void sim_multicore_reset(sim_t *s);
SIM_INTERNAL void sim_multicore_service(sim_t *s);
SIM_INTERNAL uint32_t sim_multicore_irq(sim_t *s, unsigned core);
SIM_INTERNAL uint32_t sim_multicore_read(sim_t *s, uint32_t address, bool peek, bool *available);
SIM_INTERNAL bool sim_multicore_write(sim_t *s, uint32_t address, uint32_t value);
SIM_INTERNAL void sim_system_reset(sim_t *s);
SIM_INTERNAL uint32_t sim_system_read(sim_t *s, uint32_t address, bool peek, bool *available);
SIM_INTERNAL bool sim_system_write(sim_t *s, uint32_t address, uint32_t value);
SIM_INTERNAL bool sim_system_held(sim_t *s, uint32_t address);
SIM_INTERNAL void sim_peripherals_reset_mask(sim_t *s, uint32_t mask);
SIM_INTERNAL void sim_pio_reset_block(sim_t *s, unsigned block);

/* APB atomic register aliases: XOR, SET, CLEAR, or an ordinary write. */
static inline uint32_t atomic_value(uint32_t old, uint32_t val, uint32_t alias) {
    return alias == 1 ? old ^ val : alias == 2 ? old | val : alias == 3 ? old & ~val : val;
}

#endif
