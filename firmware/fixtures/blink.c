/* A real ARM ELF for the simulator's direct-load boot policy. No Pico SDK/ROM. */
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define SIO 0xd0000000u
#define LED (1u << 25)
#define BUTTON (1u << 14)
void reset_handler(void);
__attribute__((section(".vectors"), used))
const u32 vectors[] = {0x20042000u, (u32)reset_handler};

static void serial(const char *s) {
    while (*s) REG(0x40034000u) = (unsigned char)*s++;
}

void reset_handler(void) {
    REG(0x40014000u + 25 * 8 + 4) = 5; /* GPIO25: SIO */
    REG(0x40014000u + 14 * 8 + 4) = 5;
    REG(0x4001c000u + 4 + 14 * 4) = 0x5a; /* input enable, pull up */
    REG(SIO + 0x24) = LED; /* GPIO_OE_SET */
    REG(0x40034030u) = 0x301; /* UART0 enable, TX/RX enable */
    serial("RP2040 simulator ready\r\n");
    u32 previous = REG(0x40054028u);
    u32 was_pressed = 0;
    for (;;) {
        u32 now = REG(0x40054028u);
        u32 pressed = !(REG(SIO + 4) & BUTTON);
        if (pressed != was_pressed) {
            serial(pressed ? "Button pressed\r\n" : "Button released\r\n");
            was_pressed = pressed;
        }
        if ((u32)(now - previous) >= (pressed ? 50000u : 250000u)) {
            REG(SIO + 0x1c) = LED; /* GPIO_OUT_XOR */
            previous = now;
        }
    }
}
