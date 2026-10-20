"""Exercise USB adapter discovery, bridge ownership and asynchronous role results."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / 'openwrt/luci-app-t551m/root/usr/libexec/unisoc-modem/device-usb'
loader = importlib.machinery.SourceFileLoader('device_usb', str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
usb = importlib.util.module_from_spec(spec)
loader.exec_module(usb)

with tempfile.TemporaryDirectory() as tmp:
    usb.SYS = Path(tmp) / 'sys'
    usb.RUN = Path(tmp) / 'run'
    usb.RUN.mkdir()
    for name in ('usb', 'gadget'):
        (usb.SYS / 'bus' / name).mkdir(parents=True)
    policy = usb.SYS / 'bus/platform/devices/typec/usb_role_policy'
    policy.parent.mkdir(parents=True)
    policy.write_text('auto\n')
    policy.with_name('usb_role').write_text('device\n')
    settings = {'role_auto': '0', 'lan_auto': '1'}
    config = {'network.lan_device.name': ['br-lan'], 'network.lan_device.type': ['bridge'],
              'network.lan_device.ports': ['usb0'], 'network.wan.device': ['eth2']}
    calls = []
    def command(*args, **kw):
        calls.append(args)
        if args == ('uci', '-q', 'get', 'unisoc_modem.main.lan_device'):
            return 'br-lan'
        if args[:3] == ('uci', '-q', 'add_list'):
            config['network.lan_device.ports'].append(args[3].split('=', 1)[1])
        return ''
    def nic(name, bus, driver, master=None):
        path = usb.SYS / 'class/net' / name
        (path / 'device').mkdir(parents=True)
        (path / 'type').write_text('1\n')
        (path / 'carrier').write_text('1\n')
        (path / 'address').write_text('02:00:00:00:00:01\n')
        (path / 'device/subsystem').symlink_to(usb.SYS / 'bus' / bus)
        drv = usb.SYS / 'drivers' / driver
        drv.mkdir(parents=True, exist_ok=True)
        (path / 'device/driver').symlink_to(drv)
        if master:
            target = usb.SYS / 'class/net' / master
            target.mkdir(parents=True, exist_ok=True)
            (path / 'master').symlink_to(target)
    nic('usb0', 'gadget', 'ncm')
    nic('eth1', 'usb', 'r8152')
    nic('eth2', 'usb', 'cdc_ether')
    nic('eth3', 'usb', 'asix', 'br-guest')
    nic('wlan9', 'usb', 'rtl8xxxu')
    with patch.object(usb, 'command', command), patch.object(usb, 'network_config', lambda: {k:list(v) for k,v in config.items()}), \
         patch.object(usb, 'setting', lambda k,d='': settings.get(k,d)), \
         patch.object(usb, 'save', lambda k,v: settings.update({k:v})):
        assert {d['name'] for d in usb.adapters()} == {'eth1', 'eth2', 'eth3'}
        assert usb.net_sync()['attached'] == ['eth1']
        assert config['network.lan_device.ports'] == ['usb0', 'eth1']
        assert ('ip', 'link', 'set', 'dev', 'eth1', 'master', 'br-lan') in calls
        before = calls.count(('uci', 'commit', 'network'))
        usb.net_sync()
        assert calls.count(('uci', 'commit', 'network')) == before, 'No repeated flash writes/reloads'
        settings['lan_auto'] = '0'
        nic('eth4', 'usb', 'ax88179_178a')
        assert usb.net_sync()['attached'] == ['eth1'], 'Saved adapters reconnect with auto-add disabled'
        try: usb.net_sync('eth2')
        except ValueError: pass
        else: raise AssertionError('WAN adapter stolen')
        try: usb.net_sync('usb0')
        except ValueError: pass
        else: raise AssertionError('Gadget mistaken for host NIC')
        assert usb.net_sync('eth4')['attached'] == ['eth4']
        assert not usb.adapter('../../etc/passwd')
        print('PASS: physical Ethernet discovery; gadget/WiFi rejection; LAN reconnect; WAN/other bridge preserved')
        with patch.object(usb.subprocess, 'Popen') as spawn:
            assert usb.queue_role('host', '1')['pending'] == 1
            token = usb.job_read()['token']
            assert usb.state()['pending'] == 1
            assert policy.read_text().strip() == 'auto', 'RPC response precedes role change'
            assert usb.apply_role('host', '1', token)['ok'] == 1
            assert policy.read_text().strip() == 'host' and settings['role_auto'] == '1'
            assert usb.state()['policy'] == 'host' and usb.state()['role'] == 'device'
            usb.queue_role('auto', '0'); token = usb.job_read()['token']
            assert usb.apply_role('device', '0', 'old-job')['superseded'] == 1
            assert usb.apply_role('auto', '0', token)['ok'] == 1
            assert 'host-sink' not in usb.role_modes()
            try: usb.queue_role('host-sink', '0')
            except ValueError: pass
            else: raise AssertionError('External host exposed without kernel capability')
            capability = policy.with_name('usb_host_sink_supported')
            capability.write_text('1\n')
            assert 'host-sink' in usb.state()['role_supported']
            try: usb.queue_role('host-sink', '1')
            except ValueError: pass
            else: raise AssertionError('Experimental external host persisted at boot')
            usb.queue_role('host-sink', '0'); token = usb.job_read()['token']
            assert usb.apply_role('host-sink', '0', token)['ok'] == 1
            assert settings['role_auto'] == '0' and usb.state()['role'] == 'device'
            usb.queue_role('host-sink', '0'); token = usb.job_read()['token']
            capability.unlink()
            assert usb.apply_role('host-sink', '0', token)['ok'] == 0
            usb.queue_role('device', '0'); token = usb.job_read()['token']
            policy.unlink()
            assert usb.apply_role('device', '0', token)['ok'] == 0
            assert usb.state()['last_error'] and not usb.state()['pending']
        print('PASS: async role acknowledgement, policy vs attachment, stale-job rejection and actionable failure')

        with patch.object(usb, 'net_modes', lambda: ['ncm','ecm','rndis']), \
             patch.object(usb, 'net_current', lambda: 'ncm'), patch.object(usb, 'gadget_mode') as gadget:
            for args in [('rndis','once','1'),('ecm','permanent','0')]:
                assert usb.save_net(*args)['reboot_required'] == 1
            gadget.assert_not_called()
            for args in [('rndis;reboot','once','1'),('ncm','bad','1'),('ncm','once','yes')]:
                try: usb.save_net(*args)
                except ValueError: pass
                else: raise AssertionError('Invalid network settings accepted')
            settings.update(net_mode='rndis', net_scope='once', net_auto='1')
            usb.boot_net()
            gadget.assert_called_once_with('rndis')
            assert settings['net_auto'] == '0', 'Successful once mode was not consumed'
            usb.boot_net()
            gadget.assert_called_once()
            (usb.RUN / 'net-boot.json').unlink()
            settings['net_auto'] = '1'
            gadget.side_effect = RuntimeError('simulated bind failure, NCM restored')
            usb.boot_net()
            assert settings['net_auto'] == '1', 'Failed boot must not consume the choice'
            assert usb.state()['net_boot']['error']
        print('PASS: mode validation, deferred change, one-boot consumption, service-reload and failure handling')
