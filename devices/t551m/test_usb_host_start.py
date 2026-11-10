"""Execute the actual host startup tail and core against stale gadget registers."""
from pathlib import Path
import subprocess
import sys
import tempfile

kernel = Path(sys.argv[1])
glue = (kernel / 'drivers/usb/musb/musb_sprd.c').read_text()
core = (kernel / 'drivers/usb/musb/musb_core.c').read_text()
start = glue.index('static int musb_sprd_otg_start_host(')
start = glue.index('\n\t\tMUSB_HST_MODE(musb);', start) if 'Restore POWER and IRQ masks' not in glue else glue.index('\n\t\tmusb_reset_all_fifo_2_default(musb);', start)
tail = glue[start:glue.index('\n\t} else {', start)]
start = core.index('static void musb_enable_interrupts(')
end = core.index('\n/*', core.index('\nvoid musb_start(', start) + 1)
functions = core[start:end]
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define __iomem
typedef uint8_t u8;
enum {MUSB_DEVCTL, MUSB_INTRTXE, MUSB_INTRRXE, MUSB_INTRUSBE, MUSB_TESTMODE, MUSB_POWER};
enum {MUSB_POWER_ISOUPDATE=0x80, MUSB_POWER_HSENAB=0x20, MUSB_DEVCTL_SESSION=1,
      MUSB_DEVCTL_VBUS=0x18, MUSB_HOST=1, MUSB_OTG=3, USB_SPEED_HIGH=3,
      USB_SPEED_UNKNOWN=0, OTG_STATE_A_WAIT_BCON=7, OTG_STATE_B_IDLE=1,
      MUSB_DMA_SPRD=0x400, USB_DR_MODE_HOST=1};
struct config {int maximum_speed;};
struct ops {int quirks;};
struct musb { unsigned char *mregs; unsigned short intrtxe,intrrxe,epmask;
    int is_active,port_mode,state,softconnect; struct config *config; struct ops *ops; };
struct sprd_glue {int dr_mode;};
static int locked,host,resumed,reset,data_role;
#define musb_dbg(...) ((void)0)
#define MUSB_HST_MODE(m) (host=1)
#define spin_lock_irq(p) (locked=1)
#define spin_unlock_irq(p) (locked=0)
static u8 musb_readb(u8 *p,int off) {return p[off];}
static void musb_writeb(u8 *p,int off,u8 val) {p[off]=val;}
static void musb_writew(u8 *p,int off,unsigned short val) {p[off]=val;}
static int musb_get_state(struct musb *m) {return m->state;}
static void musb_set_state(struct musb *m,int state) {m->state=state;}
static void musb_reset_all_fifo_2_default(struct musb *m) {reset++;}
static void musb_platform_enable(struct musb *m) {
    assert(locked && host && reset);
    m->is_active=1; m->mregs[MUSB_DEVCTL] |= MUSB_DEVCTL_SESSION;
}
static void musb_host_resume_root_hub(struct musb *m) {assert(!locked); resumed++;}
static void sprd_external_data_role(struct sprd_glue *g,bool host_mode) {
    assert(!locked && host_mode); data_role++;
}
'''
test = r'''
int main(void) {
    for (int stale=0; stale<3; stale++) {
        unsigned char regs[6]={0}; struct config cfg={USB_SPEED_HIGH};struct ops ops={MUSB_DMA_SPRD};
        struct musb m={.mregs=regs,.config=&cfg,.epmask=0xff,.port_mode=MUSB_OTG,
            .softconnect=1,.is_active=1,.ops=&ops}; struct sprd_glue g={0};
        /* Cold boot, disconnected gadget, and previously suspended host. */
        regs[MUSB_POWER] = stale ? 0xe3 : 0;
        regs[MUSB_INTRUSBE] = stale ? 4 : 0;
        regs[MUSB_DEVCTL] = stale ? MUSB_DEVCTL_VBUS : 0;
        locked=host=resumed=reset=data_role=0;
        start_host(&g,&m);
        assert(g.dr_mode==USB_DR_MODE_HOST && host && !locked);
        assert(m.state==OTG_STATE_A_WAIT_BCON && !m.softconnect);
        assert(regs[MUSB_POWER]==(MUSB_POWER_ISOUPDATE|MUSB_POWER_HSENAB));
        assert(regs[MUSB_DEVCTL]&MUSB_DEVCTL_SESSION);
        assert(regs[MUSB_INTRUSBE]==0xf7);
        assert(regs[MUSB_INTRTXE]==0xff && regs[MUSB_INTRRXE]==0xfe);
        assert(resumed==1 && reset==1 && data_role==1);
    }
    puts("PASS: cold/gadget/suspended-host startup restores IRQs, speed, session and root hub");
}
'''
with tempfile.TemporaryDirectory(prefix='musb-host-start-') as tmp:
    path = Path(tmp)
    (path/'test.c').write_text(prelude+functions+
        '\nstatic void start_host(struct sprd_glue *glue, struct musb *musb) {\n'+tail+'\n}\n'+test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    str(path/'test.c'), '-o', str(path/'test')], check=True)
    subprocess.run([str(path/'test')], check=True)
