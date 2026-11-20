# The bundled SSD1306 driver is prepended by the example loader.
from machine import Pin, I2C, SPI, ADC
import time

bus = I2C(0, sda=Pin(4), scl=Pin(5), freq=100000)
display = SSD1306_I2C(128, 64, bus)
adc = ADC(26)
spi = SPI(0, baudrate=100000, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs = Pin(17, Pin.OUT, value=0)
reply = bytearray(2)
spi.write_readinto(b'\xa5\x5a', reply)
cs(1)
bus.writeto(0x48, b'\x00', False)
sensor = bus.readfrom(0x48, 2)
try:
    bus.writeto(0x3d, b'\x00')
except OSError:
    print('expected NACK at 0x3d')
print('Bus sensor=%d SPI=%d' % (sensor[0], reply[0]))
digits = [b'\x3e\x51\x49\x45\x3e', b'\x00\x42\x7f\x40\x00',
          b'\x42\x61\x51\x49\x46', b'\x21\x41\x45\x4b\x31',
          b'\x18\x14\x12\x7f\x10', b'\x27\x45\x45\x45\x39',
          b'\x3c\x4a\x49\x49\x30', b'\x01\x71\x09\x05\x03',
          b'\x36\x49\x49\x49\x36', b'\x06\x49\x49\x29\x1e']
while True:
    raw = adc.read_u16() >> 4
    temperature = raw * 100 // 4096
    display.fill(0)
    for d, number in enumerate((temperature // 10, temperature % 10)):
        for x, value in enumerate(digits[number]):
            display.buffer[20 + d * 7 + x] = value
    display.fill_rect(12, 24, temperature, 8, 1)
    display.show()
    print('temperature:%d,voltage:%.3f' % (temperature, raw * 3.3 / 4095))
    time.sleep_ms(250)
