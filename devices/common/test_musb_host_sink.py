"""Exercise the actual shared MUSB readiness and MUSB input arbitration as C code."""
from pathlib import Path
import os
import re
import runpy
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def function(text, name):
    start = re.search(r'^[^\n]*\b' + name + r'\([^;]*$', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


class HostSink(unittest.TestCase):
    def setUp(self):
        self.kernel = os.environ.get('MUSB_TEST_KERNEL')
        if not self.kernel:
            self.skipTest('Set MUSB_TEST_KERNEL to the prepared MUSB kernel')
        self.kernel = Path(self.kernel)
        self.charger = 'bq2560x' if (self.kernel / 'drivers/power/supply/bq2560x-charger.c').is_file() else 'aw32257'

    def test_adapter_idempotence_and_model_scope(self):
        apply = runpy.run_path(str(HERE / 'musb-host-sink.py'))['apply']
        names = ['drivers/usb/typec/sc27xx_typec.c',
                 'drivers/usb/phy/phy-sprd-qogirn6lite.c',
                 'drivers/usb/musb/musb_sprd.c',
                 'drivers/usb/musb/musb_core.c',
                 'drivers/usb/musb/musb_host.c',
                 'drivers/power/supply/bq2560x-charger.c' if self.charger == 'bq2560x' else
                 'drivers/power/supply/aw32257_charger.c']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in names:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes((self.kernel / name).read_bytes())
            (root / 'include/linux/usb').mkdir(parents=True)
            apply(root, charger=self.charger)
            bq = root / 'drivers/power/supply/bq2560x-charger.c'
            if bq.exists():
                self.assertEqual(bq.read_bytes(), (self.kernel / bq.relative_to(root)).read_bytes())
            first = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            apply(root, charger=self.charger)
            self.assertEqual(first, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})
        for model in ('m8', 'k1', 'yuanhang41s'):
            self.assertNotIn("common/musb-host-sink.py", (HERE.parent / model / 'prepare-kernel.py').read_text())

    def test_actual_ready_and_input_arbitration(self):
        glue = (self.kernel / 'drivers/usb/musb/musb_sprd.c').read_text().replace('e5_', 'sprd_')
        header = (HERE / 'musb-host-sink.h').read_text().replace('e5_', 'sprd_')
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
enum {EXTCON_USB,USB_CHARGER_PRESENT,USB_CHARGER_ABSENT,CHARGER_DETECT_DONE=4};
enum {DRD_STATE_IDLE,DRD_STATE_HOST_IDLE,DRD_STATE_HOST,DRD_STATE_PERIPHERAL};
enum {MUSB_ID_FLOAT,MUSB_ID_GROUND};
enum {USB_CHG_STATE_UNDETECT,USB_CHG_STATE_DETECTED,USB_CHG_STATE_RETRY_DETECTED};
enum {ID,B_SESS_VLD,B_SUSPEND,A_RECOVER,B_DATA_DISABLED};
#define READ_ONCE(x) (x)
#define dev_info(...) ((void)0)
struct extcon_dev {int usb;};
struct usb_phy {int flags,chg_state;};
struct work_struct {int unused;};
struct delayed_work {struct work_struct work;};
struct sprd_glue {
 struct extcon_dev *external_vbus,*edev;struct usb_phy *xceiv;
 bool external_host,charging_mode,in_restart,gadget_suspend,host_recover,vbus_active;
 int drd_state,id_state,chg_state,usb_data_enabled;
 unsigned long inputs;void *sm_usb_wq,*dev;struct delayed_work sm_work;
};
static bool policy;
static int queued;
static bool sprd_external_policy(struct sprd_glue *g){(void)g;return policy;}
static int extcon_get_state(struct extcon_dev *e,int cable){(void)cable;return e->usb;}
static void flush_delayed_work(struct delayed_work *w){(void)w;}
static void queue_delayed_work(void *q,struct delayed_work *w,int d){(void)q;(void)w;(void)d;queued++;}
static void set_bit(int b,unsigned long *p){*p|=1ul<<b;}
static void clear_bit(int b,unsigned long *p){*p&=~(1ul<<b);}
static bool bit(int b,unsigned long p){return !!(p&(1ul<<b));}
''' + function(header, 'sprd_external_ready') + function(glue, 'musb_sprd_ext_event_notify') + r'''
int main(void) {
 struct extcon_dev power={1},cc={1};struct usb_phy phy={CHARGER_DETECT_DONE,USB_CHARGER_PRESENT};
 struct sprd_glue g={.external_vbus=&power,.edev=&cc,.xceiv=&phy,.id_state=MUSB_ID_FLOAT,
   .chg_state=USB_CHG_STATE_DETECTED,.usb_data_enabled=1,.charging_mode=true,.vbus_active=true};
 policy=true;assert(sprd_external_ready(&g));
 musb_sprd_ext_event_notify(&g);assert(!bit(ID,g.inputs)&&!bit(B_SESS_VLD,g.inputs));
 g.drd_state=DRD_STATE_HOST_IDLE;musb_sprd_ext_event_notify(&g);assert(!bit(ID,g.inputs));
 g.external_host=true;g.drd_state=DRD_STATE_HOST;
 for(int missing=0;missing<5;missing++) {
   power.usb=cc.usb=1;phy.flags=CHARGER_DETECT_DONE;phy.chg_state=USB_CHARGER_PRESENT;policy=true;
   if(missing==0)power.usb=0;if(missing==1)cc.usb=0;if(missing==2)phy.flags=0;
   if(missing==3)phy.chg_state=USB_CHARGER_ABSENT;if(missing==4)policy=false;
   assert(!sprd_external_ready(&g));musb_sprd_ext_event_notify(&g);assert(bit(ID,g.inputs));
 }
 policy=true;power.usb=cc.usb=1;phy.flags=CHARGER_DETECT_DONE;phy.chg_state=USB_CHARGER_PRESENT;
 g.external_host=false;g.drd_state=DRD_STATE_HOST;g.id_state=MUSB_ID_GROUND;
 musb_sprd_ext_event_notify(&g);assert(bit(ID,g.inputs)); /* Tear down the old sourcing host. */
 g.drd_state=DRD_STATE_IDLE;musb_sprd_ext_event_notify(&g);assert(!bit(ID,g.inputs));
 policy=false;g.id_state=MUSB_ID_FLOAT;g.charging_mode=false;
 musb_sprd_ext_event_notify(&g);assert(bit(ID,g.inputs)&&bit(B_SESS_VLD,g.inputs));
 g.id_state=MUSB_ID_GROUND;musb_sprd_ext_event_notify(&g);assert(!bit(ID,g.inputs));
 g.usb_data_enabled=0;musb_sprd_ext_event_notify(&g);assert(bit(B_DATA_DISABLED,g.inputs));
 assert(queued>0);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'inputs.c').write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-misleading-indentation',
                            str(root / 'inputs.c'), '-o', str(root / 'inputs')], check=True)
            subprocess.run([str(root / 'inputs')], check=True)

    def test_actual_boost_entry_never_sources_external_power(self):
        source = (self.kernel / 'drivers/usb/musb/musb_sprd.c').read_text().replace('e5_', 'sprd_')
        start = source.index('static int musb_sprd_otg_start_host(')
        end = source.index('\n\t\tif (!glue->enable_pm_suspend_in_host', start)
        prefix = source[start:end] + '\n\t}\n\treturn 0;\n}\n'
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stddef.h>
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define IS_ERR(p) (!(p))
#define IS_ENABLED(c) 0
#define EPROBE_DEFER 517
enum {EXTCON_USB};
struct regulator {int enabled;};
struct extcon_dev {int usb;};
struct musb {void *controller;};
struct musb_hdrc_platform_data {int unused;};
struct sprd_glue {struct musb *musb;struct regulator *vbus;struct extcon_dev *external_vbus;
 bool external_host,use_pdhub_c2c;void *dev;};
static bool policy,ready;
static int enables,enable_error;
static bool sprd_external_ready(struct sprd_glue *g){(void)g;return ready;}
static bool sprd_external_policy(struct sprd_glue *g){(void)g;return policy;}
static struct musb_hdrc_platform_data *dev_get_platdata(void *p){(void)p;return NULL;}
static struct regulator *devm_regulator_get_optional(void *d,const char *n){(void)d;(void)n;return NULL;}
static int regulator_is_enabled(struct regulator *r){return r->enabled;}
static int regulator_enable(struct regulator *r){(void)r;enables++;return enable_error;}
static int extcon_get_state(struct extcon_dev *e,int c){(void)c;return e->usb;}
''' + prefix + r'''
int main(void) {
 struct regulator r={0};struct extcon_dev power={1};struct musb m={0};
 struct sprd_glue g={.musb=&m,.vbus=&r,.external_vbus=&power};
 policy=ready=true;assert(musb_sprd_otg_start_host(&g,1)==0&&enables==0);
 r.enabled=1;assert(musb_sprd_otg_start_host(&g,1)==-EBUSY&&enables==0);
 r.enabled=-EIO;assert(musb_sprd_otg_start_host(&g,1)==-EBUSY&&enables==0);
 r.enabled=0;ready=false;assert(musb_sprd_otg_start_host(&g,1)==-ENOLINK&&enables==0);
 policy=false;assert(musb_sprd_otg_start_host(&g,1)==-EBUSY&&enables==0);
 power.usb=0;assert(musb_sprd_otg_start_host(&g,1)==0&&enables==1);
 enable_error=-EIO;assert(musb_sprd_otg_start_host(&g,1)==-EIO&&enables==2);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'boost.c').write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable',
                            str(root / 'boost.c'), '-o', str(root / 'boost')], check=True)
            subprocess.run([str(root / 'boost')], check=True)

    def test_unknown_charger_online_requires_actual_external_session(self):
        phy = (self.kernel / 'drivers/usb/phy/phy-sprd-qogirn6lite.c').read_text().replace('e5_', 'sprd_')
        path = self.kernel / 'drivers/power/supply/aw32257_charger.c'
        if not path.is_file():
            self.skipTest('Not an AW32257 profile')
        charger = path.read_text().replace('e5_', 'sprd_')
        if 'sprd_hsphy_external_power(bq->usb_phy)' not in charger:
            self.skipTest('BQ2560x uses hardware power-good, without the AW-specific adapter')
        if 'bool sprd_hsphy_external_power(' not in phy:
            self.skipTest('External ONLINE revision has not been prepared yet')
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
enum {USB_CHARGER_PRESENT,USB_CHARGER_ABSENT,CHARGER_DETECT_DONE=4};
#define READ_ONCE(x) (x)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct device_node {int unused;};
struct device {struct device_node *of_node;};
struct usb_phy {int chg_state,flags;struct device *dev;};
struct sprd_hsphy {struct usb_phy phy;bool external_host;};
struct aw32257_device {int limit;struct usb_phy *usb_phy;};
static struct device_node cc;
static bool physical_power,have_provider=true;
static struct device_node *of_parse_phandle(struct device_node *n,const char *p,int i){(void)n;(void)p;(void)i;return have_provider?&cc:NULL;}
static void of_node_put(struct device_node *n){(void)n;}
static bool sprd_typec_external_power(struct device_node *n){return n&&physical_power;}
''' + function(phy, 'sprd_hsphy_external_power') + function(charger, 'aw32257_charger_get_online') + r'''
int main(void) {
 struct device d={&cc};struct sprd_hsphy p={.phy={USB_CHARGER_PRESENT,CHARGER_DETECT_DONE,&d}};
 struct aw32257_device b={.usb_phy=&p.phy};int online=-1;
 physical_power=true;p.external_host=true;
 assert(!aw32257_charger_get_online(&b,&online)&&online); /* UNKNOWN limit is zero. */
 p.external_host=false;assert(!aw32257_charger_get_online(&b,&online)&&!online);
 p.external_host=true;physical_power=false;
 assert(!aw32257_charger_get_online(&b,&online)&&!online);
 physical_power=true;p.phy.flags=0;
 assert(!aw32257_charger_get_online(&b,&online)&&!online);
 p.phy.flags=CHARGER_DETECT_DONE;p.phy.chg_state=USB_CHARGER_ABSENT;
 assert(!aw32257_charger_get_online(&b,&online)&&!online);
 p.phy.chg_state=USB_CHARGER_PRESENT;have_provider=false;
 assert(!aw32257_charger_get_online(&b,&online)&&!online);
 b.limit=500;assert(!aw32257_charger_get_online(&b,&online)&&online); /* Existing SDP path. */
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'online.c').write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(root / 'online.c'),
                            '-o', str(root / 'online')], check=True)
            subprocess.run([str(root / 'online')], check=True)



    def test_bq_online_uses_hardware_power_good(self):
        path = self.kernel / 'drivers/power/supply/bq2560x-charger.c'
        if not path.is_file():
            self.skipTest('Not a BQ2560x profile')
        source = function(path.read_text(), 'bq2560x_charger_usb_get_property')
        start = source.index('case POWER_SUPPLY_PROP_ONLINE:')
        stop = source.index('break;', start)
        body = source[start:stop].split(':', 1)[1]
        code = r'''
#include <assert.h>
#include <stdbool.h>
typedef unsigned char u8;
#define BIT(n) (1u << (n))
#define BQ2560X_REG_8 8
static int bus_error,chip_status;
static int bq2560x_read(void *info,int reg,u8 *v){(void)info;assert(reg==8);*v=chip_status;return bus_error;}
struct value {int intval;};
static int online(struct value *val){void *info=0;int ret=0;
''' + body + r'''
return ret;}
int main(void){struct value v={-1};
 chip_status=BIT(2);assert(!online(&v)&&v.intval==1);
 chip_status=0;assert(!online(&v)&&v.intval==0);
 chip_status=0xff;assert(!online(&v)&&v.intval==1);
 bus_error=-5;v.intval=-1;assert(online(&v)==-5&&v.intval==-1);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bq.c').write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(root / 'bq.c'),
                            '-o', str(root / 'bq')], check=True)
            subprocess.run([str(root / 'bq')], check=True)


    def test_host_session_pm_ownership_and_cleanup(self):
        import resource
        def no_core_dump():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        core = (self.kernel / 'drivers/usb/musb/musb_core.c').read_text()
        stop = function(core, 'musb_stop')
        cleanup = function((self.kernel / 'drivers/usb/musb/musb_host.c').read_text(), 'musb_host_cleanup')
        check = function(core, 'musb_pm_runtime_check_session')
        prelude = r'''
#include <assert.h>
#include <stdbool.h>
typedef unsigned char u8;
enum {MUSB_DEVCTL, MUSB_DEVCTL_SESSION=1, MUSB_DEVCTL_HR=2,
 MUSB_DEVCTL_FSDEV=4, MUSB_DEVCTL_LSDEV=8, MUSB_DEVCTL_BDEVICE=128,
 MUSB_QUIRK_B_DISCONNECT_99=0x99,MUSB_QUIRK_B_INVALID_VBUS_91=0x91,
 MUSB_QUIRK_A_DISCONNECT_19=0x19,MUSB_DMA_SPRD=0x400,MUSB_PERIPHERAL=8};
struct dev {int usage;};
struct ops {int quirks;};
struct hcd {bool rh_registered;};
struct musb {struct dev *controller;struct ops *ops;bool session;struct hcd *hcd;int port_mode;
 int quirk_retries,irq_work;void *mregs;};
static int devctl,queued,disabled;static void *system_wq;
static struct musb *running;
static void __attribute__((unused)) flush_delayed_work(int *work);
static void usb_remove_hcd(struct hcd *h);
#define trace_musb_state(...) ((void)0)
#define fallthrough __attribute__((fallthrough))
#define dev_err(...) ((void)0)
#define msecs_to_jiffies(x) (x)
static int musb_readb(void *r,int n){(void)r;(void)n;return devctl;}
static void musb_writeb(void *r,int n,int v){(void)r;(void)n;devctl=v;}
static bool musb_state_needs_recheck(struct musb*m,int v,const char*s){(void)m;(void)v;(void)s;return false;}
static void pm_runtime_mark_last_busy(struct dev*d){(void)d;}
static int pm_runtime_get_sync(struct dev*d){return ++d->usage,0;}
static void pm_runtime_put_autosuspend(struct dev*d){assert(d->usage>0);--d->usage;}
static void musb_platform_disable(struct musb*m){(void)m;disabled++;}
static void musb_disable_interrupts(struct musb*m){(void)m;}
static void musb_platform_try_idle(struct musb*m,int t){(void)m;(void)t;}
#define mod_delayed_work(q,w,t) ((void)(q),(void)(w),(void)(t),queued=1)
#define schedule_delayed_work(w,t) ((void)(w),(void)(t),queued=1)
'''
        main = r'''
static void usb_remove_hcd(struct hcd *h){assert(h->rh_registered);musb_stop(running);h->rh_registered=false;}
static void flush_delayed_work(int *work){(void)work;if(queued){queued=0;musb_pm_runtime_check_session(running);}}
int main(void){
 struct ops ops={MUSB_DMA_SPRD};struct dev dev={0};struct hcd h={0};struct musb m={.controller=&dev,.ops=&ops,.hcd=&h};running=&m;
 for(int cycle=0;cycle<3;cycle++){
  /* Actual SPRD role work owns one reference. Legacy core accounting
   * used to add another; keep both teardown cases under test. */
  dev.usage++;devctl=MUSB_DEVCTL_SESSION;musb_pm_runtime_check_session(&m);
  assert(m.session==LEGACY_PM && dev.usage==1+LEGACY_PM);
  queued=0;h.rh_registered=true;musb_host_cleanup(&m);
  assert(devctl==0);dev.usage--; /* Matching glue host-session put. */
  if(queued)musb_pm_runtime_check_session(&m); /* Existing core IRQ work. */
  assert(dev.usage==0&&!m.session); /* Parent can now suspend and change role. */
  musb_host_cleanup(&m);assert(dev.usage==0);
 }
 ops.quirks=0;devctl=MUSB_DEVCTL_SESSION;musb_pm_runtime_check_session(&m);
 assert(m.session&&dev.usage==1);devctl=0;musb_pm_runtime_check_session(&m);
 assert(!m.session&&!dev.usage);
 queued=0;h.rh_registered=true;musb_host_cleanup(&m);assert(!queued);
 assert(disabled==4);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = re.sub(r'\s*if \(musb->ops->quirks & MUSB_DMA_SPRD\) \{.*?\n\t\}', '', cleanup, flags=re.S)
            guard='\tif (musb->ops->quirks & MUSB_DMA_SPRD)\n\t\treturn;\n'
            has_owner_guard='SPRD role work owns runtime PM' in check
            legacy=check.replace(guard,'',1) if has_owner_guard else check
            variants=[('fixed',check,cleanup,not has_owner_guard),
                      ('legacy-balanced',legacy,cleanup,True),
                      ('old',legacy,old,True)]
            for name, pm_check, body, legacy_pm in variants:
                (root / (name+'.c')).write_text(prelude + pm_check + stop + body +
                    '\n#define LEGACY_PM '+str(int(legacy_pm))+'\n'+main)
                subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-variable',
                                str(root/(name+'.c')),'-o',str(root/name)],check=True)
                result = subprocess.run([str(root/name)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                        preexec_fn=no_core_dump)
                if name != 'old':
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                else:
                    self.assertNotEqual(result.returncode, 0, 'Old teardown must reproduce the leaked session')

class VbusOnly(unittest.TestCase):
    def test_manual_fallback_attachment_takeover_and_cleanup(self):
        prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define IS_ERR_OR_NULL(p) (!(p) || (p)==(void *)-1)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
#define dev_info(...) ((void)0)
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define msecs_to_jiffies(x) (x)
#define system_wq NULL
enum {EXTCON_USB, EXTCON_SINK, EXTCON_USB_HOST};
enum {TYPEC_PORT_UFP, TYPEC_PORT_DRP, TYPEC_PORT_DFP};
enum {SC27XX_ATTACHED_SNK, SC27XX_DEBUG_CABLE, SC27XX_ATTACHED_SRC};
struct extcon_dev {int levels[3];};
struct work_struct {int unused;};
struct delayed_work {struct work_struct work;};
struct sc27xx_typec {
 bool external_vbus_stopped,external_host_policy,vbus_only_connect,partner_connected;
 int role_lock,external_vbus_lock,mode,pre_state;void *dev;
 struct extcon_dev *vbus_dev,*edev;struct delayed_work external_vbus_work;
};
static int changes,queued,delay,locked;
static void mutex_lock(int *m){(void)m;assert(!locked);locked=1;}
static void mutex_unlock(int *m){(void)m;assert(locked);locked=0;}
static int extcon_get_state(struct extcon_dev *d,int n){assert(!IS_ERR_OR_NULL(d));return d->levels[n];}
static void extcon_set_state_sync(struct extcon_dev *d,int n,bool value){assert(locked);d->levels[n]=value;changes++;}
static void mod_delayed_work(void *q,struct delayed_work *w,int t){(void)q;(void)w;queued++;delay=t;}
static void cancel_delayed_work_sync(struct delayed_work *w){(void)w;queued=0;}
'''
        main = r'''
int main(void) {
 struct extcon_dev power={{1,0,0}},cc={{0,0,0}};
 struct sc27xx_typec s={.vbus_dev=&power,.edev=&cc,.mode=TYPEC_PORT_UFP};
 /* Automatic/device/ordinary host never acquire the fallback. */
 sc27xx_external_vbus_queue(&s);assert(!queued);
 sc27xx_external_vbus_work(&s.external_vbus_work.work);assert(!changes);
 s.external_host_policy=true;
 sc27xx_external_vbus_queue(&s);assert(queued==1&&delay==500);
 sc27xx_external_vbus_work(&s.external_vbus_work.work);
 assert(s.vbus_only_connect&&!s.partner_connected&&cc.levels[EXTCON_USB]&&cc.levels[EXTCON_SINK]);
 assert(!cc.levels[EXTCON_USB_HOST]&&changes==2);
 sc27xx_external_vbus_work(&s.external_vbus_work.work);assert(changes==2); /* No repeated events. */
 /* Detach cancels presence immediately, even if CC never sent an IRQ. */
 power.levels[EXTCON_USB]=0;sc27xx_external_vbus_queue(&s);assert(delay==0);
 sc27xx_external_vbus_work(&s.external_vbus_work.work);
 assert(!s.vbus_only_connect&&!cc.levels[EXTCON_USB]&&!cc.levels[EXTCON_SINK]);
 /* Stale delayed arrival cannot invent power after the plug is removed. */
 power.levels[EXTCON_USB]=1;sc27xx_external_vbus_queue(&s);assert(delay==500);
 power.levels[EXTCON_USB]=0;sc27xx_external_vbus_work(&s.external_vbus_work.work);assert(!s.vbus_only_connect);
 power.levels[EXTCON_USB]=1;sc27xx_external_vbus_work(&s.external_vbus_work.work);assert(s.vbus_only_connect);
 /* A late real CC sink takes over without a false unplug/replug. */
 int before=changes;s.partner_connected=true;s.pre_state=SC27XX_ATTACHED_SNK;
 sc27xx_external_vbus_work(&s.external_vbus_work.work);
 assert(!s.vbus_only_connect&&changes==before&&cc.levels[EXTCON_USB]);
 s.partner_connected=false;cc.levels[EXTCON_USB]=cc.levels[EXTCON_SINK]=0;
 sc27xx_external_vbus_work(&s.external_vbus_work.work);assert(s.vbus_only_connect);
 /* Explicit policy exit releases fallback even while external VBUS stays high. */
 s.external_host_policy=false;mutex_lock(&s.role_lock);sc27xx_external_vbus_clear(&s);mutex_unlock(&s.role_lock);
 assert(!s.vbus_only_connect&&!cc.levels[EXTCON_USB]);
 s.external_host_policy=true;
 /* An active source or unreadable/absent power provider must not be accepted. */
 for(int i=0;i<6;i++) {
  s.mode=TYPEC_PORT_UFP;s.vbus_dev=&power;cc.levels[EXTCON_USB_HOST]=0;
  if(i==0)s.vbus_dev=NULL;if(i==1)s.vbus_dev=(void *)-1;
  if(i==2)power.levels[EXTCON_USB]=-EIO;if(i==3)s.mode=TYPEC_PORT_DFP;
  if(i==4)cc.levels[EXTCON_USB_HOST]=1;if(i==5)cc.levels[EXTCON_USB_HOST]=-EIO;
  sc27xx_external_vbus_work(&s.external_vbus_work.work);assert(!s.vbus_only_connect);
  power.levels[EXTCON_USB]=1;
 }
 s.mode=TYPEC_PORT_UFP;cc.levels[EXTCON_USB_HOST]=0;
 sc27xx_external_vbus_queue(&s);sc27xx_external_vbus_cleanup(&s);assert(!queued&&s.external_vbus_stopped);
 sc27xx_external_vbus_queue(&s);sc27xx_external_vbus_work(&s.external_vbus_work.work);
 assert(!queued&&!s.vbus_only_connect&&!locked);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = runpy.run_path(str(HERE/'dwc3-typec-vbus.py'))['header']
            variants = [('musb', 'role_lock', 'external_host_policy', (HERE/'host-sink-typec.h').read_text()),
                        ('dwc3', 'policy_lock', 'external_host_policy', adapter(lock='policy_lock',policy='external_host_policy')),
                        ('41s', 'role_lock', 'host_sink', adapter(lock='role_lock',policy='host_sink'))]
            for name, lock, policy, header in variants:
                with self.subTest(driver=name):
                    fixture = lambda s:s.replace('role_lock',lock).replace('external_host_policy',policy)
                    (root / 'vbus.c').write_text(fixture(prelude) + header + fixture(main))
                    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-misleading-indentation',
                                    '-fsanitize=address,undefined','-fno-pie','-no-pie',
                                    str(root / 'vbus.c'), '-o', str(root / 'vbus')], check=True)
                    subprocess.run([str(root / 'vbus')], check=True)


if __name__ == '__main__':
    unittest.main()
