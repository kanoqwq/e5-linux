#!/usr/bin/env python3
"""Compile the actual T512N setters/getters against a simulated stock register map."""
from pathlib import Path
import runpy
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
apply = runpy.run_path(str(HERE/'charger.py'))['apply']
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    supply = root/'drivers/power/supply'
    supply.mkdir(parents=True)
    path = supply/'bq2560x-charger.c'
    path.write_text('''
#include <stdint.h>
#include <assert.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define BQ2560X_REG_2 2
#define BQ2560X_REG_ICHG_MASK 63
struct bq2560x_charger_info { u8 reg; int read_error; };
static int bq2560x_read(struct bq2560x_charger_info *info, int addr, u8 *val)
{ assert(addr == 2); if(info->read_error) return -EIO; *val=info->reg; return 0; }
static int bq2560x_update_bits(struct bq2560x_charger_info *info, int addr, u8 mask, u8 val)
{ assert(addr == 2); info->reg=(info->reg & ~mask) | (val & mask); return 0; }
static int bq2560x_charger_set_current(struct bq2560x_charger_info *info, u32 cur) { return 0; }
static int bq2560x_charger_get_current(struct bq2560x_charger_info *info, u32 *cur) { return 0; }
int main(void) {
 struct bq2560x_charger_info info = {.reg=0x80}; u32 current=0;
 assert(!bq2560x_charger_set_current(&info,500000)); assert(info.reg==0x88);
 assert(!bq2560x_charger_get_current(&info,&current)); assert(current==500000);
 assert(t512n_charge_code(58)==0 && t512n_charge_code(59)==1);
 assert(t512n_charge_code(815)==13 && t512n_charge_code(816)==14);
 assert(t512n_charge_code(2875)==50 && t512n_charge_code(4000)==50);
 for(unsigned ma=0;ma<=5000;ma++) {
  assert(!bq2560x_charger_set_current(&info,ma*1000));
  assert((info.reg & 0xc0)==0x80);
  assert(!bq2560x_charger_get_current(&info,&current));
  assert(current<=2875000 && current<=ma*1000);
 }
 info.read_error=1; current=123;
 assert(bq2560x_charger_get_current(&info,&current)==-EIO && current==123);
}
''')
    apply(root)
    before=path.read_bytes()
    apply(root)
    assert path.read_bytes()==before
    subprocess.run(['cc','-Wall','-Wextra','-Werror',str(path),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('T512N stock current map: actual C setters/getters, 5001 currents, I/O error, idempotency PASS')
