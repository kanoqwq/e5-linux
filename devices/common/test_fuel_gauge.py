#!/usr/bin/env python3
"""Offline tests using the patched driver's actual C functions and fake PMIC."""
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
apply = runpy.run_path(str(HERE/'fuel-gauge.py'))['apply']


def function(source, name):
    match = re.search(r'(?m)^static (?:int|void)\s+' + name + r'\([^;{}]*\)\s*\{', source)
    assert match, name
    start = source.index('{', match.start())
    end, depth = start + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#include <limits.h>
typedef uint32_t u32;
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define SPRD_FGU_FCC_PERCENT 1000
#define SPRD_FGU_NORMAL_POWERON 5
#define SPRD_FGU_MAGIC_NUMBER 0x5a5aa5a5
'''

REGISTER_STUBS = r'''
#define UMP96XX_FGU_CAP_INTEGER_MASK 0xff
#define UMP96XX_FGU_CAP_DECIMAL_MASK 0xf
#define UMP96XX_FGU_CAP_DECIMAL_SHIFT 8
#define UMP96XX_FGU_FCC_PERCENT 1000
#define UMP96XX_FGU_MAGIC_NUMBER SPRD_FGU_MAGIC_NUMBER
#define UMP96XX_FGU_CAP_AREA_MASK 0xfff
#define UMP96XX_FGU_USER_AREA_STATUS0 0xa8
#define UMP96XX_FGU_USER_AREA_STATUS1 0xc8
#define UMP96XX_FGU_USER_AREA_CLEAR0 0xa4
#define UMP96XX_FGU_USER_AREA_CLEAR1 0xc4
#define UMP96XX_FGU_USER_AREA_SET0 0xa0
#define UMP96XX_FGU_USER_AREA_SET1 0xc0
struct sprd_fgu_info { int regmap,base,dev; };
static unsigned register_value, encoded_value; static int read_error,writes;
static int regmap_read(int map,int address,unsigned *value){(void)map;(void)address;*value=register_value;return read_error;}
static int regmap_write_bits(int map,int address,unsigned mask,unsigned value){(void)map;(void)mask;writes++;if(address==0xa0||address==0xc0)encoded_value=value;return 0;}
static int regmap_update_bits(int map,int address,unsigned mask,unsigned value){return regmap_write_bits(map,address,mask,value);}
static void usleep_range(int low,int high){(void)low;(void)high;}
'''
REGISTER_MAIN = r'''
int main(void) {
 struct sprd_fgu_info info={0};int valid=0,cap;
 for(unsigned raw=0;raw<4096;raw++) {
  unsigned whole=raw&255,tenths=raw>>8;
  bool expected=whole<=100&&tenths<=9&&!(whole==100&&tenths);
  register_value=raw|0x5000;cap=-123;
  int ret=ump96xx_fgu_read_last_cap(&info,&cap);
  assert((ret==0)==expected);
  if(expected){assert(cap==(int)(whole*10+tenths));valid++;}
  else assert(ret==-ERANGE && cap==-123);
  cap=-123;ret=ump96xx_fgu_read_normal_temperature_cap(&info,&cap);
  assert((ret==0)==expected);
 }
 assert(valid==1001);
 for(int value=0;value<=1000;value++) {
  writes=0;assert(!ump96xx_fgu_save_last_cap(&info,value));
  assert(writes==3&&encoded_value==(unsigned)((value/10)|((value%10)<<8)));
  writes=0;assert(!ump96xx_fgu_save_normal_temperature_cap(&info,value));assert(writes==3);
 }
 writes=0;assert(!ump96xx_fgu_save_last_cap(&info,630));assert(!ump96xx_fgu_save_last_cap(&info,630));assert(writes==6);
 int bad[]={-1,1001,INT_MAX};
 for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++) {
  writes=0;assert(ump96xx_fgu_save_last_cap(&info,bad[i])==-ERANGE&&!writes);
  assert(ump96xx_fgu_save_normal_temperature_cap(&info,bad[i])==-ERANGE&&!writes);
 }
 writes=0;assert(!ump96xx_fgu_save_normal_temperature_cap(&info,SPRD_FGU_MAGIC_NUMBER)&&!writes);
 read_error=-EIO;assert(ump96xx_fgu_read_last_cap(&info,&cap)==-EIO);
 puts("PASS: all 4096 retained encodings, 1001 valid SOC values, factory masked-write format, invalid writes and bus errors");
}
'''

CORE_STUBS = r'''
struct sprd_fgu_info;
struct ops {
 bool (*is_first_poweron)(struct sprd_fgu_info *);
 int (*read_last_cap)(struct sprd_fgu_info *,int *);
 int (*read_normal_temperature_cap)(struct sprd_fgu_info *,int *);
 int (*save_last_cap)(struct sprd_fgu_info *,int);
 int (*save_normal_temperature_cap)(struct sprd_fgu_info *,int);
 int (*save_boot_mode)(struct sprd_fgu_info *,int);
};
struct sprd_fgu_info { struct ops *ops; };
struct sprd_fgu_data {struct sprd_fgu_info *fgu_info;int dev,boot_volt_uv,cap_table,table_len,normal_temp_cap,boot_cap;bool is_first_poweron;};
static int retained_ui,retained_normal,ui_read_error,normal_read_error,voltage_error,ui_writes,normal_writes,mode_writes;
static int fail_ui_writes,fail_normal_writes,mismatch_ui_reads,mismatch_normal_reads,temp_loss;
static int settle_ui,settle_normal,settle_waits;
#define read_poll_timeout(op,val,cond,sleep,timeout,before,...) ({ \
 int status=-ETIMEDOUT; \
 assert((sleep)==200&&(timeout)==2000&&!(before)); \
 for(int elapsed=0;elapsed<=timeout;elapsed+=sleep) { \
  val=op(__VA_ARGS__);if(cond){status=0;break;}settle_waits++; \
 } status; })
static bool first_power;
static bool first(struct sprd_fgu_info *i){(void)i;return first_power;}
static int read_ui(struct sprd_fgu_info *i,int *v){(void)i;*v=retained_ui+(mismatch_ui_reads?1:0);if(settle_ui>0){settle_ui--;*v=0;}return ui_read_error;}
static int read_normal(struct sprd_fgu_info *i,int *v){(void)i;*v=retained_normal+(mismatch_normal_reads?1:0);if(settle_normal>0){settle_normal--;*v=0;}return normal_read_error;}
static int save_ui(struct sprd_fgu_info *i,int v){(void)i;ui_writes++;if(fail_ui_writes>0){fail_ui_writes--;return -EIO;}retained_ui=v;return 0;}
static int save_normal(struct sprd_fgu_info *i,int v){(void)i;normal_writes++;if(fail_normal_writes>0){fail_normal_writes--;return -EIO;}retained_normal=v;return 0;}
static int save_mode(struct sprd_fgu_info *i,int mode){(void)i;assert(mode==5);mode_writes++;return 0;}
static int sprd_fgu_get_boot_voltage(struct sprd_fgu_data *d,int *v){(void)d;*v=3800000;return voltage_error;}
static int sprd_fgu_ocv2cap(int table,int len,int voltage){(void)table;(void)len;(void)voltage;return 420;}
static void sprd_fgu_boot_cap_calibration(struct sprd_fgu_data *d,int ocv,int voltage,int *cap){(void)d;(void)ocv;(void)voltage;(void)cap;}
static void sprd_fgu_capacity_loss_by_temperature(struct sprd_fgu_data *d,int *cap){(void)d;*cap-=temp_loss;}
static int sprd_fgu_capacity_remap(struct sprd_fgu_data *d,int cap){(void)d;return cap;}
static struct ops ops={first,read_ui,read_normal,save_ui,save_normal,save_mode};
static struct sprd_fgu_info info={&ops};
static struct sprd_fgu_data fresh(void) {
 retained_ui=retained_normal=630;ui_read_error=normal_read_error=voltage_error=0;
 ui_writes=normal_writes=mode_writes=0;fail_ui_writes=fail_normal_writes=0;
 mismatch_ui_reads=mismatch_normal_reads=temp_loss=0;first_power=false;
 settle_ui=settle_normal=settle_waits=0;
 return (struct sprd_fgu_data){.fgu_info=&info};
}
'''
CORE_MAIN = r'''
int main(void) {
 struct sprd_fgu_data d;int cap;
 d=fresh();assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(cap==630&&d.boot_cap==630&&!ui_writes&&normal_writes==1);
 d=fresh();ui_read_error=-ERANGE;assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(cap==630&&d.boot_cap==630&&retained_ui==630);
 d=fresh();normal_read_error=-ERANGE;assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(cap==630&&d.boot_cap==630);
 d=fresh();retained_ui=retained_normal=2565;assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(cap==420&&d.boot_cap==420&&retained_ui==420&&retained_normal==420);
 d=fresh();ui_read_error=normal_read_error=-ERANGE;assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(cap==420&&d.boot_cap==420);
 d=fresh();first_power=true;temp_loss=30;assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(cap==420&&d.normal_temp_cap==420&&d.boot_cap==390&&d.is_first_poweron);
 d=fresh();retained_ui=retained_normal=1000;assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(cap==1000&&d.boot_cap==1000);
 d=fresh();retained_ui=retained_normal=0;assert(!sprd_fgu_get_boot_capacity(&d,&cap));assert(!cap&&!d.boot_cap);
 d=fresh();ui_read_error=-EIO;assert(sprd_fgu_get_boot_capacity(&d,&cap)==-EIO&&!ui_writes&&!normal_writes);
 d=fresh();normal_read_error=-EIO;assert(sprd_fgu_get_boot_capacity(&d,&cap)==-EIO&&!ui_writes&&!normal_writes);
 d=fresh();voltage_error=-EIO;assert(sprd_fgu_get_boot_capacity(&d,&cap)==-EIO&&!ui_writes&&!normal_writes);
 puts("PASS: preserve valid saved SOC, recover invalid field/pair, first-boot temperature semantics, true empty/full and read errors");
 d=fresh();d.normal_temp_cap=600;assert(!sprd_fgu_save_capacity(&d,610));assert(retained_ui==610&&retained_normal==600&&ui_writes==1&&normal_writes==1);
 d=fresh();d.normal_temp_cap=1000;settle_ui=settle_normal=1;
 assert(!sprd_fgu_save_capacity(&d,1000)&&ui_writes==1&&normal_writes==1&&settle_waits==2);
 d=fresh();d.normal_temp_cap=1000;settle_ui=settle_normal=5;
 assert(!sprd_fgu_save_capacity(&d,1000)&&ui_writes==1&&normal_writes==1&&settle_waits==10);
 d=fresh();d.normal_temp_cap=0;assert(!sprd_fgu_save_capacity(&d,0)&&!settle_waits);
 d=fresh();d.normal_temp_cap=600;fail_ui_writes=1;assert(!sprd_fgu_save_capacity(&d,610));assert(ui_writes==2&&normal_writes==1);
 d=fresh();d.normal_temp_cap=600;fail_ui_writes=2;assert(sprd_fgu_save_capacity(&d,610)==-EIO);assert(ui_writes==2&&!normal_writes);
 d=fresh();d.normal_temp_cap=600;mismatch_ui_reads=1;assert(sprd_fgu_save_capacity(&d,610)==-ETIMEDOUT);assert(ui_writes==2&&!normal_writes);
 d=fresh();d.normal_temp_cap=600;mismatch_normal_reads=1;assert(sprd_fgu_save_capacity(&d,610)==-ETIMEDOUT);assert(normal_writes==2);
 d=fresh();d.normal_temp_cap=1001;assert(sprd_fgu_save_capacity(&d,610)==-ERANGE&&!ui_writes&&!normal_writes);
 d=fresh();assert(sprd_fgu_save_capacity(&d,-1)==-ERANGE&&!ui_writes);assert(sprd_fgu_save_capacity(&d,1001)==-ERANGE&&!ui_writes);
 d=fresh();d.normal_temp_cap=SPRD_FGU_MAGIC_NUMBER;assert(!sprd_fgu_save_capacity(&d,610));assert(ui_writes==1&&!normal_writes);
 puts("PASS: bounded write/readback retry, failure propagation, invalid-state rejection and preserved sentinel");
}
'''

SHUTDOWN_STUBS = r'''
#define WRITE_ONCE(p,v) ((p)=(v))
#define CM_UVLO_CALIBRATION_CNT_THRESHOLD 3
struct charger_manager;
struct info_ops { void (*shutdown)(void *); };
struct info {struct info_ops *ops;};
struct desc { int uvlo_trigger_cnt,cap; };
struct charger_manager {bool shutdown_flag;struct desc *desc;struct info *fchg_info,*vchg_info;int dev,fullbatt_vchk_work,cap_update_work,uvlo_work;};
struct platform_device {struct charger_manager *cm;};
static int cm_monitor_work=1,stopped,saved;
static struct charger_manager *platform_get_drvdata(struct platform_device *p){return p->cm;}
static void cancel_delayed_work_sync(int *work){assert(*work!=4);stopped|=1<<*work;}
static void cancel_delayed_work(int *work){assert(*work==4);stopped|=1<<*work;}
static int set_batt_cap(struct charger_manager *cm,int cap){assert(cm->shutdown_flag&&stopped==30&&cap==630);saved++;return 0;}
'''
SHUTDOWN_MAIN = r'''
int main(void) {
 struct info info={0};struct desc desc={.cap=630};
 struct charger_manager cm={.desc=&desc,.fchg_info=&info,.vchg_info=&info,.fullbatt_vchk_work=2,.cap_update_work=3,.uvlo_work=4};
 struct platform_device p={&cm};charger_manager_shutdown(&p);assert(saved==1&&stopped==30);
 desc.uvlo_trigger_cnt=3;charger_manager_shutdown(&p);assert(saved==1);
 puts("PASS: writers stop before final save, UVLO cannot cancel itself synchronously, low-voltage policy preserved");
}
'''


REMOVE_STUBS = r'''
struct power_supply { bool alive; };
struct charger_manager;
struct ops {void (*remove)(void *);};
struct info {struct ops *ops;};
struct charger_manager {int entry,cap_update_work,fullbatt_vchk_work,uvlo_work;
 struct info *fchg_info,*vchg_info; struct power_supply *charger_psy;};
struct platform_device {struct charger_manager *cm;};
static int cm_list_mtx,setup_polling,cm_monitor_work,cancels,disabled;
static struct charger_manager *platform_get_drvdata(struct platform_device *p){return p->cm;}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void list_del(int *p){(void)p;assert(cm_list_mtx);}
static void cancel_work_sync(int *p){(void)p;cancels++;}
static void cancel_delayed_work_sync(int *p){(void)p;cancels++;}
static void try_charger_enable(struct charger_manager *c,bool on){assert(!on&&c->charger_psy->alive);disabled++;}
'''

REMOVE_MAIN = r'''
int main(void){
 struct power_supply psy={true};struct info info={0};
 struct charger_manager cm={.fchg_info=&info,.vchg_info=&info,.charger_psy=&psy};
 struct platform_device dev={&cm};charger_manager_remove(&dev);
 assert(cancels==5&&disabled==1&&psy.alive);
 /* devres owns the only release, after remove has finished. */
 psy.alive=false;
 puts("PASS: CM remove stops writers and disables charging before its single devres release");
}
'''


CHARGE_STUBS = r'''
typedef long long s64;
#define CM_CAP_FULL_PERCENT 1000
struct desc {int cap;};struct charger_manager {int dev;struct desc *desc;};
static int total=5000000,read_error;
static int get_batt_total_cap(struct charger_manager *c,int *v){(void)c;*v=total;return read_error;}
static long long div_s64(long long value,int divisor){return value/divisor;}
'''
CHARGE_MAIN = r'''
int main(void){
 struct desc d={0};struct charger_manager cm={.desc=&d};int result;
 int sizes[]={2000000,3100000,4000000,5000000,6000000,10000000,20000000};
 for(unsigned n=0;n<sizeof(sizes)/sizeof(sizes[0]);n++)for(d.cap=0;d.cap<=1000;d.cap++){
  total=sizes[n];assert(!cm_get_charge_now(&cm,&result));
  assert(result==(long long)total*d.cap/1000);
 }
 total=5000000;d.cap=948;assert(!cm_get_charge_now(&cm,&result)&&result==4740000);
 read_error=-EIO;assert(cm_get_charge_now(&cm,&result)==-EIO);
 puts("PASS: charge_now 2-20 Ah at all 1001 SOC values, 94.8% regression and read errors; no signed multiply overflow");
}
'''


def main():
    kernel=Path(sys.argv[1]).resolve()
    paths=[line[6:] for line in (HERE/'fuel-gauge-state.patch').read_text().splitlines() if line.startswith('+++ b/')]
    with tempfile.TemporaryDirectory(prefix='fgu-test-') as directory:
        root=Path(directory)
        for name in paths:
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(subprocess.check_output(['git','-c','safe.directory='+str(kernel),'-C',str(kernel),'show','HEAD:'+name]))
        apply(root)
        before={name:(root/name).read_bytes() for name in paths};apply(root)
        assert before=={name:(root/name).read_bytes() for name in paths}
        hardware=(root/paths[0]).read_text();core=(root/paths[1]).read_text();cm=(root/paths[2]).read_text()
        cases=[
            ('registers',REGISTER_STUBS,'\n'.join(function(hardware,n) for n in ('ump96xx_fgu_read_last_cap','ump96xx_fgu_read_normal_temperature_cap','ump96xx_fgu_save_last_cap','ump96xx_fgu_save_normal_temperature_cap')),REGISTER_MAIN),
            ('restore',CORE_STUBS,function(core,'sprd_fgu_get_boot_capacity')+'\n'+function(core,'sprd_fgu_read_saved_cap')+'\n'+function(core,'sprd_fgu_save_capacity'),CORE_MAIN),
            ('shutdown',SHUTDOWN_STUBS,function(cm,'charger_manager_shutdown'),SHUTDOWN_MAIN),
            ('remove',REMOVE_STUBS,function(cm,'charger_manager_remove'),REMOVE_MAIN),
            ('charge-now',CHARGE_STUBS,function(cm,'cm_get_charge_now'),CHARGE_MAIN),
        ]
        for name,stubs,body,main_code in cases:
            source=root/(name+'.c');source.write_text(PRELUDE+stubs+body+main_code)
            executable=root/name
            subprocess.run(['cc','-std=gnu11','-Wall','-Werror','-Wno-unused-but-set-variable',str(source),'-o',str(executable)],check=True)
            subprocess.run([str(executable)],check=True)
        broken=root/paths[0];broken.write_text(broken.read_text().replace('return -ERANGE;','return -EINVAL;',1))
        before={name:(root/name).read_bytes() for name in paths}
        try:apply(root)
        except ValueError:pass
        else:raise AssertionError('Changed vendor source accepted')
        assert before=={name:(root/name).read_bytes() for name in paths}
        print('PASS: idempotence and all-or-nothing source validation')


if __name__=='__main__':main()
