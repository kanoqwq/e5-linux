#!/usr/bin/env python3
"""Compile the production SIM event decisions with fake AT/MM objects."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
source = (root / 'plugins/unisoc/mm-broadband-modem-unisoc.c').read_text()


def function(name, result='void'):
    start = source.index('static ' + result + '\n' + name + ' (')
    return source[start:source.index('\n}\n', start) + 3]


model = r'''
#include <glib.h>
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
typedef int MMModemState;
enum { MM_MODEM_STATE_UNKNOWN, MM_MODEM_STATE_INITIALIZING, MM_MODEM_STATE_FAILED,
       MM_MODEM_STATE_LOCKED, MM_MODEM_STATE_REGISTERED };
typedef struct { int dummy; } MMBaseSim;
static void sim_unref(MMBaseSim *sim) {}
G_DEFINE_AUTOPTR_CLEANUP_FUNC(MMBaseSim,sim_unref)
typedef struct {
 gboolean sim_events_enabled,sim_check_running,sim_check_again;
} MMBroadbandModemUnisocPrivate;
typedef struct { MMBroadbandModemUnisocPrivate priv; MMBaseSim *sim; int state; } MMBaseModem;
typedef MMBaseModem MMBroadbandModemUnisoc;
typedef MMBaseModem MMIfaceModem;
typedef struct { const char *reply; int error; } GAsyncResult;
typedef struct { int success,error,unrefs; } GTask;
typedef void (*GAsyncReadyCallback)(void);
#define MM_IFACE_MODEM(p) (p)
#define MM_BROADBAND_MODEM_UNISOC(p) (p)
#define MM_IFACE_MODEM_SIM "sim"
#define MM_IFACE_MODEM_STATE "state"
#define MM_MOBILE_EQUIPMENT_ERROR 1
#define MM_MOBILE_EQUIPMENT_ERROR_SIM_NOT_INSERTED 10
#define MM_CORE_ERROR 2
#define MM_CORE_ERROR_FAILED 1
static int reprobes,parent_checks,queries;
static MMBroadbandModemUnisocPrivate *get_private(MMBaseModem *m){return &m->priv;}
static void g_object_get(MMBaseModem *m,...){
 va_list ap;va_start(ap,m);const char *key;
 while((key=va_arg(ap,const char*))){void *v=va_arg(ap,void*);
 if(!strcmp(key,"sim"))*(MMBaseSim**)v=m->sim;
 else if(!strcmp(key,"state"))*(int*)v=m->state;
 else assert(0);
 }va_end(ap);
}
static void *g_object_ref(void *p){return p;}
static void g_object_unref(GTask *task){task->unrefs++;}
static void g_task_return_error(GTask *task,GError *error){task->error=error->code;g_error_free(error);}
static void g_task_return_boolean(GTask *task,gboolean ok){task->success=ok;}
static const char *mm_base_modem_at_command_finish(MMBaseModem *m,GAsyncResult *res,GError **error){
 if(res->error)*error=g_error_new(MM_MOBILE_EQUIPMENT_ERROR,res->error,"AT failure");
 return res->reply;
}
static void mm_iface_modem_process_sim_event(MMBaseModem *m){reprobes++;}
static void sim_parent_check_ready(void){}
static void sim_event_checked(void){}
static void parent_check(MMIfaceModem *m,GAsyncReadyCallback callback,GTask *task){parent_checks++;}
static struct {void (*check_for_sim_swap)(MMIfaceModem*,GAsyncReadyCallback,GTask*);} parent={parent_check};
static typeof(&parent) iface_modem_parent=&parent;
static void sim_swap_check(MMIfaceModem *m,GAsyncReadyCallback callback,gpointer data){queries++;}
'''
tests = r'''
int main(void){
 const char *valid[]={"3,0","3,2","3,100","3,4","3,1,2","3,1,34"," 3 , 1 , 25 ","3,1,1","3,1,7",
                      "3,0,0,1"," 3 , 0 , 0 , 1 ","3,2,1,1","3,1,2,0,1"};
 const char *bad[]={"","3","3,1","3,1,99","2,1,2","3,2junk","3,2,1,x","-3,2","3,-2","3,4294967296","3,1,-2",
                    "3,0,0,1junk","3,0,0,-1","3,0,0,4294967296","3,0,0,","3,0,,1","3,1,99,0,1"};
 for(unsigned i=0;i<G_N_ELEMENTS(valid);i++)assert(sim_ecind_changed(valid[i]));
 for(unsigned i=0;i<G_N_ELEMENTS(bad);i++)assert(!sim_ecind_changed(bad[i]));
 MMBaseSim card={0};MMBaseModem modem={.sim=&card,.state=MM_MODEM_STATE_REGISTERED,.priv.sim_events_enabled=1};
 GAsyncResult inputs[]={{NULL,14},{NULL,0},{"+CPIN: READY",0},{NULL,10}};
 for(unsigned i=0;i<G_N_ELEMENTS(inputs);i++){
  GTask task={0};reprobes=parent_checks=0;
  sim_pin_checked(&modem,&inputs[i],&task);
  if(i<2)assert(task.error && !reprobes && !parent_checks);
  if(i==2)assert(parent_checks==1 && !reprobes);
  if(i==3)assert(reprobes==1 && task.success && !parent_checks);
 }
 modem.state=MM_MODEM_STATE_FAILED;GTask task={0};reprobes=0;
 sim_pin_checked(&modem,&inputs[3],&task);assert(task.success && !reprobes);
 modem.sim=NULL;task=(GTask){0};
 sim_pin_checked(&modem,&inputs[2],&task);assert(reprobes==1 && !parent_checks);
 modem.priv.sim_events_enabled=0;task=(GTask){0};
 sim_pin_checked(&modem,&inputs[2],&task);assert(reprobes==1 && !parent_checks);
 modem=(MMBaseModem){.priv.sim_events_enabled=1,.state=MM_MODEM_STATE_INITIALIZING};
 sim_check_request(&modem);assert(!queries);
 modem.state=MM_MODEM_STATE_REGISTERED;
 sim_check_request(&modem);assert(queries==1 && modem.priv.sim_check_running);
 sim_check_request(&modem);sim_check_request(&modem);
 assert(queries==1 && modem.priv.sim_check_again);
 modem.priv.sim_events_enabled=0;sim_check_request(&modem);assert(queries==1);
 /* Actual K1 insertion: 3,100 while busy, then extended ready 3,0,0,1.
  * The busy query must not remove/reprobe; the later event must not be lost. */
 modem=(MMBaseModem){.priv.sim_events_enabled=1,.state=MM_MODEM_STATE_FAILED};
 queries=reprobes=parent_checks=0;
 assert(sim_ecind_changed("3,100"));sim_check_request(&modem);assert(queries==1);
 task=(GTask){0};sim_pin_checked(&modem,&inputs[0],&task);
 assert(task.error==14 && !reprobes && !parent_checks);
 modem.priv.sim_check_running=FALSE;
 assert(sim_ecind_changed("3,0,0,1"));sim_check_request(&modem);assert(queries==2);
 task=(GTask){0};sim_pin_checked(&modem,&inputs[2],&task);
 assert(task.success && reprobes==1 && !parent_checks);
 puts("PASS: stock ECIND filtering, confirmed absence vs busy, no duplicate removal, event coalescing, unchanged boot path");
}
'''
with tempfile.TemporaryDirectory(prefix='e5-sim-events-') as directory:
    path = Path(directory)
    (path/'test.c').write_text(model + function('power_parse_uint', 'gboolean') +
                             function('sim_ecind_changed', 'gboolean') +
                             function('sim_pin_checked') + function('sim_check_request') + tests)
    flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', 'glib-2.0'], text=True).split()
    subprocess.run(['cc', '-Wall', '-Werror', str(path/'test.c'), '-o', str(path/'test'), *flags], check=True)
    subprocess.run([str(path/'test')], check=True)
