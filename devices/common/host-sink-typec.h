/* SPDX-License-Identifier: GPL-2.0-only */
/* Some powered adapters supply VBUS without a CC partner. Like the vendor's
 * VBUS-only path, publish power/data presence, never a fabricated CC partner
 * or USB-HOST event. This fallback belongs only to the manual sink policy.
 */
static void sc27xx_external_vbus_clear(struct sc27xx_typec *sc)
{
	if (!sc->vbus_only_connect)
		return;
	sc->vbus_only_connect = false;
	/* A real sink attachment may have taken over these same extcon levels. */
	if (sc->partner_connected &&
	    (sc->pre_state == SC27XX_ATTACHED_SNK || sc->pre_state == SC27XX_DEBUG_CABLE))
		return;
	extcon_set_state_sync(sc->edev, EXTCON_USB, false);
	extcon_set_state_sync(sc->edev, EXTCON_SINK, false);
}

static void sc27xx_external_vbus_work(struct work_struct *work)
{
	struct sc27xx_typec *sc = container_of(to_delayed_work(work),
					struct sc27xx_typec, external_vbus_work);
	bool present;

	mutex_lock(&sc->role_lock);
	present = !READ_ONCE(sc->external_vbus_stopped) &&
		  READ_ONCE(sc->external_host_policy) && sc->mode == TYPEC_PORT_UFP &&
		  !IS_ERR_OR_NULL(sc->vbus_dev) && extcon_get_state(sc->vbus_dev, EXTCON_USB) > 0 &&
		  !sc->partner_connected && extcon_get_state(sc->edev, EXTCON_USB_HOST) == 0;
	if (!present) {
		sc27xx_external_vbus_clear(sc);
	} else if (!sc->vbus_only_connect) {
		sc->vbus_only_connect = true;
		extcon_set_state_sync(sc->edev, EXTCON_SINK, true);
		extcon_set_state_sync(sc->edev, EXTCON_USB, true);
		dev_info(sc->dev, "manual external host: VBUS-only attachment\n");
	}
	mutex_unlock(&sc->role_lock);
}

static void sc27xx_external_vbus_queue(struct sc27xx_typec *sc)
{
	unsigned long flags, delay = 0;
	/* Reuse the vendor's 500 ms CC grace period once per arrival. No polling
	 * and no delayed detach: the worker always rechecks the current inputs.
	 */
	if (!READ_ONCE(sc->vbus_only_connect) && !IS_ERR_OR_NULL(sc->vbus_dev) &&
	    extcon_get_state(sc->vbus_dev, EXTCON_USB) > 0)
		delay = msecs_to_jiffies(500);
	spin_lock_irqsave(&sc->external_vbus_lock, flags);
	if (!sc->external_vbus_stopped &&
	    (READ_ONCE(sc->external_host_policy) || READ_ONCE(sc->vbus_only_connect)))
		mod_delayed_work(system_wq, &sc->external_vbus_work, delay);
	spin_unlock_irqrestore(&sc->external_vbus_lock, flags);
}

static void sc27xx_external_vbus_cleanup(struct sc27xx_typec *sc)
{
	unsigned long flags;
	spin_lock_irqsave(&sc->external_vbus_lock, flags);
	WRITE_ONCE(sc->external_vbus_stopped, true);
	spin_unlock_irqrestore(&sc->external_vbus_lock, flags);
	cancel_delayed_work_sync(&sc->external_vbus_work);
}
