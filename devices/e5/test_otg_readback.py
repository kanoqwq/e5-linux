#!/usr/bin/env python3
"""Execute actual AW regulator/read-bit code; no I2C or device access."""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
source = (Path(sys.argv[1])/'drivers/power/supply/aw32257_charger.c').read_text()
extract = runpy.run_path(str(HERE.parent/'t551m/test_wifi_tx.py'))['function']
replace = runpy.run_path(str(HERE.parent/'t551m/prepare-kernel.py'))['replace_function']
apply = runpy.run_path(str(HERE/'charger.py'))['apply_state']

prelude = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#define AW32257_REG_CONTROL 1
#define AW32257_BIT_OPA_MODE 0
#define aw32257_CON1 1
#define CON0_OTG_MASK 1
#define CON1_OTG_SHIFT 3
#define BIT(n) (1U << (n))
typedef unsigned char u8;
struct aw32257_device { int unused; };
struct regulator_dev { struct aw32257_device *data; };
static struct aw32257_device *rdev_get_drvdata(struct regulator_dev *d) { return d->data; }
static int control, failure;
static int aw32257_i2c_read(struct aw32257_device *b, u8 reg) {
 (void)b;assert(reg==AW32257_REG_CONTROL);return failure?failure:control;
}
'''
checks = r'''
int main(void) {
 struct aw32257_device b={0};struct regulator_dev d={&b};int wrong=0;
 for(control=0;control<256;control++) {
  int got=aw32257_otg_charger_vbus_is_enabled(&d);
  if(EXPECT_BUG) { assert(got==0);wrong+=(got!=!!(control&BIT(0))); }
  else assert(got==!!(control&BIT(0)));
 }
 assert(wrong==(EXPECT_BUG?128:0));
 failure=-EIO;assert(aw32257_otg_charger_vbus_is_enabled(&d)==-EIO);
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='e5-otg-readback-') as folder:
    root=Path(folder)
    def execute(text, buggy):
        names=['aw32257_i2c_read_mask']
        if not buggy:names.append('aw32257_i2c_read_bit')
        names.append('aw32257_otg_charger_vbus_is_enabled')
        (root/'test.c').write_text(prelude+'\n'.join(extract(text,n) for n in names)+
            '\n#define EXPECT_BUG '+str(int(buggy))+'\n'+checks)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
        subprocess.run([str(root/'test')],check=True)
    if 'e5-aw32257-otg-readback-v1' not in source:
        execute(source,True)
    candidate=root/'charger.c';candidate.write_text(source)
    apply(candidate,replace)
    fixed=candidate.read_bytes();apply(candidate,replace)
    assert candidate.read_bytes()==fixed,'OTG fix is not idempotent'
    execute(candidate.read_text(),False)
print('PASS AW OTG readback: all 256 control values, unrelated bits, I/O errors and idempotent preparation')
