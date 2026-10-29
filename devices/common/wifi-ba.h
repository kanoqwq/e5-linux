/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef SC2355_BA_RECOVERY_H
#define SC2355_BA_RECOVERY_H

#include "tx.h"
#include "common/delay_work.h"

/* Private work metadata: only request, never generation/ctx, goes to CP. */
struct sc2355_ba_work {
	struct host_addba_param request;
	u64 generation;
	u8 ctx_id;
};

static inline bool sc2355_ba_ap(struct sprd_vif *vif)
{
	return vif->mode == SPRD_MODE_AP || vif->mode == SPRD_MODE_P2P_GO;
}

static inline bool sc2355_ba_stagger(struct sprd_hif *hif, struct sprd_vif *vif)
{
	int i, peers = 0;

	if (!sc2355_ba_ap(vif))
		return false;
	for (i = 0; i < MAX_LUT_NUM; i++) {
		struct sprd_peer_entry *peer = &hif->peer_entry[i];

		if (peer->ctx_id == vif->ctx_id && peer->ht_enable &&
		    peer->vowifi_enabled != 1 && is_valid_ether_addr(peer->tx.da) &&
		    ++peers > 1)
			return true;
	}
	return false;
}

/* All *_locked helpers require hif->ba_lock. No firmware waits under it. */
static inline void sc2355_ba_reset_locked(struct sprd_hif *hif, u8 lut)
{
	hif->ba_generation[lut]++;
	hif->ba_pending[lut] = 0;
	hif->ba_rearm[lut] = 0;
	hif->peer_entry[lut].ba_tx_done_map = 0;
}

static inline void sc2355_ba_reset(struct sprd_hif *hif, u8 lut)
{
	if (lut >= MAX_LUT_NUM)
		return;
	spin_lock_bh(&hif->ba_lock);
	sc2355_ba_reset_locked(hif, lut);
	spin_unlock_bh(&hif->ba_lock);
}

static inline bool sc2355_ba_identity(struct sprd_hif *hif,
				     struct sc2355_ba_work *work)
{
	u8 lut = work->request.lut_index;
	u8 tid = work->request.addba_param.tid;
	struct sprd_peer_entry *peer;

	if (lut >= MAX_LUT_NUM || tid >= 8)
		return false;
	peer = &hif->peer_entry[lut];
	return hif->ba_generation[lut] == work->generation &&
		peer->ctx_id == work->ctx_id &&
		ether_addr_equal(peer->tx.da, work->request.perr_mac_addr);
}

static inline bool sc2355_ba_matches(struct sprd_hif *hif,
				     struct sc2355_ba_work *work)
{
	return sc2355_ba_identity(hif, work) &&
		test_bit(work->request.addba_param.tid,
			 &hif->ba_pending[work->request.lut_index]);
}

static inline bool sc2355_ba_ready(struct sprd_hif *hif, struct sprd_vif *vif,
				  struct sc2355_ba_work *work, s64 now)
{
	u8 lut = work->request.lut_index, tid = work->request.addba_param.tid;
	struct sprd_peer_entry *peer;

	if (!sc2355_ba_identity(hif, work) || vif->ctx_id != work->ctx_id)
		return false;
	peer = &hif->peer_entry[lut];
	return peer->ht_enable && peer->vowifi_enabled != 1 &&
		!test_bit(tid, &peer->ba_tx_done_map) &&
		!test_bit(tid, &hif->ba_pending[lut]) &&
		!((vif->mode == SPRD_MODE_STATION || vif->mode == SPRD_MODE_P2P_CLIENT) &&
		  !peer->ip_acquired) &&
		(!peer->time[tid] || now - peer->time[tid] > 3000000000LL) &&
		(!sc2355_ba_stagger(hif, vif) || !hif->ba_gate_valid ||
		 now - hif->ba_last_admit >= 120000000LL);
}

static inline void sc2355_ba_finish(struct sprd_hif *hif,
				  struct sc2355_ba_work *work, bool accepted)
{
	u8 lut = work->request.lut_index;
	u8 tid = work->request.addba_param.tid;

	spin_lock_bh(&hif->ba_lock);
	if (sc2355_ba_matches(hif, work)) {
		clear_bit(tid, &hif->ba_pending[lut]);
		if (!accepted || test_bit(tid, &hif->ba_rearm[lut]))
			clear_bit(tid, &hif->peer_entry[lut].ba_tx_done_map);
		else
			set_bit(tid, &hif->peer_entry[lut].ba_tx_done_map);
		clear_bit(tid, &hif->ba_rearm[lut]);
	}
	spin_unlock_bh(&hif->ba_lock);
}

static inline void sc2355_ba_release_work(struct sprd_work *work)
{
	if (work->len == sizeof(struct sc2355_ba_work))
		sc2355_ba_finish(&work->vif->priv->hif,
				  (struct sc2355_ba_work *)work->data, false);
}

static inline void sc2355_ba_topology(struct sprd_vif *vif)
{
	struct sprd_hif *hif = &vif->priv->hif;
	int i;

	if (!sc2355_ba_ap(vif))
		return;
	spin_lock_bh(&hif->ba_lock);
	if (!sc2355_ba_stagger(hif, vif)) {
		spin_unlock_bh(&hif->ba_lock);
		return;
	}
	for (i = 0; i < MAX_LUT_NUM; i++) {
		struct sprd_peer_entry *peer = &hif->peer_entry[i];

		if (peer->ctx_id != vif->ctx_id || !peer->ht_enable ||
		    peer->vowifi_enabled == 1 || !is_valid_ether_addr(peer->tx.da))
			continue;
		/* V4 rearm, scoped to this AP. In-flight attempts finish first. */
		hif->ba_rearm[i] |= hif->ba_pending[i];
		peer->ba_tx_done_map &= hif->ba_pending[i];
	}
	spin_unlock_bh(&hif->ba_lock);
}

static inline void sc2355_ba_deltxba(struct sprd_hif *hif, u8 lut, u8 tid)
{
	if (lut >= MAX_LUT_NUM || tid >= 8)
		return;
	spin_lock_bh(&hif->ba_lock);
	clear_bit(tid, &hif->peer_entry[lut].ba_tx_done_map);
	if (test_bit(tid, &hif->ba_pending[lut]))
		set_bit(tid, &hif->ba_rearm[lut]);
	spin_unlock_bh(&hif->ba_lock);
}
#endif
