#!/usr/bin/env python3
"""Execute the generated AW32257 setters with register failures and USB budgets."""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
kernel = Path(sys.argv[1])
source = (kernel / 'drivers/power/supply/aw32257_charger.c').read_text()
extract = runpy.run_path(str(HERE.parent / 't551m/test_wifi_tx.py'))['function']
prelude = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
#define AW32257_CHARGER_DISABLE 1
#define AW32257_REG_CURRENT 4
#define AW32257_REG_CONTROL 1
#define AW32257_MASK_VI_CHRG 0x78
#define AW32257_MASK_RESET 0x80
#define AW32257_SHIFT_VI_CHRG 3
struct aw32257_device {
 int requested_charge_ua,input_budget_ua,charging;
 int pmic,charger_pd,charger_pd_mask,lock;
 struct {int resistor_sense;} init_data;
};
static unsigned char registers[11]={0xd0,0x30,0xae,0x53,1,0x22,0xfb,0x11,0xff,0,0};
static int failure,pmic_failure;
static int regmap_update_bits(int map,int reg,int mask,int value) {
 (void)map;(void)reg;(void)mask;(void)value;return pmic_failure?-EIO:0;
}
static void mutex_lock(int *lock){assert(!*lock);*lock=1;}
static void mutex_unlock(int *lock){assert(*lock);*lock=0;}
static int aw32257_exec_command(struct aw32257_device *b,int cmd) {
 (void)b;(void)cmd;if(failure)return -EIO;registers[1]|=4;return 0;
}
static int aw32257_i2c_write_mask(struct aw32257_device *b,int reg,int value,int mask,int shift) {
 (void)b;if(failure)return -EIO;assert(reg==4||reg==1);registers[reg]=(registers[reg]&~mask)|((value<<shift)&mask);return 0;
}
static int aw32257_i2c_read_mask(struct aw32257_device *b,int reg,int mask,int shift) {
 (void)b;return failure?-EIO:(registers[reg]&mask)>>shift;
}
enum power_supply_property {POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,
 POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT,POWER_SUPPLY_PROP_STATUS,
 POWER_SUPPLY_PROP_CALIBRATE,POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX};
union power_supply_propval {int intval;};
struct power_supply {struct aw32257_device *data;};
static void *power_supply_get_drvdata(struct power_supply *p) {return p->data;}
#define dev_err(...) ((void)0)
static int aw32257_set_battery_regulation_voltage(struct aw32257_device *b,int value) {
 (void)b;(void)value;return 0;
}
'''
tests = r'''
int main(void) {
 struct aw32257_device b={.requested_charge_ua=496000,.input_budget_ua=3000000,.charging=1,.init_data={33}};
 const int expected[]={496000,620000,868000,992000,1116000,1240000,1364000,1488000,
 1612000,1736000,1860000,1984000,2108000,2232000,2356000,2480000};
 for(int i=0;i<16;i++) {
  assert(aw32257_set_charge_current(&b,expected[i])==0);
  assert((registers[4]>>3)==i && (registers[4]&7)==1);
  assert(aw32257_get_charge_current(&b)==expected[i]/1000);
  assert(aw32257_current_code(expected[i]-1,33)==(i?i-1:-ERANGE));
 }
 assert(aw32257_set_charge_current(&b,INT_MAX)==0 && (registers[4]>>3)==15);
 assert(aw32257_set_current_limit(&b,500000)==0 && (registers[4]>>3)==0);
 assert(aw32257_get_current_limit(&b)==500);
 assert(aw32257_set_current_limit(&b,3000000)==0 && (registers[4]>>3)==15);
 assert(aw32257_set_charge_current(&b,650000)==0 && (registers[4]>>3)==1);
 failure=1;
 assert(aw32257_set_current_limit(&b,500000)==-EIO && b.input_budget_ua==3000000);
 assert(aw32257_get_charge_current(&b)==-EIO);
 failure=0;
 assert(aw32257_set_charge_current(&b,0)==0 && !b.charging && (registers[1]&4));
 assert(aw32257_current_code(-1,33)==-EINVAL && aw32257_current_code(500000,0)==-EINVAL);
 assert(registers[1]==0x34 && registers[2]==0xae && registers[6]==0xfb);
 b.requested_charge_ua=650000;
 struct power_supply psy={&b};union power_supply_propval v={.intval=1};
 assert(aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_CALIBRATE,&v)==0 && b.charging);
 v.intval=0;
 assert(aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_CALIBRATE,&v)==0 && !b.charging);
 failure=1;v.intval=1;
 assert(aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_CALIBRATE,&v)==-EIO && !b.charging);
 failure=0;
 /* Real CE may be enabled even while the software state says stopped. */
 registers[1]=0x30;b.charging=0;v.intval=0;
 assert(!aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_STATUS,&v));
 assert(registers[1]==0x34&&!b.charging&&!b.lock);
 /* Stock start clears HiZ/boost too, retaining all other control bits. */
 registers[1]=0x37;v.intval=1;
 assert(!aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_STATUS,&v));
 assert(registers[1]==0x30&&b.charging&&!b.lock);
 failure=1;v.intval=0;
 assert(aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_STATUS,&v)==-EIO&&b.charging&&!b.lock);
 failure=0;pmic_failure=1;
 assert(aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_STATUS,&v)==-EIO&&b.charging&&!b.lock);
 pmic_failure=0;v.intval=1;b.input_budget_ua=0;
 assert(aw32257_power_supply_set_property(&psy,POWER_SUPPLY_PROP_STATUS,&v)==-ERANGE&&!b.lock);
 puts("PASS: native current codes, USB budgets, real stop despite stale flag, stock start bits, failed commands preserve state and release mutex");
}
'''
# Avoid the prototype when looking up the setter body.
source = source.replace('static int aw32257_set_charge_current(struct aw32257_device *bq, int uA);', '')
functions = '\n'.join(extract(source, name) for name in (
    'aw32257_set_charge_current', 'aw32257_get_charge_current',
    'aw32257_set_current_limit', 'aw32257_get_current_limit',
    'aw32257_charger_start_charge','aw32257_charger_stop_charge',
    'aw32257_charger_set_status','aw32257_power_supply_set_property_locked',
    'aw32257_power_supply_set_property'))
with tempfile.TemporaryDirectory() as directory:
    c = Path(directory) / 'test.c'
    c.write_text(prelude + (HERE / 'charger-current.h').read_text() + functions + tests)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(c), '-o', directory + '/test'], check=True)
    subprocess.run([directory + '/test'], check=True)
