"""Port the factory AW883xx driver, retaining its ACF monitor/protection."""
import hashlib
import json
from pathlib import Path
import re


def apply(kernel):
    kernel = Path(kernel)
    port = Path(__file__).with_name('port')
    manifest = json.loads((port / 'aw883xx-sources.json').read_text())
    dest = kernel / 'sound/soc/codecs/aw883xx'
    dest.mkdir(exist_ok=True)
    for name, digest in manifest['files'].items():
        content = (port / 'aw883xx' / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('Factory source changed: ' + name)
        source = content.decode()
        if name == 'aw883xx.h':
            source = source.replace('MODULE_IMPORT_NS(VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver);', '')
        if name == 'aw883xx.c':
            # 6.18 made this API a function-like macro, which cannot be used
            # directly as a callback in the vendor codec abstraction.
            source = source.replace('static struct aw_componet_codec_ops aw_componet_codec_ops = {',
                'static void aw883xx_unregister_component(struct device *dev)\n'
                '{\n\tsnd_soc_unregister_component(dev);\n}\n\n'
                'static struct aw_componet_codec_ops aw_componet_codec_ops = {', 1)
            source = source.replace('.unregister_codec = snd_soc_unregister_component,',
                                    '.unregister_codec = aw883xx_unregister_component,')
            source = source.replace('strlcpy(', 'strscpy(').replace('GPIOF_DIR_IN', 'GPIOF_IN')
            # These GPIOs were acquired with devm_gpio_request_one; devres
            # releases them after remove, just as on failed probe.
            source = re.sub(r'\tif \(gpio_is_valid\(aw883xx->(?:irq|reset)_gpio\)\)\n'
                            r'\t\tdevm_gpio_free\(&i2c->dev, aw883xx->(?:irq|reset)_gpio\);\n', '', source)
            source = source.replace('#include <vendor/common/smartpa_stat.h>',
                '/* Android-only manufacturing diagnostic counters are not a PA control. */')
            source = source.replace('smartpa_count_add();', '').replace('smartpa_stat_update();', '')
            source = source.replace('static int aw883xx_i2c_probe(struct i2c_client *i2c,\n\t\t\t\tconst struct i2c_device_id *id)',
                                    'static int aw883xx_i2c_probe(struct i2c_client *i2c)')
            source = source.replace('static int aw883xx_i2c_remove(struct i2c_client *i2c)',
                                    'static void aw883xx_i2c_remove(struct i2c_client *i2c)')
            start = source.index('static void aw883xx_i2c_remove(')
            end = source.index('static const struct i2c_device_id', start)
            source = source[:start] + source[start:end].replace('return 0;', 'return;') + source[end:]
        if name == 'aw883xx_calib.c':
            source = re.sub(r'struct +class \*class', 'const struct class *class', source)
            source = source.replace('struct class_attribute *attr', 'const struct class_attribute *attr')
            source = source.replace('.name = "smartpa",\n\t.owner = THIS_MODULE,', '.name = "smartpa",')
        if name == 'aw883xx_spin.c':
            source = source.replace('strlcpy(', 'strscpy(')
        (dest / name).write_text(source)
    (dest / 'Makefile').write_text(
        'snd-soc-aw883xx-objs := aw883xx.o aw883xx_monitor.o aw883xx_bin_parse.o aw883xx_device.o aw883xx_init.o aw883xx_calib.o aw883xx_spin.o\n'
        'obj-$(CONFIG_SND_SOC_AW883XX_FACTORY) += snd-soc-aw883xx.o\n')
    for rel, marker, addition in (
        ('sound/soc/codecs/Makefile', 'CONFIG_SND_SOC_AW883XX_FACTORY',
         'obj-$(CONFIG_SND_SOC_AW883XX_FACTORY) += aw883xx/\n'),
        ('sound/soc/codecs/Kconfig', 'config SND_SOC_AW883XX_FACTORY',
         'config SND_SOC_AW883XX_FACTORY\n\ttristate "Factory AW883xx smart amplifier"\n\tdepends on I2C\n\tselect GPIOLIB\n'),
    ):
        path = kernel / rel
        source = path.read_text()
        if marker not in source:
            path.write_text(source + '\n' + addition)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
