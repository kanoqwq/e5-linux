#!/usr/bin/env python3
"""Exercise SET/CLEAR command semantics, including unchanged SET readback."""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
extract=runpy.run_path(str(HERE/'test_fuel_gauge.py'))['function']

STUBS=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
typedef unsigned int u32;
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define UMP96XX_FGU_CAP_INTEGER_MASK 0xff
#define UMP96XX_FGU_CAP_DECIMAL_MASK 0xf
#define UMP96XX_FGU_CAP_DECIMAL_SHIFT 8
#define UMP96XX_FGU_CAP_AREA_MASK 0xfff
#define UMP96XX_FGU_MODE_AREA_MASK 0xf000
#define UMP96XX_FGU_MODE_AREA_SHIFT 12
#define UMP96XX_FGU_FCC_PERCENT 1000
#define UMP96XX_FGU_MAGIC_NUMBER 0x5a5aa5a5
#define UMP96XX_FGU_USER_AREA_SET0 0xa0
#define UMP96XX_FGU_USER_AREA_CLEAR0 0xa4
#define UMP96XX_FGU_USER_AREA_STATUS0 0xa8
#define UMP96XX_FGU_USER_AREA_SET1 0xc0
#define UMP96XX_FGU_USER_AREA_CLEAR1 0xc4
#define UMP96XX_FGU_USER_AREA_STATUS1 0xc8
struct sprd_fgu_info {int regmap,base,dev;};
static unsigned status[2]={0x5000,0xa000},set[2]={0x5000,0xa000},clear[2],pending[2];
static int operations,fail_at,writes;
static int regmap_read(int map,int reg,unsigned *value) {
 (void)map;
 int i=reg>=0xc0;int base=i?0xc0:0xa0;
 *value=reg==base?set[i]:reg==base+4?clear[i]:status[i];return 0;
}
static int write_reg(int reg,unsigned value) {
 if(++operations==fail_at)return -EIO;
 int i=reg>=0xc0;int base=i?0xc0:0xa0;
 writes++;
 if(reg==base){set[i]=value;pending[i]=value;}
 else if(reg==base+4){
  if(value)pending[i]=status[i]&~value;
  else status[i]=pending[i];
  clear[i]=value;
 }else assert(0);
 return 0;
}
static int update(int reg,unsigned mask,unsigned value,bool force) {
 unsigned old;regmap_read(0,reg,&old);unsigned now=(old&~mask)|(value&mask);
 return (force||now!=old)?write_reg(reg,now):0;
}
static int regmap_update_bits(int map,int reg,unsigned mask,unsigned value){(void)map;return update(reg,mask,value,false);}
static int regmap_write_bits(int map,int reg,unsigned mask,unsigned value){(void)map;return update(reg,mask,value,true);}
static void usleep_range(int low,int high){assert(low==200&&high==210);}
'''

TESTS=r'''
int main(void) {
 struct sprd_fgu_info info={0};int cap;
 for(int value=0;value<=1000;value++) {
  for(int repeat=0;repeat<3;repeat++) {
   writes=0;assert(!ump96xx_fgu_save_last_cap(&info,value));
   assert(!ump96xx_fgu_read_last_cap(&info,&cap)&&cap==value&&writes==3);
   assert((status[0]&0xf000)==0x5000);
   writes=0;assert(!ump96xx_fgu_save_normal_temperature_cap(&info,value));
   assert(!ump96xx_fgu_read_normal_temperature_cap(&info,&cap)&&cap==value&&writes==3);
   assert((status[1]&0xf000)==0xa000);
  }
 }
 for(int step=1;step<=3;step++) {
  clear[0]=0;
  operations=0;fail_at=step;
  assert(ump96xx_fgu_save_last_cap(&info,500)==-EIO);
 }
 fail_at=0;
 assert(!ump96xx_fgu_save_last_cap(&info,500));
 assert(!ump96xx_fgu_read_last_cap(&info,&cap)&&cap==500);
 puts("PASS: all 1001 SOC values repeated three times per bank, boot bits and bus failures");
}
'''


def main():
    kernel=Path(sys.argv[1]).resolve()
    apply=runpy.run_path(str(HERE/'fuel-gauge.py'))['apply']
    names=[l[6:] for l in (HERE/'fuel-gauge-state.patch').read_text().splitlines() if l.startswith('+++ b/')]
    with tempfile.TemporaryDirectory(prefix='e5-latch-test-') as temp:
        root=Path(temp)
        for name in names:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(subprocess.check_output(['git','-c','safe.directory='+str(kernel),
                                                  '-C',str(kernel),'show','HEAD:'+name]))
        apply(root)
        source=(root/names[0]).read_text()
        body='\n'.join(extract(source,n) for n in ('ump96xx_fgu_save_last_cap',
            'ump96xx_fgu_save_normal_temperature_cap','ump96xx_fgu_read_last_cap',
            'ump96xx_fgu_read_normal_temperature_cap'))
        for fixed in (True,False):
            p=root/'latch.c';exe=root/'latch'
            code=body if fixed else body.replace('regmap_write_bits(', 'regmap_update_bits(')
            p.write_text(STUBS+code+TESTS)
            subprocess.run(['cc','-Wall','-Werror','-Wno-unused-function',str(p),'-o',str(exe)],check=True)
            result=subprocess.run([str(exe)],capture_output=True,text=True)
            if fixed:
                assert result.returncode==0,result.stderr
                print(result.stdout,end='')
            else:
                assert result.returncode!=0,'unchanged SET write suppression was not detected'
                print('PASS: reproducer rejects the old skipped-SET implementation')


if __name__=='__main__':main()
