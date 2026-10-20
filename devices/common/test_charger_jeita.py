#!/usr/bin/env python3
"""Run the patched CM JEITA functions against startup and thermal transitions."""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
extract = runpy.run_path(str(HERE/'test_fuel_gauge.py'))['function']


def main():
    source = (Path(sys.argv[1])/'drivers/power/supply/charger-manager.c').read_text()
    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define dev_info(...) ((void)0)
struct cm_jeita_info {int temp_up_trigger,temp_down_trigger,jeita_status,jeita_temperature;bool jeita_changed;};
struct zone {int temp,recovery_temp,current_ua,term_volt;};
struct charger_desc {struct cm_jeita_info jeita_info;struct zone *jeita_tab;int jeita_tab_size;};
struct charger_manager {struct charger_desc *desc;int dev;};
static void cm_manager_set_ffs(struct charger_manager *cm,int t){(void)cm;(void)t;}
'''
    body='\n'.join(extract(source, n) for n in (
        'cm_jeita_temp_goes_down','cm_jeita_temp_goes_up',
        'jeita_info_init','cm_manager_get_jeita_status'))
    tests=r'''
static struct zone zones[]={{0,20,0,0},{150,130,1000000,4200000},
                           {450,430,500000,4100000},{600,580,0,0}};
static struct charger_desc d={.jeita_tab=zones,.jeita_tab_size=4};
static struct charger_manager cm={.desc=&d};
static int sample(int temperature) {
 int zone=cm_manager_get_jeita_status(&cm,temperature);
 d.jeita_info.jeita_status=zone;d.jeita_info.jeita_temperature=temperature;
 d.jeita_info.jeita_changed=false;return zone;
}
int main(void) {
 /* Warm boot: inherited overheat state must not survive reinitialization. */
 for(int t=21;t<600;t++) {
  d.jeita_info=(struct cm_jeita_info){.jeita_status=4,.jeita_temperature=250,.jeita_changed=true};
  int zone=sample(t);if(zone<=0||zone>=4)fprintf(stderr,"bad startup temp=%d zone=%d\n",t,zone);assert(zone>0&&zone<4);
 }
 for(int t=-100;t<=0;t++){jeita_info_init(&d.jeita_info);assert(sample(t)==0);}
 for(int t=600;t<=800;t++){jeita_info_init(&d.jeita_info);assert(sample(t)==4);}
 jeita_info_init(&d.jeita_info);assert(sample(320)==2);assert(sample(397)==2);
 assert(sample(610)==4);assert(sample(590)==4);assert(sample(570)==3);
 assert(sample(440)==3);assert(sample(420)==2);
 assert(sample(-10)==0);assert(sample(10)==0);assert(sample(30)==1);
 /* A charger/table switch restarts classification using the stock baseline. */
 d.jeita_info.jeita_changed=true;assert(sample(320)==2);
 assert(sample(320)==2);assert(sample(350)==2);assert(sample(300)==2);
 /* This E5's actual DT (temperature fields are stored with +1000). */
 struct zone e5[]={{0,0,0,4340000},{150,150,650000,4340000},
                  {450,450,1650000,4370000},{550,550,650000,4340000}};
 d.jeita_tab=e5;
 for(int t=1;t<550;t++) {
  d.jeita_info=(struct cm_jeita_info){.jeita_status=4,.jeita_temperature=250,.jeita_changed=true};
  int zone=sample(t);assert(zone>0&&zone<4);
 }
 jeita_info_init(&d.jeita_info);assert(sample(397)==2);assert(sample(307)==2);
 assert(sample(550)==4);assert(sample(540)==3);assert(sample(0)==0);assert(sample(10)==1);
 puts("PASS: 579 warm starts, true hot/cold stops, hysteresis and charger/table switches");
 puts("PASS: 549 starts using the connected E5's actual DT temperature/voltage table");
}
'''
    with tempfile.TemporaryDirectory(prefix='e5-jeita-test-') as temp:
        path=Path(temp)/'test.c';path.write_text(prelude+body+tests)
        exe=Path(temp)/'test'
        subprocess.run(['cc','-Wall','-Werror',str(path),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)


if __name__ == '__main__':
    main()
