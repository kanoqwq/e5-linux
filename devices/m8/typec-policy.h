/* SPDX-License-Identifier: GPL-2.0-only */
/* UMP9620 legacy CC policy. DWC3 still follows real Type-C attachments and
 * owns PHY/boost sequencing; a policy write never fabricates an ID event.
 */
static bool sc27xx_policy_accepts(struct sc27xx_typec *sc, u32 state)
{
	return !((sc->mode == TYPEC_PORT_UFP && state == SC27XX_ATTACHED_SRC) ||
		 (sc->mode == TYPEC_PORT_DFP &&
		  (state == SC27XX_ATTACHED_SNK || state == SC27XX_DEBUG_CABLE)));
}

static int sc27xx_policy_hw(struct sc27xx_typec *sc, int mode)
{
	u32 value;
	switch (mode) {
	case TYPEC_PORT_UFP: value = SC27XX_MODE_SNK; break;
	case TYPEC_PORT_DFP: value = SC27XX_MODE_SRC; break;
	case TYPEC_PORT_DRP: value = SC27XX_MODE_DRP; break;
	default: return -EINVAL;
	}
	return regmap_update_bits(sc->regmap, sc->base + sc->var_data->mode,
				  SC27XX_MODE_MASK, value);
}

static int sc27xx_policy_set(struct sc27xx_typec *sc, int mode)
{
	u32 mask = SC27XX_CC1_SW_SWITCH_MASK | SC27XX_CC2_SW_SWITCH_MASK;
	u32 open = SC27XX_CC1_SW_SWITCH(SC27XX_TYPEC_NC) |
		   SC27XX_CC2_SW_SWITCH(SC27XX_TYPEC_NC);
	int old, ret, restore;
	if (IS_ENABLED(CONFIG_SPRD_TYPEC_TCPM))
		return -EOPNOTSUPP;
	if (mode != TYPEC_PORT_UFP && mode != TYPEC_PORT_DFP && mode != TYPEC_PORT_DRP)
		return -EINVAL;
	mutex_lock(&sc->policy_lock);
	old = sc->mode;
	if (old == mode) {
		ret = 0;
		goto out;
	}
	/* Only explicit policy changes pulse CC; normal attach/detach is unchanged. */
	ret = regmap_update_bits(sc->regmap, sc->base + SC27XX_SW_CFG, mask, open);
	if (ret)
		goto out;
	if (sc->partner_connected)
		sc27xx_typec_disconnect(sc, sc->state);
	sc27xx_typec_connect_state = 0;
	ret = sc27xx_policy_hw(sc, mode);
	if (!ret) {
		sc->mode = mode;
		msleep(30);
	} else {
		restore = sc27xx_policy_hw(sc, old);
		if (restore)
			dev_err(sc->dev, "cannot restore Type-C policy: %d\n", restore);
	}
	restore = regmap_update_bits(sc->regmap, sc->base + SC27XX_SW_CFG, mask, 0);
	if (!ret && restore) {
		ret = restore;
		if (!sc27xx_policy_hw(sc, old))
			sc->mode = old;
		regmap_update_bits(sc->regmap, sc->base + SC27XX_SW_CFG, mask, 0);
	}
out:
	mutex_unlock(&sc->policy_lock);
	return ret;
}

static ssize_t usb_role_policy_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	struct sc27xx_typec *sc = dev_get_drvdata(dev);
	int mode;
	if (!sc)
		return -EAGAIN;
	mode = READ_ONCE(sc->mode);
	return sysfs_emit(buf, "%s\n", mode == TYPEC_PORT_UFP ? "device" :
			  mode == TYPEC_PORT_DFP ? "host" : "auto");
}

static ssize_t usb_role_policy_store(struct device *dev,
		struct device_attribute *attr, const char *buf, size_t count)
{
	struct sc27xx_typec *sc = dev_get_drvdata(dev);
	int mode, ret;
	if (!sc)
		return -EAGAIN;
	if (sysfs_streq(buf, "auto"))
		mode = TYPEC_PORT_DRP;
	else if (sysfs_streq(buf, "device"))
		mode = TYPEC_PORT_UFP;
	else if (sysfs_streq(buf, "host"))
		mode = TYPEC_PORT_DFP;
	else
		return -EINVAL;
	ret = sc27xx_policy_set(sc, mode);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(usb_role_policy);

static ssize_t usb_role_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	struct sc27xx_typec *sc = dev_get_drvdata(dev);
	if (!sc)
		return -EAGAIN;
	return sysfs_emit(buf, "%s\n", extcon_get_state(sc->edev, EXTCON_USB_HOST) > 0 ?
			  "host" : extcon_get_state(sc->edev, EXTCON_USB) > 0 ? "device" : "none");
}
static DEVICE_ATTR_RO(usb_role);
