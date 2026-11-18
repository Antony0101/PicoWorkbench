typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 timer_count;
volatile u32 button_count;
void reset_handler(void);
void timer_irq(void);
void gpio_irq(void);
__attribute__((section(".vectors"), used))
const u32 vectors[48] = {
    [0] = 0x20042000u, [1] = (u32)reset_handler,
    [16] = (u32)timer_irq, [29] = (u32)gpio_irq,
};

__attribute__((noinline)) static void increment(volatile u32 *count) { ++*count; }

void timer_irq(void) {
    REG(0x40054034u) = 1;
    increment(&timer_count);
    if (timer_count < 3) REG(0x40054010u) = REG(0x40054028u) + 2000;
}

void gpio_irq(void) {
    REG(0x400140f4u) = 1u << 26; /* GPIO14 falling edge W1C */
    increment(&button_count);
}

void reset_handler(void) {
    REG(0x40014074u) = 5;
    REG(0x4001c03cu) = 0x5a;
    REG(0x40014104u) = 1u << 26;
    REG(0x40054038u) = 1;
    REG(0xe000e100u) = 1 | (1u << 13);
    REG(0x40054010u) = REG(0x40054028u) + 2000;
    while (timer_count < 3 || button_count < 1) __asm volatile("wfi");
    __asm volatile("bkpt 0");
    for (;;) {}
}
