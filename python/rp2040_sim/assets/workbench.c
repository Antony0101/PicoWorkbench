typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define LED (1u << 25)
#define BUTTON (1u << 14)
void reset_handler(void);
__attribute__((section(".vectors"), used))
const u32 vectors[] = {0x20042000u, (u32)reset_handler};

static void serial(const char *s) { while (*s) REG(0x40034000u) = (unsigned char)*s++; }
static void hex(u32 value) {
    const char *digits = "0123456789ABCDEF";
    for (int i = 12; i >= 0; i -= 4) REG(0x40034000u) = digits[(value >> i) & 15];
}
static u32 sensor_read(u32 reg) {
    REG(0x40044010u) = reg;
    REG(0x40044010u) = 0x700;
    while (!(REG(0x40044070u) & 8)) {}
    return REG(0x40044010u);
}

void reset_handler(void) {
    REG(0x400140ccu) = 5;
    REG(0x40014074u) = 5;
    REG(0x4001c03cu) = 0x5a;
    REG(0xd0000024u) = LED | (1u << 17);
    REG(0x40034030u) = 0x301;
    REG(0x40014024u) = 3;
    REG(0x4001402cu) = 3;
    REG(0x4001c014u) = 0x5a;
    REG(0x4001c018u) = 0x5a;
    REG(0x40044000u) = 0x65;
    REG(0x40044004u) = 0x48;
    REG(0x4004406cu) = 1;
    REG(0x40014084u) = 1;
    REG(0x4001408cu) = 5;
    REG(0x40014094u) = 1;
    REG(0x4001409cu) = 1;
    REG(0x4003c000u) = 7;
    REG(0x4003c010u) = 2;
    REG(0x4003c004u) = 2;
    REG(0x40014004u) = 4; /* PWM on GP0 */
    REG(0x40050010u) = 999;
    REG(0x4005000cu) = 250;
    REG(0x40050000u) = 1;
    REG(0x4004c000u) = 1;
    serial("RP2040 workbench ready\r\n");
    u32 previous = REG(0x40054028u), sequence = 0, was_pressed = 0;
    for (;;) {
        u32 now = REG(0x40054028u);
        u32 pressed = !(REG(0xd0000004u) & BUTTON);
        if (pressed != was_pressed) {
            serial(pressed ? "Button pressed\r\n" : "Button released\r\n");
            was_pressed = pressed;
        }
        if ((u32)(now - previous) >= (pressed ? 50000u : 250000u)) {
            previous = now;
            REG(0xd000001cu) = LED;
            serial("id="); hex(sensor_read(0));
            serial(" temp="); hex(sensor_read(1));
            REG(0x4004c000u) = 5;
            while (!(REG(0x4004c000u) & 256)) {}
            serial(" adc="); hex(REG(0x4004c004u));
            REG(0x4003c008u) = ++sequence & 255;
            serial(" spi="); hex(REG(0x4003c008u));
            serial("\r\n");
        }
    }
}
