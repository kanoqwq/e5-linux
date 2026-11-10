"""eUICC protocol/validation fixtures; never opens a real modem or card."""
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOP = Path(__file__).resolve().parents[2]
APP = TOP/'openwrt'/('luci-app-t551m' if (TOP/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300')
SOURCE = APP/'root/usr/libexec/unisoc-modem/euicc'
loader = importlib.machinery.SourceFileLoader('euicc_under_test', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
e = importlib.util.module_from_spec(spec); loader.exec_module(e)
EID = '89049032000100123450001234567890'
ICCID = '8912345678901234567'


def request(action='scan', **extra):
    value=dict(job='1'*32,slot='0',action=action,transport='csim',eid=EID,iccid=ICCID,confirm='yes')
    value.update(extra)
    return value


def tlv(tag, body):
    size=len(body)
    length=bytes([size]) if size<128 else bytes([0x81,size])
    return bytes.fromhex(tag)+length+body


class Card:
    """Small synthetic eUICC APDU peer for the actual upstream lpac binary."""
    def __init__(self):
        self.commands=[];self.enabled=True;self.deleted=False;self.nickname='Test'
        self.wire=None

    def payload(self, data):
        if data[:2]==bytes.fromhex('0070'):
            return bytes.fromhex('019000' if data[2]==0 else '9000')
        if data[1]==0xa4:
            return bytes.fromhex('9000')
        body=data[5:]
        if body.startswith(bytes.fromhex('BF3E')):
            result=tlv('BF3E',tlv('5A',bytes.fromhex(EID)))
        elif body.startswith(bytes.fromhex('BF2D')):
            info=b''
            if not self.deleted:
                bcd=ICCID+'F'
                encoded=bytes(int(bcd[i+1]+bcd[i],16) for i in range(0,len(bcd),2))
                info=tlv('E3',tlv('5A',encoded)+tlv('9F70',bytes([int(self.enabled)]))+
                         tlv('90',self.nickname.encode())+tlv('91',b'Example')+tlv('92',b'Data'))
            result=tlv('BF2D',tlv('A0',info))
        elif body.startswith(bytes.fromhex('BF28')):
            result=tlv('BF28',tlv('A0',b''))
        elif body[:2] in (bytes.fromhex('BF31'),bytes.fromhex('BF32'),bytes.fromhex('BF33'),bytes.fromhex('BF29')):
            tag=body[:2].hex()
            if tag=='bf31':self.enabled=True
            elif tag=='bf32':self.enabled=False
            elif tag=='bf33':self.deleted=True
            elif tag=='bf29':
                pos=body.find(b'\x90',3)
                if pos>=0:self.nickname=body[pos+2:pos+2+body[pos+1]].decode()
            result=tlv(tag,tlv('80',b'\0'))
        else:
            return bytes.fromhex('6A86')  # optional chip-info fields are absent
        return result+bytes.fromhex('9000')

    def command(self, body):
        self.commands.append(body)
        if body in ('+CSIM=?','+CGLA=?','+SFUN=5','+SFUN=4','+SFUN=2'):
            return 'OK\r\n'
        if body.startswith('+CCHO='):
            return '+CCHO: 1\r\nOK\r\n'
        if body=='+CCHC=1':
            return 'OK\r\n'
        if body.startswith('+CSIM=') or body.startswith('+CGLA='):
            encoded=body.split('"')[1]
            data=self.payload(bytes.fromhex(encoded)).hex().upper()
            kind='CSIM' if body.startswith('+CSIM=') else 'CGLA'
            return f'+{kind}: {len(data)},"{data}"\r\nOK\r\n'
        raise AssertionError('Unexpected command: '+body)


class Validation(unittest.TestCase):
    def test_basic_and_empty_profile_card_scan(self):
        self.assertEqual(e.validate(request())['slot'],'0')
        adapter=e.APDU(Card(),'csim')
        self.assertEqual(adapter.request({'func':'connect','param':None}),{'ecode':0})
        self.assertEqual(adapter.owner.commands,['+CSIM=?'])  # no CPIN gate on an empty eUICC

    def test_slot_and_job_validation(self):
        for slot in (0,2,'2','../x',None):
            r=request();r['slot']=slot
            with self.assertRaises(e.Failure):e.validate(r)
        for job in ('../etc/passwd','',None,'x'*32):
            r=request();r['job']=job
            with self.assertRaises(e.Failure):e.validate(r)

    def test_only_exposed_actions_and_exact_confirmation(self):
        for action in ('purge','reset','at','defaultsmdp','discovery'):
            with self.assertRaises(e.Failure):e.validate(request(action))
        for action in ('enable','disable','delete','download'):
            r=request(action);r['confirm']='YES'
            with self.assertRaisesRegex(e.Failure,'confirmation_required'):e.validate(r)

    def test_card_identity_and_iccid_are_required_for_mutation(self):
        for field in ('eid','iccid'):
            r=request('disable');r[field]='x'
            with self.assertRaises(e.Failure):e.validate(r)

    def test_activation_input_is_not_a_shell_or_command(self):
        good=request('download',activation='LPA:1$rsp.example.com$MATCHING-ID',confirmation='1234')
        self.assertEqual(e.validate(good)['activation'],good['activation'])
        for code in (123,'LPA:2$rsp.example.com$id','LPA:1$https://rsp.example.com$id','LPA:1$rsp.example.com$id\nATZ','a'*4096):
            r=request('download',activation=code)
            with self.assertRaises(e.Failure):e.validate(r)

    def test_profile_fields_are_bounded_and_do_not_include_icons(self):
        row=dict(iccid=ICCID,profileState='enabled',icon='SECRET',profileNickname='<script>x</script>')
        clean=e.clean_profiles([row])[0]
        self.assertNotIn('icon',clean)
        self.assertEqual(clean['profileNickname'],'<script>x</script>')  # UI must render text, never HTML
        self.assertEqual(len(e.clean_profiles([row] * 325)), 325)
        for bad in (None,{},[dict(row,profileState=None)],[dict(row,iccid='../')]):
            with self.assertRaises(e.Failure):e.clean_profiles(bad)


class APDU(unittest.TestCase):
    def test_csim_allocates_private_channel_and_only_uses_it(self):
        card=Card();adapter=e.APDU(card,'csim')
        self.assertEqual(adapter.request({'func':'logic_channel_open','param':e.ISDR}),{'ecode':1})
        response=adapter.request({'func':'transmit','param':'81E2910006BF3E035C015A'})
        self.assertIn(EID,response['data'])
        adapter.cleanup();self.assertEqual(adapter.channels,set())
        self.assertTrue(card.commands[-1].endswith('"0070800100"'))

    def test_failed_close_keeps_channel_for_cleanup_retry(self):
        card = Card()
        adapter = e.APDU(card, 'logical')
        adapter.channels.add(1)
        with patch.object(card, 'command', side_effect=e.Failure('at_timeout')):
            with self.assertRaisesRegex(e.Failure, 'at_timeout'):
                adapter.close(1)
        self.assertEqual(adapter.channels, {1})
        adapter.cleanup()
        self.assertEqual(adapter.channels, set())
        self.assertEqual(card.commands, ['+CCHC=1'])

    def test_logical_interface_is_explicit_not_an_after_error_fallback(self):
        card=Card();adapter=e.APDU(card,'logical')
        adapter.request({'func':'logic_channel_open','param':e.ISDR})
        adapter.request({'func':'transmit','param':'81E2910006BF3E035C015A'})
        adapter.cleanup()
        self.assertTrue(card.commands[0].startswith('+CCHO='))
        self.assertTrue(card.commands[1].startswith('+CGLA=1,'))
        self.assertEqual(card.commands[-1],'+CCHC=1')
        self.assertFalse(any(c.startswith('+CSIM=') for c in card.commands))

    def test_basic_channel_unknown_channel_and_other_aid_are_refused(self):
        adapter=e.APDU(Card(),'csim')
        for req in ({'func':'transmit','param':'00A40000'}, {'func':'transmit','param':'82E2910000'},
                    {'func':'logic_channel_open','param':'A000000001'}, {'func':'logic_channel_close','param':'00'}):
            with self.assertRaises(e.Failure):adapter.request(req)
        self.assertFalse(adapter.owner.commands)

    def test_bad_response_length_or_multiple_replies_are_rejected(self):
        for response in ('+CSIM: 8,"9000"\r\nOK\r\n','+CSIM: 3,"900"\r\nOK\r\n',
                         '+CSIM: 4,"9000"\n+CSIM: 4,"9000"\nOK\n'):
            with self.assertRaises(e.Failure):e.APDU.parse_data(response,'CSIM')

    def test_absent_isdr_is_not_mistaken_for_an_empty_profile_list(self):
        card=Card();original=card.payload
        card.payload=lambda data: bytes.fromhex('6A82') if data[1]==0xa4 else original(data)
        adapter=e.APDU(card,'csim')
        with self.assertRaisesRegex(e.Failure,'not_euicc'):
            adapter.request({'func':'logic_channel_open','param':e.ISDR})
        adapter.cleanup();self.assertFalse(adapter.channels)


class Network(unittest.TestCase):
    def test_esim_root_extends_web_roots_without_disabling_verification(self):
        cert = APP/'root/usr/share/unisoc-euicc/ca/gsma-rsp2-root-ci1.pem'
        der = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding='ascii'))
        self.assertEqual(hashlib.sha256(der).hexdigest(),
                         '5e3e91fd454327c3af5d32a7a73bbc59fe43aa7d85fd32d5db44423f80a56bb3')
        default = ssl.create_default_context()
        with patch.object(e, 'RSP_CA', cert):
            context = e.tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        roots = {hashlib.sha256(c).digest() for c in context.get_ca_certs(binary_form=True)}
        self.assertIn(hashlib.sha256(der).digest(), roots)
        self.assertTrue(all(hashlib.sha256(c).digest() in roots for c in default.get_ca_certs(binary_form=True)))
        self.assertEqual(context.verify_flags, default.verify_flags)

    def test_missing_or_invalid_esim_root_fails_without_unverified_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory)/'missing.pem'
            with patch.object(e, 'RSP_CA', cert):
                with self.assertRaisesRegex(e.Failure, 'server_ca'):e.tls_context()
                cert.write_text('not a certificate')
                with self.assertRaisesRegex(e.Failure, 'server_ca'):e.tls_context()

    def test_tls_rejection_does_not_retry_with_verification_disabled(self):
        cert = APP/'root/usr/share/unisoc-euicc/ca/gsma-rsp2-root-ci1.pem'
        with patch.object(e, 'RSP_CA', cert), patch.object(e, 'TLSConnection') as connection:
            connection.return_value.request.side_effect = ssl.SSLCertVerificationError('untrusted')
            with self.assertRaisesRegex(e.Failure, 'server_tls'):
                e.http_request({'url':'https://rsp.example/a','tx':'','headers':[]})
            self.assertEqual(connection.call_count, 1)
            self.assertEqual(connection.call_args.kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
            connection.return_value.close.assert_called_once()

    def test_no_plain_http_credentials_ports_or_redirect_proxy_target(self):
        for url in ('http://rsp.example/a','https://a:b@rsp.example/a','https://rsp.example:8443/a','https://rsp.example/#x','https://x\nHost: bad'):
            with self.assertRaises(e.Failure):e.http_request({'url':url,'tx':'','headers':[]})

    def test_private_and_mixed_dns_are_rejected_before_connect(self):
        for addresses in (['127.0.0.1'],['169.254.169.254'],['::1'],['1.1.1.1','10.0.0.1']):
            values=[(socket.AF_INET,socket.SOCK_STREAM,0,'',(ip,443)) for ip in addresses]
            with patch.object(e.socket,'getaddrinfo',return_value=values),patch.object(e.socket,'socket') as sock:
                with self.assertRaisesRegex(e.Failure,'server_address'):
                    e.TLSConnection('rsp.example',context=ssl.create_default_context()).connect()
                sock.assert_not_called()

    def test_header_injection_and_unbounded_requests_are_refused(self):
        for headers in (['Host: 127.0.0.1'],['User-Agent: x\r\nX: y'],{'x':'y'}):
            with self.assertRaises(e.Failure):
                e.http_request({'url':'https://rsp.example/a','tx':'','headers':headers})


class Workflow(unittest.TestCase):
    def setUp(self):
        sleeper = patch.object(e.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
    def info(self):return {'eidValue':EID,'EUICCInfo2':None}
    def profiles(self,enabled=True):return [dict(iccid=ICCID,profileState='enabled' if enabled else 'disabled',profileNickname='Test')]

    def test_multi_se_scan_selects_first_element_and_reuses_probe_info(self):
        for transport in ('csim', 'logical'):
            def reply(args, adapter, progress):
                if args == ['chip', 'info']:
                    self.assertIn(adapter.aid, e.SE_AIDS)
                    return self.info()
                return []
            with patch.object(e, 'lpac', side_effect=reply) as run:
                result = e.perform(request(scan_se=True, transport=transport), Card(), lambda _: None)
            self.assertEqual((result['aid'], result['ses']), (e.SE_AIDS[0], 3))
            self.assertEqual([c.args[1].aid for c in run.call_args_list],
                             [*e.SE_AIDS, e.SE_AIDS[0], e.SE_AIDS[0]])

    def test_multi_se_scan_handles_only_second_element_present(self):
        with patch.object(e, 'lpac', side_effect=[e.Failure('not_euicc'), self.info(), [], []]):
            result = e.perform(request(scan_se=True), Card(), lambda _: None)
        self.assertEqual((result['aid'], result['ses']), (e.SE_AIDS[1], 2))

    def test_standard_card_scan_uses_default_application_when_estk_is_absent(self):
        card = Card()
        payload = card.payload
        card.payload = lambda data: bytes.fromhex('6A82') if data[1] == 0xa4 else payload(data)
        with patch.object(e, 'lpac', side_effect=[self.info(), [], []]) as run:
            result = e.perform(request(scan_se=True), card, lambda _: None)
        self.assertEqual((result['aid'], result['ses']), ('', 0))
        self.assertTrue(all(call.args[1].aid == e.ISDR for call in run.call_args_list))

    def test_multi_se_mutations_keep_selected_aid_for_every_command(self):
        for transport in ('csim', 'logical'):
            for aid in e.SE_AIDS:
                for action, replies in (
                    ('disable', [self.info(), self.profiles(), None, self.profiles(False), []]),
                    ('enable', [self.info(), self.profiles(False), None, self.profiles(), []]),
                    ('delete', [self.info(), self.profiles(False), [], None, [], [], self.info()]),
                    ('nickname', [self.info(), self.profiles(), None, self.profiles(), []]),
                    ('download', [self.info(), [], None, [], [], self.info()]),
                    ('notifications', [self.info(), [], [], []]),
                ):
                    with self.subTest(transport=transport, aid=aid, action=action), \
                         patch.object(e, 'lpac', side_effect=replies) as run:
                        e.perform(request(action, aid=aid, transport=transport, nickname='Test',
                                          activation='LPA:1$example.com$test'), Card(), lambda _: None)
                        self.assertTrue(all(c.args[1].aid == aid and c.args[1].transport == transport
                                            for c in run.call_args_list))

    def test_identity_mismatch_stops_before_profile_changes(self):
        r=request('disable');r['eid']='1'*32
        with patch.object(e,'lpac',return_value=self.info()) as run:
            with self.assertRaisesRegex(e.Failure,'card_changed'):e.perform(r,Card(),lambda _:None)
            self.assertEqual(run.call_count,1)

    def test_enabled_profile_is_never_deleted(self):
        with patch.object(e,'lpac',side_effect=[self.info(),self.profiles()]):
            with self.assertRaisesRegex(e.Failure,'profile_enabled'):e.perform(request('delete'),Card(),lambda _:None)

    def test_disable_is_explicit_and_read_back_without_any_radio_reset(self):
        with patch.object(e,'lpac',side_effect=[self.info(),self.profiles(),None,self.profiles(False),[]]) as run:
            result=e.perform(request('disable'),Card(),lambda _:None)
        self.assertEqual(result['profiles'][0]['profileState'],'disabled')
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['chip','info'],['profile','list'],['profile','disable',ICCID],['profile','list'],['notification','list']])

    def test_success_reply_with_unchanged_state_reports_readback_warning(self):
        with patch.object(e,'lpac',side_effect=[self.info(),self.profiles(),None] + [self.profiles()]*6 + [[]]):
            result = e.perform(request('disable'),Card(),lambda _:None)
        self.assertEqual(result['warning'], 'readback_failed')

    def test_switch_waits_for_fresh_profile_state_after_refresh(self):
        card = Card()
        with patch.object(e, 'lpac', side_effect=[self.info(), self.profiles(), None,
                         e.Failure('apdu_channel'), self.profiles(), self.profiles(False), []]) as run:
            result = e.perform(request('disable'), card, lambda _: None)
        self.assertEqual(card.commands, ['+SFUN=5', '+SFUN=4', '+SFUN=2'])
        self.assertEqual(result['profiles'][0]['profileState'], 'disabled')
        self.assertNotIn('warning', result)
        self.assertEqual(sum(c.args[0] == ['profile', 'disable', ICCID] for c in run.call_args_list), 1)

    def test_download_readback_failure_preserves_success_and_fresh_capacity(self):
        updated = {'eidValue': EID, 'EUICCInfo2': {'extCardResource': {'freeNonVolatileMemory': 12345}}}
        with patch.object(e, 'lpac', side_effect=[self.info(), [], None,
                         e.Failure('apdu_channel'), [], updated]) as run:
            result = e.perform(request('download', activation='LPA:1$example.com$test'), Card(), lambda _: None)
        self.assertEqual(result['warning'], 'readback_failed')
        self.assertIsNone(result['profiles'])
        self.assertEqual(result['free_bytes'], 12345)
        self.assertEqual(sum(c.args[0][:2] == ['profile', 'download'] for c in run.call_args_list), 1)

    def test_delete_notification_failure_preserves_verified_deletion(self):
        with patch.object(e, 'lpac', side_effect=[self.info(), self.profiles(False), [], None,
                         [], e.Failure('apdu_channel'), self.info()]):
            result = e.perform(request('delete'), Card(), lambda _: None)
        self.assertEqual(result['profiles'], [])
        self.assertIsNone(result['notifications'])
        self.assertEqual(result['warning'], 'notification_list')

    def test_failed_capacity_refresh_marks_old_capacity_unavailable(self):
        with patch.object(e, 'lpac', side_effect=[self.info(), [], None, [], [], e.Failure('apdu_channel')]):
            result = e.perform(request('download', activation='LPA:1$example.com$test'), Card(), lambda _: None)
        self.assertIsNone(result['free_bytes'])
        self.assertEqual(result['chip'], {'eidValue': EID})
        self.assertEqual(result['warning'], 'chip_info')

    def test_download_does_not_enable_new_profile_or_change_imei(self):
        r=request('download',activation='LPA:1$rsp.example$id',confirmation='1234')
        with patch.object(e,'lpac',side_effect=[self.info(),[],None,[],[],self.info()]) as run:
            e.perform(r,Card(),lambda _:None)
        commands=[c.args[0] for c in run.call_args_list]
        self.assertEqual(commands[2],['profile','download','-p','-a',r['activation'],'-c','1234'])
        self.assertFalse(any('enable' in c or '-i' in c for c in commands))

    def test_notification_batch_keeps_failed_entries_and_verifies_selected_card(self):
        rows=[{'seqNumber':2},{'seqNumber':5}]
        req=request('notifications',sequences=[5,2],remove=False)
        with patch.object(e,'lpac',side_effect=[self.info(),rows,None,e.Failure('lpac_failed'),[],[{'seqNumber':5}]]) as run:
            result=e.perform(req,Card(),lambda _:None)
        self.assertEqual((result['processed'],result['failed']),(1,1))
        self.assertEqual(run.call_args_list[2].args[0],['notification','process','-r','2'])
        self.assertEqual(run.call_args_list[3].args[0],['notification','process','-r','5'])

    def test_large_notification_list_can_process_one_selected_entry(self):
        rows = [{'seqNumber': n} for n in range(1025)]
        with patch.object(e, 'lpac', side_effect=[self.info(), rows, None, [], rows[:-1]]) as run:
            result = e.perform(request('notifications', sequences=[1024]), Card(), lambda _: None)
        self.assertEqual(run.call_args_list[2].args[0], ['notification', 'process', '-r', '1024'])
        self.assertEqual(result['processed'], 1)
        self.assertEqual(len(result['notifications']), 1024)
        self.assertEqual(e.validate(request('notifications', sequences=list(range(325))))['sequences'], list(range(325)))

    def test_unknown_notification_is_never_removed(self):
        with patch.object(e,'lpac',side_effect=[self.info(),[{'seqNumber':2}]]) as run:
            with self.assertRaisesRegex(e.Failure,'notification_missing'):
                e.perform(request('notifications',sequences=[9],remove=True),Card(),lambda _:None)
        self.assertEqual(run.call_count,2)

    def test_delete_lost_reply_is_read_back_once_and_never_replayed(self):
        req=request('delete')
        with patch.object(e,'lpac',side_effect=[self.info(),self.profiles(False),[],e.Failure('lpac_failed'),[],[],self.info()]) as run:
            result=e.perform(req,Card(),lambda _:None)
        self.assertEqual(result['profiles'],[])
        self.assertEqual(result['diagnostic'],'delete_response_lost_verified_absent')
        self.assertEqual(sum(call.args[0][:2]==['profile','delete'] for call in run.call_args_list),1)
        self.assertEqual(sum(call.args[0]==['profile','list'] for call in run.call_args_list),2)

    def test_failed_delete_keeps_original_reason_when_profile_is_still_present(self):
        error = e.Failure('lpac_failed', {'message': 'es10c_delete_profile', 'data': 'notAllowed'})
        with patch.object(e, 'lpac', side_effect=[self.info(), self.profiles(False), [], error, self.profiles(False)]):
            with self.assertRaises(e.Failure) as caught:
                e.perform(request('delete'), Card(), lambda _: None)
        self.assertIs(caught.exception, error)

    def test_download_with_only_old_notifications_reads_them_once_after_write(self):
        notes = [{'seqNumber': 1}]
        with patch.object(e, 'lpac', side_effect=[self.info(), notes, None, [], notes, self.info()]) as run:
            result = e.perform(request('download', activation='LPA:1$example.com$test'), Card(), lambda _: None)
        self.assertEqual(result['notifications'], notes)
        self.assertEqual(run.call_count, 6)

    def test_download_returns_profiles_read_after_installation(self):
        profiles = self.profiles(False)
        with patch.object(e, 'lpac', side_effect=[self.info(), [], None, profiles, [], self.info()]) as run:
            result = e.perform(request('download', activation='LPA:1$example.com$test'), Card(), lambda _: None)
        self.assertEqual(result['profiles'], e.clean_profiles(profiles))
        self.assertEqual(run.call_args_list[3].args[0], ['profile', 'list'])


class Preview(unittest.TestCase):
    def test_lpac_failure_preserves_json_message_and_data_on_nonzero_exit(self):
        payload = dict(code=-1, message='confirmation_code', data='required')
        script = 'import json,sys;print(json.dumps({"type":"lpa","payload":' + repr(payload) + '}));sys.exit(1)'
        with patch.object(e, 'LPAC', sys.executable), patch.dict(e.CONTROL, cancelled=False):
            with self.assertRaises(e.Failure) as caught:
                e.lpac(['-c', script], e.APDU(Card(), 'csim'), lambda _: None)
        self.assertEqual(caught.exception.code, 'lpac_failed')
        self.assertEqual(caught.exception.detail, payload)

    def test_at_failure_keeps_cme_reason_without_echoed_command(self):
        owner = object.__new__(e.Owner)
        owner.routing = ('fixture',)
        owner.binary = '/fixture/at'
        owner.slot = 0
        owner.env = {}
        result = subprocess.CompletedProcess([], 1, 'AT+CSIM=private\r\n+CME ERROR: 14\r\n')
        with patch.object(e.Owner, 'route_signature', return_value=owner.routing), \
             patch.object(e.subprocess, 'run', return_value=result):
            with self.assertRaises(e.Failure) as caught:
                owner.command('+CSIM=private')
        self.assertEqual(caught.exception.detail, {'message': '+CME ERROR: 14'})

    def test_real_child_process_waits_for_preview_and_drops_the_profile_icon(self):
        for accepted in (True,False):
            script='''import json,sys
def emit(kind,payload):print(json.dumps(dict(type=kind,payload=payload)),flush=True)
emit('progress',{'message':'es8p_meatadata_parse','data':{'iccid':'8912345678901234567','profileName':'Example','icon':'private-large-blob'}})
emit('progress',{'message':'preview','data':'y/n'})
answer=sys.stdin.read(1) # lpac profile/download.c uses getchar(), not fgets().
emit('apdu',{'func':'connect','param':None})
try:
 reply=json.loads(sys.stdin.readline())
 assert reply=={'type':'apdu','payload':{'ecode':0}}
except Exception:
 emit('lpa',{'code':-1,'data':None});sys.exit(1)
emit('lpa',{'code':0 if answer=='y' else -1,'data':None})
'''
            events=[]
            def progress(stage,detail=None):
                events.append((stage,detail))
                return accepted if stage=='preview' else None
            with patch.object(e,'LPAC',sys.executable),patch.dict(e.CONTROL,cancelled=False,process=None,preview_fd=None):
                if accepted:e.lpac(['-u','-c',script],e.APDU(Card(),'csim'),progress)
                else:
                    with self.assertRaisesRegex(e.Failure,'cancelled'):
                        e.lpac(['-u','-c',script],e.APDU(Card(),'csim'),progress)
            self.assertEqual(events[0][0],'metadata')
            self.assertNotIn('icon',events[0][1])
            self.assertEqual(events[1][0],'preview')


class Jobs(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='euicc-fixture-')
        self.addCleanup(self.temp.cleanup)
        change=patch.object(e,'RUN',Path(self.temp.name));change.start();self.addCleanup(change.stop)

    def state(self,data=None,**extra):
        req=e.validate(data or request())
        state=dict(ok=1,job=req['job'],slot=req['slot'],state='done',result={},
                   request_hash=hashlib.sha256(e.dump(dict(sorted(req.items()))).encode()).hexdigest())
        state.update(extra);e.atom(e.job_path(req['job']),state)
        return state

    def test_same_job_retry_returns_original_result_without_spawning(self):
        self.state()
        with patch.object(e.subprocess,'Popen') as spawn:
            result=e.start(request())
        self.assertEqual(result['state'],'done');spawn.assert_not_called()
        self.assertNotIn('request_hash',result)

    def test_reused_job_number_cannot_change_operation(self):
        self.state()
        with self.assertRaisesRegex(e.Failure,'request_changed'):e.start(request('disable'))

    def test_dead_or_unknown_pid_does_not_leave_a_permanent_running_job(self):
        self.state(state='running',pid=123,ticks=None)
        with patch.object(e,'process_ticks',return_value=None):
            self.assertEqual(e.status('1'*32)['error'],'operation_interrupted')

    def test_worker_publishes_original_failure_detail(self):
        self.state(state='running')
        detail = {'code': -1, 'message': 'confirmation_code', 'data': 'required'}
        with patch.object(e, 'perform', side_effect=e.Failure('lpac_failed', detail)), \
             patch.object(e, 'Owner'), patch.object(e.signal, 'signal'), patch.object(e.signal, 'alarm'), \
             patch.object(e.sys, 'stdin', io.TextIOWrapper(io.BytesIO(e.dump(request()).encode()))):
            e.worker('1' * 32, os.open(os.devnull, os.O_RDONLY))
        result = e.status('1' * 32)
        self.assertEqual(result['error'], 'lpac_failed')
        self.assertEqual(result['error_detail'], detail)


@unittest.skipUnless(os.name=='posix','Actual inherited flock fixtures require Linux')
class RoutingLock(unittest.TestCase):
    def test_card_routing_prevents_a_job_before_any_worker_is_started(self):
        with tempfile.TemporaryDirectory(prefix='euicc-lock-') as tmp,patch.object(e,'RUN',Path(tmp)),patch.object(e,'LPAC',sys.executable):
            with open(Path(tmp)/'operation.lock','a') as holder:
                e.lock_file(holder.fileno(),False)
                with patch.object(e.subprocess,'Popen') as spawn:
                    with self.assertRaisesRegex(e.Failure,'operation_busy'):e.start(request())
                    spawn.assert_not_called()

    def test_guard_preserves_pid_and_holds_the_lock_until_exec_child_exits(self):
        with tempfile.TemporaryDirectory(prefix='euicc-guard-') as tmp:
            root=Path(tmp);pidfile=root/'pid'
            code='''import runpy,sys,os
from pathlib import Path
module=runpy.run_path(sys.argv[1])
module['main'].__globals__['RUN']=Path(sys.argv[2])
Path(sys.argv[3]).write_text(str(os.getpid()))
sys.argv=['euicc','--guard',sys.executable,'-c','import os,sys;print(os.getpid(),flush=True);sys.stdin.readline()']
module['main']()
'''
            proc=subprocess.Popen([sys.executable,'-c',code,str(SOURCE),tmp,str(pidfile)],
                                  stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            try:
                child=int(proc.stdout.readline().strip())
                self.assertEqual(child,int(pidfile.read_text()))
                with open(root/'operation.lock','a') as contender:
                    with self.assertRaises(BlockingIOError):e.lock_file(contender.fileno(),True)
                proc.stdin.write('\n');proc.stdin.flush();self.assertEqual(proc.wait(timeout=5),0)
                with open(root/'operation.lock','a') as contender:e.lock_file(contender.fileno(),True)
            finally:
                if proc.poll() is None:proc.kill();proc.wait()
                proc.stdin.close();proc.stdout.close()


@unittest.skipUnless(os.environ.get('UNISOC_LPAC_REFERENCE'), 'Set verified upstream LPAC reference binary for real stdio integration')
class NativeLPAC(unittest.TestCase):
    def setUp(self):
        binary=os.environ['UNISOC_LPAC_REFERENCE'];self.assertTrue(Path(binary).is_file())
        change=patch.object(e,'LPAC',binary);change.start();self.addCleanup(change.stop)

    def test_official_lpac_chip_profiles_and_notifications_with_both_at_adapters(self):
        for transport in ('csim','logical'):
            card=Card();result=e.perform(request(transport=transport),card,lambda _:None)
            self.assertEqual(result['eid'],EID)
            self.assertEqual(result['profiles'][0]['iccid'],ICCID)
            self.assertEqual(result['profiles'][0]['profileState'],'enabled')
            self.assertEqual(result['notifications'],[])

    def test_official_lpac_disable_enable_rename_and_delete_are_verified(self):
        card=Card()
        for action in ('disable','enable','disable'):
            result=e.perform(request(action),card,lambda _:None)
            self.assertEqual(result['profiles'][0]['profileState'],'enabled' if action=='enable' else 'disabled')
        result=e.perform(request('nickname',nickname='Changed'),card,lambda _:None)
        self.assertEqual(result['profiles'][0]['profileNickname'],'Changed')
        result=e.perform(request('delete'),card,lambda _:None)
        self.assertEqual(result['profiles'],[])


if __name__=='__main__':unittest.main(verbosity=2)
