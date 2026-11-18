/* Direct-load PIO0 SM0 demo: GPIO25, 2 Hz at the 1 MHz functional clock.
 * Two PIO instructions, 25 state-machine clocks per level, divider 10000.
 * The CPU sleeps while PIO drives the waveform. No SDK or pioasm required.
 */
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define PIO 0x50200000u
void reset_handler(void);
__attribute__((section(".vectors"), used))
const u32 vectors[] = {0x20042000u, (u32)reset_handler};

void reset_handler(void) {
    REG(0x40014000u + 25 * 8 + 4) = 6; /* GPIO25 -> PIO0 */
    REG(PIO + 0x48) = 0xf801; /* SET PINS,1 [24] */
    REG(PIO + 0x4c) = 0xf800; /* SET PINS,0 [24] */
    REG(PIO + 0xc8) = 10000u << 16;
    REG(PIO + 0xcc) = 1u << 12; /* wrap top=1, bottom=0 */
    REG(PIO + 0xdc) = (1u << 26) | (25u << 5); /* SET count=1, base=25 */
    REG(PIO + 0xd8) = 0xe081; /* forced SET PINDIRS,1, while disabled */
    REG(PIO) = 0x111; /* restart SM0, divider0, and enable SM0 */
    for (;;) __asm volatile("wfi");
}
