#include <stdio.h>
#include "pico/stdlib.h"
#include "values.h"
#include "generated.h"
#ifdef PROJECT_B
#include "gate.pio.h"
#endif
volatile int project_total;
int main(void) {
    stdio_init_all();
    gpio_init(25);
    gpio_set_dir(25, GPIO_OUT);
    project_total = left_value() + right_value() + GENERATED_VALUE;
    gpio_put(25, project_total == 42);
    printf("project:%d\n", project_total);
    for (;;) { tight_loop_contents(); }
}
