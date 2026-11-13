"""Extend the legacy UMP9620 CC policy with manual external-power host data."""
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent


def apply(kernel):
    kernel = Path(kernel)
    path = kernel / 'drivers/usb/typec/sc27xx_typec.c'
    source = path.read_text()
    def edit(old, new):
        nonlocal source
        if new in source:
            return
        if source.count(old) != 1:
            raise ValueError('DWC3 CC policy anchor: ' + old[:70])
        source = source.replace(old, new, 1)

    for include in ('#include <linux/of_platform.h>', '#include <linux/usb/role.h>',
                    '#include <linux/usb/sprd-dwc3-host-sink.h>'):
        if include not in source:
            edit('#include <linux/module.h>', '#include <linux/module.h>\n'+include)
    edit('struct mutex policy_lock;', 'struct mutex policy_lock;\n\tbool external_host_policy;')
    edit('&dev_attr_usb_role.attr,', '&dev_attr_usb_role.attr,\n\t&dev_attr_usb_host_sink_supported.attr,')
    policy = (HERE.parent / 'm8/typec-policy.h').read_text()
    policy = policy.replace('struct sc27xx_typec *sc, int mode)\n{\n\tu32 mask',
                            'struct sc27xx_typec *sc, int mode, bool external)\n{\n\tu32 mask', 1)
    policy = policy.replace('out:\n\tmutex_unlock(&sc->policy_lock);\n\treturn ret;',
        'out:\n\tif (!ret)\n\t\tWRITE_ONCE(sc->external_host_policy, external);\n'
        '\tmutex_unlock(&sc->policy_lock);\n\tif (!ret)\n\t\textcon_sync(sc->edev, EXTCON_USB);\n\treturn ret;', 1)
    policy = policy.replace('\tmode = READ_ONCE(sc->mode);',
        '\tif (READ_ONCE(sc->external_host_policy))\n\t\treturn sysfs_emit(buf, "host-sink\\n");\n\tmode = READ_ONCE(sc->mode);', 1)
    policy = policy.replace('\tint mode, ret;', '\tint mode, ret;\n\tbool external = false;', 1)
    policy = policy.replace('\telse\n\t\treturn -EINVAL;\n\tret = sc27xx_policy_set(sc, mode);',
        '\telse if (sysfs_streq(buf, "host-sink")) {\n\t\tmode = TYPEC_PORT_UFP;\n\t\texternal = true;\n\t} else\n\t\treturn -EINVAL;\n\tret = sc27xx_policy_set(sc, mode, external);', 1)
    start = policy.index('static ssize_t usb_role_show(')
    policy = policy[:start] + (HERE / 'dwc3-typec-role.h').read_text()
    policy += '''
static ssize_t usb_host_sink_supported_show(struct device *dev,
        struct device_attribute *attr, char *buf)
{
    struct sc27xx_typec *sc = dev_get_drvdata(dev);
    return sysfs_emit(buf, "%d\\n", sc && !sc->use_pdhub_c2c && !IS_ENABLED(CONFIG_SPRD_TYPEC_TCPM));
}
static DEVICE_ATTR_RO(usb_host_sink_supported);
'''
    policy += (HERE / 'dwc3-typec-api.h').read_text()
    path.write_text(source)
    (path.parent / 'ums9620-typec-policy.h').write_text(policy)
    runpy.run_path(str(HERE/'dwc3-typec-vbus.py'))['apply'](
        kernel, lock='policy_lock', policy='external_host_policy',
        policy_header='ums9620-typec-policy.h', irq_label='handled')
