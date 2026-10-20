// SPDX-License-Identifier: GPL-2.0-or-later
/* Sitronix ST7123 SPI/RAM-download adaptation.
 * Protocol derived from Sitronix's 2018 GPL driver (CT Chen), as shipped in
 * ZTE's kernel b50db5b6224c11db42a246e8fcdaad1e1d59f478.
 * No flash/OTP programming, factory test interface or embedded firmware.
 */
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/gpio/consumer.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/spi/spi.h>
#include <linux/unaligned.h>

#define ST_CONTACTS 10
#define ST_CHUNK 4096

struct st7123 {
	struct spi_device *spi;
	struct gpio_desc *reset, *irq_gpio;
	struct input_dev *input;
	const struct firmware *fw;
	struct mutex lock;
	u8 *tx, *rx;
	bool running, resume_running;
};

static int st_command(struct st7123 *ts, const u8 *data, size_t len)
{
	memcpy(ts->tx, data, len);
	return spi_write(ts->spi, ts->tx, len);
}

static int st_ram(struct st7123 *ts, u32 addr, const u8 *data,
		  u8 *readback, size_t len)
{
	while (len) {
		size_t n = min_t(size_t, len, ST_CHUNK);
		struct spi_transfer transfer = { .tx_buf = ts->tx };
		int ret;

		memset(ts->tx, 0, n + 4);
		ts->tx[0] = addr >> 16;
		ts->tx[1] = addr >> 8;
		ts->tx[2] = addr;
		if (readback) {
			ts->tx[0] |= 0x80;
			transfer.rx_buf = ts->rx;
			transfer.len = n + 4; /* three address bytes plus one dummy */
		} else {
			memcpy(ts->tx + 3, data, n);
			transfer.len = n + 3;
		}
		ret = spi_sync_transfer(ts->spi, &transfer, 1);
		if (ret)
			return ret;
		if (readback) {
			memcpy(readback, ts->rx + 4, n);
			readback += n;
		} else {
			data += n;
		}
		addr += n;
		len -= n;
	}
	return 0;
}

static int st_reg(struct st7123 *ts, u16 reg, u8 *data, size_t len, bool read)
{
	struct spi_transfer xfers[2] = {
		{ .tx_buf = ts->tx, .len = 2,
		  .delay = { .value = 10, .unit = SPI_DELAY_UNIT_USECS } },
		{ .len = len + (read ? 1 : 0) },
	};
	int ret;

	ts->tx[0] = (reg >> 8) | (read ? 0x80 : 0);
	ts->tx[1] = reg;
	if (read) {
		xfers[1].rx_buf = ts->rx;
	} else {
		memcpy(ts->tx + 2, data, len);
		xfers[1].tx_buf = ts->tx + 2;
	}
	ret = spi_sync_transfer(ts->spi, xfers, 2);
	if (!ret && read)
		memcpy(data, ts->rx + 1, len);
	usleep_range(30, 40);
	return ret;
}

static u32 st_crc24(const u8 *data, size_t len)
{
	u32 crc = 0;
	int bit;

	while (len--) {
		crc ^= (u32)*data++ << 16;
		for (bit = 0; bit < 8; bit++)
			crc = (crc << 1) ^ ((crc & BIT(23)) ? 0x864cfb : 0);
	}
	return crc & 0xffffff;
}

static int st_download(struct st7123 *ts)
{
	static const u8 unlock[][5] = {
		{ 0x53, 0x71, 0x23, 0xa5, 0x3c },
		{ 0x53, 0x71, 0x23, 0x05, 0x55 },
		{ 0x53, 0x71, 0x23, 0x55, 0x55 },
		{ 0x53, 0x71, 0x23, 0x14, 0x55 },
		{ 0x00, 0xf3, 0x00, 0x5a, 0xa5 },
		{ 0x00, 0xf3, 0x02, 0x00, 0x01 },
		{ 0x00, 0xf3, 0x02, 0x00, 0x00 },
		{ 0x00, 0xf3, 0x02, 0x00, 0x01 },
	};
	static const u8 finish[][5] = {
		{ 0x00, 0xf3, 0x02, 0x00, 0x03 },
		{ 0x00, 0xf3, 0x00, 0x00, 0x00 },
		{ 0x00, 0xf0, 0x06, 0x00, 0x01 },
		{ 0x53, 0x71, 0x23, 0x50, 0x4c },
		{ 0x53, 0x71, 0x23, 0x55, 0x4c },
		{ 0x53, 0x71, 0x23, 0x41, 0x4c },
		{ 0x53, 0x71, 0x23, 0x5a, 0xc3 },
	};
	const u8 *fw = ts->fw->data;
	size_t length = ts->fw->size, cfg, cfg_len, crc_off, info;
	u32 cfg_ram;
	u8 check[4];
	bool large;
	int i, ret;

	if (length < 0x1000c)
		return -EINVAL;
	large = fw[0x84] == 0;
	cfg = large ? 0x12000 : 0x10000;
	if (length < cfg + 12)
		return -EINVAL;
	crc_off = large ? get_unaligned_be24(fw + 0x85) : get_unaligned_be16(fw + 0x84);
	info = large ? get_unaligned_be24(fw + 0x96) : get_unaligned_be16(fw + 0x93);
	cfg_ram = get_unaligned_be16(fw + (large ? 0x93 : 0x91));
	cfg_len = get_unaligned_be16(fw + cfg + 10) + 3;
	if (crc_off < 2 || crc_off >= cfg || info > cfg - 4 ||
	    cfg_len < 4 || cfg_len > length - cfg ||
	    cfg_ram + cfg_len > 0x10000 ||
	    memcmp(fw + info, "TFI3", 4) || memcmp(fw + cfg, "CFT1", 4) ||
	    st_crc24(fw, crc_off - 2) != get_unaligned_be24(fw + crc_off - 2))
		return -EINVAL;

	gpiod_set_value_cansleep(ts->reset, 0);
	usleep_range(1000, 1100);
	gpiod_set_value_cansleep(ts->reset, 1);
	for (i = 0; i < ARRAY_SIZE(unlock); i++) {
		ret = st_command(ts, unlock[i], sizeof(unlock[i]));
		if (ret)
			return ret;
	}
	/* Check silicon revision before selecting the extra 8 KiB PRAM bank. */
	ret = st_ram(ts, 0x0101c2, NULL, check, 2);
	if (ret || (large && check[0] < 0x47))
		return ret ?: -ENODEV;
	dev_info(&ts->spi->dev, "silicon revision=%02x; PRAM=%u KiB\n",
		 check[0], large ? 72 : 64);
	ret = st_ram(ts, 0x7f0000, fw, NULL, 0x10000);
	if (!ret && large)
		ret = st_ram(ts, 0x400000, fw + 0x10000, NULL, 0x2000);
	if (ret)
		return ret;
	for (i = 0x1000; i <= 0x10000; i += 0x1000) {
		ret = st_ram(ts, 0x7f0000 + i - 4, NULL, check, sizeof(check));
		if (ret || memcmp(check, fw + i - 4, sizeof(check))) {
			dev_err(&ts->spi->dev, "PRAM readback failed at %#x: ret=%d, got=%*ph, expected=%*ph\n",
				i - 4, ret, 4, check, 4, fw + i - 4);
			return ret ?: -EIO;
		}
	}
	if (large) {
		for (i = 0x1000; i <= 0x2000; i += 0x1000) {
			ret = st_ram(ts, 0x400000 + i - 4, NULL, check, sizeof(check));
			if (ret || memcmp(check, fw + 0x10000 + i - 4, sizeof(check)))
				return ret ?: -EIO;
		}
	}
	ret = st_ram(ts, cfg_ram, fw + cfg, NULL, cfg_len);
	if (!ret)
		ret = st_ram(ts, cfg_ram + cfg_len - 4, NULL, check, sizeof(check));
	if (ret || memcmp(check, fw + cfg + cfg_len - 4, sizeof(check)))
		return ret ?: -EIO;
	for (i = 0; i < ARRAY_SIZE(finish); i++) {
		ret = st_command(ts, finish[i], sizeof(finish[i]));
		if (ret)
			return ret;
		if (i == 2)
			msleep(20);
	}
	msleep(20);
	return 0;
}

static irqreturn_t st_irq(int irq, void *arg)
{
	struct st7123 *ts = arg;
	u8 points[4 + ST_CONTACTS * 7];
	int i, ret;

	mutex_lock(&ts->lock);
	ret = st_reg(ts, 0x10, points, sizeof(points), true);
	if (!ret && (points[0] & 0x80)) {
		/* Same explicit firmware RstChip request as the factory IRQ path. */
		for (i = 0; i < ST_CONTACTS; i++) {
			input_mt_slot(ts->input, i);
			input_mt_report_slot_state(ts->input, MT_TOOL_FINGER, false);
		}
		input_mt_sync_frame(ts->input);
		input_sync(ts->input);
		ret = st_download(ts);
		if (ret)
			dev_err(&ts->spi->dev, "firmware-requested RAM restore failed: %d\n", ret);
		mutex_unlock(&ts->lock);
		return IRQ_HANDLED;
	}
	if (!ret && !(points[0] & 0x80)) {
		for (i = 0; i < ST_CONTACTS; i++) {
			u8 *p = points + 4 + i * 7;
			bool active = p[0] & 0x80;
			input_mt_slot(ts->input, i);
			input_mt_report_slot_state(ts->input, MT_TOOL_FINGER, active);
			if (active) {
				input_report_abs(ts->input, ABS_MT_POSITION_X,
					((p[0] & 0x3f) << 8) | p[1]);
				input_report_abs(ts->input, ABS_MT_POSITION_Y,
					((p[2] & 0x3f) << 8) | p[3]);
			}
		}
		input_mt_sync_frame(ts->input);
		input_sync(ts->input);
	}
	mutex_unlock(&ts->lock);
	return IRQ_HANDLED;
}

static int st_open(struct input_dev *input)
{
	struct st7123 *ts = input_get_drvdata(input);
	u8 control;
	int ret;

	mutex_lock(&ts->lock);
	ret = st_reg(ts, 2, &control, 1, true);
	if (!ret) {
		control &= ~BIT(1);
		ret = st_reg(ts, 2, &control, 1, false);
	}
	if (!ret)
		ts->running = true;
	mutex_unlock(&ts->lock);
	if (!ret)
		enable_irq(ts->spi->irq);
	return ret;
}

static void st_close(struct input_dev *input)
{
	struct st7123 *ts = input_get_drvdata(input);
	u8 control;

	if (!ts->running)
		return;
	disable_irq(ts->spi->irq);
	mutex_lock(&ts->lock);
	if (!st_reg(ts, 2, &control, 1, true)) {
		control |= BIT(1);
		st_reg(ts, 2, &control, 1, false);
	}
	ts->running = false;
	mutex_unlock(&ts->lock);
}

static void st_release_firmware(void *arg)
{
	struct st7123 *ts = arg;
	release_firmware(ts->fw);
}

static int st_probe(struct spi_device *spi)
{
	struct device *dev = &spi->dev;
	struct st7123 *ts;
	u8 resolution[4];
	u16 width, height;
	int ret;

	ts = devm_kzalloc(dev, sizeof(*ts), GFP_KERNEL);
	if (!ts)
		return -ENOMEM;
	ts->spi = spi;
	mutex_init(&ts->lock);
	spi_set_drvdata(spi, ts);
	ts->tx = devm_kzalloc(dev, ST_CHUNK + 8, GFP_KERNEL);
	ts->rx = devm_kzalloc(dev, ST_CHUNK + 8, GFP_KERNEL);
	if (!ts->tx || !ts->rx)
		return -ENOMEM;
	spi->bits_per_word = 8;
	spi->max_speed_hz = min(spi->max_speed_hz, 8000000U);
	ret = spi_setup(spi);
	if (ret)
		return ret;
	ts->reset = devm_gpiod_get(dev, "rst", GPIOD_OUT_HIGH);
	if (IS_ERR(ts->reset))
		return dev_err_probe(dev, PTR_ERR(ts->reset), "reset GPIO unavailable\n");
	ts->irq_gpio = devm_gpiod_get(dev, "irq", GPIOD_IN);
	if (IS_ERR(ts->irq_gpio))
		return dev_err_probe(dev, PTR_ERR(ts->irq_gpio), "IRQ GPIO unavailable\n");
	spi->irq = gpiod_to_irq(ts->irq_gpio);
	if (spi->irq < 0)
		return spi->irq;
	ret = request_firmware(&ts->fw, "sitronix/st7123.bin", dev);
	if (ret)
		return dev_err_probe(dev, ret, "own touch firmware unavailable\n");
	ret = devm_add_action_or_reset(dev, st_release_firmware, ts);
	if (ret)
		return ret;
	ret = st_download(ts);
	if (ret)
		return dev_err_probe(dev, ret, "RAM firmware download failed\n");
	ret = st_reg(ts, 5, resolution, sizeof(resolution), true);
	if (ret)
		return ret;
	width = get_unaligned_be16(resolution) & 0x3fff;
	height = get_unaligned_be16(resolution + 2) & 0x3fff;
	if (!width || !height || width > 4096 || height > 4096)
		return -EINVAL;
	ts->input = devm_input_allocate_device(dev);
	if (!ts->input)
		return -ENOMEM;
	ts->input->name = "Sitronix ST7123";
	ts->input->id.bustype = BUS_SPI;
	ts->input->open = st_open;
	ts->input->close = st_close;
	input_set_drvdata(ts->input, ts);
	input_set_abs_params(ts->input, ABS_MT_POSITION_X, 0, width - 1, 0, 0);
	input_set_abs_params(ts->input, ABS_MT_POSITION_Y, 0, height - 1, 0, 0);
	ret = input_mt_init_slots(ts->input, ST_CONTACTS, INPUT_MT_DIRECT);
	if (ret)
		return ret;
	ret = devm_request_threaded_irq(dev, spi->irq, NULL, st_irq,
		IRQF_ONESHOT | IRQF_TRIGGER_FALLING | IRQF_NO_AUTOEN, dev_name(dev), ts);
	if (ret)
		return ret;
	ret = input_register_device(ts->input);
	if (!ret)
		dev_info(dev, "RAM touch ready: %ux%u\n", width, height);
	return ret;
}

static int st_suspend(struct device *dev)
{
	struct st7123 *ts = dev_get_drvdata(dev);
	ts->resume_running = ts->running;
	st_close(ts->input);
	return 0;
}

static int st_resume(struct device *dev)
{
	struct st7123 *ts = dev_get_drvdata(dev);
	int ret;

	mutex_lock(&ts->lock);
	ret = st_download(ts);
	mutex_unlock(&ts->lock);
	if (!ret && ts->resume_running)
		ret = st_open(ts->input);
	return ret;
}

static DEFINE_SIMPLE_DEV_PM_OPS(st_pm, st_suspend, st_resume);
static const struct of_device_id st_match[] = {
	{ .compatible = "sitronix_ts" },
	{}
};
MODULE_DEVICE_TABLE(of, st_match);
static const struct spi_device_id st_ids[] = {
	{ "sitronix_ts" },
	{}
};
MODULE_DEVICE_TABLE(spi, st_ids);
static struct spi_driver st_driver = {
	.driver = { .name = "sitronix-st7123", .of_match_table = st_match,
		.pm = pm_sleep_ptr(&st_pm) },
	.probe = st_probe,
	.id_table = st_ids,
};
module_spi_driver(st_driver);
MODULE_DESCRIPTION("Sitronix ST7123 RAM-download SPI touchscreen");
MODULE_LICENSE("GPL");
MODULE_FIRMWARE("sitronix/st7123.bin");
