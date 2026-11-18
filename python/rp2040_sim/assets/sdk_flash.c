/* Runs only with the real ROM. Exercise SDK flash erase/program and boot2 return. */
#include <stdio.h>
#include "pico/stdlib.h"
#include "hardware/flash.h"
#include "hardware/sync.h"

int main(void) {
    stdio_init_all();
    uint8_t page[FLASH_PAGE_SIZE];
    for (unsigned i = 0; i < sizeof(page); i++) page[i] = (uint8_t)(i ^ 0x5a);
    uint32_t irq = save_and_disable_interrupts();
    flash_range_erase(0x1f0000, FLASH_SECTOR_SIZE);
    flash_range_program(0x1f0000, page, sizeof(page));
    restore_interrupts(irq);
    const uint8_t *written = (const uint8_t *)(XIP_BASE + 0x1f0000);
    for (unsigned i = 0; i < sizeof(page); i++) if (written[i] != page[i]) __breakpoint();
    puts("SDK flash erase/program verified");
    for (;;) __wfi();
}
