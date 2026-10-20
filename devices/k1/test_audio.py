"""K1 stock AO receiver routing through the real shared voice state machine."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if os.name == 'nt':
    raise unittest.SkipTest('Linux voice backend; run under WSL')

TOP=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('k1_voice',TOP/'rootfs/overlay/opt/e5/e5-call-audio.py')
voice=importlib.util.module_from_spec(spec);spec.loader.exec_module(voice)


class Pcm:
    def __init__(self,*args):self.closed=False
    def start(self):pass
    def close(self):self.closed=True


class Hardware(voice.VoiceHardware):
    def __init__(self):
        self.receiver_wiring='aol';self.receiver_output='receiver'
        self.saved={};self.handles=[];self.card=0;self.device=5
        self.speaker=False;self.bluetooth=False;self.bt_rate=8000;self.band=1
        self.volume=15;self.remote_volume=False;self.volume_curves={};self.applied_volume=None
        # K1's own codec.xml WB1: handset uses AO6/DACS0, handsfree AO2/DACS0.
        self.codec_gains={2:{'ao_playback_volume':6,'dacs_playback_volume':0,'dacl_playback_volume':3},
                          9:{'ao_playback_volume':2,'dacs_playback_volume':0,'ear_playback_volume':15}}
        self.values={name:'before-'+name for name in self.controls()}
        self.original=dict(self.values);self.events=[]
    def get(self,name):return self.values[name]
    def put(self,name,value):self.values[name]=value;self.events.append((name,value))


class Audio(unittest.TestCase):
    def setUp(self):
        self.pcm=patch.object(voice,'VoicePcm',Pcm);self.pcm.start();self.addCleanup(self.pcm.stop)
        self.hw=Hardware()

    def test_factory_handset_route_and_own_analog_gains(self):
        h=self.hw;h.start()
        # AngelCare.K1.YUAN.YD.T1.0 ODM devices/handset/on, normalized booleans.
        expected={'AO Mixer AOL Switch':'on','AO Mixer AOR Switch':'on','DA AOR Switch':'on',
                  'AOL EAR Sel':'AOL','Earpiece Function':'on','Speaker Function':'off'}
        for name,value in expected.items():self.assertEqual(h.values[name],value,name)
        self.assertEqual(h.values['EAR_HPL Mixer DACHPL Switch'],'off')
        self.assertEqual(h.values['HPL EAR Sel'],'HPL')
        self.assertEqual(h.values['DAC Gain DAC Playback Volume'],0)
        self.assertEqual(h.values['AO Gain AO Playback Volume'],6)
        self.assertEqual(h.values['EAR Gain EAR Playback Volume'],h.original['EAR Gain EAR Playback Volume'])
        self.assertEqual(h.values['NXP Profile Select'],0x02020005)
        self.assertFalse(h.speaker);self.assertFalse(h.speaker_path)
        h.stop();self.assertEqual(h.values,h.original)

    def test_phone_handsfree_bluetooth_keep_mute_volume_and_existing_pcms(self):
        h=self.hw;h.start();handles=list(h.handles);h.mute(True);h.set_volume(4)
        h.set_speaker(True)
        self.assertEqual(h.values['Speaker Function'],'on');self.assertEqual(h.values['Earpiece Function'],'off')
        self.assertEqual(h.values['AO Gain AO Playback Volume'],2)
        self.assertEqual(h.values['NXP Profile Select'],0x09090005)
        h.set_route('bluetooth',16000)
        self.assertEqual(h.values['Speaker Function'],'off');self.assertEqual(h.values['Earpiece Function'],'off')
        self.assertEqual(h.values['VBC_SRC_BT_DAC'],'16000')
        h.set_route('receiver')
        self.assertEqual(h.values['Earpiece Function'],'on');self.assertEqual(h.values['AOL EAR Sel'],'AOL')
        self.assertEqual(h.values['DAC Gain DAC Playback Volume'],0)
        self.assertEqual(h.values['AO Gain AO Playback Volume'],6)
        self.assertEqual(h.values['VBC ADC2 DG Set'],'0,0')
        self.assertEqual(h.handles,handles);self.assertEqual(h.volume,4)
        h.set_volume(0);self.assertEqual(h.values['VBC_DL_MUTE'],'enable')
        h.network(0x10,1);self.assertEqual(h.values['AOL EAR Sel'],'AOL')
        h.stop();self.assertEqual(h.values,h.original)
        self.assertTrue(all(p.closed for p in handles))

    def test_failed_receiver_transition_restores_live_handsfree(self):
        h=self.hw;h.set_speaker(True);h.start();before=dict(h.values);put=h.put;failed=False
        def inject(name,value):
            nonlocal failed
            if name=='Earpiece Function' and value=='on' and not failed:
                failed=True;raise RuntimeError('injected receiver failure')
            put(name,value)
        h.put=inject
        with self.assertRaises(RuntimeError):h.set_route('receiver')
        self.assertEqual(h.values,before);self.assertTrue(h.speaker)
        h.stop();self.assertEqual(h.values,h.original)

    def test_wiring_default_validation_and_other_boards_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'receiver-wiring'
            self.assertEqual(voice.VoiceHardware.load_receiver_wiring(path),'hpl')
            path.write_text('aol\n');self.assertEqual(voice.VoiceHardware.load_receiver_wiring(path),'aol')
            path.write_text('invalid\n')
            with self.assertRaises(ValueError):voice.VoiceHardware.load_receiver_wiring(path)
        h=self.hw;h.receiver_wiring='hpl';h.start()
        self.assertEqual(h.values['EAR_HPL Mixer DACHPL Switch'],'on')
        self.assertEqual(h.values['DAC Gain DAC Playback Volume'],3)
        h.stop();h.receiver_output='speaker';h.start()
        self.assertEqual(h.values['Speaker Function'],'on')
        self.assertEqual(h.values['Earpiece Function'],'off')
        self.assertEqual(h.values['AO Gain AO Playback Volume'],2)
        h.stop();self.assertEqual(h.values,h.original)


if __name__=='__main__':unittest.main()
