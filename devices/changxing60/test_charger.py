"""Execute the prepared charger's actual register helpers against bus stubs."""
from pathlib import Path
import os
import re
import runpy
import subprocess
import tempfile
import unittest

HERE=Path(__file__).resolve().parent


def function(source,name):
    match=re.search(r'static (?:int|bool)\s+'+name+r'\([^;{}]*\)\s*\{',source)
    if not match:raise ValueError('Missing helper '+name)
    end=source.index('{',match.start())+1
    depth=1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}')
        end+=1
    return source[match.start():end]


class Charger(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','Linux C compiler required')
    def test_factory_voltage_and_hardware_status_with_bus_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            supply=root/'drivers/power/supply'
            supply.mkdir(parents=True)
            target=supply/'bq2560x-charger.c'
            (supply/'sprd_battery_info.c').write_text(
                '\tof_property_read_u32_index(battery_np, "charge-dcp-current-microamp", 1,\n'
                '\t\t\t\t   &info->cur.dcp_limit);\n')
            target.write_bytes((HERE.parent/'m8/port/drivers/power/supply/bq2560x-charger.c').read_bytes())
            for name in ('Kconfig','Makefile'):(supply/name).write_text('')
            runpy.run_path(str(HERE/'charger.py'))['apply'](root)
            code=target.read_text()
            fixture='''#include <assert.h>
#include <errno.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
struct bq2560x_charger_info { int unused; };
#define BIT(n) (1u<<(n))
#define BQ2560X_REG_4 4
#define BQ2560X_REG_8 8
#define BQ2560X_REG_TERMINAL_VOLTAGE_MASK 0xf8
#define POWER_SUPPLY_STATUS_CHARGING 1
#define POWER_SUPPLY_STATUS_DISCHARGING 2
#define POWER_SUPPLY_STATUS_NOT_CHARGING 3
#define POWER_SUPPLY_STATUS_FULL 4
static u8 regs[12];
static int bus_error;
static int bq2560x_read(struct bq2560x_charger_info *i, u8 reg, u8 *value) {
    (void)i; assert(reg<12); if(bus_error) return -EIO; *value=regs[reg]; return 0;
}
static int bq2560x_update_bits(struct bq2560x_charger_info *i, u8 reg, u8 mask, u8 value) {
    (void)i; assert(reg<12); if(bus_error) return -EIO;
    regs[reg]=(regs[reg]&~mask)|(value&mask); return 0;
}
'''
            for name in ('bq2560x_charger_set_termina_vol',
                         'bq2560x_charger_get_termina_vol','bq2560x_charger_get_status'):
                fixture+=function(code,name)+'\n'
            fixture+='''int main(void) {
    struct bq2560x_charger_info i={0}; u32 value;
    for (u32 mv=0; mv<=6000; mv++) {
        /* Replay the captured AArch64 setter arithmetic (3e20..3e54). */
        unsigned packed=mv<3856?0:(mv>>4)>0x120?0xc0:(((mv+0xf0)>>2)+8)&~7u;
        regs[4]=7;
        assert(!bq2560x_charger_set_termina_vol(&i,mv));
        assert(regs[4]==((packed&0xf8)|7));
    }
    assert(!bq2560x_charger_set_termina_vol(&i,4400));
    assert(!bq2560x_charger_get_termina_vol(&i,&value) && value==4432);
    for(unsigned code=0; code<32; code++) {
        regs[4]=code<<3;
        assert(!bq2560x_charger_get_termina_vol(&i,&value));
        assert(value==(code==15?4352:3856+code*32));
    }
    for(unsigned reg=0; reg<256; reg++) {
        unsigned state=(reg>>3)&3;
        int want=!(reg&4)?2:state==0?3:state==3?4:1;
        regs[8]=reg;
        assert(bq2560x_charger_get_status(&i)==want);
    }
    bus_error=1; regs[4]=0x97;
    assert(bq2560x_charger_set_termina_vol(&i,4400)==-EIO && regs[4]==0x97);
    assert(bq2560x_charger_get_termina_vol(&i,&value)==-EIO);
    assert(bq2560x_charger_get_status(&i)==-EIO);
    return 0;
}
'''
            source=root/'test.c';source.write_text(fixture)
            binary=root/'test'
            subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)


    def test_dcp_input_ceiling_is_model_only_and_preserves_other_currents(self):
        policy=runpy.run_path(str(HERE/'charger.py'))['dcp_input_policy']
        original=''.join('\tof_property_read_u32_index(battery_np, "charge-'+kind+'-current-microamp", '+str(index)+',\n'
            '\t\t\t\t   &info->cur.'+kind+('_cur' if index==0 else '_limit')+');\n'
            for kind in ('sdp','dcp','cdp','unknown') for index in (0,1))
        changed=policy(original)
        self.assertEqual(policy(changed),changed)
        self.assertEqual(changed.replace('\n\t/* User-requested P720S20 DCP input ceiling; not battery charge current. */\n'
            '\tif (info->cur.dcp_limit > 0)\n\t\tinfo->cur.dcp_limit = 2500000;', ''), original)
        with self.assertRaises(ValueError):policy('unrecognized parser')
        if os.name=='nt':return
        fixture='''#include <assert.h>
#include <string.h>
struct currents {int sdp_cur,sdp_limit,dcp_cur,dcp_limit,cdp_cur,cdp_limit,unknown_cur,unknown_limit;};
struct info {struct currents cur;};
static int missing;
static void of_property_read_u32_index(void *np,const char *name,int index,int *value) {
    (void)np; if(missing) return;
    *value=!strcmp(name,"charge-sdp-current-microamp")?500000:1000000;
    if(!strcmp(name,"charge-cdp-current-microamp") && index==1)*value=1500000;
}
static void load(struct info *info) {void *battery_np=0;
'''+changed+'''}
int main(void) {
    struct info info={0};load(&info);
    assert(info.cur.dcp_limit==2500000 && info.cur.dcp_cur==1000000);
    assert(info.cur.sdp_limit==500000 && info.cur.cdp_limit==1500000 && info.cur.unknown_limit==1000000);
    missing=1;info.cur.dcp_limit=-22;load(&info);assert(info.cur.dcp_limit==-22);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'test.c').write_text(fixture)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True)


if __name__=='__main__':unittest.main()
