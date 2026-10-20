#!/usr/bin/env python3
"""Generate the 41S media UCM path from the recipient's Android tables."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

TOP = Path(__file__).resolve().parents[2]


def controls(root, path):
    node = root.find(path)
    if node is None:
        raise ValueError('Missing factory audio route: ' + path)
    result = []
    for control in node.findall('ctl'):
        name, value = control.attrib['name'], control.attrib['val']
        # Same enum values, renamed in the current VBC driver's ABI.
        if name == 'VBC_IIS_MST_WIDTH_SET' and value in ('WD_16BIT', 'WD_20BIT', 'WD_24BIT', 'WD_32BIT'):
            value = 'MST_' + value
        if any(c in name + value for c in "'\"\\\n\r"):
            raise ValueError('Unexpected control syntax')
        value = ','.join([value] * int(control.get('count', '1')))
        result.append((name, value))
    return result


def csets(values):
    return ''.join(f'\t\tcset "name=\'{name}\' {value}"\n' for name, value in values)


def generate(route_path, pga_path, template=None):
    route = ET.parse(route_path).getroot()
    pga = ET.parse(pga_path).getroot()
    gain_node = pga.find('./Music/Handsfree/Playback/VBC_DAC0_DG/volume0')
    if gain_node is None:
        raise ValueError('Missing recipient media gain')
    gain = int(gain_node.attrib['val'], 0)
    if not 1 <= gain <= 127:
        raise ValueError('Invalid recipient media gain')
    backend_on = controls(route, 'be_switch/smart_spk_p/on')
    backend_off = controls(route, 'be_switch/smart_spk_p/off')
    # Only the media streams belong to this UCM device. Voice/VoIP routing
    # is owned by the call-audio service; do not turn on a speaker on calls.
    media = {'S_NORMAL_AP01_P_HIFI SWITCH', 'S_NORMAL_AP23_P_HIFI SWITCH',
             'S_FAST_P_HIFI SWITCH', 'S_OFFLOAD_HIFI SWITCH',
             'S_FAST_P_SMART_AMP SWITCH'}
    enabled = [(n, v) for n, v in backend_on if n in media]
    disabled = [(n, v) for n, v in backend_off if n in media]
    if {n for n, _ in enabled} != media or {n for n, _ in disabled} != media:
        raise ValueError('Factory media backends changed')
    mux = controls(route, 'vbc_iis_mux/only_smart_spk_p/on')
    if ('SYS_IIS3', 'vbc_iism0') not in mux:
        raise ValueError('Unrecognized digital amplifier pin routing')
    music = controls(route, 'usecase_device/default/on')
    if ('aw_dev_0_prof', 'Music') not in music:
        raise ValueError('Factory PA music profile missing')
    template = Path(template) if template is not None else TOP / 'rootfs/overlay/usr/share/alsa/ucm2/sprdphone-sc273/HiFi.conf'
    source = template.read_text(encoding='utf-8')
    # Retain the shared capture/receiver definitions. Replace the speaker
    # device, and remove the donor's analog playback path from the verb.
    for name in ('S_NORMAL_AP01_P_CODEC SWITCH', 'S_NORMAL_AP23_P_CODEC SWITCH',
                 'S_FAST_P_CODEC SWITCH', 'S_OFFLOAD_CODEC SWITCH'):
        source = source.replace(f"name='{name}' on", f"name='{name}' off")
    source = source.replace("name='VBC DAC0 DG Set' 39,39",
                            f"name='VBC DAC0 DG Set' {gain},{gain}")
    speaker = '''SectionDevice."Speaker" {
\tComment "Factory digital speaker"
\tConflictingDevice [ "Earpiece" ]
\tEnableSequence [
''' + csets(mux + music + enabled) + '''\t]
\tDisableSequence [
''' + csets(disabled + controls(route, 'vbc_iis_mux/only_smart_spk_p/off')) + '''\t]
\tValue {
\t\tPlaybackPriority 100
\t\tPlaybackPCM "hw:${CardId},3"
\t\tPlaybackChannels 2
\t\tPlaybackRate 48000
\t}
}

'''
    start = source.index('SectionDevice."Speaker" {')
    end = source.index('SectionDevice."Earpiece" {', start)
    source = source[:start] + speaker + source[end:]
    source = re.sub(r'\A.*?(?=SectionVerb)',
        '# Generated from this unit\'s Android audio_route.xml and audio_pga.xml.\n'
        '# AW883xx ACF protections and calibrated profile are retained.\n\n', source, flags=re.S)
    return source, gain


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--route', type=Path, required=True)
    parser.add_argument('--pga', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, required=True)
    args = parser.parse_args()
    text, gain = generate(args.route, args.pga)
    args.firmware.mkdir(parents=True, exist_ok=True)
    (args.firmware / 'yuanhang41s-HiFi.conf').write_text(text, encoding='utf-8', newline='\n')
    (args.firmware / 'yuanhang41s-media-max').write_text(str(gain) + '\n', newline='\n')
