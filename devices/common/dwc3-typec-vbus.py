"""Manual VBUS-only sink attachment on the existing legacy DWC3 CC path.

The controller, PHY, boost interlocks and charger policy remain model-owned.
Reuse the tested MUSB attachment worker with this CC driver's lock/flag names.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent


def header(*, lock, policy):
    if (lock, policy) not in (('policy_lock', 'external_host_policy'), ('role_lock', 'host_sink')):
        raise ValueError('Unknown legacy Type-C policy layout')
    text = (HERE/'host-sink-typec.h').read_text()
    text = text.replace('sc->role_lock', 'sc->'+lock).replace('sc->external_host_policy', 'sc->'+policy)
    # devm callbacks have a void* argument. The manual worker stays identical.
    return text.replace('static void sc27xx_external_vbus_cleanup(struct sc27xx_typec *sc)\n{',
                        'static void sc27xx_external_vbus_cleanup(void *data)\n{\n'
                        '\tstruct sc27xx_typec *sc = data;')


def apply(kernel, *, lock, policy, policy_header, irq_label):
    kernel = Path(kernel)
    path = kernel/'drivers/usb/typec/sc27xx_typec.c'
    source = path.read_text()

    def edit(old, new):
        nonlocal source
        if new in source:
            return
        if source.count(old) != 1:
            raise ValueError('DWC3 VBUS-only anchor: '+old[:90])
        source = source.replace(old, new, 1)

    edit('bool '+policy+';', 'bool '+policy+';\n\tbool external_vbus_stopped;\n'
         '\tspinlock_t external_vbus_lock;\n\tstruct delayed_work external_vbus_work;')
    include_anchor = ('static irqreturn_t sc27xx_typec_interrupt(' if policy == 'host_sink' else
                      '#include "'+policy_header+'"')
    edit(include_anchor, '#include "sprd-host-sink-typec.h"\n\n'+include_anchor)
    # Keep M8's existing "mutex_init; extcon allocation" anchor contiguous so
    # its base preparer cannot inject a second mutex_init on the next build.
    init_anchor = ('\tmutex_init(&sc->policy_lock);' if lock == 'policy_lock' else
                   '\tsc->edev = devm_extcon_dev_allocate(')
    edit(init_anchor, '\tspin_lock_init(&sc->external_vbus_lock);\n'
         '\tINIT_DELAYED_WORK(&sc->external_vbus_work, sc27xx_external_vbus_work);\n'+init_anchor)
    # Register cleanup after the devm extcon, before sysfs/IRQ/notifier exposure.
    edit('\tsc->dev = &pdev->dev;', '\tsc->dev = &pdev->dev;\n'
         '\tret = devm_add_action_or_reset(&pdev->dev, sc27xx_external_vbus_cleanup, sc);\n'
         '\tif (ret)\n\t\treturn ret;')
    edit('\tdev_info(sc->dev, "vbus_connect_value:%d\\n", sc->vbus_connect_value);',
         '\tsc27xx_external_vbus_queue(sc);\n'
         '\tdev_info(sc->dev, "vbus_connect_value:%d\\n", sc->vbus_connect_value);')
    edit(irq_label+':\n\tmutex_unlock(&sc->'+lock+');',
         irq_label+':\n\tsc27xx_external_vbus_queue(sc);\n\tmutex_unlock(&sc->'+lock+');')
    start = source.index('static void sc27xx_typec_remove(')
    tail = source[start:]
    old = '\tstruct sc27xx_typec *sc = platform_get_drvdata(pdev);'
    if 'sc27xx_external_vbus_cleanup(sc);' not in tail:
        if tail.count(old) != 1:
            raise ValueError('DWC3 Type-C remove anchor')
        source = source[:start]+tail.replace(old, old+'\n\n\tsc27xx_external_vbus_cleanup(sc);', 1)
    policy_path = path.parent/policy_header
    body = policy_path.read_text()
    old = 'out:\n\tmutex_unlock(&sc->'+lock+');'
    # The legacy common setter publishes its successful flag at out; 41S does
    # so in its same-mode/success branches. In both cases retain failed choices.
    if policy == 'external_host_policy':
        old = 'out:\n\tif (!ret)\n\t\tWRITE_ONCE(sc->external_host_policy, external);\n\tmutex_unlock(&sc->'+lock+');'
        begin = 'out:\n\tif (!ret)\n\t\tWRITE_ONCE(sc->external_host_policy, external);\n'
    else:
        begin = 'out:\n'
    new = begin+'\tif (!ret && !READ_ONCE(sc->'+policy+'))\n'
    new += '\t\tsc27xx_external_vbus_clear(sc);\n\tmutex_unlock(&sc->'+lock+');\n'
    new += '\tif (!ret)\n\t\tsc27xx_external_vbus_queue(sc);'
    if new not in body:
        if body.count(old) != 1:
            raise ValueError('DWC3 external policy completion anchor')
        body = body.replace(old, new, 1)
    path.write_text(source)
    policy_path.write_text(body)
    (path.parent/'sprd-host-sink-typec.h').write_text(header(lock=lock, policy=policy))
