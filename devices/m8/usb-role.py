#!/usr/bin/env python3
"""Wire the M8 legacy Type-C/boost/combined-PHY topology to DWC3 DRD."""
from pathlib import Path
import runpy


def apply(kernel):
    kernel = Path(kernel)
    here = Path(__file__).resolve().parent
    replace_function = runpy.run_path(str(here.parent / 't551m/prepare-kernel.py'))['replace_function']

    def patch(name, old, new):
        path = kernel / name
        text = path.read_text()
        if new in text:
            return
        if text.count(old) != 1:
            raise ValueError('M8 USB role anchor: ' + name + ': ' + old[:80])
        path.write_text(text.replace(old, new))

    (kernel / 'drivers/usb/dwc3/dwc3-sprd-role.h').write_bytes((here / 'dwc3-role.h').read_bytes())
    glue = 'drivers/usb/dwc3/dwc3-of-simple.c'
    patch(glue, '#include <linux/reset.h>', '#include <linux/reset.h>\n#include "dwc3-sprd-role.h"')
    patch(glue, '\tbool\t\t\tneed_reset;', '\tbool\t\t\tneed_reset;\n\tstruct sprd_dwc3_role *role;')
    patch(glue, '\tsimple->dev = dev;', '''\tsimple->dev = dev;
\tsimple->role = sprd_dwc3_role_prepare(dev);
\tif (IS_ERR(simple->role))
\t\treturn dev_err_probe(dev, PTR_ERR(simple->role), "Type-C resources unavailable\\n");''')
    patch(glue, '\tpm_runtime_get_sync(dev);\n\n\treturn 0;', '''\tpm_runtime_get_sync(dev);
\tret = sprd_dwc3_role_start(simple->role);
\tif (ret) {
\t\tof_platform_depopulate(dev);
\t\tpm_runtime_put_noidle(dev);
\t\tpm_runtime_disable(dev);
\t\tgoto err_clk_disable;
\t}
\treturn 0;''')
    patch(glue, '\tof_platform_depopulate(simple->dev);',
          '\tsprd_dwc3_role_stop(simple->role);\n\tof_platform_depopulate(simple->dev);')

    phy = 'drivers/usb/phy/phy-sprd-ums9620-ssphy.c'
    patch(phy, '\tif (on) {\n\t\tregmap_write(phy->ana_g0l,', '''\tif (on) {
\t\t/* Stop sink detection before sourcing our own VBUS. */
\t\tWRITE_ONCE(phy->is_host, true);
\t\tsprd_usb_changed(&phy->bc1p2_info, USB_CHARGER_ABSENT);
\t\tkthread_flush_work(&phy->bc1p2_info.usb_change_kwork.work);
\t\tkthread_flush_work(&phy->bc1p2_info.bc1p2_kwork.work);
\t\tflush_work(&phy->charger_work);
\t\tsprd_ssphy_dpdm_switch(x, true);
\t\tregmap_write(phy->ana_g0l,''')
    patch(phy, '\t\tphy->is_host = false;\n\t}\n\n\treturn ret;', '''\t\t/* Glue has already disabled boost and let its voltage fall. */
\t\tsprd_ssphy_dpdm_switch(x, false);
\t\tWRITE_ONCE(phy->is_host, false);
\t\tsprd_usb_changed(&phy->bc1p2_info,
\t\t\textcon_get_state(x->edev, EXTCON_USB) > 0 ?
\t\t\tUSB_CHARGER_PRESENT : USB_CHARGER_ABSENT);
\t}

\treturn ret;''')
    patch(phy, '''\tif (phy->is_host)
\t\treturn NOTIFY_OK;
\tsprd_usb_changed(&phy->bc1p2_info,''', '''\tif (phy->is_host)
\t\treturn NOTIFY_OK;
\t/* Return D+/D- to detection immediately, including a quick replug
\t * whose ABSENT notification may be coalesced by the worker. */
\tsprd_ssphy_dpdm_switch(x, false);
\tsprd_usb_changed(&phy->bc1p2_info,''')

    # The old worker stores only the LAST VBUS state. A quick unplug/replug
    # could discard ABSENT and retain the previous cable's detection result.
    header = 'include/linux/power/sprd-bc1p2.h'
    patch(header, '\tbool vbus_events;', '\tbool vbus_events;\n\tbool detached;')
    bc = 'drivers/usb/phy/sprd-bc1p2.c'
    patch(bc, '\tusb_phy->chg_state = state;', '''\tif (state == USB_CHARGER_ABSENT)
\t\tbc1p2_info->detached = true;
\tusb_phy->chg_state = state;''')
    patch(bc, '''\t\t\t\t\t\t\t  usb_change_kwork.work);

\tspin_lock''', '''\t\t\t\t\t\t\t  usb_change_kwork.work);
\tbool detached;

\tspin_lock''')
    patch(bc, '''\twhile (bc1p2_info->vbus_events) {
\t\tbc1p2_info->vbus_events = false;''', '''\twhile (bc1p2_info->vbus_events) {
\t\tbc1p2_info->vbus_events = false;
\t\tdetached = bc1p2_info->detached;
\t\tbc1p2_info->detached = false;''')
    patch(bc, '''\t\tkthread_queue_delayed_work(&bc1p2_info->bc1p2_kworker,
\t\t\t\t\t   &bc1p2_info->bc1p2_kwork, 0);''', '''\t\tif (detached) {
\t\t\tmutex_lock(&bc1p2_info->bc1p2->bc1p2_lock);
\t\t\tbc1p2_info->phy->chg_type = UNKNOWN_TYPE;
\t\t\tbc1p2_info->phy->flags &= ~CHARGER_DETECT_DONE;
\t\t\tbc1p2_info->bc1p2->detect_state = CHG_STATE_UNDETECT;
\t\t\tbc1p2_info->bc1p2->retry_chg_detect_count = 0;
\t\t\tbc1p2_info->bc1p2->type = UNKNOWN_TYPE;
\t\t\tbc1p2_info->rework = false;
\t\t\tmutex_unlock(&bc1p2_info->bc1p2->bc1p2_lock);
\t\t}
\t\tkthread_queue_delayed_work(&bc1p2_info->bc1p2_kworker,
\t\t\t\t\t   &bc1p2_info->bc1p2_kwork, 0);''')

    typec = 'drivers/usb/typec/sc27xx_typec.c'
    path = kernel / typec
    text = path.read_text()
    text = replace_function(text, 'sc27xx_typec_interrupt', r'''
\tstruct sc27xx_typec *sc = data;
\tu32 event, state;
\tbool attached;
\tint ret;

\tret = regmap_read(sc->regmap, sc->base + SC27XX_INT_MASK, &event);
\tif (ret)
\t\treturn IRQ_NONE;
\tevent &= sc->var_data->event_mask;
\tret = regmap_read(sc->regmap, sc->base + SC27XX_STATUS, &state);
\t/* Acknowledge only the snapshot, before callbacks can sleep. */
\tregmap_write(sc->regmap, sc->base + sc->var_data->int_clr, event);
\tif (ret)
\t\treturn IRQ_HANDLED;
\tsc->state = state & sc->var_data->state_mask;
\tattached = sc->state == SC27XX_ATTACHED_SNK ||
\t\t   sc->state == SC27XX_ATTACHED_SRC ||
\t\t   sc->state == SC27XX_DEBUG_CABLE || sc->state == SC27XX_AUDIO_CABLE;
\tif (event & SC27XX_ERR_IN_INT)
\t\tsc27xx_typec_err_recover_state_disen();
\tif (!(event & (SC27XX_DE_ATTACH_MASK | SC27XX_ERR_IN_INT)))
\t\treturn IRQ_HANDLED;

\t/* Hardware already debounces CC (105 ms). Do not drop a valid attach
\t * merely because preceding cable transitions were close together.
\t * Both bits may be latched: remove the old partner, then use CURRENT
\t * stable CC state rather than giving the attach bit unconditional priority.
\t */
\tif (sc->partner_connected &&
\t    ((event & SC27XX_DETACH_INT) || !attached ||
\t     (sc->pre_state != sc->state && sc->state != SC27XX_DEBUG_CABLE))) {
\t\tsc27xx_typec_disconnect(sc, sc->state);
\t\tsc27xx_typec_connect_state = 0;
\t}
\tif (attached) {
\t\tret = sc27xx_typec_connect(sc, sc->state);
\t\tsc27xx_typec_connect_state = sc->partner_connected;
\t\tif (ret)
\t\t\tdev_warn(sc->dev, "failed to register partner: %d\n", ret);
\t} else if (event & SC27XX_DETACH_INT) {
\t\tsc27xx_typec_connect_state = 0;
\t\tsc27xx_typec_random_tdrp(sc);
\t}
\tdev_info(sc->dev, "CC state=%u event=%#x connected=%d\n",
\t\t sc->state, event, sc->partner_connected);
\treturn IRQ_HANDLED;'''.replace('\\t', '\t'))
    # Keep the old diagnostic helper out of the build's unused warnings.
    text = text.replace('static bool sc27xx_typec_check(', 'static __maybe_unused bool sc27xx_typec_check(')
    text = text.replace('struct typec_partner_desc desc;', 'struct typec_partner_desc desc = {};')
    text = text.replace('if (!sc->partner)\n\t\treturn -ENODEV;', '''if (IS_ERR(sc->partner)) {
\t\tint err = PTR_ERR(sc->partner);
\t\tsc->partner = NULL;
\t\treturn err;
\t}''')
    text = text.replace('if (!sc->port) {', 'if (IS_ERR(sc->port)) {')
    text = text.replace('if (extcon_get_state(sc->vbus_dev, EXTCON_USB) == true)',
                        'if (sc->vbus_dev && extcon_get_state(sc->vbus_dev, EXTCON_USB) > 0)')
    # No interrupt/notifier can see uninitialized work items.
    init = '''\tsc->pd_swap_evt = TYPEC_NO_SWAP;
\tINIT_DELAYED_WORK(&sc->typec_int_clr_work, sc27xx_typec_int_clr_work);
\tINIT_DELAYED_WORK(&sc->drswap_work, sc27xx_typec_extcon_drswap_work);
\tINIT_DELAYED_WORK(&sc->vbus_only_work, sc27xx_typec_vbus_only_work);
\tINIT_DELAYED_WORK(&sc->err_clr_work, sc27xx_typec_err_clr_work);
'''
    if text.count(init) != 1:
        raise ValueError('Type-C work initialization anchor')
    irq_anchor = '\tret = devm_request_threaded_irq(sc->dev,'
    placed = init + '\n' + irq_anchor
    if placed not in text:
        text = text.replace(init, '')
        text = text.replace(irq_anchor, placed)
    path.write_text(text)
