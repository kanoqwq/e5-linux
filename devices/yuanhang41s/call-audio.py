#!/usr/bin/python3
"""41S digital PA wiring, using this unit's stock route XML.

The shared service still owns CP notifications, mute, calibrated voice volume,
PCM startup, route transactions and restoration. Only board wiring differs.
"""
from pathlib import Path
import xml.etree.ElementTree as ET


def hardware_class(base, route_path=Path('/lib/firmware/audio_route.xml')):
    data = route_path.read_bytes()
    if len(data) > 1024 * 1024 or b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise ValueError('Invalid recipient route XML')
    route = ET.fromstring(data)

    def controls(path):
        node = route.find(path)
        if node is None:
            raise ValueError('Missing recipient voice route: ' + path)
        result = {}
        for ctl in node.findall('ctl'):
            name, value = ctl.attrib['name'], ctl.attrib['val']
            if name == 'VBC_IIS_MST_WIDTH_SET':
                value = 'MST_' + value
            result[name] = ','.join([value] * int(ctl.get('count', '1')))
        return result

    digital = controls('vbc_iis_mux/only_smart_spk_p/on')
    receiver = controls('devices/handset/on')
    receiver_off = controls('devices/handset/off')
    pa_off = controls('vbc_iis_mux/only_smart_spk_p/off')
    codec = controls('vbc_iis_mux/only_codec_p/on')
    pa = controls('usecase_device/default/on')
    if digital.get('SYS_IIS3') != 'vbc_iism0' or pa.get('aw_dev_0_prof') != 'Music':
        raise ValueError('Unrecognized recipient voice PA routing')
    extra = (*digital, *receiver, *receiver_off, *pa_off, *codec, *pa,
             'S_VOICE_P_HIFI SWITCH')

    class BoardVoiceHardware(base):
        def controls(self):
            return tuple(dict.fromkeys((*super().controls(), *extra)))

        def board_output(self):
            if self.bluetooth:
                # The shared service has already made the BT backend before
                # disconnecting the previous one; never leave the PA attached.
                self.put('S_VOICE_P_HIFI SWITCH', 'off')
                return
            if self.speaker_path:
                for name, value in receiver_off.items():
                    self.put(name, value)
                for name, value in {**digital, **pa}.items():
                    self.put(name, value)
                # Both playback backends share the CP voice scene. Make the
                # new backend before breaking the old one on a live call.
                self.put('S_VOICE_P_HIFI SWITCH', 'on')
                self.put('S_VOICE_P_CODEC SWITCH', 'off')
            else:
                self.put('S_VOICE_P_CODEC SWITCH', 'on')
                self.put('S_VOICE_P_HIFI SWITCH', 'off')
                for name, value in {**pa_off, **codec, **receiver}.items():
                    self.put(name, value)

        def output(self):
            if self.bluetooth:
                super().output()
            else:
                # The board has no analog loudspeaker; the earpiece is real
                # and retains the recipient handset calibration.
                self.put('Speaker Function', 'off')
                self.put('Earpiece Function', 'off')
            self.board_output()

        def profiles(self):
            super().profiles()
            # Shared profile setup restores codec muxes. Reapply the selected
            # digital wiring after it, including on a bandwidth notification.
            self.board_output()

    return BoardVoiceHardware
