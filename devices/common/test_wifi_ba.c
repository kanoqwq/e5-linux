/* Compile real BA code with fault injection; no radio/hardware access. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint64_t u64;
typedef int64_t s64;
#define MAX_LUT_NUM 32
#define SPRD_MODE_AP 3
#define SPRD_MODE_P2P_GO 4
#define SPRD_MODE_STATION 5
#define SPRD_MODE_P2P_CLIENT 6
#define SPRD_WORK_ADDBA 7
#define DOT11_ADDBA_POLICY_IMMEDIATE 1
#define CMD_ADDBA_REQ 40
#define CMD_DELBA_REQ 41
#define ADDBA_REQ_RESULT_SUCCESS 0
#define DEL_LUT_INDEX 0
#define ADD_LUT_INDEX 1
#define UPD_LUT_INDEX 2
#define GFP_KERNEL 0
#define fallthrough __attribute__((fallthrough))
#define wl_err(...) ((void)0)
#define wl_info(...) ((void)0)
static _Thread_local int locked;
#define spin_lock_bh(p) do { pthread_mutex_lock(p); assert(!locked); locked=1; } while(0)
#define spin_unlock_bh(p) do { assert(locked); locked=0; pthread_mutex_unlock(p); } while(0)
static bool test_bit(unsigned bit, unsigned long *p) { return (__atomic_load_n(p,__ATOMIC_RELAXED)&(1UL<<bit))!=0; }
static void set_bit(unsigned bit, unsigned long *p) { __atomic_fetch_or(p,1UL<<bit,__ATOMIC_RELAXED); }
static void clear_bit(unsigned bit, unsigned long *p) { __atomic_fetch_and(p,~(1UL<<bit),__ATOMIC_RELAXED); }
static void ether_addr_copy(u8 *a,const u8 *b) { memcpy(a,b,6); }
static bool ether_addr_equal(const u8 *a,const u8 *b) { return !memcmp(a,b,6); }
static bool is_valid_ether_addr(const u8 *a) { return !(a[0]&1) && memcmp(a,"\0\0\0\0\0\0",6); }
struct sprd_vif;
struct host_addba_param {
	u8 lut_index,perr_mac_addr[6],dialog_token;
	struct { u16 amsdu_permit:1,ba_policy:1,tid:4,buffer_size:10; } __attribute__((packed)) addba_param;
	u16 timeout;
} __attribute__((packed));
struct sprd_peer_entry {
	struct { u8 da[6]; } tx;
	u8 lut_index,ctx_id,ht_enable,vht_enable,vowifi_enabled,ip_acquired;
	unsigned long ba_tx_done_map;
	s64 time[8];
};
struct sprd_priv;
struct sprd_hif {
	struct sprd_priv *priv;
	pthread_mutex_t ba_lock;
	u64 ba_generation[32];
	unsigned long ba_pending[32],ba_rearm[32],tx_num[32];
	s64 ba_last_admit;
	bool ba_gate_valid;
	int hw_type,fw_power_down;
	struct sprd_peer_entry peer_entry[32];
};
struct sprd_priv { struct sprd_hif hif; };
struct sprd_vif { struct sprd_priv *priv; int mode; u8 ctx_id; };
struct sprd_work {
	struct sprd_vif *vif;
	int id,hw_type,len;
	void (*release)(struct sprd_work *);
	u8 data[];
};
struct evt_sta_lut_ind { u8 ctx_id,action,sta_lut_index,is_ht_enable,is_vht_enable,ra[6]; };
struct sprd_msg { u8 data[128]; };
static struct sprd_priv priv;
static struct sprd_vif vif;
static struct sprd_work *queue[64];
static pthread_mutex_t queue_lock=PTHREAD_MUTEX_INITIALIZER;
static s64 now;
static _Atomic int work_allocations,refs,allocated;
static int queued,commands,fail_work,fail_reply,fail_command,fw_error,reply_len,status;
static bool missing_vif;
static void (*during_command)(void);
static s64 ktime_get_ns(void) { return now; }
static struct sprd_hif *sc2355_get_hif(void) { return &priv.hif; }
static struct sprd_vif *sc2355_ctxid_to_vif(struct sprd_priv *p,u8 ctx) {
	if(missing_vif || ctx!=vif.ctx_id)return NULL;
	refs++;return &vif;
}
static void sprd_put_vif(struct sprd_vif *v) { assert(v==&vif);refs--; }
static void *kzalloc(size_t n,int flag) {
	assert(!locked);
	if(fail_reply)return NULL;
	void *p=calloc(1,n);assert(p);allocated++;return p;
}
static void kfree(void *p) { if(p){allocated--;free(p);} }
static struct sprd_work *sprd_alloc_work(int n) {
	assert(!locked);work_allocations++;
	if(fail_work)return NULL;
	struct sprd_work *w=calloc(1,sizeof(*w)+n);assert(w);w->len=n;allocated++;return w;
}
static void sprd_queue_work(struct sprd_priv *p,struct sprd_work *w) {
	assert(!locked);pthread_mutex_lock(&queue_lock);assert(queued<64);queue[queued++]=w;pthread_mutex_unlock(&queue_lock);
}
static void sc2355_work_host_wakeup_fw(struct sprd_vif *v) { assert(!locked); }
static struct sprd_msg *get_cmdbuf(struct sprd_priv *p,struct sprd_vif *v,int n,u8 cmd) {
	assert(!locked);assert(n==(int)sizeof(struct host_addba_param));assert(cmd==CMD_ADDBA_REQ);
	if(fail_command)return NULL;
	struct sprd_msg *m=calloc(1,sizeof(*m));assert(m);allocated++;return m;
}
static int send_cmd_recv_rsp(struct sprd_priv *p,struct sprd_msg *m,u8 *reply,u16 *n) {
	assert(!locked);commands++;
	struct host_addba_param *a=(void *)m->data;
	assert(a->dialog_token==1 && a->addba_param.buffer_size==64 && !a->addba_param.amsdu_permit);
	if(during_command)during_command();
	*n=reply_len;reply[0]=status;kfree(m);return fw_error;
}
static void sc2355_defrag_recover(struct sprd_vif *v,u8 i) { assert(!locked); }
static void sc2355_peer_entry_delba(struct sprd_hif *h,u8 i) { assert(!locked); }
static void sc2355_dis_flush_txlist(struct sprd_hif *h,u8 i) { assert(!locked); }
void sc2355_tx_addba(struct sprd_hif *,struct sprd_peer_entry *,u8);
/* BA_IMPLEMENTATION */

static void reset_fixture(void) {
	static bool initialized;
	assert(allocated==0 && refs==0);
	if(initialized)pthread_mutex_destroy(&priv.hif.ba_lock);
	memset(&priv,0,sizeof(priv));priv.hif.priv=&priv;pthread_mutex_init(&priv.hif.ba_lock,NULL);
	initialized=true;
	vif=(struct sprd_vif){&priv,SPRD_MODE_AP,1};queued=commands=work_allocations=0;
	fail_work=fail_reply=fail_command=fw_error=status=0;reply_len=13;missing_vif=false;during_command=NULL;now=10000000000LL;
	for(int i=0;i<32;i++)priv.hif.peer_entry[i].ctx_id=255;
}
static void event(int action,int lut,int ctx) {
	struct evt_sta_lut_ind e={ctx,action,lut,1,1,{2,0,0,0,0,lut}};
	sc2355_event_sta_lut(&vif,(void *)&e,sizeof(e));
	priv.hif.tx_num[lut]=10;
}
static void submit(int lut,int tid) { sc2355_tx_prepare_addba(&priv.hif,lut,&priv.hif.peer_entry[lut],tid); }
static void finish_queue(bool cancel) {
	for(int i=0;i<queued;i++) {
		struct sprd_work *w=queue[i];
		if(!cancel)sc2355_tx_send_addba(w->vif,w->data,w->len);
		w->release(w);kfree(w);
	}
	queued=0;assert(!refs && !allocated);
}
static void test_topology_and_admission(void) {
	reset_fixture();event(ADD_LUT_INDEX,4,1);submit(4,0);assert(queued==1);finish_queue(false);
	assert(priv.hif.peer_entry[4].ba_tx_done_map==1);
	for(int n=5;n<=6;n++) {
		now+=4000000000LL;event(ADD_LUT_INDEX,n,1);
		for(int i=4;i<=n;i++)assert(!priv.hif.peer_entry[i].ba_tx_done_map);
		for(int i=4;i<=n;i++) {
			submit(i,0);assert(queued==1);
			if(i<n) { int calls=work_allocations;submit(i+1,0);assert(queued==1 && work_allocations==calls); }
			finish_queue(false);now+=120000000;
		}
		for(int i=4;i<=n;i++)assert(priv.hif.peer_entry[i].ba_tx_done_map==1);
	}
	assert(commands==6);
}
static void test_scope_and_inflight_rearm(void) {
	reset_fixture();event(ADD_LUT_INDEX,4,1);submit(4,7);assert(queued==1);
	priv.hif.peer_entry[8].ctx_id=2;priv.hif.peer_entry[8].ht_enable=1;
	priv.hif.peer_entry[8].tx.da[0]=2;priv.hif.peer_entry[8].ba_tx_done_map=1;
	event(ADD_LUT_INDEX,5,1);assert(priv.hif.ba_rearm[4]==128);
	int calls=work_allocations;submit(4,7);assert(work_allocations==calls && queued==1);
	finish_queue(false);assert(!priv.hif.peer_entry[4].ba_tx_done_map);
	assert(priv.hif.peer_entry[8].ba_tx_done_map==1);
	priv.hif.peer_entry[5].ba_tx_done_map=1;vif.mode=SPRD_MODE_STATION;
	sc2355_ba_topology(&vif);assert(priv.hif.peer_entry[5].ba_tx_done_map==1);
	vif.mode=SPRD_MODE_P2P_GO;sc2355_ba_topology(&vif);assert(!priv.hif.peer_entry[5].ba_tx_done_map);
}
static void test_failures_and_retry(void) {
	for(int fault=0;fault<7;fault++) {
		reset_fixture();event(ADD_LUT_INDEX,4,1);
		if(fault==0)missing_vif=true;
		if(fault==1)fail_work=1;
		if(fault==2)fail_reply=1;
		if(fault==3)fail_command=1;
		if(fault==4)fw_error=-ETIMEDOUT;
		if(fault==5)reply_len=0;
		if(fault==6)status=1;
		submit(4,0);finish_queue(false);
		assert(!priv.hif.peer_entry[4].ba_tx_done_map && !priv.hif.ba_pending[4]);
		missing_vif=false;fail_work=fail_reply=fail_command=fw_error=status=0;reply_len=1;
		if(fault>=1) { int calls=work_allocations;submit(4,0);assert(work_allocations==calls); }
		now+=3001000000LL;submit(4,0);assert(queued==1);finish_queue(false);
		assert(priv.hif.peer_entry[4].ba_tx_done_map==1);
	}
}
static void reuse_during_command(void) {
	event(UPD_LUT_INDEX,4,1);now+=4000000000LL;submit(4,0);
}
static void deltxba_during_command(void) { sc2355_ba_deltxba(&priv.hif,4,0); }
static void test_cancel_and_reuse(void) {
	reset_fixture();event(ADD_LUT_INDEX,4,1);submit(4,0);finish_queue(true);
	assert(!priv.hif.ba_pending[4] && !priv.hif.peer_entry[4].ba_tx_done_map);
	now+=4000000000LL;submit(4,0);event(DEL_LUT_INDEX,4,1);event(ADD_LUT_INDEX,4,1);
	finish_queue(false);assert(commands==0);
	now+=4000000000LL;submit(4,0);during_command=reuse_during_command;
	/* Complete the old job only; its callback cannot clear the new claim. */
	struct sprd_work *old=queue[0];queued=0;
	sc2355_tx_send_addba(old->vif,old->data,old->len);old->release(old);kfree(old);
	assert(queued==1 && priv.hif.ba_pending[4]==1);
	during_command=NULL;finish_queue(false);assert(priv.hif.peer_entry[4].ba_tx_done_map==1);
	now+=4000000000LL;sc2355_ba_deltxba(&priv.hif,4,0);submit(4,0);during_command=deltxba_during_command;
	finish_queue(false);assert(!priv.hif.peer_entry[4].ba_tx_done_map);
}
static void test_guards_and_sta(void) {
	reset_fixture();event(ADD_LUT_INDEX,4,1);submit(4,8);
	sc2355_ba_deltxba(&priv.hif,32,0);sc2355_ba_deltxba(&priv.hif,4,8);
	sc2355_tx_prepare_addba(&priv.hif,32,&priv.hif.peer_entry[4],0);
	sc2355_tx_prepare_addba(&priv.hif,4,NULL,0);assert(queued==0);
	vif.mode=SPRD_MODE_STATION;submit(4,0);assert(!queued);
	priv.hif.peer_entry[4].ip_acquired=1;submit(4,0);assert(queued==1);finish_queue(false);
	assert(!priv.hif.ba_gate_valid);
	now+=4000000000LL;priv.hif.peer_entry[4].ba_tx_done_map=0;priv.hif.peer_entry[4].vowifi_enabled=1;
	submit(4,0);assert(!queued);
	priv.hif.peer_entry[4].vowifi_enabled=0;submit(4,0);assert(queued==1);
	priv.hif.peer_entry[4].vowifi_enabled=1;int sent=commands;
	finish_queue(false);assert(commands==sent && !priv.hif.ba_pending[4]);
}
static void *race_submit(void *unused) { for(int n=0;n<100;n++)submit(4,0);return NULL; }
static void test_parallel_tx(void) {
	reset_fixture();event(ADD_LUT_INDEX,4,1);pthread_t threads[8];
	for(int i=0;i<8;i++)assert(!pthread_create(&threads[i],NULL,race_submit,NULL));
	for(int i=0;i<8;i++)pthread_join(threads[i],NULL);
	assert(queued==1);finish_queue(false);assert(commands==1);
}
static void test_single_client_and_stable_updates(void) {
	reset_fixture();event(ADD_LUT_INDEX,4,1);
	submit(4,0);submit(4,6);assert(queued==2 && !priv.hif.ba_gate_valid);
	finish_queue(false);assert(priv.hif.peer_entry[4].ba_tx_done_map==65);
	now+=4000000000LL;event(ADD_LUT_INDEX,5,1);
	submit(4,0);finish_queue(false);now+=120000000LL;submit(5,0);finish_queue(false);
	assert(priv.hif.peer_entry[4].ba_tx_done_map==1);
	event(UPD_LUT_INDEX,5,1);
	/* Stock resets the updated peer, but a stable UPDATE must not reset 4. */
	assert(priv.hif.peer_entry[4].ba_tx_done_map==1);
	int calls=work_allocations;submit(5,0);assert(work_allocations==calls && queued==0);
	event(DEL_LUT_INDEX,5,1);sc2355_ba_deltxba(&priv.hif,4,0);now+=4000000000LL;
	submit(4,0);submit(4,6);assert(queued==2);finish_queue(false);
}
int main(void) {
	_Static_assert(sizeof(struct host_addba_param)==12,"wire format changed");
	test_topology_and_admission();test_scope_and_inflight_rearm();test_failures_and_retry();
	test_cancel_and_reuse();test_guards_and_sta();test_parallel_tx();
	test_single_client_and_stable_updates();
	puts("PASS: real BA C lifecycle, V4 gate/rearm, failure cleanup, cancellation, LUT reuse, concurrent TX and STA isolation");
}
