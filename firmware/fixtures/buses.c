typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
volatile u32 results[6];
void reset_handler(void);
__attribute__((section(".vectors"), used))
const u32 vectors[] = {0x20042000u, (u32)reset_handler};

static u32 sensor_read(u32 reg) {
    REG(0x40044010u) = reg;
    REG(0x40044010u) = 0x700; /* READ, STOP, RESTART */
    while (!(REG(0x40044070u) & 8)) {}
    return REG(0x40044010u);
}

void reset_handler(void) {
    REG(0x40014024u) = 3; /* GP4: I2C0 SDA */
    REG(0x4001402cu) = 3; /* GP5: I2C0 SCL */
    REG(0x4001c014u) = 0x5a;
    REG(0x4001c018u) = 0x5a;
    REG(0x40044000u) = 0x65;
    REG(0x40044004u) = 0x48;
    REG(0x4004406cu) = 1;
    results[0] = sensor_read(0);
    results[1] = sensor_read(1);
    REG(0x40044010u) = 2;
    REG(0x40044010u) = 0x29c; /* register 2 = 0x9c, STOP */
    results[2] = sensor_read(2);
    REG(0x40044004u) = 0x49; /* no device: expect NACK */
    REG(0x40044010u) = 0x200;
    results[3] = REG(0x40044080u);
    (void)REG(0x40044040u);

    REG(0x40014084u) = 1; /* GP16 RX */
    REG(0x4001408cu) = 5; /* GP17 CS controlled through SIO */
    REG(0x40014094u) = 1; /* GP18 SCK */
    REG(0x4001409cu) = 1; /* GP19 TX */
    REG(0xd0000018u) = 1u << 17;
    REG(0xd0000024u) = 1u << 17;
    REG(0x4003c000u) = 7; /* 8-bit frames, mode 0 */
    REG(0x4003c010u) = 2;
    REG(0x4003c004u) = 2;
    REG(0x4003c008u) = 0xa5;
    results[4] = REG(0x4003c008u);
    REG(0x4003c008u) = 0x5a;
    results[5] = REG(0x4003c008u);
    __asm volatile("bkpt 0");
    for (;;) {}
}
