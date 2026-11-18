/* Direct-load DMA -> PIO demo. Twelve words make six GPIO25 pulses.
 * DMA0 uses PIO0 TX0 DREQ; FIFO backpressure paces the transfer while the
 * CPU sleeps. At 1 MHz each output level lasts 250 ms. DMA IRQ0 fires
 * when the final word enters the FIFO, before the final pin transition.
 */
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define PIO 0x50200000u
#define DMA 0x50000000u
void reset_handler(void);
void dma_handler(void);
__attribute__((section(".vectors"), used))
const u32 vectors[28] = {[0] = 0x20042000u, [1] = (u32)reset_handler,
                        [27] = (u32)dma_handler};
static const u32 waveform[12] = {1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0};
volatile u32 completed;
void dma_handler(void) {
    REG(DMA + 0x400) = 1;
    completed++;
    const char *text = "DMA complete\n";
    while (*text) REG(0x40034000u) = (u32)*text++;
}
void reset_handler(void) {
    REG(0x40034030u) = 0x101; /* UART0 TX for completion message */
    REG(0x40014000u + 25 * 8 + 4) = 6;
    REG(PIO + 0x48) = 0x7801; /* OUT PINS,1 [24], wrap to itself */
    REG(PIO + 0xc8) = 10000u << 16;
    REG(PIO + 0xcc) = 0;
    REG(PIO + 0xd0) = (1u << 25) | (1u << 17) | (3u << 18); /* autopull after 1 bit */
    REG(PIO + 0xdc) = (1u << 26) | (1u << 20) | (25u << 5) | 25u;
    REG(PIO + 0xd8) = 0xe081; /* SET PINDIRS,1 while disabled */
    REG(DMA) = (u32)waveform;
    REG(DMA + 4) = PIO + 0x10;
    REG(DMA + 8) = 12;
    REG(DMA + 0x404) = 1;
    REG(0xe000e100u) = 1u << 11;
    REG(DMA + 12) = 1 | (2u << 2) | (1u << 4); /* word, increment read, DREQ0 */
    REG(PIO) = 0x111;
    for (;;) __asm volatile("wfi");
}
