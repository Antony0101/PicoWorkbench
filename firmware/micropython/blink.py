from machine import Pin
import time

led = Pin(25, Pin.OUT)
for _ in range(4):
    led.toggle()
    time.sleep_ms(250)
print("MicroPython blink complete")
