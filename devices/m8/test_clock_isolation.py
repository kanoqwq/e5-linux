"""Exercise real gate-driver access conditions, with isolated MMIO stubs."""
from pathlib import Path
import os
import re
import runpy
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class ClockIsolation(unittest.TestCase):
    def test_only_ai_child_gate_flags_change_and_preparation_is_repeatable(self):
        module = runpy.run_path(str(HERE / 'clock-isolation.py'))
        original = (HERE / 'port/drivers/clk/sprd/ums9620-clk.c').read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'drivers/clk/sprd/ums9620-clk.c'
            target.parent.mkdir(parents=True)
            target.write_text(original)
            module['apply'](root)
            fixed = target.read_text()
            module['apply'](root)
            self.assertEqual(fixed, target.read_text())
            self.assertEqual(original.count('SPRD_GATE_NON_AON') + 7,
                             fixed.count('SPRD_GATE_NON_AON'))
            # The flags do not force parent clocks on or change AI mux rates.
            restored = fixed
            for name in module['GATES']:
                pattern = r'(static SPRD_SC_GATE_CLK_HW\(' + name + r',[\s\S]*?CLK_IGNORE_UNUSED, )SPRD_GATE_NON_AON'
                restored, count = re.subn(pattern, r'\g<1>0', restored)
                self.assertEqual(count, 1)
            self.assertEqual(original, restored)

    @unittest.skipIf(os.name == 'nt', 'Linux C compiler required')
    def test_real_core_skips_mmio_only_when_non_aon_parent_is_off(self):
        kernel = os.environ.get('E5_TEST_KERNEL')
        if not kernel:
            self.skipTest('Set E5_TEST_KERNEL to the prepared kernel')
        core = (Path(kernel) / 'drivers/clk/sprd/gate.c').read_text()
        start = core.index('static int sprd_gate_is_enabled(')
        end = core.index('\n}', start) + 2
        fixture = '''#include <assert.h>
#include <stddef.h>
#define SPRD_GATE_NON_AON 2
#define CLK_GATE_SET_TO_DISABLE 1
struct clk_hw { int dummy; };
struct sprd_clk_common { void *regmap; unsigned reg; };
struct sprd_gate { unsigned flags, enable_mask; struct sprd_clk_common common; };
static struct sprd_gate gate;
static struct clk_hw parent;
static int parent_present, parent_enabled, reads;
static unsigned value;
#define hw_to_sprd_gate(hw) (&gate)
static struct clk_hw *clk_hw_get_parent(struct clk_hw *hw) { (void)hw; return parent_present ? &parent : NULL; }
static int clk_hw_is_enabled(struct clk_hw *hw) { (void)hw; return parent_enabled; }
static int regmap_read(void *map, unsigned reg, unsigned *out) { (void)map; (void)reg; reads++; *out=value; return 0; }
''' + core[start:end] + '''
int main(void) {
 struct clk_hw hw; gate.flags=SPRD_GATE_NON_AON; gate.enable_mask=4;
 assert(sprd_gate_is_enabled(&hw)==0 && reads==0);
 parent_present=1;
 assert(sprd_gate_is_enabled(&hw)==0 && reads==0);
 parent_enabled=1; value=4;
 assert(sprd_gate_is_enabled(&hw)==1 && reads==1);
 value=0; assert(sprd_gate_is_enabled(&hw)==0 && reads==2);
 gate.flags=0; parent_enabled=0; value=4;
 assert(sprd_gate_is_enabled(&hw)==1 && reads==3);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'test.c').write_text(fixture)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror',
                            str(root / 'test.c'), '-o', str(root / 'test')], check=True)
            subprocess.run([str(root / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
