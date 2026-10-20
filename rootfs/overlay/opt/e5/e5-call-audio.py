#!/usr/bin/python3
"""E5 hostless CP voice audio, implementing libcallaudio's session-bus API.

The card has no ACP ports (probing every vendor FE is not safe/useful). Voice
uses the DSP's FE_ST_VOICE, not a PipeWire client stream. Keep playback/capture
prepared and started for the call, and restore the exact mixer state afterwards.
This service never controls the modem, creates calls, answers or hangs up.
"""
import concurrent.futures
import ctypes as C
import fcntl
import logging
import math
import os
from pathlib import Path
import re
import runpy
import signal
import struct
import sys
import threading

NAME = 'org.mobian_project.CallAudio'
OBJECT = '/org/mobian_project/CallAudio'
XML = '''<node><interface name="org.mobian_project.CallAudio">
<method name="SelectMode"><arg type="u" direction="in"/><arg type="b" direction="out"/></method>
<method name="EnableSpeaker"><arg type="b" direction="in"/><arg type="b" direction="out"/></method>
<method name="MuteMic"><arg type="b" direction="in"/><arg type="b" direction="out"/></method>
<property name="AudioMode" type="u" access="read"/>
<property name="SpeakerState" type="u" access="read"/>
<property name="MicState" type="u" access="read"/>
</interface></node>'''


class AlsaControl:
    """Persistent ALSA control handle; cache metadata, never mixer values.

    Direct elem_write preserves the vendor's raw profile/volume indices,
    including controls whose advertised signed maximum is incorrect.
    """
    def __init__(self, card):
        self.lib = C.CDLL('libasound.so.2')
        self.handle, self.elements = C.c_void_p(), {}
        self.lock = threading.RLock()
        P, U, I = C.c_void_p, C.c_uint, C.c_int
        def bind(name, result, *args):
            fn = getattr(self.lib, 'snd_' + name)
            fn.restype, fn.argtypes = result, list(args)
            setattr(self, name, fn)
        bind('strerror', C.c_char_p, I)
        bind('ctl_open', I, C.POINTER(P), C.c_char_p, I)
        bind('ctl_close', I, P)
        for obj in ('info', 'value'):
            bind('ctl_elem_' + obj + '_malloc', I, C.POINTER(P))
            bind('ctl_elem_' + obj + '_free', None, P)
        bind('ctl_elem_info_set_interface', None, P, I)
        bind('ctl_elem_info_set_name', None, P, C.c_char_p)
        bind('ctl_elem_info_set_item', None, P, U)
        bind('ctl_elem_info_get_item_name', C.c_char_p, P)
        for key in ('type', 'count', 'items', 'numid'):
            bind('ctl_elem_info_get_' + key, U, P)
        bind('ctl_elem_value_set_numid', None, P, U)
        for key, result, arg in [('boolean', I, C.c_long), ('integer', C.c_long, C.c_long),
                                 ('enumerated', U, U)]:
            bind('ctl_elem_value_get_' + key, result, P, U)
            bind('ctl_elem_value_set_' + key, None, P, U, arg)
        for key in ('info', 'read', 'write'):
            bind('ctl_elem_' + key, I, P, P)
        self.check(self.ctl_open(C.byref(self.handle), f'hw:{card}'.encode(), 0), 'open mixer')

    def check(self, result, operation):
        if result < 0:
            raise RuntimeError(operation + ': ' + self.strerror(result).decode())

    def element(self, name):
        if name in self.elements: return self.elements[name]
        info, value = C.c_void_p(), C.c_void_p()
        try:
            self.check(self.ctl_elem_info_malloc(C.byref(info)), 'allocate mixer info')
            self.ctl_elem_info_set_interface(info, 2)  # MIXER
            self.ctl_elem_info_set_name(info, name.encode())
            self.check(self.ctl_elem_info(self.handle, info), 'mixer info ' + name)
            kind, count = self.ctl_elem_info_get_type(info), self.ctl_elem_info_get_count(info)
            if kind not in (1, 2, 3) or not 0 < count <= 128:
                raise RuntimeError('unsupported mixer type/count: ' + name)
            choices = []
            if kind == 3:
                for index in range(self.ctl_elem_info_get_items(info)):
                    self.ctl_elem_info_set_item(info, index)
                    self.check(self.ctl_elem_info(self.handle, info), 'mixer enum ' + name)
                    choices.append(self.ctl_elem_info_get_item_name(info).decode())
            self.check(self.ctl_elem_value_malloc(C.byref(value)), 'allocate mixer value')
            self.ctl_elem_value_set_numid(value, self.ctl_elem_info_get_numid(info))
            self.elements[name] = kind, count, choices, value
            return self.elements[name]
        except Exception:
            if value: self.ctl_elem_value_free(value)
            raise
        finally:
            if info: self.ctl_elem_info_free(info)

    def get(self, name):
        with self.lock:
            kind, count, choices, value = self.element(name)
            self.check(self.ctl_elem_read(self.handle, value), 'read ' + name)
            getter = getattr(self, 'ctl_elem_value_get_' + {1:'boolean', 2:'integer', 3:'enumerated'}[kind])
            values = [getter(value, i) for i in range(count)]
            return ','.join(('on' if v else 'off') if kind == 1 else str(v) for v in values)

    def put(self, name, setting):
        with self.lock:
            kind, count, choices, value = self.element(name)
            fields = str(setting).split(',')
            if len(fields) == 1: fields *= count
            if len(fields) != count: raise ValueError('wrong mixer value count: ' + name)
            values = []
            for field in fields:
                if kind == 3 and field in choices: number = choices.index(field)
                elif kind == 1 and field in ('on', 'off'): number = int(field == 'on')
                else: number = int(field, 0)
                if kind == 3 and not 0 <= number < len(choices) or kind == 1 and number not in (0, 1):
                    raise ValueError('invalid mixer value: ' + name)
                values.append(number)
            setter = getattr(self, 'ctl_elem_value_set_' + {1:'boolean', 2:'integer', 3:'enumerated'}[kind])
            for i, number in enumerate(values): setter(value, i, number)
            self.check(self.ctl_elem_write(self.handle, value), 'write ' + name)

    def close(self):
        for _, _, _, value in self.elements.values(): self.ctl_elem_value_free(value)
        self.elements.clear()
        if self.handle:
            self.ctl_close(self.handle)
            self.handle = C.c_void_p()

    def __del__(self):
        if hasattr(self, 'elements'): self.close()


class VoicePcm:
    """A hostless PCM: no AP audio is copied into the CP voice stream."""
    def __init__(self, card, device, direction):
        self.lib = C.CDLL('libasound.so.2')
        self.handle = C.c_void_p()
        pointer = C.c_void_p
        specs = {
            'open': [C.POINTER(pointer), C.c_char_p, C.c_int, C.c_int],
            'close': [pointer], 'prepare': [pointer], 'start': [pointer],
            'hw_params_malloc': [C.POINTER(pointer)],
            'hw_params_any': [pointer, pointer],
            'hw_params_set_access': [pointer, pointer, C.c_int],
            'hw_params_set_format': [pointer, pointer, C.c_int],
            'hw_params_set_channels': [pointer, pointer, C.c_uint],
            'hw_params_set_rate': [pointer, pointer, C.c_uint, C.c_int],
            'hw_params_set_period_size_near': [pointer, pointer, C.POINTER(C.c_ulong), C.POINTER(C.c_int)],
            'hw_params_set_buffer_size_near': [pointer, pointer, C.POINTER(C.c_ulong)],
            'hw_params': [pointer, pointer],
        }
        for name, args in specs.items():
            func = getattr(self.lib, 'snd_pcm_' + name)
            func.argtypes, func.restype = args, C.c_int
        self.lib.snd_pcm_forward.argtypes = [pointer, C.c_ulong]
        self.lib.snd_pcm_forward.restype = C.c_long
        self.lib.snd_pcm_hw_params_free.argtypes = [pointer]
        self.lib.snd_pcm_hw_params_free.restype = None
        self.lib.snd_strerror.argtypes = [C.c_int]
        self.lib.snd_strerror.restype = C.c_char_p
        params = pointer()
        try:
            self.check(self.lib.snd_pcm_open(C.byref(self.handle),
                       f'hw:{card},{device}'.encode(), direction, 1), 'open')
            self.check(self.lib.snd_pcm_hw_params_malloc(C.byref(params)), 'allocate parameters')
            for name, args in [('any', ()), ('set_access', (3,)), ('set_format', (2,)),
                               ('set_channels', (1,)), ('set_rate', (8000, 0))]:
                self.check(getattr(self.lib, 'snd_pcm_hw_params_' + name)(self.handle, params, *args), name)
            period, buffer, rounding = C.c_ulong(640), C.c_ulong(1280), C.c_int(0)
            self.check(self.lib.snd_pcm_hw_params_set_period_size_near(self.handle, params, C.byref(period), C.byref(rounding)), 'period')
            self.check(self.lib.snd_pcm_hw_params_set_buffer_size_near(self.handle, params, C.byref(buffer)), 'buffer')
            self.check(self.lib.snd_pcm_hw_params(self.handle, params), 'hw_params')
            self.check(self.lib.snd_pcm_prepare(self.handle), 'prepare')
            # ALSA refuses an empty playback start. Advance its application
            # pointer without writing samples: FE_ST_VOICE has no AP DMA.
            if direction == 0:
                moved = self.lib.snd_pcm_forward(self.handle, buffer.value)
                self.check(moved, 'hostless forward')
                if not moved:
                    raise RuntimeError('hostless playback pointer did not advance')
        except Exception:
            self.close()
            raise
        finally:
            if params:
                self.lib.snd_pcm_hw_params_free(params)

    def check(self, result, operation):
        if result < 0:
            raise RuntimeError(f'{operation}: {self.lib.snd_strerror(result).decode()}')

    def start(self):
        self.check(self.lib.snd_pcm_start(self.handle), 'start')

    def close(self):
        if self.handle:
            self.lib.snd_pcm_close(self.handle)
            self.handle = C.c_void_p()


class VoiceHardware:
    # Logical "phone" selection may share the speaker on single-transducer
    # hardware. Keep UI intent separate from codec wiring and calibration.
    receiver_output = 'receiver'
    receiver_wiring = 'hpl'
    receiver_volume_ceiling = 15
    # Shared voice routes from this device's Android parameters.
    ROUTES = {
        'S_VOICE_P_CODEC SWITCH': 'on', 'S_VOICE_C_CODEC SWITCH': 'on',
        'VBC_MUX_DAC1_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS0',
        # Android's only_codec_p route uses DAC0 here, including voice.
        'VBC_MUX_IIS0_PORT_DO_SEL': 'IIS_DO_VAL_DAC0',
        'VBC_MUX_ADC2': 'ADC_IN_IIS1_ADC',
        'VBC_MUX_ADC2_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS1',
        'VBC DAC1 DG Set': '24,24', 'VBC DAC1 DSP MDG Set': '1,1024',
        # Android forces DL mute during route changes and explicitly releases
        # it after startup. A cached "disable" does not prove DSP is unmuted.
        'VBC_DL_MUTE': 'enable',
        'VBC ADC2 DG Set': '24,24',
        # The HAL sets this to voice_volume + 1, after applying AS/CVS.
        'VBC_VOLUME': '7',
        'Speaker Mute': 'off', 'Earpiece Mute': 'off', 'Mic Function': 'on',
    }
    # Stock handset uses EAR_HPL, not the commented-out EAR_AOL alternative.
    RECEIVER = {
        'Speaker Function': 'off',
        'AO Mixer AOL Switch': 'off', 'AO Mixer AOR Switch': 'off',
        'DA AOR Switch': 'off', 'AOL EAR Sel': 'EAR',
        'EAR_AOL Mixer DACAOL Switch': 'off',
        'DAHP OS D': '5', 'EAR_HPL Mixer DACHPL Switch': 'on',
        'HPL EAR Sel': 'EAR', 'VBC_MIXER1_DAC0': 'HALF_ADD',
        'Earpiece Function': 'on',
    }
    # Some stock boards connect their receiver to AO, not EAR_HPL. Keep the
    # endpoint (Earpiece Function) distinct from a shared loudspeaker board.
    RECEIVER_AOL = {
        'Speaker Function': 'off',
        'EAR_HPL Mixer DACHPL Switch': 'off', 'HPL EAR Sel': 'HPL',
        'EAR_AOL Mixer DACAOL Switch': 'off', 'VBC_MIXER1_DAC0': 'NOT_MIX',
        'AO Mixer AOL Switch': 'on', 'AO Mixer AOR Switch': 'on',
        'DA AOR Switch': 'on', 'AOL EAR Sel': 'AOL',
        'Earpiece Function': 'on',
    }
    SPEAKER = {
        'Earpiece Function': 'off',
        'EAR_HPL Mixer DACHPL Switch': 'off', 'HPL EAR Sel': 'HPL',
        'EAR_AOL Mixer DACAOL Switch': 'off', 'VBC_MIXER1_DAC0': 'NOT_MIX',
        'AOL EAR Sel': 'AOL', 'AO Mixer AOL Switch': 'on',
        'AO Mixer AOR Switch': 'on', 'DA AOR Switch': 'on',
        'Speaker Function': 'on',
    }
    GAINS = ('DAC Gain DAC Playback Volume', 'AO Gain AO Playback Volume',
             'EAR Gain EAR Playback Volume')
    # The E5 driver's CVS selector retains the old NXP label (same profile 2).
    PROFILES = ('DSP VBC Profile Select', 'Audio Structure Profile Select', 'NXP Profile Select')
    # CP voice goes directly between the modem DSP and controller PCM/IIS3.
    # Sending SCO samples through an AP sound-server sink is not this route.
    BLUETOOTH = {
        'SYS_IIS0': 'vbc_iis3',
        'VBC_MUX_DAC0_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS0',
        'VBC_MUX_DAC1_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS3',
        'VBC_MUX_IIS3_PORT_DO_SEL': 'IIS_DO_VAL_DAC1',
        'VBC_MUX_MST_IIS0_PORT_DO_SEL': 'IIS_DO_VAL_DAC0',
        # Park the physical codec/USB outputs while the internal master is
        # supplying DAC0. These are part of stock only_btsco_p, not A2DP.
        'VBC_MUX_IIS0_PORT_DO_SEL': 'IIS_DO_VAL_DAC2',
        'VBC_MUX_IIS2_PORT_DO_SEL': 'IIS_DO_VAL_DAC2',
        'VBC_MUX_ADC2': 'ADC_IN_IIS0_ADC',
        'VBC_MUX_ADC0_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS3',
        'VBC_MUX_ADC1_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS3',
        'VBC_MUX_ADC2_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS3',
        'VBC_IIS_TX0_WD_SEL': 'WD_16BIT', 'VBC_IIS_TX1_WD_SEL': 'WD_16BIT',
        'VBC_IIS_RX0_WD_SEL': 'WD_16BIT', 'VBC_IIS_RX1_WD_SEL': 'WD_16BIT',
        'VBC_IIS_RX2_WD_SEL': 'WD_16BIT',
        'VBC_IIS_MASTER_ENALBE': 'enable',
        'VBC_IIS_MST_SEL_0_TYPE': 'VBC_MASTER_INTERNAL',
        'Speaker Function': 'off', 'Earpiece Function': 'off',
        # Make before break: both BEs share the same DSP voice scene/refcount.
        # Closing the last old BE first shuts down CP voice in mid-call.
        'S_VOICE_P_BT SWITCH': 'on', 'S_VOICE_C_BT SWITCH': 'on',
        'S_VOICE_P_CODEC SWITCH': 'off', 'S_VOICE_C_CODEC SWITCH': 'off',
    }
    BT_SRC = ('VBC_SRC_BT_DAC', 'VBC_SRC_BT_ADC')
    CODEC_IIS = {
        # only_codec_p/c in the stock route. FE_ST_VOICE is S16, but the
        # physical codec link uses 24-bit slots and right-high DAC0 framing.
        'VBC_IIS_TX0_WD_SEL': 'WD_24BIT', 'VBC_IIS_TX1_WD_SEL': 'WD_24BIT',
        'VBC_IIS_RX0_WD_SEL': 'WD_24BIT', 'VBC_IIS_RX1_WD_SEL': 'WD_24BIT',
        'VBC_IIS_RX2_WD_SEL': 'WD_24BIT',
        'DSP_DAC0_LR_EXCHANGE': 'enable', 'VBC_IIS_TX0_LRMOD_SEL': 'RIGHT_HIGH',
        'VBC_MUX_DAC0_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS0',
        'VBC_MUX_DAC1_IIS_PORT_SEL': 'VBC_IIS_PORT_IIS0',
        'VBC_MUX_IIS0_PORT_DO_SEL': 'IIS_DO_VAL_DAC0',
        'VBC_MUX_IIS2_PORT_DO_SEL': 'IIS_DO_VAL_DAC2',
        'VBC_MUX_IIS3_PORT_DO_SEL': 'IIS_DO_VAL_DAC2',
    }

    def __init__(self):
        pcm = Path('/proc/asound/pcm').read_text()
        match = re.search(r'^(\d+)-(\d+): FE_ST_VOICE ', pcm, re.M)
        if not match:
            raise RuntimeError('no FE_ST_VOICE PCM')
        self.card, self.device = map(int, match.groups())
        if 'sprdphone-sc2730' not in Path('/proc/asound/cards').read_text():
            raise RuntimeError('not an E5 sound card')
        self.saved, self.handles = {}, []
        self.band = 0
        self.speaker = False
        self.bluetooth = False
        self.bt_rate = 8000
        self.volume = 15
        self.remote_volume = False
        self.volume_curves = self.load_volume_curves()
        profile = runpy.run_path(str(Path(__file__).with_name('e5_call_profiles.py')))['prepare']()
        if profile is not None:
            self.volume_curves = self.load_volume_curves(data=profile)
        self.codec_gains = self.load_codec_gains()
        self.receiver_output = self.load_receiver_output()
        self.receiver_wiring = self.load_receiver_wiring()
        self.receiver_volume_ceiling = self.load_receiver_volume_ceiling()
        self.applied_volume = None
        self.mixer = AlsaControl(self.card)

    @staticmethod
    def load_receiver_output(path=Path('/etc/e5/receiver-output')):
        try:
            value = path.read_text().strip()
        except FileNotFoundError:
            return 'receiver'
        except OSError as error:
            logging.warning('cannot read receiver wiring: %s', error)
            return 'receiver'
        if value not in ('receiver', 'speaker'):
            logging.warning('invalid receiver wiring; using separate receiver')
            return 'receiver'
        return value

    @property
    def speaker_path(self):
        return not self.bluetooth and (self.speaker or self.receiver_output == 'speaker')

    @staticmethod
    def load_receiver_wiring(path=Path('/etc/e5/receiver-wiring')):
        try:
            value = path.read_text().strip()
        except FileNotFoundError:
            return 'hpl'
        if value not in ('hpl', 'aol'):
            raise ValueError('unsupported receiver wiring; expected hpl or aol')
        return value

    @staticmethod
    def load_receiver_volume_ceiling(path=Path('/etc/e5/receiver-volume-ceiling')):
        try:
            value = int(path.read_text().strip())
        except FileNotFoundError:
            return 15
        if not 1 <= value <= 15:
            raise ValueError('receiver volume ceiling must be 1..15')
        return value

    @staticmethod
    def load_codec_gains(path=Path('/lib/firmware/codec.xml')):
        # The analog stage must match this unit, not a fixed donor-board gain.
        # Hold the stock maximum-call analog gain; adjust volume in DSP only,
        # avoiding analog switches on each keypress. HAL index6 -> DSP step7.
        import xml.etree.ElementTree as ET
        try:
            data = path.read_bytes()
            if len(data) > 4 * 1024 * 1024 or b'<!DOCTYPE' in data or b'<!ENTITY' in data:
                raise ValueError('invalid codec parameter XML')
            result = {}
            fields = {'dacl_playback_volume': 3, 'dacs_playback_volume': 3,
                      'ao_playback_volume': 7, 'ear_playback_volume': 15}
            for node in ET.fromstring(data).iter():
                if 'mode' not in node.attrib: continue
                values = {}
                for field, maximum in fields.items():
                    entry = node.find(field + '/volume6')
                    if entry is not None:
                        value = int(entry.get('val'), 0)
                        if 0 <= value <= maximum: values[field] = value
                result[int(node.attrib['mode'], 0)] = values
            return result
        except (OSError, ValueError, TypeError, ET.ParseError) as error:
            logging.warning('cannot read codec call gains: %s; using legacy values', error)
            return {}

    @staticmethod
    def load_volume_curves(path=Path('/lib/firmware/audio_structure'), data=None):
        # Read the same AS profile blob loaded by the driver, never a donor's
        # calibration. This is the v4 0x2da-byte AS layout used by this backend.
        # In particular, some SWB tables have an out-of-order/louder entry.
        try:
            data = path.read_bytes() if data is None else data
            magic, count, size = struct.unpack_from('<16sII', data)
            if magic.rstrip(b'\0') != b'audio_profile' or size != 0x2da or len(data) != 24 + count * size:
                raise ValueError('unknown audio structure layout')
            return {mode: struct.unpack_from('<7H', data, 24 + mode * size + 0x244)
                    for mode in range(count)}
        except (OSError, ValueError, struct.error) as error:
            logging.warning('cannot read voice volume curves: %s; using native steps 1-7', error)
            return {}

    def dsp_volume(self):
        # Keep the already verified Bluetooth gain; HFP VGS owns its volume.
        if self.bluetooth:
            return 7
        if not self.volume:
            return 1  # explicit downlink mute remains authoritative at zero
        # Preserve the full UI travel while capping phone-mode loudness to
        # the board's equivalent native UI level; handsfree keeps full range.
        level = self.volume if self.speaker else (
            1 + (self.volume - 1) * (self.receiver_volume_ceiling - 1) / 14)
        if level == 15:
            return 7
        # Acoustic mode follows user intent even when both routes share a
        # transducer: the native handset curve is much quieter than handsfree.
        mode = self.profile_word(self.band, self.speaker) >> 24
        gains = self.volume_curves.get(mode, ())
        if len(gains) == 7 and gains[-1] > 0:
            # Retain only positive gains no louder than the previous fixed
            # maximum, sort by audible gain, and map the UI to those points.
            points = [(i + 1, math.log(g)) for i, g in enumerate(gains) if 0 < g <= gains[-1]]
            low, high = min(g for _, g in points), math.log(gains[-1])
            target = low + (high - low) * (level - 1) / 14
            return min(points, key=lambda p: (abs(p[1] - target), p[0]))[0]
        return 1 + int((level - 1) * 6 // 14)

    def controls(self):
        return tuple(dict.fromkeys((*self.ROUTES, *self.RECEIVER, *self.SPEAKER,
                                    *self.GAINS, *self.PROFILES, *self.BLUETOOTH, *self.BT_SRC, *self.CODEC_IIS)))

    @staticmethod
    def profile_word(band, speaker=False, bluetooth=False):
        # Own Handset NB1/WB1/SWB1/FB1: 0/2/4/5; Handsfree: 7/9/11/12.
        # Param ID equals the mode's logical ID;
        # the low byte is DAI_ID_VOICE=5, as in the Unisoc HAL.
        mode = ((28, 30, 32, 33) if bluetooth else (7, 9, 11, 12) if speaker else (0, 2, 4, 5))[band]
        return (mode << 24) | (mode << 16) | 5

    def profiles(self):
        word = self.profile_word(self.band, self.speaker, self.bluetooth)
        for name in self.PROFILES:
            self.put(name, word)
        self.put('VBC_VOLUME', self.dsp_volume())
        if self.bluetooth:
            # hw_params/start and profile changes overwrite SRC; this must be
            # repeated after them, using the negotiated SCO codec's rate.
            for name in self.BT_SRC:
                self.put(name, str(self.bt_rate))
            return
        for name, value in self.CODEC_IIS.items(): self.put(name, value)
        # Wire the real amplifier with its own codec gains, but retain the
        # handset DSP profile/volume ceiling for the logical phone mode.
        physical_mode = self.profile_word(self.band, self.speaker_path) >> 24
        gains = self.codec_gains.get(physical_mode, {})
        ao_path = self.speaker_path or self.receiver_wiring == 'aol'
        self.put('DAC Gain DAC Playback Volume', gains.get(
            'dacs_playback_volume' if ao_path else 'dacl_playback_volume', 0 if ao_path else 3))
        self.put('AO Gain AO Playback Volume', gains.get('ao_playback_volume',
            (3, 3, 3, 4)[self.band] if self.speaker_path else 6))
        if not ao_path:
            self.put('EAR Gain EAR Playback Volume', gains.get('ear_playback_volume', (2, 2, 2, 4)[self.band]))
        logging.info('voice profiles=%#x, bandwidth=%d, output=%s, wiring=%s, VBC_VOLUME=%d',
                     word, self.band, 'speaker' if self.speaker_path else 'receiver',
                     'aol' if ao_path else 'hpl', self.dsp_volume())

    def output(self):
        # Disable the old endpoint before rewiring; never feed both outputs.
        self.put('Speaker Function', 'off')
        self.put('Earpiece Function', 'off')
        if self.bluetooth:
            for name, value in self.BLUETOOTH.items(): self.put(name, value)
            return
        route = (self.SPEAKER if self.speaker_path else
                 self.RECEIVER_AOL if self.receiver_wiring == 'aol' else self.RECEIVER)
        for name, value in route.items():
            self.put(name, value)

    def set_speaker(self, enabled):
        self.set_route('speaker' if enabled else 'receiver')

    def set_route(self, route, rate=8000):
        if route not in ('receiver', 'speaker', 'bluetooth') or rate not in (8000, 16000):
            raise ValueError('unsupported voice route')
        enabled, bluetooth = route == 'speaker', route == 'bluetooth'
        if enabled == self.speaker and bluetooth == self.bluetooth and (not bluetooth or rate == self.bt_rate):
            return
        if not self.handles:
            self.speaker, self.bluetooth, self.bt_rate = enabled, bluetooth, rate
            return
        names = self.controls()
        before = {name: self.get(name) for name in names}
        previous = self.speaker, self.bluetooth, self.bt_rate
        try:
            self.put('VBC_DL_MUTE', 'enable')
            self.put('VBC DAC1 DSP MDG Set', '1,1024')
            self.speaker, self.bluetooth, self.bt_rate = enabled, bluetooth, rate
            if previous[1] and not bluetooth:
                # Restore BT-only clock/matrix controls, then restore the
                # codec route. Preserve mute until the new route is ready.
                for name in (*self.BLUETOOTH, *self.BT_SRC):
                    if not name.startswith('S_VOICE_') and name in self.saved: self.put(name, self.saved[name])
                for name, value in self.ROUTES.items():
                    if name != 'VBC ADC2 DG Set': self.put(name, value)
                self.put('S_VOICE_P_BT SWITCH', 'off')
                self.put('S_VOICE_C_BT SWITCH', 'off')
            self.output()
            self.profiles()
            self.put('VBC ADC2 DG Set', before['VBC ADC2 DG Set'])
            self.unmute_downlink()
        except Exception:
            self.speaker, self.bluetooth, self.bt_rate = previous
            self.restore(before)
            raise

    def unmute_downlink(self):
        # Profiles/startup can reset gain. Reapply before releasing route mute.
        self.apply_volume(force=True)

    def set_volume(self, level, remote=False):
        level = max(0, min(15, int(level)))
        if (level, bool(remote)) == (self.volume, self.remote_volume):
            return
        before = self.volume, self.remote_volume
        self.volume, self.remote_volume = level, bool(remote)
        if self.handles:
            try:
                self.apply_volume()
            except Exception:
                self.volume, self.remote_volume = before
                raise

    def apply_volume(self, force=False):
        # The native HAL changes VBC_VOLUME (AS/CVS volume index + 1), not
        # DAC1 DG alone. On the codec route that DG readback changed while the
        # actual call stayed loud. Keep DAC1 at its existing baseline locally;
        # use the calibrated DSP table, without touching analog or mic mute.
        # HFP remote volume is already audible and must not be attenuated twice.
        step = self.dsp_volume()
        effective = self.volume if self.bluetooth and not self.remote_volume else 15
        gain = 24 - (15 - effective) if effective else 0
        muted = not self.volume
        previous = self.applied_volume
        if force or previous is None or previous[0] != step:
            self.put('VBC_VOLUME', step)
        if force or previous is None or previous[1] != gain:
            self.put('VBC DAC1 DG Set', f'{gain},{gain}')
        # Avoid redundant DSP mute/fade commands when already unmuted.
        # Nonzero local volume changes only change the native step.
        if force or previous is None or previous[2] != muted:
            self.put('VBC DAC1 DSP MDG Set', '1,1024' if muted else '0,1024')
            self.put('VBC_DL_MUTE', 'enable' if muted else 'disable')
        self.applied_volume = step, gain, muted

    def network(self, net, band):
        if band not in (0, 1, 2, 3):
            logging.warning('unsupported DSP voice bandwidth %s', band)
            return
        if self.band == band:
            return  # duplicate notifications must not repeatedly mute a call
        self.band = band
        logging.info('DSP network message net=%#x, bandwidth=%d', net, band)
        if self.handles:
            self.profiles()
            self.unmute_downlink()

    def get(self, name):
        return self.mixer.get(name)

    def put(self, name, value):
        self.mixer.put(name, value)

    def start(self):
        if self.handles:
            return
        # Read the full snapshot before changing even the first route.
        self.saved = {name: self.get(name) for name in self.controls()}
        try:
            self.profiles()
            for name, value in self.ROUTES.items():
                self.put(name, value)
            self.output()
            for direction in (0, 1):
                self.handles.append(VoicePcm(self.card, self.device, direction))
            # Android prepares both directions before starting either one.
            for pcm in self.handles:
                pcm.start()
            # The vendor applies the parameters again after starting both PCMs.
            self.profiles()
            self.unmute_downlink()
            logging.info('VOICE-RUNNING hw:%s,%s playback/capture, %s, 8000 Hz',
                         self.card, self.device, 'speaker' if self.speaker else 'receiver')
        except Exception:
            self.stop()
            raise

    def stop(self):
        for pcm in reversed(self.handles):
            pcm.close()
        self.handles.clear()
        saved, self.saved = self.saved, {}
        self.speaker = False
        self.bluetooth = False
        self.bt_rate = 8000
        self.applied_volume = None
        self.restore(saved)
        logging.info('VOICE-STOPPED; prior mixer/profile state restored')

    def restore(self, values):
        errors = []
        # Rollback of a live transition follows the same make-before-break
        # rule. Restoring in reverse order alone could close the new backend
        # before reopening the original, shutting down their shared scene.
        enabled = {name for name, value in values.items()
                   if name.startswith('S_VOICE_') and str(value) in ('on', '1')}
        for name in enabled:
            try: self.put(name, values[name])
            except Exception as error: errors.append(str(error))
        for name, value in reversed(tuple(values.items())):
            if name in enabled: continue
            try:
                self.put(name, value)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError('; '.join(errors))

    def mute(self, mute):
        if self.handles:
            self.put('VBC ADC2 DG Set', '0,0' if mute else '24,24')


def main():
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    logging.basicConfig(level=logging.INFO, format='%(asctime)s e5-call-audio: %(message)s')
    hardware = VoiceHardware()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop = GLib.MainLoop()
    states = {'AudioMode': 0, 'SpeakerState': 0, 'MicState': 1}
    connection = None
    pipe_fd = None
    pipe_stop = threading.Event()

    def voice_notifications():
        nonlocal pipe_fd
        try:
            pipe_fd = os.open('/dev/audio_pipe_voice', os.O_RDWR)
            logging.info('DSP voice notification channel opened')
            while not pipe_stop.is_set():
                try:
                    data = os.read(pipe_fd, 20)
                except OSError:
                    if pipe_stop.is_set():
                        break
                    raise
                if pipe_stop.is_set():
                    break
                if len(data) != 20:
                    raise RuntimeError('short DSP notification')
                command, channel, p0, p1, p2, p3 = struct.unpack('<HH4I', data)
                logging.info('DSP notification cmd=%#x channel=%d params=%#x,%#x,%#x,%#x', command, channel, p0, p1, p2, p3)
                if command == 0 and channel == 2:
                    executor.submit(hardware.network, p0, p1)
                elif command == 0x35 and channel == 2:
                    # Stock HAL logs this as "cp voice enable", not bandwidth.
                    logging.info('CP voice enabled=%s (mask=%#x)', bool(p0), p0)
        except Exception:
            if not pipe_stop.is_set():
                logging.exception('DSP voice notification listener failed')
        finally:
            if pipe_fd is not None:
                os.close(pipe_fd)
                pipe_fd = None

    pipe_thread = threading.Thread(target=voice_notifications, daemon=True)
    pipe_thread.start()

    def properties():
        return {name: GLib.Variant('u', value) for name, value in states.items()}

    def finish(invocation, future):
        try:
            future.result()
            success = True
        except Exception:
            logging.exception('audio operation failed')
            success = False
        if connection:
            connection.emit_signal(None, OBJECT, 'org.freedesktop.DBus.Properties',
                                   'PropertiesChanged', GLib.Variant('(sa{sv}as)', (NAME, properties(), [])))
        invocation.return_value(GLib.Variant('(b)', (success,)))
        return False

    def operation(method, value):
        if method == 'SelectMode':
            if value:
                hardware.start()
            else:
                hardware.stop()
            states['AudioMode'] = int(value)
            states['SpeakerState'] = int(hardware.speaker)
            states['MicState'] = 1
        elif method == 'EnableSpeaker':
            hardware.set_speaker(value)
            states['SpeakerState'] = int(hardware.speaker)
        else:
            hardware.mute(value)
            states['MicState'] = 0 if value else 1

    def method_call(bus, sender, path, interface, method, parameters, invocation):
        value = parameters.unpack()[0]
        if method == 'SelectMode' and value not in (0, 1):
            invocation.return_dbus_error('org.freedesktop.DBus.Error.InvalidArgs', 'mode must be 0 or 1')
            return
        logging.info('%s(%s)', method, value)
        future = executor.submit(operation, method, value)
        future.add_done_callback(lambda result: GLib.idle_add(finish, invocation, result))

    def acquired(bus, name):
        nonlocal connection
        connection = bus
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        bus.register_object(OBJECT, info, method_call,
                            lambda b, s, p, i, name: GLib.Variant('u', states[name]), None)

    def shutdown(*args):
        pipe_stop.set()
        if pipe_fd is not None:
            # AUDIO_PIPE_WAKEUP releases the driver's blocking receive.
            try:
                fcntl.ioctl(pipe_fd, 0x40044100, struct.pack('i', 1))
            except OSError:
                pass
        future = executor.submit(hardware.stop)
        future.add_done_callback(lambda result: GLib.idle_add(loop.quit))
        return False

    Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.NONE,
                     acquired, None, lambda *args: shutdown())
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    try:
        loop.run()
    finally:
        pipe_stop.set()
        pipe_thread.join(timeout=2)
        executor.shutdown(wait=True)
        hardware.stop()


if __name__ == '__main__':
    main()
