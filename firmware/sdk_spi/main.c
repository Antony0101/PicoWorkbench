#include <stdio.h>
#include "pico/stdlib.h"
#include "hardware/spi.h"
const uint8_t transmitted[] = {0x00, 0x2a, 0x7f, 0xff};
uint8_t received[4];
int main(void) {
    stdio_init_all();
    spi_init(spi0, 1000000);
    gpio_set_function(19, GPIO_FUNC_SPI);
    gpio_set_function(16, GPIO_FUNC_SPI);
    gpio_set_function(18, GPIO_FUNC_SPI);
    gpio_init(17);
    gpio_set_dir(17, GPIO_OUT);
    gpio_put(17, 0);
    spi_write_read_blocking(spi0, transmitted, received, sizeof(received));
    gpio_put(17, 1);
    printf("SPI %02x %02x %02x %02x\n", received[0], received[1], received[2], received[3]);
    for (;;) { tight_loop_contents(); }
}
