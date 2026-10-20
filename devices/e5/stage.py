#!/usr/bin/env python3
"""Install the shared dashboard/mobile features into an offline E5 root.

Keep E5 firmware, calibration, amplifier, backlight, credentials and boot
selection. Internal t551m-* service/RPC names remain stable compatibility APIs.
"""
import argparse
import json
import os
from pathlib import Path
import runpy

TOP = Path(__file__).resolve().parents[2]
SHARED = TOP / 'devices/t551m'
source_bytes = runpy.run_path(str(TOP / 'devices/staging.py'))['source_bytes']


def copy(root, source, target, mode=0o755, preserve=False):
    dest = root / target
    if preserve and dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source_bytes(source))
    dest.chmod(mode)


def link(root, name, service):
    path = root / 'etc/rc.d' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        if os.readlink(path) != '../init.d/' + service:
            raise ValueError('Unexpected service link: ' + str(path))
    elif path.exists():
        raise ValueError('Service link occupied: ' + str(path))
    else:
        path.symlink_to('../init.d/' + service)


def stage_nv(root):
    # Each unit creates isolated loop-backed copies from its OWN partitions
    # before either vendor writer starts. No donor NV data is staged here.
    for name in ('prepare', 'verify', 'stop'):
        copy(root, SHARED / ('nv-' + name + '.py'), 'opt/e5/t551m-nv-' + name)
    service = (TOP / 'openwrt/overlay/etc/init.d/e5-vendor').read_text()
    service = service.replace('START=20', 'START=20\nSTOP=10')
    service = service.replace('procd_set_param respawn 3600 5 0',
                              'procd_set_param respawn 3600 5 0\n\tprocd_set_param term_timeout 2')
    service = service.replace('start_service() {',
                              'start_service() {\n\trm -f /run/e5-vendor-prepared\n\t/opt/e5/t551m-nv-prepare || return 1')
    # Keep the deployed startup handshake responsive, with the same 12 s
    # bound. This wait ends at readiness; it is not a background poller.
    service = service.replace('instance cp_diskserver /opt/e5/android-run cp_diskserver',
        "instance cp_diskserver /bin/sh -c 'i=0; until [ -e /run/e5-vendor-prepared ]; do "
        "i=$((i + 1)); [ $i -lt 120 ] || exit 1; sleep 0.1; done; exec /opt/e5/android-run cp_diskserver'")
    service += '\nstop_service() {\n\t/opt/e5/t551m-nv-stop quiesce\n}\n\nservice_stopped() {\n\t/opt/e5/t551m-nv-stop detach\n}\n'
    (root / 'etc/init.d/e5-vendor').write_text(service)
    (root / 'etc/init.d/e5-vendor').chmod(0o755)
    vendor = root / 'opt/e5/vendor-start.sh'
    text = vendor.read_text()
    anchor = '  [ -n "$n" ] && ln -sfn "/dev/$d" "/dev/block/by-name/$n"'
    if text.count(anchor) != 1:
        raise ValueError('Unexpected vendor partition setup')
    text = text.replace(anchor,
        '  case "$n" in nr_fixnv*|nr_runtimenv*|nr_deltanv*|calinv) continue ;; esac\n' + anchor)
    text = text.replace('for p in nr_modem nr_phy nr_deltanv nr_fixnv1 nr_fixnv2;',
                        'for p in nr_modem nr_phy;')
    # E5's 64-bit loader parses bootconfig AFTER cmdline. Keep each isolated
    # NV alias bound to its own loop, and select the intended firmware/NV slot
    # consistently in both inputs instead of loading slot B's stale calibration.
    anchor = 'mountpoint -q "$A/proc/cmdline" || mount --bind /run/e5-cmdline.android "$A/proc/cmdline"'
    if text.count(anchor) != 1:
        raise ValueError('Unexpected vendor cmdline mount')
    text = text.replace(anchor, anchor + '''
if [ "$modem_slot" != "current" ] && [ -r /proc/bootconfig ]; then
  sed "s/\\(androidboot.slot_suffix *= *\\\"\\)_[ab]\\\"/\\1_$modem_slot\\\"/" /proc/bootconfig > /run/e5-bootconfig.android
  mountpoint -q "$A/proc/bootconfig" || mount --bind /run/e5-bootconfig.android "$A/proc/bootconfig"
fi
''')
    text = text.replace('echo "stage=exec"', 'touch /run/e5-vendor-prepared\necho "stage=exec"', 1)
    vendor.write_text(text)
    link(root, 'K10e5-vendor', 'e5-vendor')


def stage(root):
    root = Path(root).resolve()
    if root == Path('/') or os.name == 'nt':
        raise ValueError('Run in Linux/WSL against an offline root, never the running system')
    marker = root / 'etc/e5/device-profile'
    if marker.exists() and marker.read_text().strip() != 'e5':
        raise ValueError('Refusing to convert another board image into E5')
    if (root / 'etc/e5/t551m-persistent').exists():
        raise ValueError('T551M boot policy found in E5 root')
    # The shell, compositor, RPC registration and fonts belong to the base E5
    # screen package. A partial archive must not claim to be a complete image.
    for name in ('etc/init.d/e5-vendor', 'etc/init.d/e5-sipc-wwan',
                 'usr/libexec/e5-infoscreen/session',
                 'usr/share/rpcd/ucode/e5-infoscreen.uc'):
        if not (root / name).is_file():
            raise ValueError('Missing E5 base component: ' + name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('e5\n')
    runpy.run_path(str(TOP / 'devices/wifi-common.py'))['stage'](root)
    runpy.run_path(str(TOP / 'devices/modem-recovery.py'))['stage'](root)
    runpy.run_path(str(TOP / 'devices/battery-guard.py'))['stage'](root)
    for name in ('vendor-start.sh', 'android-import-perms.sh', 'android-run', 'e5-modem-coldboot', 'e5-at',
                 'e5-next-boot', 'e5-os', 'e5-sd-registry', 'e5-call-audio.py'):
        copy(root, TOP / 'rootfs/overlay/opt/e5' / name, 'opt/e5/' + name)
    for name in ('usr/libexec/e5-sms', 'usr/libexec/e5-sms-receive',
                 'usr/lib/e5/sms_pdu.py', 'usr/libexec/e5-container-setup',
                 'usr/libexec/e5-dockerd'):
        copy(root, TOP / 'openwrt/overlay' / name, name)
    copy(root, TOP / 'openwrt/overlay/etc/config/e5-audio',
         'etc/config/e5-audio', 0o644, preserve=True)
    runpy.run_path(str(SHARED / 'ui.py'))['stage'](root, profile='e5')
    stage_nv(root)
    for source, target in (
        ('charge-mode.py', 'usr/libexec/e5-charge-mode'),
        ('charge-screen.py', 'usr/libexec/e5-charge-screen'),
        ('charge-art.zip', 'usr/share/e5-charge/art.zip'),
        ('charge-mode-service', 'etc/init.d/e5-charge-mode'),
        ('usb-service', 'etc/init.d/t551m-usb'),
        ('usb-host-storage.modules', 'etc/modules.d/usb-host-storage'),
        ('usb-host-network.modules', 'etc/modules.d/usb-host-network'),
    ):
        copy(root, SHARED / source, target,
             0o644 if source.endswith(('.zip', '.modules')) else 0o755)
    link(root, 'S14e5-charge-mode', 'e5-charge-mode')
    link(root, 'S25t551m-usb', 't551m-usb')
    # Make an unadapted upstream UI update fail the existing compatibility gate.
    (root / 'usr/share/e5-infoscreen/ADAPTATION').write_text('e5-mobile-v1\n')
    manifest = {'profile': 'e5', 'schema': 1,
                'hardware': 'native E5 DT, charger, backlight, touchscreen and AW87xxx',
                'features': ['aurora', 'dashboard', 'dual-sim', 'traffic', 'sms',
                             'at-workers', 'pre-dial-replay', 'mobile-screen',
                             'phone-hfp', 'cpu-controls', 'usb-controls',
                             'charger-boot', 'isolated-nv'],
                'hardware_validation': 'required on target before deployment'}
    (root / 'etc/e5/feature-port.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    stage(parser.parse_args().root)
