/* Direct-load two-core demo: core 0 sends work every 250 ms; core 1 updates
 * shared SRAM under a spinlock and drives GPIO25. Replies wake core 0 via
 * its SIO FIFO interrupt. The six-word launch handshake models the ROM API;
 * it does not depend on normal SDK startup or an embedded ROM image.
 */
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define SIO 0xd0000000u
#define TIMER 0x40054000u
void reset_handler(void); void timer_handler(void); void fifo_handler(void); void core1_entry(void);
__attribute__((section(".vectors"), used))
const u32 vectors[32] = {[0] = 0x20042000u, [1] = (u32)reset_handler,
                        [16] = (u32)timer_handler, [31] = (u32)fifo_handler};
volatile u32 sent, processed, core_ids[2];
static void lock(void) { while (!REG(SIO + 0x100)) {} }
static void unlock(void) { REG(SIO + 0x100) = 0; }
static void push(u32 word) {
    while (!(REG(SIO + 0x50) & 2)) {}
    REG(SIO + 0x54) = word;
    __asm volatile("sev");
}
static u32 pop(void) {
    while (!(REG(SIO + 0x50) & 1)) __asm volatile("wfe");
    return REG(SIO + 0x58);
}
void timer_handler(void) {
    REG(TIMER + 0x34) = 1;
    lock(); sent++; unlock();
    push(1);
    REG(TIMER + 0x10) = REG(TIMER + 0x28) + 250000u;
}
void fifo_handler(void) {
    while (REG(SIO + 0x50) & 1) {
        u32 value = REG(SIO + 0x58);
        const char *text = "Core 1 count: ";
        while (*text) REG(0x40034000u) = (u32)*text++;
        for (unsigned nibble = 0; nibble < 8; nibble++) {
            REG(0x40034000u) = "0123456789abcdef"[value >> 28];
            value <<= 4;
        }
        REG(0x40034000u) = '\n';
    }
}
void core1_entry(void) {
    core_ids[1] = REG(SIO);
    for (;;) {
        u32 word = pop();
        lock(); processed += word; u32 value = processed; unlock();
        REG(SIO + (value & 1 ? 0x14 : 0x18)) = 1u << 25;
        push(value);
    }
}
void reset_handler(void) {
    core_ids[0] = REG(SIO);
    REG(0x40014000u + 25 * 8 + 4) = 5;
    REG(SIO + 0x24) = 1u << 25;
    REG(0x40034030u) = 0x101;
    const u32 launch[6] = {0, 0, 1, 0x10000000u, 0x20041000u, (u32)core1_entry};
    for (unsigned i = 0; i < 6; i++) { push(launch[i]); (void)pop(); }
    REG(0xe000e280u) = 1u << 15; /* discard boot handshake's pending FIFO IRQ */
    REG(0xe000e100u) = (1u << 15) | 1;
    REG(TIMER + 0x38) = 1;
    REG(TIMER + 0x10) = REG(TIMER + 0x28) + 250000u;
    for (;;) __asm volatile("wfi");
}
