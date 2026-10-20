"""Compile the inserted stock OCV gate, including boundary and I/O failure paths."""
from pathlib import Path
import os,runpy,subprocess,tempfile,unittest

BOARD=Path(__file__).resolve().parent


class StockOCV(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','C fixture runs in Linux/WSL')
    def test_stale_pocv_is_rejected_without_replacing_retained_soc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'drivers/power/supply/sprd_fuel_gauge_core.c';path.parent.mkdir(parents=True)
            path.write_text('''static void sprd_fgu_boot_cap_calibration(struct sprd_fgu_data *data, int pocv_uv) {
int ret;
data->marker=44;
\tdata->track.pocv_info.valid = true;
}
/* next */
''')
            apply=runpy.run_path(str(BOARD/'fuel-gauge.py'))['apply'];apply(root);once=path.read_bytes();apply(root)
            self.assertEqual(once,path.read_bytes())
            source='''#include <assert.h>
#include <stdbool.h>
typedef long long s64;
struct sprd_fgu_data { int marker, soc; struct { struct { bool valid; } pocv_info; } track; };
static int error, live;
static int sprd_fgu_get_vbat_ocv(struct sprd_fgu_data *data,int *value){(void)data;*value=live;return error;}
#define dev_warn(...) ((void)0)
'''+path.read_text()+'''
int main(void){
struct sprd_fgu_data data={.soc=760};live=4200;
int samples[]={3517000,3899999,3900000,4200000,4500000,4500001};
for(unsigned i=0;i<sizeof(samples)/sizeof(samples[0]);i++){
 data.track.pocv_info.valid=false;
 sprd_fgu_boot_cap_calibration(&data,samples[i]);
 assert(data.track.pocv_info.valid==(samples[i]>=3900000&&samples[i]<=4500000));
 assert(data.soc==760);
}
error=-5;data.track.pocv_info.valid=false;sprd_fgu_boot_cap_calibration(&data,4200000);
assert(!data.track.pocv_info.valid&&data.soc==760);
}
'''
            fixture=root/'gate.c';fixture.write_text(source)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(fixture),'-o',str(root/'gate')],check=True)
            subprocess.run([str(root/'gate')],check=True)


if __name__=='__main__':unittest.main()
