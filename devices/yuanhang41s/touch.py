"""Stage the RAM-only Sitronix driver; never embed another unit's firmware."""
from pathlib import Path


def apply(kernel):
    kernel = Path(kernel)
    target = kernel / 'drivers/input/touchscreen/st7123.c'
    target.write_bytes(Path(__file__).with_name('st7123.c').read_bytes())
    for name, marker, addition in (
        ('drivers/input/touchscreen/Makefile', 'CONFIG_TOUCHSCREEN_ST7123',
         'obj-$(CONFIG_TOUCHSCREEN_ST7123) += st7123.o\n'),
        ('drivers/input/touchscreen/Kconfig', 'config TOUCHSCREEN_ST7123',
         'config TOUCHSCREEN_ST7123\n\ttristate "Sitronix ST7123 SPI RAM touchscreen"\n'
         '\tdepends on SPI && GPIOLIB\n'),
    ):
        path = kernel / name
        text = path.read_text()
        if marker not in text:
            path.write_text(text + '\n' + addition)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
