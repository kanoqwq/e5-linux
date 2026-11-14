"""SIM presence comes from a fresh, scoped background result, never CFUN/ICCID."""
import json
import tempfile
import unittest
from pathlib import Path
from native_dashboard_fixture import Snapshot


class SimPresence(unittest.TestCase):
    def test_live_data_card_does_not_follow_stale_boot_preference(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Snapshot(Path(temp))
            s.write('etc/config/e5-sim', "config sim 'main'\n option card '0'\n")
            s.write('sys/module/sipc_wwan/parameters/card', '1\n')
            self.assertEqual(s.run()['modem']['data_sim'], 1)

    def test_info_exports_only_selected_slot_and_live_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Snapshot(Path(temp))
            s.write('run/t551m-at2/cmd.sock','')
            s.write('proc/net/unix','/run/t551m-at2/cmd.sock\n')
            s.write('run/t551m-sims.json',json.dumps(dict(sims=[dict(slot=0,updated=120,present=False,registration='absent'),None])))
            self.assertEqual(s.run(slot='0')['modem']['sims'][0],dict(slot=0,state='absent'))
            self.assertEqual(s.run(slot='1')['modem']['sims'][1],dict(slot=1,state='unknown'))
            (s.root/'run/t551m-at2/cmd.sock').unlink()
            self.assertEqual(s.run(slot='0')['modem']['sims'][0],dict(slot=0,state='unknown'))

    def test_scoped_freshness_and_known_presence(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Snapshot(Path(temp))
            self.assertEqual(s.run('sim_state','0'),'unknown')
            def row(**changes):
                value=dict(slot=0,updated=120.0,present=False,registration='absent')
                value.update(changes)
                s.write('run/t551m-sims.json',json.dumps(dict(sims=[value,None])))
            row()
            self.assertEqual(s.run('sim_state','0'),'absent')
            self.assertEqual(s.run('sim_state','1'),'unknown')
            for change in (dict(updated=0),dict(updated=124),dict(slot=1),dict(present=None),
                           dict(updated='120'),dict(registration='unknown')):
                row(**change)
                self.assertEqual(s.run('sim_state','0'),'unknown')
            row(present=True,registration='disabled')
            self.assertEqual(s.run('sim_state','0'),'ready')
            row(present=True,registration='locked')
            self.assertEqual(s.run('sim_state','0'),'locked')
            self.assertEqual(s.run('sim_state','invalid'),'unknown')
            for value in ('broken json','null'):
                s.write('run/t551m-sims.json',value)
                self.assertEqual(s.run('sim_state','0'),'unknown')


if __name__=='__main__':unittest.main()
