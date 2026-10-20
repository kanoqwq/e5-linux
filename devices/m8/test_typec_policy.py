"""Native legacy CC-policy tests; no USB device or PMIC is accessed."""
from pathlib import Path
import os
import runpy
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <sys/types.h>
typedef unsigned int u32;
struct device_attribute {int unused;};
struct device {void *data;};
struct variant {int mode;};
struct sc27xx_typec {struct device *dev;void *regmap,*edev;struct variant *var_data;
 int base,mode,state,policy_lock;bool partner_connected;};
enum {TYPEC_PORT_DFP,TYPEC_PORT_UFP,TYPEC_PORT_DRP};
enum {SC27XX_MODE_SNK,SC27XX_MODE_SRC,SC27XX_MODE_DRP};
enum {SC27XX_ATTACHED_SNK=2,SC27XX_ATTACHED_SRC=5,SC27XX_DEBUG_CABLE=6};
#define SC27XX_MODE_MASK 3
#define SC27XX_SW_CFG 0x54
#define SC27XX_CC1_SW_SWITCH_MASK (3<<12)
#define SC27XX_CC2_SW_SWITCH_MASK (3<<14)
#define SC27XX_CC1_SW_SWITCH(x) ((x)<<12)
#define SC27XX_CC2_SW_SWITCH(x) ((x)<<14)
#define SC27XX_TYPEC_NC 3
#define EXTCON_USB 1
#define EXTCON_USB_HOST 2
#define CONFIG_SPRD_TYPEC_TCPM 0
#define IS_ENABLED(v) (v)
#define READ_ONCE(v) (v)
#define DEVICE_ATTR_RW(v) int attr_##v
#define DEVICE_ATTR_RO(v) int attr_##v
#define dev_err(...) ((void)0)
static int sc27xx_typec_connect_state,calls,fail_at,fail_also,sleeps,disconnected,usb,host;
static u32 registers[0x100];
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void *dev_get_drvdata(struct device *d){return d->data;}
static int regmap_update_bits(void *m,int reg,u32 mask,u32 value){
 (void)m;assert(reg>=0&&reg<0x100);calls++;
 if(calls==fail_at||calls==fail_also)return -EIO;
 registers[reg]=(registers[reg]&~mask)|(value&mask);return 0;
}
static int extcon_get_state(void *p,int cable){(void)p;return cable==EXTCON_USB?usb:host;}
static void sc27xx_typec_disconnect(struct sc27xx_typec *s,int state){
 (void)state;assert(s->policy_lock);s->partner_connected=false;usb=host=0;disconnected++;
}
static void msleep(int n){assert(n==30);sleeps++;}
static bool sysfs_streq(const char *a,const char *b){size_t n=strlen(b);return !strncmp(a,b,n)&&(a[n]==0||(a[n]=='\n'&&a[n+1]==0));}
static int sysfs_emit(char *b,const char *f,...){va_list a;va_start(a,f);int n=vsnprintf(b,4096,f,a);va_end(a);return n;}
'''
MAIN = r'''
static void reset(struct sc27xx_typec *s){
 calls=fail_at=fail_also=sleeps=disconnected=usb=host=0;
 memset(registers,0,sizeof(registers));registers[4]=0xa2;registers[SC27XX_SW_CFG]=0x100000;
 s->mode=TYPEC_PORT_DRP;s->policy_lock=0;s->partner_connected=true;
}
int main(void){
 struct variant v={.mode=4};struct device d={0};
 struct sc27xx_typec s={.dev=&d,.var_data=&v};d.data=&s;char buf[4096];
 int modes[]={TYPEC_PORT_UFP,TYPEC_PORT_DFP,TYPEC_PORT_DRP};
 int expected[]={SC27XX_MODE_SNK,SC27XX_MODE_SRC,SC27XX_MODE_DRP};
 reset(&s);assert(!sc27xx_policy_set(&s,TYPEC_PORT_DRP)&&!calls);
 assert(sc27xx_policy_set(&s,77)==-EINVAL&&!calls&&!s.policy_lock);
 for(int i=0;i<3;i++){
  assert(!sc27xx_policy_set(&s,modes[i]));assert(s.mode==modes[i]&&!s.policy_lock);
  assert(registers[4]==(0xa0|expected[i]));assert(registers[SC27XX_SW_CFG]==0x100000);
  int before=calls;assert(!sc27xx_policy_set(&s,modes[i])&&calls==before);
 }
 for(int fault=1;fault<=3;fault++){
  reset(&s);fail_at=fault;assert(sc27xx_policy_set(&s,TYPEC_PORT_DFP)==-EIO);
  assert(s.mode==TYPEC_PORT_DRP&&!s.policy_lock);assert(registers[4]==0xa2);
  assert(registers[SC27XX_SW_CFG]==0x100000);
  assert(disconnected==(fault==1?0:1));
 }
 reset(&s);fail_at=2;fail_also=3;assert(sc27xx_policy_set(&s,TYPEC_PORT_DFP)==-EIO&&!s.policy_lock);
 for(int i=0;i<3;i++){
  s.mode=modes[i];assert(sc27xx_policy_accepts(&s,SC27XX_ATTACHED_SRC)==(s.mode!=TYPEC_PORT_UFP));
  assert(sc27xx_policy_accepts(&s,SC27XX_ATTACHED_SNK)==(s.mode!=TYPEC_PORT_DFP));
  assert(sc27xx_policy_accepts(&s,SC27XX_DEBUG_CABLE)==(s.mode!=TYPEC_PORT_DFP));
  assert(sc27xx_policy_accepts(&s,0));
 }
 reset(&s);assert(usb_role_policy_store(&d,NULL,"host\n",5)==5);assert(s.mode==TYPEC_PORT_DFP);
 assert(usb_role_policy_show(&d,NULL,buf)>0&&!strcmp(buf,"host\n"));
 assert(usb_role_policy_store(&d,NULL,"host-sink",9)==-EINVAL);
 assert(usb_role_policy_store(&d,NULL,"device",6)==6&&s.mode==TYPEC_PORT_UFP);
 assert(usb_role_policy_store(&d,NULL,"auto",4)==4&&s.mode==TYPEC_PORT_DRP);
 host=1;assert(usb_role_show(&d,NULL,buf)>0&&!strcmp(buf,"host\n"));
 host=0;usb=1;usb_role_show(&d,NULL,buf);assert(!strcmp(buf,"device\n"));
 usb=0;usb_role_show(&d,NULL,buf);assert(!strcmp(buf,"none\n"));
 d.data=NULL;assert(usb_role_policy_store(&d,NULL,"host",4)==-EAGAIN);
 assert(usb_role_policy_show(&d,NULL,buf)==-EAGAIN&&usb_role_show(&d,NULL,buf)==-EAGAIN);
 puts("PASS: CC policies/no-op, masks, failure rollback, stale attachments, sysfs and no forced boost");
}
'''


class Policy(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Native compiler required')
    def test_actual_policy_and_sysfs_with_register_faults(self):
        with tempfile.TemporaryDirectory() as folder:
            r = Path(folder)
            (r / 'test.c').write_text(PRELUDE + (HERE / 'typec-policy.h').read_text() + MAIN)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-variable',
                            '-Wno-unused-function', '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                            str(r / 'test.c'), '-o', str(r / 'test')], check=True)
            subprocess.run([str(r / 'test')], check=True)

    @unittest.skipUnless(os.environ.get('E5_TEST_KERNEL'), 'Set E5_TEST_KERNEL to prepared legacy UMS9620 source')
    def test_real_source_idempotence_irq_serialization_and_atomic_rejection(self):
        source = Path(os.environ['E5_TEST_KERNEL']) / 'drivers/usb/typec/sc27xx_typec.c'
        apply = runpy.run_path(str(HERE / 'typec-policy.py'))['apply']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);p = root / 'drivers/usb/typec/sc27xx_typec.c'
            p.parent.mkdir(parents=True);p.write_bytes(source.read_bytes())
            apply(root);first = p.read_bytes();apply(root);self.assertEqual(first,p.read_bytes())
            text=first.decode()
            self.assertEqual(text.count('struct mutex policy_lock;'),1)
            self.assertEqual(text.count('#include "ums9620-typec-policy.h"'),1)
            irq=text[text.index('static irqreturn_t sc27xx_typec_interrupt('):text.index('\nstatic int sc27xx_vbus_ok_bypass')]
            self.assertLess(irq.index('mutex_lock('),irq.index('regmap_read('))
            self.assertIn('if (!sc27xx_policy_accepts(sc, sc->state))',irq)
            self.assertEqual(irq.count('mutex_unlock('),2)
            self.assertIn('if (sc->mode != TYPEC_PORT_DFP)',text)
            p.write_text(text.replace('static struct attribute *sc27xx_typec_attrs[] = {','changed_vendor_source {'))
            before=p.read_bytes()
            with self.assertRaisesRegex(ValueError,'policy anchor'):apply(root)
            self.assertEqual(before,p.read_bytes())

    @unittest.skipUnless(os.environ.get('E5_TEST_KERNEL'), 'Set E5_TEST_KERNEL to prepared legacy UMS9620 source')
    def test_usb_preparation_can_regenerate_irq_without_losing_policy(self):
        source=Path(os.environ['E5_TEST_KERNEL'])
        usb=runpy.run_path(str(HERE / 'usb-role.py'))['apply']
        policy=runpy.run_path(str(HERE / 'typec-policy.py'))['apply']
        names=('drivers/usb/typec/sc27xx_typec.c','drivers/usb/dwc3/dwc3-of-simple.c',
               'drivers/usb/phy/phy-sprd-ums9620-ssphy.c','drivers/usb/phy/sprd-bc1p2.c',
               'include/linux/power/sprd-bc1p2.h')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in names:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((source/name).read_bytes())
            usb(root);policy(root)
            first={n:(root/n).read_bytes() for n in names}
            usb(root);policy(root)
            self.assertEqual(first,{n:(root/n).read_bytes() for n in names})
            text=(root/names[0]).read_text()
            self.assertEqual(text.count('mutex_init(&sc->policy_lock);'),1)
            self.assertEqual(text.count('if (!sc27xx_policy_accepts(sc, sc->state))'),1)


if __name__ == '__main__':
    unittest.main()
