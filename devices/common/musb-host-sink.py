"""Opt-in MUSB host data with external 5V power.

Reuse CC sink, physical VBUS, BC1.2 and the existing state machine. Charger
online reporting is adapted only for AW32257; BQ2560x already reads power-good.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent


def apply(kernel, *, charger):
    if charger not in ("aw32257", "bq2560x"):
        raise ValueError("Unsupported external-host charger: " + charger)
    kernel = Path(kernel)
    edits = {}
    # Normalize the earlier E5 prototype when reusing a prepared private view.
    # Fresh kernel baselines do not contain any of these names.
    for name in ('drivers/usb/typec/sc27xx_typec.c',
                 'drivers/usb/phy/phy-sprd-qogirn6lite.c',
                 'drivers/usb/musb/musb_sprd.c',
                 'drivers/power/supply/aw32257_charger.c'):
        path = kernel / name
        if path.exists():
            source = path.read_text()
            normalized = source.replace('e5_typec_', 'sprd_typec_').replace('e5_hsphy_', 'sprd_hsphy_').replace('e5_external_', 'sprd_external_').replace('e5-host-sink.h', 'sprd-host-sink.h').replace('e5-musb-host-sink.h', 'sprd-musb-host-sink.h')
            if normalized != source:
                edits[path] = normalized

    def patch(name, old, new, count=1):
        path = kernel / name
        source = edits.get(path, path.read_text())
        if new in source:
            return
        if source.count(old) != count:
            raise ValueError('MUSB external-host anchor: ' + name + ': ' + old[:80])
        edits[path] = source.replace(old, new)

    typec = 'drivers/usb/typec/sc27xx_typec.c'
    patch(typec, '#include <linux/module.h>',
          '#include <linux/module.h>\n#include <linux/of_platform.h>\n#include <linux/usb/sprd-host-sink.h>')
    patch(typec, 'struct mutex role_lock;',
          'struct mutex role_lock;\n\tbool external_host_policy, external_data_host;')
    patch(typec, 'bool external_host_policy, external_data_host;',
          'bool external_host_policy, external_data_host;\n'
          '\tbool external_vbus_stopped;\n\tspinlock_t external_vbus_lock;\n'
          '\tstruct delayed_work external_vbus_work;')
    patch(typec, '#include "board-usb-role.h"',
          '#include "sprd-host-sink-typec.h"\n#include "board-usb-role.h"')
    patch(typec, '\tINIT_DELAYED_WORK(&sc->role_work, sc27xx_typec_role_work);',
          '\tINIT_DELAYED_WORK(&sc->role_work, sc27xx_typec_role_work);\n'
          '\tspin_lock_init(&sc->external_vbus_lock);\n'
          '\tINIT_DELAYED_WORK(&sc->external_vbus_work, sc27xx_external_vbus_work);')
    # The physical VBUS notifier must reach this fallback even when there is
    # no CC IRQ at all (the failing powered adapter supplies only VBUS).
    patch(typec, '\tdev_info(sc->dev, "vbus_connect_value:%d\\n", sc->vbus_connect_value);',
          '\tsc27xx_external_vbus_queue(sc);\n'
          '\tdev_info(sc->dev, "vbus_connect_value:%d\\n", sc->vbus_connect_value);')
    patch(typec, 'unlock:\n\tmutex_unlock(&sc->role_lock);',
          'unlock:\n\tsc27xx_external_vbus_queue(sc);\n\tmutex_unlock(&sc->role_lock);')
    patch(typec, '&dev_attr_usb_role.attr,',
          '&dev_attr_usb_role.attr,\n\t&dev_attr_usb_host_sink_supported.attr,')

    # Only explicitly opted-in profiles receive the fourth policy.
    policy = (HERE.parent / 't551m/usb-policy.h').read_text()
    policy = policy.replace('struct sc27xx_typec *sc, int mode)',
                            'struct sc27xx_typec *sc, int mode, bool external)', 1)
    policy = policy.replace('if (old == mode) {\n\t\tret = 0;',
                            'if (old == mode) {\n\t\tWRITE_ONCE(sc->external_host_policy, external);\n\t\tret = 0;', 1)
    policy = policy.replace('out:\n\tmutex_unlock(&sc->role_lock);\n\treturn ret;',
        'out:\n\tif (!ret) {\n\t\tWRITE_ONCE(sc->external_host_policy, external);\n'
        '\t\tif (!external)\n\t\t\tsc27xx_external_vbus_clear(sc);\n\t}\n'
        '\tmutex_unlock(&sc->role_lock);\n'
        '\tif (!ret) {\n\t\tsc27xx_external_vbus_queue(sc);\n'
        '\t\textcon_sync(sc->edev, EXTCON_USB);\n\t}\n\treturn ret;', 1)
    policy = policy.replace('\tmode = READ_ONCE(sc->mode);',
        '\tif (READ_ONCE(sc->external_host_policy))\n\t\treturn sysfs_emit(buf, "host-sink\\n");\n'
        '\tmode = READ_ONCE(sc->mode);', 1)
    policy = policy.replace('\tint mode, ret;', '\tint mode, ret;\n\tbool external = false;', 1)
    policy = policy.replace('\telse\n\t\treturn -EINVAL;\n\tret = sc27xx_typec_set_policy(sc, mode);',
        '\telse if (sysfs_streq(buf, "host-sink")) {\n\t\tmode = TYPEC_PORT_UFP;\n'
        '\t\texternal = true;\n\t} else\n\t\treturn -EINVAL;\n'
        '\tret = sc27xx_typec_set_policy(sc, mode, external);', 1)
    policy = policy.replace('\treturn sysfs_emit(buf, "%s\\n", extcon_get_state',
        '\tif (READ_ONCE(sc->external_data_host) &&\n'
        '\t    (sc->partner_connected || READ_ONCE(sc->vbus_only_connect)))\n'
        '\t\treturn sysfs_emit(buf, "host\\n");\n'
        '\treturn sysfs_emit(buf, "%s\\n", extcon_get_state', 1)
    policy += '''
static ssize_t usb_host_sink_supported_show(struct device *dev,
        struct device_attribute *attr, char *buf)
{
    return sysfs_emit(buf, "%d\\n", !IS_ENABLED(CONFIG_SPRD_TYPEC_TCPM));
}
static DEVICE_ATTR_RO(usb_host_sink_supported);

bool sprd_typec_host_sink(struct device_node *node)
{
    struct platform_device *pdev = of_find_device_by_node(node);
    struct sc27xx_typec *sc;
    bool enabled = false;
    if (!pdev)
        return false;
    sc = platform_get_drvdata(pdev);
    if (sc)
        enabled = READ_ONCE(sc->external_host_policy);
    put_device(&pdev->dev);
    return enabled;
}
EXPORT_SYMBOL_GPL(sprd_typec_host_sink);

bool sprd_typec_external_power(struct device_node *node)
{
    struct platform_device *pdev = of_find_device_by_node(node);
    struct sc27xx_typec *sc;
    bool present = false;
    if (!pdev)
        return false;
    sc = platform_get_drvdata(pdev);
    if (sc && READ_ONCE(sc->external_host_policy) && sc->vbus_dev)
        present = extcon_get_state(sc->vbus_dev, EXTCON_USB) > 0 &&
                  extcon_get_state(sc->edev, EXTCON_USB) > 0;
    put_device(&pdev->dev);
    return present;
}
EXPORT_SYMBOL_GPL(sprd_typec_external_power);

void sprd_typec_data_host(struct device_node *node, bool host)
{
    struct platform_device *pdev = of_find_device_by_node(node);
    struct sc27xx_typec *sc;
    if (!pdev)
        return;
    sc = platform_get_drvdata(pdev);
    if (sc) {
        WRITE_ONCE(sc->external_data_host, host);
        if (sc->port && sc->partner_connected)
            typec_set_data_role(sc->port, host ? TYPEC_HOST : TYPEC_DEVICE);
    }
    put_device(&pdev->dev);
}
EXPORT_SYMBOL_GPL(sprd_typec_data_host);
'''

    phy = 'drivers/usb/phy/phy-sprd-qogirn6lite.c'
    patch(phy, '#include <linux/module.h>',
          '#include <linux/module.h>\n#include <linux/usb/sprd-host-sink.h>')
    patch(phy, '\tbool\t\t\tis_host;', '\tbool\t\t\tis_host;\n\tbool external_host;')
    path = kernel / phy
    source = edits.get(path, path.read_text())
    stop = source.index('static int sprd_hostphy_set(')
    begin = source.find('void sprd_hsphy_external_host(')
    if begin < 0:
        begin = stop
    source = source[:begin] + '''void sprd_hsphy_external_host(struct usb_phy *x, bool external)
{
\tstruct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
\tbool changed = READ_ONCE(phy->external_host) != external;
\tWRITE_ONCE(phy->external_host, external);
\t/* Publish the real external-power session even when BC1.2 is UNKNOWN. */
\tif (changed)
\t\tusb_phy_notify_charger(x);
}
EXPORT_SYMBOL_GPL(sprd_hsphy_external_host);

bool sprd_hsphy_external_power(struct usb_phy *x)
{
\tstruct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
\tstruct device_node *node;
\tbool present;
\tif (!READ_ONCE(phy->external_host) || x->chg_state != USB_CHARGER_PRESENT ||
\t    !(READ_ONCE(x->flags) & CHARGER_DETECT_DONE))
\t\treturn false;
\tnode = of_parse_phandle(x->dev->of_node, "extcon", 0);
\tpresent = node && sprd_typec_external_power(node);
\tof_node_put(node);
\treturn present;
}
EXPORT_SYMBOL_GPL(sprd_hsphy_external_power);

''' + source[stop:]
    edits[path] = source
    patch(phy, 'sprd_usb_host_changed(&phy->bc1p2_info, true);',
          'sprd_usb_host_changed(&phy->bc1p2_info, !READ_ONCE(phy->external_host));')
    patch(phy, 'if (usb_phy->last_event == USB_EVENT_ID) {',
          'if (usb_phy->last_event == USB_EVENT_ID && !READ_ONCE(phy->external_host)) {')

    if charger == "aw32257":
        charger = 'drivers/power/supply/aw32257_charger.c'
        patch(charger, '#include <linux/module.h>',
              '#include <linux/module.h>\n#include <linux/usb/sprd-host-sink.h>')
        patch(charger, '\tif (info->limit)\n\t\t*online = true;',
              '\tif (info->limit || sprd_hsphy_external_power(info->usb_phy))\n\t\t*online = true;')
        patch(charger, '\t\tif (bq->limit)\n\t\t\tval->intval = aw32257_charger_get_status(bq);',
              '\t\tif (bq->limit || sprd_hsphy_external_power(bq->usb_phy))\n'
              '\t\t\tval->intval = aw32257_charger_get_status(bq);')

    glue = 'drivers/usb/musb/musb_sprd.c'
    patch(glue, '#include <linux/module.h>',
          '#include <linux/module.h>\n#include <linux/of_platform.h>\n#include <linux/usb/sprd-host-sink.h>')
    patch(glue, '\tbool\t\t\t\tuse_singlefifo;', '''\tbool external_host, external_stopped;
\tbool external_registered, external_charger_registered;
\tstruct extcon_dev *external_vbus;
\tstruct notifier_block external_nb, external_charger_nb;
\tbool\t\t\t\tuse_singlefifo;''')
    patch(glue, 'static int boot_charging;',
          '#include "sprd-musb-host-sink.h"\n\nstatic int boot_charging;')
    patch(glue, '\tif (glue->vbus_active == event) {', '''\t/* A manual policy update can retain the same real CC attachment. */
\tif (sprd_external_policy(glue) || READ_ONCE(glue->external_host))
\t\tqueue_work(glue->musb_wq, &glue->resume_work);
\tif (glue->vbus_active == event) {''')
    patch(glue, 'if (glue->dr_mode == USB_DR_MODE_HOST && !glue->use_pdhub_c2c)',
          'if (glue->dr_mode == USB_DR_MODE_HOST && !glue->use_pdhub_c2c && !glue->external_host)')
    patch(glue, 'if (glue->drd_state == DRD_STATE_HOST_IDLE && !glue->use_pdhub_c2c)',
          'if (glue->drd_state == DRD_STATE_HOST_IDLE && !glue->use_pdhub_c2c && !sprd_external_policy(glue))')
    patch(glue, '\tint ret = 0;\n\tstruct musb *musb = glue->musb;\n\tstruct musb_hdrc_platform_data *plat',
          '\tint ret = 0;\n\tbool external = on ? sprd_external_ready(glue) : glue->external_host;\n'
          '\tstruct musb *musb = glue->musb;\n\tstruct musb_hdrc_platform_data *plat')
    # This condition occurs twice in the host function, not elsewhere.
    path = kernel / glue
    source = edits.get(path, path.read_text())
    start = source.index('static int musb_sprd_otg_start_host(')
    end = source.index('\nstatic void musb_sprd_chg_detect_work', start)
    body = source[start:end]
    old = 'if (!glue->use_pdhub_c2c || !IS_ENABLED(CONFIG_SPRD_TYPEC_TCPM)) {'
    new = 'if (!external && (!glue->use_pdhub_c2c || !IS_ENABLED(CONFIG_SPRD_TYPEC_TCPM))) {'
    if new not in body:
        if body.count(old) != 2:
            raise ValueError('MUSB host boost ownership changed')
        body = body.replace(old, new)
    begin = body.index('\t\tdev_info(glue->dev, "%s: turn on host')
    stop = body.index('\n\t\tif (!external &&', begin)
    body = body[:begin] + '''\t\tdev_info(glue->dev, "%s: turn on host external=%d\\n", __func__, external);
\t\t/* Attachment may change after the state machine sampled its inputs. */
\t\tif (sprd_external_policy(glue) && !external)
\t\t\treturn -ENOLINK;
\t\t/* Refuse any externally powered session while local boost is on. */
\t\tif (external && regulator_is_enabled(glue->vbus) != 0)
\t\t\treturn -EBUSY;
''' + body[stop:]
    source = source[:start] + body + source[end:]
    edits[path] = source
    patch(glue, '\t\t\t\tret = regulator_enable(glue->vbus);', '''\t\t\t\tif (!glue->external_vbus ||
\t\t\t\t    extcon_get_state(glue->external_vbus, EXTCON_USB) > 0)
\t\t\t\t\treturn -EBUSY;
\t\t\t\tret = regulator_enable(glue->vbus);''')
    patch(glue, '\t\tusb_phy_vbus_on(glue->xceiv);', '''\t\tglue->external_host = external;
\t\tsprd_hsphy_external_host(glue->xceiv, external);
\t\tusb_phy_vbus_on(glue->xceiv);''')
    patch(glue, '\t\t\t\tregulator_disable(glue->vbus);', '''\t\t\t\tif (!external)
\t\t\t\t\tregulator_disable(glue->vbus);
\t\t\t\tusb_phy_vbus_off(glue->xceiv);
\t\t\t\tsprd_hsphy_external_host(glue->xceiv, false);
\t\t\t\tglue->external_host = false;''')
    patch(glue, '\t\tmusb_host_resume_root_hub(musb);',
          '\t\tmusb_host_resume_root_hub(musb);\n\t\tsprd_external_data_role(glue, true);')
    patch(glue, '\t\tusb_phy_vbus_off(glue->xceiv);\n\n\t\t/* Decrement pm usage count when leave host state.*/', '''\t\tusb_phy_vbus_off(glue->xceiv);
\t\tsprd_hsphy_external_host(glue->xceiv, false);
\t\tglue->external_host = false;
\t\tsprd_external_data_role(glue, false);
\t\tif (external || sprd_external_policy(glue))
\t\t\tqueue_work(glue->musb_wq, &glue->resume_work);

\t\t/* Decrement pm usage count when leave host state.*/''')
    patch(glue, '\tif (rework)\n\t\tqueue_delayed_work(glue->sm_usb_wq, &glue->chg_detect_work, delay);',
          '\tif (rework)\n\t\tqueue_delayed_work(glue->sm_usb_wq, &glue->chg_detect_work, delay);\n'
          '\telse if (sprd_external_policy(glue))\n\t\tqueue_work(glue->musb_wq, &glue->resume_work);')
    path = kernel / glue
    source = edits.get(path, path.read_text())
    begin = source.index('static void musb_sprd_ext_event_notify(')
    begin = source.index('\n\tif (', begin) + 1
    end = source.index('\n\t\tclear_bit(ID, &glue->inputs);', begin)
    end += len('\n\t\tclear_bit(ID, &glue->inputs);')
    source = source[:begin] + '''\tif ((glue->external_host && !sprd_external_ready(glue)) ||
\t    (!glue->external_host && sprd_external_ready(glue) &&
\t     glue->drd_state == DRD_STATE_HOST) ||
\t    (glue->id_state == MUSB_ID_FLOAT && !sprd_external_ready(glue)))
\t\tset_bit(ID, &glue->inputs);
\telse
\t\tclear_bit(ID, &glue->inputs);''' + source[end:]
    edits[path] = source
    patch(glue, '\tif (glue->id_state == MUSB_ID_FLOAT &&\n\t    (glue->drd_state == DRD_STATE_HOST || glue->drd_state == DRD_STATE_HOST_IDLE)) {',
          '\tif (glue->id_state == MUSB_ID_FLOAT && !sprd_external_ready(glue) &&\n\t    (glue->drd_state == DRD_STATE_HOST || glue->drd_state == DRD_STATE_HOST_IDLE)) {', count=2)
    # Above conditional exists in both charger work and resume work.
    patch(glue, '\tif (glue->charging_mode || boot_charging) {',
          '\tif ((glue->charging_mode && !sprd_external_ready(glue)) || boot_charging) {')
    patch(glue, '\tif (glue->vbus_active && !glue->in_restart && !glue->charging_mode &&',
          '\tif (!sprd_external_policy(glue) && glue->vbus_active && !glue->in_restart && !glue->charging_mode &&')
    path = kernel / glue
    source = edits.get(path, path.read_text())
    stop = source.index('\t\tif (pdata.mode == MUSB_HOST && !is_slave)')
    begin = source.rfind('\t\tret = sprd_external_start(glue);', 0, stop)
    if begin < 0:
        begin = stop
    source = source[:begin] + '''\t\tret = sprd_external_start(glue);
\t\tif (ret) {
\t\t\textcon_unregister_notifier(glue->id_edev ? glue->id_edev : glue->edev,
\t\t\t\t\t   EXTCON_USB_HOST, &glue->id_nb);
\t\t\tgoto err_extcon_vbus;
\t\t}
''' + source[stop:]
    edits[path] = source
    patch(glue, '\tusb_role_switch_unregister(glue->role_sw);\n\tplatform_device_unregister(glue->musb_pdev);',
          '\tsprd_external_stop(glue);\n\tusb_role_switch_unregister(glue->role_sw);\n\tplatform_device_unregister(glue->musb_pdev);')
    # Retire the earlier prototype's asynchronous stop hook from reused views.
    # Drain the work inside HCD cleanup instead, before removal can free musb.
    core_path = kernel / 'drivers/usb/musb/musb_core.c'
    core = core_path.read_text()
    prototype = ('\t/* SPRD: publish the stopped session even without a final IRQ. */\n'
                 '\tif (musb->ops->quirks & MUSB_DMA_SPRD)\n'
                 '\t\tmod_delayed_work(system_wq, &musb->irq_work, 0);\n')
    if prototype in core:
        edits[core_path] = core.replace(prototype, '')
    # usb_remove_hcd() clears DEVCTL and stops IRQs. The core's independent
    # session PM reference still needs its normal check, even without an IRQ.
    patch('drivers/usb/musb/musb_host.c',
          '\tusb_remove_hcd(musb->hcd);\n}',
          '\tusb_remove_hcd(musb->hcd);\n'
          '\tif (musb->ops->quirks & MUSB_DMA_SPRD) {\n'
          '\t\t/* Balance core session PM before the glue waits for suspend. */\n'
          '\t\tmod_delayed_work(system_wq, &musb->irq_work, 0);\n'
          '\t\tflush_delayed_work(&musb->irq_work);\n'
          '\t}\n}')
    for path, source in edits.items():
        path.write_text(source)
    (kernel / 'drivers/usb/typec/board-usb-policy.h').write_text(policy)
    role = (HERE.parent / 't551m/usb-role.h').read_text()
    role = role.replace('\tcancel_delayed_work_sync(&sc->role_work);',
                        '\tsc27xx_external_vbus_cleanup(sc);\n'
                        '\tcancel_delayed_work_sync(&sc->role_work);', 1)
    (kernel / 'drivers/usb/typec/board-usb-role.h').write_text(role)
    (kernel / 'drivers/usb/typec/sprd-host-sink-typec.h').write_bytes(
        (HERE / 'host-sink-typec.h').read_bytes())
    (kernel / 'drivers/usb/musb/sprd-musb-host-sink.h').write_bytes((HERE / 'musb-host-sink.h').read_bytes())
    (kernel / 'include/linux/usb/sprd-host-sink.h').write_bytes((HERE / 'host-sink-api.h').read_bytes())

    for name in ('drivers/usb/musb/e5-musb-host-sink.h', 'include/linux/usb/e5-host-sink.h'):
        (kernel / name).unlink(missing_ok=True)
