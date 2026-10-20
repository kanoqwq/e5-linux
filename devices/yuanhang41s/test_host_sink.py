"""Compile actual role work against independent power/data/error scenarios."""
from pathlib import Path
import os
import re
import runpy
import subprocess
import tempfile
import unittest

BOARD = Path(__file__).resolve().parent


def function(source, name):
    found = re.search(r'^static [^\n]*\b' + name + r'\(', source, re.M)
    start = found.start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


class HostSink(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Linux C compiler required')
    def test_unknown_bc_completion_notifies_without_forging_charger_type(self):
        kernel = os.environ.get('E5_TEST_KERNEL')
        if not kernel:
            self.skipTest('Set E5_TEST_KERNEL to a prepared 41S tree')
        source = (Path(kernel) / 'drivers/usb/phy/sprd-bc1p2.c').read_text()
        code = '''#include <assert.h>
enum { CHG_STATE_DETECTED, CHG_STATE_UNDETECT, CHG_STATE_RETRY_DETECTED, CHG_STATE_RETRY_DETECT };
enum { UNKNOWN_TYPE, SDP_TYPE, DCP_TYPE };
struct usb_phy {int chg_type;};
struct state {int detect_state;};
static struct state storage,*bc1p2=&storage;
static int events;
static void usb_phy_notify_charger(struct usb_phy *phy){(void)phy;events++;}
''' + function(source, 'sprd_bc1p2_notify_charger') + '''
int main(void){
 struct usb_phy phy={.chg_type=UNKNOWN_TYPE};storage.detect_state=CHG_STATE_RETRY_DETECTED;
 sprd_bc1p2_notify_charger(&phy);assert(events==1&&phy.chg_type==UNKNOWN_TYPE);
 storage.detect_state=CHG_STATE_RETRY_DETECT;sprd_bc1p2_notify_charger(&phy);assert(events==1);
 storage.detect_state=CHG_STATE_DETECTED;phy.chg_type=SDP_TYPE;
 sprd_bc1p2_notify_charger(&phy);assert(events==2&&phy.chg_type==SDP_TYPE);
 storage.detect_state=CHG_STATE_UNDETECT;phy.chg_type=UNKNOWN_TYPE;
 sprd_bc1p2_notify_charger(&phy);assert(events==3&&phy.chg_type==UNKNOWN_TYPE);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'completion.c').write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(root / 'completion.c'),
                            '-o', str(root / 'completion')], check=True)
            subprocess.run([str(root / 'completion')], check=True)

    def test_m8_and_other_recipients_refuse_41s_source(self):
        apply = runpy.run_path(str(BOARD.parent / 'm8/prepare-kernel.py'))['apply']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / 'drivers/usb/typec/yh41s-usb-policy.h'
            marker.parent.mkdir(parents=True)
            marker.write_text('board-specific')
            for recipient in ('m8', 'k1', 'unknown'):
                with self.assertRaisesRegex(ValueError, '41S-specific USB'):
                    apply(root, recipient=recipient)
            self.assertEqual(marker.read_text(), 'board-specific')

    @unittest.skipIf(os.name == 'nt', 'Linux C compiler required')
    def test_external_host_follows_real_vbus_attach_and_detach(self):
        kernel = os.environ.get('E5_TEST_KERNEL')
        if not kernel:
            self.skipTest('Set E5_TEST_KERNEL to a prepared 41S tree')
        source = (Path(kernel) / 'drivers/usb/phy/phy-sprd-ums9620-ssphy.c').read_text()
        bodies = function(source, 'sprd_ssphy_notify_connect') + function(source, 'sprd_ssphy_notify_disconnect')
        registers = sorted(set(re.findall(r'\b(?:MASK_|REG_|BIT_ANLG_)[A-Z0-9_]+', bodies)))
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
typedef unsigned u32;
enum usb_device_speed {USB_SPEED_UNKNOWN};
struct usb_phy {void *dev;};
struct sprd_ssphy {struct usb_phy phy;bool is_host,external_host;int inited;void *aon_apb,*ana_g0l;};
#define READ_ONCE(x) (x)
#define atomic_read(x) (*(x))
#define dev_info(...) ((void)0)
#define container_of(ptr,type,member) ((type *)((char *)(ptr)-offsetof(type,member)))
static int writes;
static unsigned values[8];
static int regmap_update_bits(void *m,unsigned reg,unsigned mask,unsigned value){(void)m;(void)reg;(void)mask;values[writes++]=value;return 0;}
''' + ''.join('#define ' + name + ' ' + str(i+1) + '\n' for i,name in enumerate(registers)) + bodies + r'''
int main(void){
 struct sprd_ssphy p={.inited=1,.is_host=true};
 sprd_ssphy_notify_connect(&p.phy,USB_SPEED_UNKNOWN);sprd_ssphy_notify_disconnect(&p.phy,USB_SPEED_UNKNOWN);
 assert(writes==0);p.external_host=true;
 sprd_ssphy_notify_connect(&p.phy,USB_SPEED_UNKNOWN);assert(writes==3&&values[0]&&values[1]&&values[2]);
 sprd_ssphy_notify_disconnect(&p.phy,USB_SPEED_UNKNOWN);assert(writes==6&&!values[3]&&!values[4]&&!values[5]);
 writes=0;p.inited=0;sprd_ssphy_notify_disconnect(&p.phy,USB_SPEED_UNKNOWN);assert(!writes);
 p.inited=1;p.is_host=false;p.external_host=false;
 sprd_ssphy_notify_connect(&p.phy,USB_SPEED_UNKNOWN);assert(writes==3);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'vbus.c').write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                            str(root / 'vbus.c'), '-o', str(root / 'vbus')], check=True)
            subprocess.run([str(root / 'vbus')], check=True)

    @unittest.skipIf(os.name == 'nt', 'Linux C compiler required')
    def test_actual_work_never_boosts_external_supply_and_handles_failures(self):
        kernel = os.environ.get('E5_TEST_KERNEL')
        if not kernel:
            self.skipTest('Set E5_TEST_KERNEL to a prepared 41S tree')
        source = (Path(kernel) / 'drivers/usb/dwc3/dwc3-sprd-role.h').read_text()
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
enum {EXTCON_USB=0, EXTCON_USB_HOST=1, USB_CHARGER_ABSENT, USB_CHARGER_PRESENT};
enum {USB_ROLE_NONE, USB_ROLE_DEVICE, USB_ROLE_HOST};
#define CHARGER_DETECT_DONE 1
#define READ_ONCE(x) (x)
#define IS_ERR_OR_NULL(x) (!(x))
#define PTR_ERR(x) (-1)
#define msecs_to_jiffies(x) (x)
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define system_wq NULL
#define container_of(ptr,type,member) ((type *)((char *)(ptr)-offsetof(type,member)))
#define to_delayed_work(ptr) container_of(ptr,struct delayed_work,work)
struct work_struct {int unused;};
struct delayed_work {struct work_struct work;};
struct device {int unused;};
struct device_node {int unused;};
struct regulator {int unused;};
struct extcon_dev {int usb,host;};
struct usb_role_switch {int role;};
struct usb_phy {int chg_state,flags;void *otg;};
struct dwc3 {void *xhci;struct work_struct drd_work;};
struct platform_device {struct device dev;};
struct sprd_dwc3_role {
 struct device *dev;struct extcon_dev *extcon,*power_extcon;
 struct regulator *vbus;struct usb_phy *phy;struct device_node *child,*typec_node;
 struct usb_role_switch *sw;struct delayed_work work;unsigned retries;
 bool enabled,registered,stopped,external_host;
};
static struct extcon_dev cc,power;
static struct usb_role_switch sw;
static struct usb_phy phy;
static struct dwc3 dwc;
static struct platform_device child;
static bool policy,phy_external;
static int enables,disables,transitions,queued,enable_error,disable_error,phy_error,role_error,power_during_flush;
static int extcon_get_state(struct extcon_dev *e,int cable){return cable==EXTCON_USB?e->usb:e->host;}
static bool yh41s_typec_host_sink(struct device_node *n){(void)n;return policy;}
static void yh41s_typec_data_role(struct device_node *n,bool h){(void)n;(void)h;}
static void yh41s_ssphy_external_host(struct usb_phy *p,bool e){(void)p;phy_external=e;}
static void *of_fwnode_handle(struct device_node *n){return n;}
static struct usb_role_switch *usb_role_switch_find_by_fwnode(void *n){(void)n;return &sw;}
static int usb_role_switch_get_role(struct usb_role_switch *s){return s->role;}
static int usb_role_switch_set_role(struct usb_role_switch *s,int role){if(role==USB_ROLE_HOST&&role_error)return role_error;s->role=role;transitions++;return 0;}
static struct platform_device *of_find_device_by_node(struct device_node *n){(void)n;return &child;}
static struct dwc3 *platform_get_drvdata(struct platform_device *p){(void)p;return &dwc;}
static void put_device(struct device *d){(void)d;}
static int regulator_enable(struct regulator *r){(void)r;enables++;assert(!power.usb);return enable_error;}
static int regulator_disable(struct regulator *r){(void)r;disables++;return disable_error;}
static int otg_set_vbus(void *p,bool host){(void)p;return host?phy_error:0;}
static void flush_work(struct work_struct *w){(void)w;if(power_during_flush)power.usb=1;}
static void msleep(int ms){(void)ms;}
static void mod_delayed_work(void *q,struct delayed_work *w,int ms){(void)q;(void)w;(void)ms;queued++;}
''' + function(source, 'sprd_dwc3_wants_host') + function(source, 'sprd_dwc3_role_work') + r'''
static struct sprd_dwc3_role reset(void){
 cc=(struct extcon_dev){0};power=cc;sw.role=USB_ROLE_DEVICE;
 phy=(struct usb_phy){.chg_state=USB_CHARGER_PRESENT,.flags=CHARGER_DETECT_DONE};
 dwc.xhci=&dwc;policy=phy_external=false;
 enables=disables=transitions=queued=enable_error=disable_error=phy_error=role_error=power_during_flush=0;
 return (struct sprd_dwc3_role){.extcon=&cc,.power_extcon=&power,.phy=&phy,.sw=&sw};
}
static void run(struct sprd_dwc3_role *r){sprd_dwc3_role_work(&r->work.work);}
int main(void){
 struct sprd_dwc3_role r=reset();policy=true;cc.usb=power.usb=1;
 run(&r);assert(sw.role==USB_ROLE_HOST&&!enables&&!r.enabled&&r.external_host&&phy_external);
 int once=transitions;run(&r);assert(transitions==once&&!enables);
 power.usb=0;run(&r);assert(sw.role==USB_ROLE_DEVICE&&!enables&&!r.external_host);
 r=reset();policy=true;cc.usb=power.usb=1;phy.flags=0;
 run(&r);assert(sw.role==USB_ROLE_DEVICE&&!enables);
 phy.flags=CHARGER_DETECT_DONE;run(&r);assert(sw.role==USB_ROLE_HOST&&!enables);
 r=reset();cc.host=1;run(&r);assert(sw.role==USB_ROLE_HOST&&enables==1&&r.enabled&&!phy_external);
 run(&r);assert(enables==1);cc.host=0;run(&r);assert(disables==1&&!r.enabled&&sw.role==USB_ROLE_DEVICE);
 r=reset();cc.host=1;power.usb=1;run(&r);assert(!enables&&sw.role==USB_ROLE_DEVICE);
 r=reset();cc.host=cc.usb=1;run(&r);assert(!enables&&sw.role==USB_ROLE_DEVICE);
 r=reset();r.enabled=true;sw.role=USB_ROLE_HOST;policy=true;cc.usb=power.usb=1;
 run(&r);assert(disables==1&&!enables&&!r.enabled&&r.external_host&&sw.role==USB_ROLE_HOST);
 r=reset();r.enabled=true;sw.role=USB_ROLE_HOST;policy=true;cc.usb=power.usb=1;disable_error=-EIO;
 run(&r);assert(disables==1&&!enables&&r.enabled&&sw.role==USB_ROLE_HOST&&!phy_external);
 r=reset();cc.host=1;phy_error=-EIO;run(&r);assert(!enables&&sw.role==USB_ROLE_DEVICE);
 r=reset();cc.host=1;role_error=-EIO;run(&r);assert(!enables&&sw.role==USB_ROLE_DEVICE);
 r=reset();cc.host=1;enable_error=-EIO;run(&r);assert(enables==1&&!r.enabled&&sw.role==USB_ROLE_DEVICE);
 r=reset();cc.host=1;power_during_flush=1;run(&r);assert(!enables&&sw.role==USB_ROLE_DEVICE);
 r=reset();policy=true;cc.usb=power.usb=1;dwc.xhci=NULL;run(&r);assert(!enables&&sw.role==USB_ROLE_DEVICE);
 r=reset();policy=true;cc.usb=power.usb=1;r.stopped=true;run(&r);assert(!enables&&!transitions);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'role.c').write_text(code)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(root / 'role.c'), '-o', str(root / 'role')], check=True)
            subprocess.run([str(root / 'role')], check=True)


if __name__ == '__main__':
    unittest.main()
