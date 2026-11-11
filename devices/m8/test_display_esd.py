"""Compile actual patched M8 FIFO/read/ESD functions, without a display device."""
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
kernel = Path(sys.argv[1])
base = Path('drivers/unisoc_platform/sprd_disp')


def function(text, name):
    start = re.search(r'(?m)^(?:static )?(?:int|void) ' + name + r'\(', text).start()
    return text[start:text.index('\n}', start) + 2]


HEAD = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define DRM_INFO(...) ((void)0)
#define DRM_WARN(...) ((void)0)
#define DRM_ERROR(...) (++errors)
#define pr_err(...) (++errors)
#define CMD_CODE_BL_PREFIX 0
#define DSI_MODE_CMD 1
struct drm_connector { void *dev; };
struct sprd_dpu { struct { int vrr_lock; } ctx; };
struct sprd_dsi { struct drm_connector connector; struct { bool enabled; } ctx; struct sprd_dpu *dpu; };
struct mipi_dsi_host { struct sprd_dsi *dsi; };
#define host_to_dsi(h) ((h)->dsi)
struct mipi_dsi_device { struct mipi_dsi_host *host; };
struct panel_info { bool cmd_dpi_mode; void *cmds[1]; int cmds_len[1]; u8 esd_check_reg,esd_check_val; };
struct sprd_panel { struct mipi_dsi_device *slave; struct panel_info info; int lock; bool enabled; };
static bool active=true;
static int size_result,read_result,reads,errors,cmd_result,payload_result,response_result;
static int payload_waits,rx_reads,mode;
static u8 read_value;
static bool sprd_check_crtc_active_state(void *dev,int index) { return active; }
static void mutex_lock(int *p) { assert(!*p);*p=1; }
static void mutex_unlock(int *p) { assert(*p);*p=0; }
static void dpu_wait_te_flush(void *ctx) { }
static int mipi_dsi_set_maximum_return_packet_size(struct mipi_dsi_device *d,int n) { return size_result; }
static int mipi_dsi_dcs_read(struct mipi_dsi_device *d,u8 reg,u8 *v,int n) { ++reads; *v=read_value;return read_result; }
static int sprd_panel_send_cmds(struct mipi_dsi_device *d,void *p,int n) { return 0; }
static int dsi_wait_tx_cmd_fifo_empty(struct sprd_dsi *d) { return cmd_result; }
static int dsi_wait_tx_payload_fifo_empty(struct sprd_dsi *d) { ++payload_waits;return payload_result; }
static int dsi_wait_rd_resp_completed(struct sprd_dsi *d) { return response_result; }
static bool dsi_hal_is_rx_payload_fifo_empty(struct sprd_dsi *d) { return rx_reads>0; }
static u32 dsi_hal_get_rx_payload(struct sprd_dsi *d) { ++rx_reads;return 0x9c; }
static void dsi_hal_set_packet_header(struct sprd_dsi *d,u8 a,u8 b,u8 c,u8 e) { }
static void dsi_hal_cmd_mode(struct sprd_dsi *d) { mode=DSI_MODE_CMD; }
static void dsi_hal_video_mode(struct sprd_dsi *d) { mode=2; }
'''
CASES = r'''
int main(void) {
    struct sprd_dpu dpu={0};struct sprd_dsi dsi={.ctx={true},.dpu=&dpu};
    struct mipi_dsi_host host={.dsi=&dsi};struct mipi_dsi_device slave={.host=&host};
    struct sprd_panel p={.slave=&slave,.enabled=true,.info={.esd_check_reg=10,.esd_check_val=0x9c}};
    int results[]={1,1,0,-ETIMEDOUT};u8 values[]={0x9c,0,0x9c,0x9c};int expected[]={0,-EINVAL,-EIO,-ETIMEDOUT};
    for (unsigned int i=0;i<4;++i) {
        size_result=0;read_result=results[i];read_value=values[i];reads=errors=0;
        assert(sprd_panel_esd_check(&p)==expected[i]);assert(reads==1);
        assert(!p.lock && !dpu.ctx.vrr_lock);
    }
    size_result=-EIO;reads=0;assert(sprd_panel_esd_check(&p)==-EIO && !reads);
    assert(!p.lock && !dpu.ctx.vrr_lock);
    active=false;assert(sprd_panel_esd_check(&p)==0);active=true;
    p.enabled=false;assert(sprd_panel_esd_check(&p)==0 && !p.lock);p.enabled=true;
    cmd_result=payload_result=0;payload_waits=errors=0;
    sprd_dsi_set_work_mode(&dsi,DSI_MODE_CMD);assert(payload_waits==1 && !errors && mode==DSI_MODE_CMD);
    payload_result=-ETIMEDOUT;sprd_dsi_set_work_mode(&dsi,2);assert(errors==1);
    payload_result=0;rx_reads=0;response_result=-ETIMEDOUT;u8 b=0;
    assert(sprd_dsi_rd_pkt(&dsi,0,0,0,0,&b,1)==-ETIMEDOUT && rx_reads==0);
    response_result=0;assert(sprd_dsi_rd_pkt(&dsi,0,0,0,0,&b,1)==1 && b==0x9c);
    return 0;
}
'''

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    for name in ('dsi/sprd_dsi_api.c', 'sprd_dsi_panel.c'):
        dest = root/base/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((kernel/base/name).read_bytes())
    apply = runpy.run_path(str(HERE/'display-esd.py'))['apply']
    apply(root)
    first = [(root/base/n).read_bytes() for n in ('dsi/sprd_dsi_api.c','sprd_dsi_panel.c')]
    apply(root)
    assert first == [(root/base/n).read_bytes() for n in ('dsi/sprd_dsi_api.c','sprd_dsi_panel.c')]
    api=(root/base/'dsi/sprd_dsi_api.c').read_text()
    panel=(root/base/'sprd_dsi_panel.c').read_text()
    code=HEAD+function(api,'sprd_dsi_set_work_mode')+'\n'+function(api,'sprd_dsi_rd_pkt')+'\n'+function(panel,'sprd_panel_esd_check')+CASES
    (root/'test.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
                    str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('PASS: FIFO success/error, read timeout/short/valid/bad status, lock balance and idempotent M8 preparation')
