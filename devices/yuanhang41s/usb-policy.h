/* SPDX-License-Identifier: GPL-2.0-only */
/* UMP9620 MODE/CC_SW fields match this unit's stock Type-C driver. */
#include <linux/of_platform.h>
static int yh41s_typec_policy(struct sc27xx_typec *sc, int mode, bool external)
{
	u32 mask = SC27XX_CC1_SW_SWITCH_MASK | SC27XX_CC2_SW_SWITCH_MASK;
	u32 open = SC27XX_CC1_SW_SWITCH(SC27XX_TYPEC_NC) |
		   SC27XX_CC2_SW_SWITCH(SC27XX_TYPEC_NC);
	u32 hw, old_hw;
	int old, ret, restore;

	if (IS_ENABLED(CONFIG_SPRD_TYPEC_TCPM) || sc->use_pdhub_c2c)
		return -EOPNOTSUPP;
	switch (mode) {
	case TYPEC_PORT_UFP: hw = SC27XX_MODE_SNK; break;
	case TYPEC_PORT_DFP: hw = SC27XX_MODE_SRC; break;
	case TYPEC_PORT_DRP: hw = SC27XX_MODE_DRP; break;
	default: return -EINVAL;
	}
	mutex_lock(&sc->role_lock);
	old = sc->mode;
	if (old == mode) { WRITE_ONCE(sc->host_sink, external); ret = 0; goto out; }
	old_hw = old == TYPEC_PORT_UFP ? SC27XX_MODE_SNK :
		 old == TYPEC_PORT_DFP ? SC27XX_MODE_SRC : SC27XX_MODE_DRP;
	if (sc->partner_connected)
		sc27xx_typec_disconnect(sc, sc->state);
	sc27xx_typec_connect_state = 0;
	/* Only an explicit policy change pulses CC. Reconnect through hardware
	 * detection; the glue still refuses boost into a sink/PC connection. */
	ret = regmap_update_bits(sc->regmap, sc->base + SC27XX_SW_CFG, mask, open);
	if (!ret)
		ret = regmap_update_bits(sc->regmap, sc->base + sc->var_data->mode,
					 SC27XX_MODE_MASK, hw);
	if (!ret) {
		sc->mode = mode;
		msleep(30);
	}
	restore = regmap_update_bits(sc->regmap, sc->base + SC27XX_SW_CFG, mask, 0);
	if (!ret) ret = restore;
	if (ret) {
		regmap_update_bits(sc->regmap, sc->base + sc->var_data->mode,
				   SC27XX_MODE_MASK, old_hw);
		sc->mode = old;
	} else {
		WRITE_ONCE(sc->host_sink, external);
	}
out:
	mutex_unlock(&sc->role_lock);
	/* Broadcast the real CC state even when only the data policy changed. */
	if (!ret) {
		dev_info(sc->dev, "USB policy=%s CC mode=%d\n", external ? "host-sink" :
			 mode == TYPEC_PORT_UFP ? "device" : mode == TYPEC_PORT_DFP ? "host" : "auto", mode);
		extcon_sync(sc->edev, EXTCON_USB);
	}
	return ret;
}

static ssize_t usb_role_policy_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	struct sc27xx_typec *sc = dev_get_drvdata(dev);
	int mode;
	if (!sc) return -EAGAIN;
	if (READ_ONCE(sc->host_sink))
		return sysfs_emit(buf, "host-sink\n");
	mode = READ_ONCE(sc->mode);
	return sysfs_emit(buf, "%s\n", mode == TYPEC_PORT_UFP ? "device" :
			  mode == TYPEC_PORT_DFP ? "host" : "auto");
}

static ssize_t usb_role_policy_store(struct device *dev,
		struct device_attribute *attr, const char *buf, size_t count)
{
	struct sc27xx_typec *sc = dev_get_drvdata(dev);
	int mode, ret;
	bool external = false;
	if (!sc) return -EAGAIN;
	if (sysfs_streq(buf, "device")) mode = TYPEC_PORT_UFP;
	else if (sysfs_streq(buf, "host")) mode = TYPEC_PORT_DFP;
	else if (sysfs_streq(buf, "auto")) mode = TYPEC_PORT_DRP;
	else if (sysfs_streq(buf, "host-sink")) { mode = TYPEC_PORT_UFP; external = true; }
	else return -EINVAL;
	ret = yh41s_typec_policy(sc, mode, external);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(usb_role_policy);

static ssize_t usb_role_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	struct sc27xx_typec *sc = dev_get_drvdata(dev);
	struct device_node *node;
	struct usb_role_switch *sw;
	enum usb_role role;
	if (!sc) return -EAGAIN;
	/* Report the controller, not an inferred role from the power attachment. */
	for_each_compatible_node(node, NULL, "snps,sprd-dwc3") {
		if (!of_device_is_available(node))
			continue;
		sw = usb_role_switch_find_by_fwnode(of_fwnode_handle(node));
		if (IS_ERR_OR_NULL(sw))
			continue;
		role = usb_role_switch_get_role(sw);
		usb_role_switch_put(sw);
		of_node_put(node);
		return sysfs_emit(buf, "%s\n", role == USB_ROLE_HOST ? "host" :
			role == USB_ROLE_DEVICE && sc->partner_connected ? "device" : "none");
	}
	return sysfs_emit(buf, "none\n");
}
static DEVICE_ATTR_RO(usb_role);

static ssize_t usb_host_sink_supported_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "1\n");
}
static DEVICE_ATTR_RO(usb_host_sink_supported);

bool yh41s_typec_host_sink(struct device_node *node)
{
	struct platform_device *pdev = of_find_device_by_node(node);
	struct sc27xx_typec *sc;
	bool enabled = false;

	if (!pdev)
		return false;
	sc = platform_get_drvdata(pdev);
	if (sc)
		enabled = READ_ONCE(sc->host_sink);
	put_device(&pdev->dev);
	return enabled;
}
EXPORT_SYMBOL_GPL(yh41s_typec_host_sink);

void yh41s_typec_data_role(struct device_node *node, bool host)
{
	struct platform_device *pdev = of_find_device_by_node(node);
	struct sc27xx_typec *sc;

	if (!pdev)
		return;
	sc = platform_get_drvdata(pdev);
	if (sc && sc->port && READ_ONCE(sc->partner_connected))
		typec_set_data_role(sc->port, host ? TYPEC_HOST : TYPEC_DEVICE);
	put_device(&pdev->dev);
}
EXPORT_SYMBOL_GPL(yh41s_typec_data_role);
