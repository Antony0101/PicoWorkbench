/* Pico SDK 2.2.0 flash ELF: SDK crt0, runtime init, GPIO and timer-backed sleep.
 * No simulator headers, custom reset handler, or replacement runtime_init.
 */
#include "pico/stdlib.h"
volatile uint32_t initialized_data = 0x12345678;
volatile uint32_t zeroed_bss;
volatile uint32_t transitions;
int main(void) {
    if (initialized_data != 0x12345678 || zeroed_bss != 0) __breakpoint();
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    for (;;) {
        gpio_put(PICO_DEFAULT_LED_PIN, 1);
        transitions++;
        sleep_ms(250);
        gpio_put(PICO_DEFAULT_LED_PIN, 0);
        transitions++;
        sleep_ms(250);
    }
}
