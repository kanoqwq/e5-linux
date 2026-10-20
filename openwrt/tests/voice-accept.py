#!/usr/bin/env python3
"""Exercise the real accept completion with fake AT results and D-Bus objects."""
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

source = (Path(sys.argv[1]) / 'mm-base-call.c').read_text()
start = source.index('static void\nhandle_accept_ready (')
callback = source[start:source.index('\n}\n', start) + 3]
model = r'''
#include <glib-object.h>
#define MM_GDBUS_CALL(x) (x)
#define mm_obj_warn(...) ((void)0)
#define mm_obj_msg(...) ((void)0)
enum { MM_CALL_STATE_RINGING_IN, MM_CALL_STATE_ACTIVE, MM_CALL_STATE_HELD,
       MM_CALL_STATE_TERMINATED, MM_CALL_STATE_REASON_ERROR, MM_CALL_STATE_REASON_ACCEPTED };
typedef struct {guint incoming_timeout;} Private;
typedef struct {Private *priv;guint state,reason;} MMBaseCall;
typedef struct {gboolean failed;} GAsyncResult;
typedef struct {MMBaseCall *self;GObject *modem;int *invocation;} HandleAcceptContext;
static guint completed,freed,removed,writes,errors;
static GQuark domain(void){return g_quark_from_static_string("fake-at");}
static gboolean finish(MMBaseCall*s,GAsyncResult*r,GError**e){
    if(r->failed)g_set_error_literal(e,domain(),14,"ATA result failed");
    return !r->failed;
}
static struct {gboolean(*accept_finish)(MMBaseCall*,GAsyncResult*,GError**);} klass={finish};
#define MM_BASE_CALL_GET_CLASS(x) (&klass)
static void mm_base_call_change_state(MMBaseCall*c,guint state,guint reason){c->state=state;c->reason=reason;writes++;}
static void mm_dbus_method_invocation_take_error(int*i,GError*e){
    g_assert_error(e,domain(),14);(*i)++;errors++;g_error_free(e);
}
static void handle_accept_context_free(HandleAcceptContext*c){freed++;}
static void mm_gdbus_call_complete_accept(MMBaseCall*c,int*i){completed++;}
static gboolean fake_remove(guint id){g_assert_cmpuint(id,==,7);removed++;return TRUE;}
#define g_source_remove fake_remove
'''
tests = r'''
int main(void){
    GObject *modem=g_object_new(G_TYPE_OBJECT,NULL);Private priv={7};
    MMBaseCall call={&priv,MM_CALL_STATE_RINGING_IN,0};int invocations=0;
    HandleAcceptContext ctx={&call,modem,&invocations};GAsyncResult failed={TRUE},ok={FALSE};
    g_object_set_data(modem,"e5-dual-sim-voice",GINT_TO_POINTER(1));
    /* A rejected/ambiguous answer keeps the same incoming call retryable.
     * Its original error still reaches the initiating UI/HFP caller. */
    handle_accept_ready(&call,&failed,&ctx);
    g_assert_cmpuint(call.state,==,MM_CALL_STATE_RINGING_IN);g_assert_cmpuint(writes,==,0);
    g_assert_cmpuint(errors,==,1);g_assert_cmpuint(completed,==,0);
    g_assert_cmpuint(priv.incoming_timeout,==,7);g_assert_cmpuint(removed,==,0);
    /* If CLCC wins the race before completion, neither ACTIVE nor HELD is
     * overwritten; a genuine end is also never resurrected. */
    guint states[]={MM_CALL_STATE_ACTIVE,MM_CALL_STATE_HELD,MM_CALL_STATE_TERMINATED};
    for(guint i=0;i<G_N_ELEMENTS(states);i++){
        call.state=states[i];handle_accept_ready(&call,&failed,&ctx);
        g_assert_cmpuint(call.state,==,states[i]);g_assert_cmpuint(writes,==,0);
    }
    /* Confirmed success retains the existing accepted state/timer semantics. */
    call.state=MM_CALL_STATE_RINGING_IN;handle_accept_ready(&call,&ok,&ctx);
    g_assert_cmpuint(call.state,==,MM_CALL_STATE_ACTIVE);g_assert_cmpuint(call.reason,==,MM_CALL_STATE_REASON_ACCEPTED);
    g_assert_cmpuint(completed,==,1);g_assert_cmpuint(removed,==,1);g_assert_cmpuint(priv.incoming_timeout,==,0);
    /* Other modem plugins retain upstream's error and success behavior. */
    g_object_set_data(modem,"e5-dual-sim-voice",NULL);
    call.state=MM_CALL_STATE_RINGING_IN;handle_accept_ready(&call,&failed,&ctx);
    g_assert_cmpuint(call.state,==,MM_CALL_STATE_TERMINATED);g_assert_cmpuint(call.reason,==,MM_CALL_STATE_REASON_ERROR);
    handle_accept_ready(&call,&ok,&ctx);g_assert_cmpuint(call.state,==,MM_CALL_STATE_ACTIVE);
    g_assert_cmpuint(errors,==,5);g_assert_cmpuint(invocations,==,5);
    g_assert_cmpuint(completed,==,2);g_assert_cmpuint(freed,==,7);
    g_object_unref(modem);return 0;
}
'''
with tempfile.TemporaryDirectory() as directory:
    p = Path(directory)
    (p / 'test.c').write_text(model + callback + tests)
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'gobject-2.0'], text=True))
    subprocess.run(['cc', '-Wall', '-Werror', str(p / 'test.c'), '-o', str(p / 'test'), *flags], check=True)
    subprocess.run([str(p / 'test')], check=True)
print('PASS: failed accept retains incoming/active/held calls, propagates error, never revives ended calls; generic behavior and success unchanged')
