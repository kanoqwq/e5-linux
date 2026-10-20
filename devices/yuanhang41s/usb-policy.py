"""Expose the common USB policy ABI on this board's existing UMP9620 CC path.

Keep automatic DRP behavior and the DWC3 real-attachment/boost ordering. No
synthetic host event, always-on boost or extra automatic sink timer is added.
"""
from pathlib import Path


def apply(kernel):
    path = Path(kernel)/'drivers/usb/typec/sc27xx_typec.c'
    source = path.read_text()
    if '#include <linux/mutex.h>' not in source:
        source = source.replace('#include <linux/module.h>', '#include <linux/module.h>\n#include <linux/mutex.h>')
    if '#include <linux/usb/yh41s-host-sink.h>' not in source:
        source = source.replace('#include <linux/module.h>', '#include <linux/module.h>\n#include <linux/usb/role.h>\n#include <linux/usb/yh41s-host-sink.h>')
    if 'struct mutex role_lock;' not in source:
        source = source.replace('struct sc27xx_typec {', 'struct sc27xx_typec {\n\tstruct mutex role_lock;')
    if 'bool host_sink;' not in source:
        source = source.replace('struct mutex role_lock;', 'struct mutex role_lock;\n\tbool host_sink;')
    if 'mutex_init(&sc->role_lock);' not in source:
        anchor = '\tret = sysfs_create_groups(&sc->dev->kobj, sc27xx_typec_groups);'
        if source.count(anchor) != 1:
            raise ValueError('Type-C policy initialization anchor changed')
        source = source.replace(anchor, '\tmutex_init(&sc->role_lock);\n'+anchor)
    include = '#include "yh41s-usb-policy.h"'
    if include not in source:
        anchor = 'static struct attribute *sc27xx_typec_attrs[] = {'
        if source.count(anchor) != 1:
            raise ValueError('Type-C attributes anchor changed')
        source = source.replace(anchor, include+'\n\n'+anchor+
            '\n\t&dev_attr_usb_role_policy.attr,\n\t&dev_attr_usb_role.attr,')
    if '&dev_attr_usb_host_sink_supported.attr,' not in source:
        source = source.replace('&dev_attr_usb_role.attr,',
                                '&dev_attr_usb_role.attr,\n\t&dev_attr_usb_host_sink_supported.attr,')
    start = source.index('static irqreturn_t sc27xx_typec_interrupt(')
    end = source.index('\nstatic int sc27xx_vbus_ok_bypass', start)
    irq = source[start:end]
    if 'yh41s_policy_unlock:' not in irq:
        anchor = '\tsc->state = state & sc->var_data->state_mask;'
        if irq.count(anchor) != 1:
            raise ValueError('Unexpected Type-C IRQ implementation')
        irq = irq.replace(anchor, '\tmutex_lock(&sc->role_lock);\n'+anchor)
        # Returns before the lock (failed register reads) stay unchanged.
        before, locked = irq.split('\tmutex_lock(&sc->role_lock);', 1)
        locked = locked.replace('return IRQ_HANDLED;', 'goto yh41s_policy_unlock;')
        anchor = '\tattached = sc->state == SC27XX_ATTACHED_SNK ||'
        locked = locked.replace(anchor, '''\t/* A policy change can overtake an IRQ's captured hardware state. */
\tif ((sc->mode == TYPEC_PORT_UFP && sc->state == SC27XX_ATTACHED_SRC) ||
\t    (sc->mode == TYPEC_PORT_DFP && sc->state == SC27XX_ATTACHED_SNK))
\t\tgoto yh41s_policy_unlock;
'''+anchor)
        tail = locked.rfind('\tgoto yh41s_policy_unlock;')
        if tail < 0:
            raise ValueError('Type-C IRQ exit changed')
        locked = locked[:tail]+'''yh41s_policy_unlock:
\tmutex_unlock(&sc->role_lock);
\treturn IRQ_HANDLED;'''+locked[tail+len('\tgoto yh41s_policy_unlock;'):]
        irq = before+'\tmutex_lock(&sc->role_lock);'+locked
        source = source[:start]+irq+source[end:]
    path.write_text(source)
    (path.parent/'yh41s-usb-policy.h').write_bytes(Path(__file__).with_name('usb-policy.h').read_bytes())


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
