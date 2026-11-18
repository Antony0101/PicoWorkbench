/* Pico SDK 2.2.0 crt0/runtime and UART stdio, with USB stdio disabled. */
#include <stdio.h>
#include "pico/stdlib.h"
int main(void) {
    stdio_init_all();
    puts("Pico SDK UART ready");
    for (;;) sleep_ms(1000);
}
