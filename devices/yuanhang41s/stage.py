#!/usr/bin/env python3
"""Stage shared handheld features with the 41S identity; no donor calibration."""
import json
import os
from pathlib import Path
import runpy

TOP = Path(__file__).resolve().parents[2]


def stage_screen(root):
    root = Path(root)
    if (root / 'etc/e5/device-profile').read_text().strip() != 'yuanhang41s':
        raise ValueError('Screen profile belongs to another board')
    session = root / 'usr/libexec/e5-infoscreen/session'
    text = session.read_text()
    command = '/usr/bin/cog -P wl --bg-color=black'
    scaled = command + ' --scale=2.0'
    if scaled in text:
        return
    if text.count(command) != 1:
        raise ValueError('Unexpected screen browser command')
    # 720x1600 native pixels -> 360x800 logical layout. Browser page zoom
    # scales the shell, embedded apps and touch coordinates together. The
    # separate saved ui_size preference continues to control content size.
    session.write_text(text.replace(command, scaled))


def stage_bluetooth(root):
    root = Path(root)
    if (root / 'etc/e5/device-profile').read_text().strip() != 'yuanhang41s':
        raise ValueError('Bluetooth profile belongs to another board')
    modules = root / 'etc/modules.d/41-yuanhang41s'
    text = modules.read_text()
    if 'sprdbt_tty\n' not in text.splitlines(keepends=True):
        modules.write_text(text.rstrip() + '\nsprdbt_tty\n')
    config = root / 'etc/bluetooth/main.conf'
    if config.is_file():
        config.write_text(config.read_text().replace('Name = E5\n', 'Name = Yuanhang41S\n'))


def stage_vibrator(root):
    root = Path(root)
    if (root / 'etc/e5/device-profile').read_text().strip() != 'yuanhang41s':
        raise ValueError('Vibrator profile belongs to another board')
    modules = root / 'etc/modules.d/41-yuanhang41s'
    text = modules.read_text()
    if 'sc27xx-vibra' not in text.splitlines():
        modules.write_text(text.rstrip() + '\nsc27xx-vibra\n')


def stage_audio(root):
    root = Path(root)
    if (root / 'etc/e5/device-profile').read_text().strip() != 'yuanhang41s':
        raise ValueError('Audio profile belongs to another board')
    copy = runpy.run_path(str(TOP / 'devices/e5/stage.py'))['copy']
    for name in ('e5-audio-dsp', 'e5-call-audio.py'):
        copy(root, TOP / 'rootfs/overlay/opt/e5' / name, 'opt/e5/' + name)
    copy(root, TOP / 'openwrt/overlay/usr/libexec/e5-volume', 'usr/libexec/e5-volume')
    audio = root / 'opt/e5/e5-audio-dsp'
    text = audio.read_text().replace(' snd-soc-sprd-pa-aw87xxx', ' snd-soc-aw883xx')
    text = text.replace('amixer -q -c 0 cset name=SYS_IIS0 vbc_iis3 || return 1',
                        'amixer -q -c 0 cset name=SYS_IIS3 vbc_iism0 || return 1')
    start = text.index('bind_card() {')
    end = text.index('\n}\n', start) + 3
    text = text[:start] + '''bind_card() {
    bind_pd_consumers || :
    for attempt in $(seq 1 20); do
        grep -q sprdphone /proc/asound/cards 2>/dev/null && return 0
        sleep 1
    done
    echo "Audio card unavailable; skipping legacy unbind" >&2
    return 1
}
''' + text[end:]
    audio.write_text(text)
    # Keep all CP ownership/volume/state handling in the shared service. This
    # recipient needs a digital speaker backend and a separate analog earpiece.
    copy(root, TOP / 'devices/yuanhang41s/call-audio.py', 'opt/e5/yuanhang41s_call_audio.py')
    voice = root / 'opt/e5/e5-call-audio.py'
    text = voice.read_text()
    anchor = 'def main():'
    if text.count(anchor) != 1:
        raise ValueError('Unexpected shared voice service entry point')
    voice.write_text(text.replace(anchor,
        'sys.path.insert(0, str(Path(__file__).parent))\n'
        'from yuanhang41s_call_audio import hardware_class\n'
        'VoiceHardware = hardware_class(VoiceHardware)\n\n\n' + anchor))
    ucm = root / 'usr/share/alsa/ucm2/sprdphone-sc273/HiFi.conf'
    ucm.unlink(missing_ok=True)
    ucm.symlink_to('/lib/firmware/yuanhang41s-HiFi.conf')
    volume = root / 'usr/libexec/e5-volume'
    text = volume.read_text()
    text = text.replace('MAX=15\n', '''MAX=15
HW_GAIN=$(cat /lib/firmware/yuanhang41s-media-max) || exit 1
case "$HW_GAIN" in ''|*[!0-9]*) exit 1 ;; esac
[ "$HW_GAIN" -ge 1 ] && [ "$HW_GAIN" -le 127 ] || exit 1
''', 1)
    text = text.replace('gain() { [ "$1" -le 0 ] && echo 0 || echo $((39 - (MAX - $1) * 2)); }',
        '''gain() {
    [ "$1" -gt 0 ] || { echo 0; return; }
    local g=$((HW_GAIN - (MAX - $1) * 2))
    [ "$g" -ge 1 ] || g=1
    echo "$g"
}''')
    text = text.replace('&& g=39', '&& g=$HW_GAIN')
    volume.write_text(text)


def stage(root):
    root = Path(root).resolve()
    if os.name == 'nt' or root == Path('/'):
        raise ValueError('Use Linux/WSL with an offline 41S root')
    marker = root / 'etc/e5/device-profile'
    if marker.exists() and marker.read_text().strip() != 'yuanhang41s':
        raise ValueError('Refusing to convert another device profile')
    for name in ('etc/init.d/e5-vendor', 'etc/init.d/e5-sipc-wwan',
                 'usr/libexec/e5-infoscreen/session', 'usr/share/rpcd/ucode/e5-infoscreen.uc'):
        if not (root / name).is_file():
            raise ValueError('Missing shared base component: ' + name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('yuanhang41s\n')
    for name, value in (('device-model', '中兴远航41S'), ('device-hostname', 'Yuanhang-41S')):
        (marker.parent / name).write_text(value + '\n')
    shared = runpy.run_path(str(TOP / 'devices/e5/stage.py'))
    copy, link = shared['copy'], shared['link']
    for path in (TOP / 'rootfs/overlay/opt/e5').iterdir():
        if path.is_file():
            copy(root, path, 'opt/e5/' + path.name)
    # These loader nodes are system:system 0660 on this phone's Android;
    # its imported vendor ueventd.rc covers stty/spipe but omits these nodes.
    permissions = root / 'opt/e5/node-perms.sh'
    text = permissions.read_text()
    text = text.replace('\nexit 0\n', '''
# P720S11 stock loader permissions (captured 2026-10-20).
for node in /dev/modem /dev/chsys /dev/cp_dump; do
    [ ! -c "$node" ] || { chown 1000:1000 "$node"; chmod 0660 "$node"; }
done
exit 0
''')
    permissions.write_text(text)
    # Runtime DT providers precede consumers even when they have no imported
    # symbols. The boot image also uses this order; this covers root autoload.
    modules = root / 'etc/modules.d/41-yuanhang41s'
    modules.parent.mkdir(parents=True, exist_ok=True)
    modules.write_text('sprd-sipc-virt-bus\nsprd_power_manager\nunisoc-mailbox\n'
        'sipc-core\nspipe\nsbuf_bridge\nsblock_bridge\nsprd_modem_loader\n'
        'sprd_cp_dump\nsprd_time_sync\nsprd_time_sync_cp\npanfrost\n'
        'sipa-sys\nsipa-core\nsipa-dele\nsipa_eth\nsipa_usb\n'
        'wcn_bsp\nsprd_wlan_combo\n')
    stage_bluetooth(root)
    stage_vibrator(root)
    for path in ('devices/wifi-common.py', 'devices/modem-recovery.py', 'devices/battery-guard.py'):
        runpy.run_path(str(TOP / path))['stage'](root)
    for name in ('etc/init.d/e5-board-identity', 'etc/uci-defaults/97-e5-hostname'):
        copy(root, TOP / 'openwrt/overlay' / name, name)
    link(root, 'S11e5-board-identity', 'e5-board-identity')
    runpy.run_path(str(TOP / 'devices/t551m/ui.py'))['stage'](root, profile='yuanhang41s')
    stage_screen(root)
    stage_audio(root)
    shared['stage_nv'](root)
    # Firmware slot is supplied by this unit's collector, not inferred from
    # the OpenWrt slot. A root without it must not start another slot's CP.
    service = root / 'etc/init.d/e5-vendor'
    text = service.read_text()
    text = text.replace('start_service() {', '''start_service() {
    if [ ! -s /etc/e5/modem-slot ]; then
        local slot=$(cat /lib/firmware/android-firmware-slot 2>/dev/null)
        case "$slot" in a|b) ;; *) logger -t e5-vendor 'Missing collected Android firmware slot'; return 1 ;; esac
        printf '%s\\n' "$slot" > /etc/e5/modem-slot || return 1
    fi''', 1)
    service.write_text(text)
    for source, target in (
        ('charge-mode.py', 'usr/libexec/e5-charge-mode'),
        ('charge-screen.py', 'usr/libexec/e5-charge-screen'),
        ('charge-art.zip', 'usr/share/e5-charge/art.zip'),
        ('charge-mode-service', 'etc/init.d/e5-charge-mode'),
        ('usb-service', 'etc/init.d/t551m-usb'),
        ('usb-host-storage.modules', 'etc/modules.d/usb-host-storage'),
        ('usb-host-network.modules', 'etc/modules.d/usb-host-network'),
    ):
        copy(root, TOP / 'devices/t551m' / source, target,
             0o644 if source.endswith(('.zip', '.modules')) else 0o755)
    link(root, 'S14e5-charge-mode', 'e5-charge-mode')
    link(root, 'S25t551m-usb', 't551m-usb')
    (root / 'usr/share/e5-infoscreen/ADAPTATION').write_text('yuanhang41s-bringup-v1\n')
    (marker.parent / 'feature-port.json').write_text(json.dumps({
        'profile': 'yuanhang41s', 'schema': 1, 'status': 'bringup',
        'hardware_validation': 'Permanent 100 GiB root: display/touch, SIM2 data, hotspot, media, CPU/thermal/voltage, USB policy, HOST/SINK control and charge control checked; normal OTG user-confirmed; calls, BT pairing, powered-dock data, physical charger and full battery cycle pending',
        'vendor_abi': 'arm64', 'display': [720, 1600],
        'audio_amplifier': 'AW883xx factory ACF and own IIS3 route; media sound confirmed',
        'staged_features': ['aurora', 'dashboard', 'dual-sim', 'traffic', 'sms',
                            'at-workers', 'pre-dial-replay', 'mobile-screen',
                            'phone-hfp', 'cpu-controls', 'usb-controls',
                            'charger-boot', 'isolated-nv', 'notification-sounds',
                            'bluetooth', 'cpu-cores', 'cpu-boot-voltage'],
    }, indent=2) + '\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    stage(parser.parse_args().root)
