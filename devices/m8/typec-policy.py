"""Expose legacy UMP9620 CC policy to the existing shared USB backend."""
from pathlib import Path


def apply(kernel):
    source = Path(kernel) / 'drivers/usb/typec/sc27xx_typec.c'
    text = source.read_text()

    def edit(old, new):
        nonlocal text
        if new in text:
            return
        if text.count(old) != 1:
            raise ValueError('UMS9620 Type-C policy anchor: ' + old[:70])
        text = text.replace(old, new, 1)

    edit('#include <linux/module.h>', '#include <linux/module.h>\n#include <linux/mutex.h>')
    edit('struct sc27xx_typec {', 'struct sc27xx_typec {\n\tstruct mutex policy_lock;')
    edit('\tsc->edev = devm_extcon_dev_allocate(',
         '\tmutex_init(&sc->policy_lock);\n\tsc->edev = devm_extcon_dev_allocate(')
    # After connect/disconnect are defined, before the IRQ and sysfs handlers.
    edit('static irqreturn_t sc27xx_typec_interrupt(',
         '#include "ums9620-typec-policy.h"\n\nstatic irqreturn_t sc27xx_typec_interrupt(')
    edit('static struct attribute *sc27xx_typec_attrs[] = {',
         'static struct attribute *sc27xx_typec_attrs[] = {\n'
         '\t&dev_attr_usb_role_policy.attr,\n\t&dev_attr_usb_role.attr,')
    start = text.index('static irqreturn_t sc27xx_typec_interrupt(')
    end = text.index('\nstatic int sc27xx_vbus_ok_bypass', start)
    irq = text[start:end]
    if 'mutex_lock(&sc->policy_lock)' not in irq:
        irq = irq.replace('\tret = regmap_read(', '\tmutex_lock(&sc->policy_lock);\n\tret = regmap_read(', 1)
        irq = irq.replace('\t\treturn IRQ_NONE;', '\t\tgoto no_event;')
        irq = irq.replace('\t\treturn IRQ_HANDLED;', '\t\tgoto handled;')
        marker = '\tsc->state = state & sc->var_data->state_mask;'
        if irq.count(marker) != 1:
            raise ValueError('UMS9620 Type-C state sample')
        irq = irq.replace(marker, marker + '\n\tif (!sc27xx_policy_accepts(sc, sc->state))\n\t\tgoto handled;')
        irq = irq.replace('\treturn IRQ_HANDLED;', 'handled:\n\tmutex_unlock(&sc->policy_lock);\n\treturn IRQ_HANDLED;\nno_event:\n\tmutex_unlock(&sc->policy_lock);\n\treturn IRQ_NONE;')
        text = text[:start] + irq + text[end:]
    # VBUS fallback must not advertise a sink while an explicit source policy
    # is selected. Keep notifier spinlocks out of sleeping CC/extcon work.
    edit('\t\tsc27xx_typec_handle_vbus(sc);',
         '\t\tmutex_lock(&sc->policy_lock);\n'
         '\t\tif (sc->mode != TYPEC_PORT_DFP)\n'
         '\t\t\tsc27xx_typec_handle_vbus(sc);\n'
         '\t\tmutex_unlock(&sc->policy_lock);')
    source.write_text(text)
    (source.parent / 'ums9620-typec-policy.h').write_bytes(Path(__file__).with_suffix('.h').read_bytes())


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
