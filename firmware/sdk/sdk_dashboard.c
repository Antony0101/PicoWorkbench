/* Phase 3 dashboard: SDK I2C OLED, ADC telemetry, sensor and CPU-driven SPI. */
#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/i2c.h"
#include "hardware/spi.h"
#include "hardware/adc.h"

static uint8_t pixels[1025] = {0x40};
static const uint8_t digits[10][5] = {
    {0x3e,0x51,0x49,0x45,0x3e}, {0,0x42,0x7f,0x40,0},
    {0x42,0x61,0x51,0x49,0x46}, {0x21,0x41,0x45,0x4b,0x31},
    {0x18,0x14,0x12,0x7f,0x10}, {0x27,0x45,0x45,0x45,0x39},
    {0x3c,0x4a,0x49,0x49,0x30}, {1,0x71,9,5,3},
    {0x36,0x49,0x49,0x49,0x36}, {6,0x49,0x49,0x29,0x1e}
};
static void command(uint8_t value) {
    uint8_t data[] = {0x80, value};
    i2c_write_blocking(i2c0, 0x3c, data, 2, false);
}
static void show(unsigned temperature) {
    memset(pixels+1, 0, 1024);
    unsigned values[] = {temperature/10, temperature%10};
    for (unsigned d=0; d<2; ++d)
        for (unsigned x=0; x<5; ++x) pixels[1+20+d*7+x] = digits[values[d]][x];
    for (unsigned x=0; x<temperature && x<100; ++x) pixels[1+3*128+12+x] = 0xff;
    command(0x21); command(0); command(127);
    command(0x22); command(0); command(7);
    i2c_write_blocking(i2c0, 0x3c, pixels, sizeof(pixels), false);
}
int main(void) {
    stdio_init_all();
    adc_init(); adc_gpio_init(26); adc_select_input(0);
    i2c_init(i2c0, 100000);
    gpio_set_function(4, GPIO_FUNC_I2C); gpio_set_function(5, GPIO_FUNC_I2C);
    uint8_t init[] = {0xae,0x20,0,0x40,0xa1,0xa8,63,0xc8,0xd3,0,0xda,0x12,
                      0xd5,0x80,0xd9,0xf1,0xdb,0x30,0x81,255,0xa4,0xa6,0xad,0x30,0x8d,0x14,0xaf};
    for (unsigned i=0; i<sizeof(init); ++i) command(init[i]);
    spi_init(spi0, 100000);
    gpio_set_function(16, GPIO_FUNC_SPI); gpio_set_function(18, GPIO_FUNC_SPI);
    gpio_set_function(19, GPIO_FUNC_SPI); gpio_init(17); gpio_set_dir(17, true);
    uint8_t tx[] = {0xa5,0x5a}, rx[2];
    gpio_put(17, false); spi_write_read_blocking(spi0, tx, rx, 2); gpio_put(17, true);
    uint8_t reg=0, sensor[2];
    i2c_write_blocking(i2c0, 0x48, &reg, 1, true);
    i2c_read_blocking(i2c0, 0x48, sensor, 2, false);
    int missing = i2c_write_blocking(i2c0, 0x3d, &reg, 1, false);
    printf("Bus sensor=%u SPI=%u missing=%d\n", sensor[0], rx[0], missing);
    while (true) {
        unsigned raw = adc_read(), temperature = raw * 100 / 4096;
        show(temperature);
        printf("temperature:%u,voltage:%.3f\n", temperature, raw * 3.3 / 4095);
        sleep_ms(250);
    }
}
