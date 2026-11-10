#!/usr/bin/env python3
"""Replay real MUSB startup/session-PM code with no hardware or mounted tree."""
from pathlib import Path
import runpy
import argparse
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('kernel',type=Path)
parser.add_argument('--gadget-source',type=Path,help='Unchanged source when testing an unmounted overlay upperdir')
args=parser.parse_args();kernel=args.kernel
def function(text,name):
    import re
    found=re.search(r'(?:static )?(?:void|int) '+name+r'\([^;{}]*\)\s*\{',text)
    assert found,name
    end=found.end();depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[found.start():end]+'\n'


prelude=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef uint8_t u8;
#define __iomem
enum {MUSB_DEVCTL,MUSB_POWER,MUSB_TESTMODE};
enum {MUSB_DEVCTL_SESSION=1,MUSB_DEVCTL_HR=2,MUSB_DEVCTL_VBUS=0x18,
 MUSB_DEVCTL_FSDEV=0x40,MUSB_DEVCTL_LSDEV=0x20,MUSB_DEVCTL_BDEVICE=0x80,
 MUSB_QUIRK_B_DISCONNECT_99=0x99,MUSB_QUIRK_B_INVALID_VBUS_91=0x91,
 MUSB_QUIRK_A_DISCONNECT_19=0x19,MUSB_POWER_ISOUPDATE=0x80,MUSB_POWER_HSENAB=0x20,
 MUSB_DMA_SPRD=0x400,MUSB_OTG=3,MUSB_HOST=1,
 OTG_STATE_B_IDLE=1,OTG_STATE_A_WAIT_BCON=7,USB_SPEED_HIGH=3,USB_SPEED_UNKNOWN=0};
struct dev {int usage;};struct ops {int quirks;};struct config {int maximum_speed;};
struct musb;struct usb_gadget {struct musb *owner;};
struct usb_gadget_driver {int max_speed;};struct phy {int last_event;void *otg;};
struct musb {struct dev *controller;struct ops *ops;struct config *config;u8 *mregs;
 int port_mode,state,is_active,quirk_retries,irq_work,softconnect,lock;bool session;
 struct phy *xceiv;void *phy;struct usb_gadget g;struct usb_gadget_driver *gadget_driver;};
#define USB_EVENT_ID 1
#define PHY_MODE_USB_DEVICE 1
#define EINVAL 22
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define musb_dbg(...) ((void)0)
#define trace_musb_state(...) ((void)0)
#define dev_err(...) ((void)0)
#define fallthrough __attribute__((fallthrough))
#define msecs_to_jiffies(x) (x)
static u8 musb_readb(u8 *r,int n){return r[n];}
static void musb_writeb(u8 *r,int n,u8 v){r[n]=v;}
static int musb_get_state(struct musb *m){return m->state;}
static void musb_set_state(struct musb*m,int state){m->state=state;}
static struct musb *gadget_to_musb(struct usb_gadget*g){return g->owner;}
static void otg_set_peripheral(void *otg,struct usb_gadget*g){(void)otg;(void)g;}
static void phy_set_mode(void *phy,int mode){(void)phy;(void)mode;}
static int vbus_calls;
static void musb_platform_set_vbus(struct musb*m,int on){assert(on==1);vbus_calls++;m->mregs[MUSB_DEVCTL]|=MUSB_DEVCTL_SESSION;}
static void musb_enable_interrupts(struct musb *m){(void)m;}
static void musb_platform_enable(struct musb *m){(void)m;}
static bool musb_state_needs_recheck(struct musb*m,int v,const char*s){(void)m;(void)v;(void)s;return false;}
static void pm_runtime_mark_last_busy(struct dev*d){(void)d;}
static int pm_runtime_get_sync(struct dev*d){++d->usage;return 0;}
static void pm_runtime_put_autosuspend(struct dev*d){assert(d->usage>0);--d->usage;}
static void schedule_delayed_work(int *w,int t){(void)w;(void)t;}
'''
checks=r'''
int main(void){
 struct ops ops={MUSB_DMA_SPRD};struct config cfg={USB_SPEED_HIGH};struct dev dev={0};
 u8 regs[3]={MUSB_DEVCTL_BDEVICE,0,0};
 struct musb m={.controller=&dev,.ops=&ops,.config=&cfg,.mregs=regs,.port_mode=MUSB_OTG,.state=OTG_STATE_B_IDLE};
 /* A gadget bind before a cable must not create a host session or keep
  * the child active while the glue waits for autosuspend to enter host. */
 musb_start(&m);musb_pm_runtime_check_session(&m);
 assert(!!(regs[MUSB_DEVCTL]&MUSB_DEVCTL_SESSION)==EXPECT_BUG);
 assert(m.session==(EXPECT_BUG && !ROLE_PM) && dev.usage==(EXPECT_BUG && !ROLE_PM));
 regs[MUSB_DEVCTL]&=~MUSB_DEVCTL_SESSION;musb_pm_runtime_check_session(&m);
 assert(dev.usage==0);
 /* A previous ID event must not make a returned/rebound gadget source
  * VBUS. The legacy callback would overwrite the correct device SESSION. */
 struct phy phy={.last_event=USB_EVENT_ID};struct usb_gadget_driver driver={USB_SPEED_HIGH};
 m.xceiv=&phy;m.g.owner=&m;regs[0]=MUSB_DEVCTL_BDEVICE|MUSB_DEVCTL_VBUS;
 vbus_calls=0;assert(!musb_gadget_start(&m.g,&driver));
 assert(vbus_calls==EXPECT_BUG);musb_pm_runtime_check_session(&m);
 assert(dev.usage==(EXPECT_BUG && !ROLE_PM));
 regs[0]&=~MUSB_DEVCTL_SESSION;musb_pm_runtime_check_session(&m);assert(!dev.usage);
 /* The actual failing cold-boot register value persisted despite the
  * software SESSION guard: 0x99 with no CC-selected host/peripheral.
  * The old PM check takes a permanent extra reference; the role owner
  * must be able to let the child and parent suspend before first OTG. */
 regs[0]=0x99;musb_pm_runtime_check_session(&m);
 assert(dev.usage==!ROLE_PM && m.session==!ROLE_PM);
 regs[0]=0;musb_pm_runtime_check_session(&m);assert(!dev.usage&&!m.session);
 /* Keep valid peripheral power and five real host sessions intact.
  * The platform already owns a get/put around each real session. */
 regs[MUSB_DEVCTL]=MUSB_DEVCTL_BDEVICE|MUSB_DEVCTL_VBUS;
 musb_start(&m);assert(m.is_active && !(regs[MUSB_DEVCTL]&MUSB_DEVCTL_SESSION));
 for(int cycle=0;cycle<5;cycle++){
  dev.usage++;regs[MUSB_DEVCTL]=0;m.state=OTG_STATE_A_WAIT_BCON;
  musb_start(&m);musb_pm_runtime_check_session(&m);
  assert(m.session==!ROLE_PM && dev.usage==1+!ROLE_PM);
  regs[MUSB_DEVCTL]=0;musb_pm_runtime_check_session(&m);
  assert(!m.session&&dev.usage==1);dev.usage--;assert(!dev.usage);
 }
 dev.usage++;regs[0]=0x99;musb_pm_runtime_check_session(&m);
 assert(dev.usage==1+!ROLE_PM);regs[0]=0;musb_pm_runtime_check_session(&m);
 assert(dev.usage==1);dev.usage--;
 m.state=OTG_STATE_B_IDLE;m.port_mode=MUSB_HOST;regs[0]=0;
 musb_start(&m);assert(regs[0]&MUSB_DEVCTL_SESSION);
 ops.quirks=0;m.port_mode=MUSB_OTG;regs[0]=0;
 musb_start(&m);assert(regs[0]&MUSB_DEVCTL_SESSION);
 regs[0]=MUSB_DEVCTL_BDEVICE|MUSB_DEVCTL_VBUS;vbus_calls=0;
 assert(!musb_gadget_start(&m.g,&driver));assert(vbus_calls==1);
 musb_pm_runtime_check_session(&m);assert(m.session&&dev.usage==1);
 regs[0]=0;musb_pm_runtime_check_session(&m);assert(!m.session&&!dev.usage);
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='musb-gadget-session-') as folder:
    root=Path(folder)
    names=('musb_core.c','musb_host.c','musb_sprd.c','musb_gadget.c')
    for name in names:
        path=root/'drivers/usb/musb'/name;path.parent.mkdir(parents=True,exist_ok=True)
        original=args.gadget_source if name=='musb_gadget.c' and args.gadget_source else kernel/'drivers/usb/musb'/name
        path.write_bytes(original.read_bytes())
    path=root/'drivers/usb/musb/musb_core.c';old=path.read_text()
    gadget=root/'drivers/usb/musb/musb_gadget.c';old_gadget=gadget.read_text()
    apply=runpy.run_path(str(HERE/'usb_host.py'))['apply']
    apply(root);fixed=path.read_text();fixed_gadget=gadget.read_text()
    apply(root);assert path.read_text()==fixed and gadget.read_text()==fixed_gadget
    for text,gadget_text,buggy in ((old,old_gadget,'SPRD CC owns role selection' not in old),(fixed,fixed_gadget,False)):
        code=prelude+function(text,'musb_start')+function(text,'musb_pm_runtime_check_session')+function(gadget_text,'musb_gadget_start')
        role_pm='SPRD role work owns runtime PM' in text
        (root/'test.c').write_text(code+'\n#define EXPECT_BUG '+str(int(buggy))+
            '\n#define ROLE_PM '+str(int(role_pm))+'\n'+checks)
        subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
        subprocess.run([str(root/'test')],check=True)
print('PASS: retained cold DEVCTL=0x99, old PM hold, gadget bind, platform-owned peripheral/host sessions and other platforms')
