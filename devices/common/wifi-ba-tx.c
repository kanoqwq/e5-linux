/* SPDX-License-Identifier: GPL-2.0-only */
/* SC2355 BA lifecycle recovery; port of the user's V4 rearm/gate policy. */
void sc2355_tx_prepare_addba(struct sprd_hif *hif, unsigned char lut_index,
			     struct sprd_peer_entry *peer_entry, unsigned char tid)
{
	if (lut_index >= MAX_LUT_NUM || tid >= 8 ||
	    peer_entry != &hif->peer_entry[lut_index] ||
	    hif->tx_num[lut_index] <= 9 || !peer_entry->ht_enable ||
	    peer_entry->vowifi_enabled == 1 ||
	    test_bit(tid, &peer_entry->ba_tx_done_map) ||
	    test_bit(tid, &hif->ba_pending[lut_index]))
		return;
	sc2355_tx_addba(hif, peer_entry, tid);
}

void sc2355_tx_addba(struct sprd_hif *hif,
		     struct sprd_peer_entry *peer_entry, unsigned char tid)
{
	struct sc2355_ba_work saved, *payload;
	struct host_addba_param *addba = &saved.request;
	struct sprd_work *work;
	struct sprd_vif *vif;
	s64 now;
	u8 lut;
	bool ready;

	if (tid >= 8 || !peer_entry)
		return;
	memset(&saved, 0, sizeof(saved));
	addba->addba_param.tid = tid;
	spin_lock_bh(&hif->ba_lock);
	lut = peer_entry->lut_index;
	if (lut >= MAX_LUT_NUM || peer_entry != &hif->peer_entry[lut] ||
	    !peer_entry->ht_enable || peer_entry->vowifi_enabled == 1 ||
	    !is_valid_ether_addr(peer_entry->tx.da)) {
		spin_unlock_bh(&hif->ba_lock);
		return;
	}
	addba->lut_index = lut;
	ether_addr_copy(addba->perr_mac_addr, peer_entry->tx.da);
	saved.ctx_id = peer_entry->ctx_id;
	saved.generation = hif->ba_generation[lut];
	spin_unlock_bh(&hif->ba_lock);
	vif = sc2355_ctxid_to_vif(hif->priv, saved.ctx_id);
	if (!vif)
		return;
	/* Gate/retry rejection must not allocate on every data packet. */
	spin_lock_bh(&hif->ba_lock);
	ready = sc2355_ba_ready(hif, vif, &saved, ktime_get_ns());
	spin_unlock_bh(&hif->ba_lock);
	if (!ready)
		goto put_vif;
	work = sprd_alloc_work(sizeof(saved));
	if (!work) {
		/* Retain the stock per-peer retry interval under memory pressure,
		 * without claiming BA success or blocking any other client.
		 */
		spin_lock_bh(&hif->ba_lock);
		now = ktime_get_ns();
		if (sc2355_ba_ready(hif, vif, &saved, now))
			peer_entry->time[tid] = now;
		spin_unlock_bh(&hif->ba_lock);
		goto put_vif;
	}
	now = ktime_get_ns();
	spin_lock_bh(&hif->ba_lock);
	if (!sc2355_ba_ready(hif, vif, &saved, now)) {
		spin_unlock_bh(&hif->ba_lock);
		kfree(work);
		goto put_vif;
	}
	/* Short state lock only; no CAS loop, sleep, or firmware wait. */
	if (sc2355_ba_stagger(hif, vif)) {
		hif->ba_last_admit = now;
		hif->ba_gate_valid = true;
	}
	peer_entry->time[tid] = now;
	set_bit(tid, &hif->ba_pending[lut]);
	set_bit(tid, &peer_entry->ba_tx_done_map);
	spin_unlock_bh(&hif->ba_lock);
	addba->dialog_token = 1;
	addba->addba_param.amsdu_permit = 0;
	addba->addba_param.ba_policy = DOT11_ADDBA_POLICY_IMMEDIATE;
	addba->addba_param.tid = tid;
	addba->addba_param.buffer_size = 64;
	work->vif = vif;
	work->id = SPRD_WORK_ADDBA;
	work->hw_type = hif->hw_type;
	work->release = sc2355_ba_release_work;
	payload = (struct sc2355_ba_work *)work->data;
	*payload = saved;
	if (hif->fw_power_down == 1) {
		hif->fw_power_down = 0;
		sc2355_work_host_wakeup_fw(vif);
	}
	sprd_queue_work(vif->priv, work);
put_vif:
	sprd_put_vif(vif);
}

static int sc2355_ba_command(struct sprd_priv *priv, struct sprd_vif *vif,
			    void *data, int len, unsigned char cmd_id)
{
	struct sprd_msg *msg;
	u16 rlen = 1 + sizeof(struct host_addba_param);
	u8 *rbuf;
	int ret;

	/* Allocate reply first: get_cmdbuf owns a pooled command and skb. */
	rbuf = kzalloc(rlen, GFP_KERNEL);
	if (!rbuf)
		return -ENOMEM;
	msg = get_cmdbuf(priv, vif, len, cmd_id);
	if (!msg) {
		kfree(rbuf);
		return -ENOMEM;
	}
	memcpy(msg->data, data, len);
	ret = send_cmd_recv_rsp(priv, msg, rbuf, &rlen);
	if (!ret && cmd_id == CMD_ADDBA_REQ &&
	    (!rlen || rbuf[0] != ADDBA_REQ_RESULT_SUCCESS))
		ret = -EIO;
	kfree(rbuf);
	return ret;
}

void sc2355_tx_ba_mgmt(struct sprd_priv *priv, struct sprd_vif *vif,
			    void *data, int len, unsigned char cmd_id)
{
	sc2355_ba_command(priv, vif, data, len, cmd_id);
}

void sc2355_tx_send_addba(struct sprd_vif *vif, void *data, int len)
{
	struct sprd_hif *hif = &vif->priv->hif;
	struct sc2355_ba_work *work = data;
	bool valid;
	int ret;

	if (len != sizeof(*work))
		return;
	spin_lock_bh(&hif->ba_lock);
	valid = sc2355_ba_matches(hif, work) && vif->ctx_id == work->ctx_id;
	if (valid) {
		struct sprd_peer_entry *peer = &hif->peer_entry[work->request.lut_index];

		valid = peer->ht_enable && peer->vowifi_enabled != 1;
	}
	spin_unlock_bh(&hif->ba_lock);
	if (!valid) {
		sc2355_ba_finish(hif, work, false);
		return;
	}
	ret = sc2355_ba_command(vif->priv, vif, &work->request,
				 sizeof(work->request), CMD_ADDBA_REQ);
	sc2355_ba_finish(hif, work, !ret);
}

void sc2355_event_sta_lut(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_hif *hif = &vif->priv->hif;
	struct evt_sta_lut_ind *sta_lut = (void *)data;
	struct sprd_peer_entry *peer;
	bool changed;
	u8 i;

	if (!data || len < sizeof(*sta_lut) || hif != sc2355_get_hif())
		return;
	i = sta_lut->sta_lut_index;
	if (i >= MAX_LUT_NUM)
		return;
	peer = &hif->peer_entry[i];
	wl_info("ctx_id:%d,action:%d,lut:%d\n", sta_lut->ctx_id, sta_lut->action, i);
	switch (sta_lut->action) {
	case DEL_LUT_INDEX:
		spin_lock_bh(&hif->ba_lock);
		sc2355_ba_reset_locked(hif, i);
		peer->ht_enable = 0;
		peer->ip_acquired = 0;
		spin_unlock_bh(&hif->ba_lock);
		sc2355_defrag_recover(vif, i);
		sc2355_peer_entry_delba(hif, i);
		spin_lock_bh(&hif->ba_lock);
		memset(peer, 0, sizeof(*peer));
		peer->ctx_id = 0xff;
		hif->tx_num[i] = 0;
		spin_unlock_bh(&hif->ba_lock);
		sc2355_dis_flush_txlist(hif, i);
		break;
	case UPD_LUT_INDEX:
		sc2355_peer_entry_delba(hif, i);
		sc2355_dis_flush_txlist(hif, i);
		fallthrough;
	case ADD_LUT_INDEX:
		spin_lock_bh(&hif->ba_lock);
		changed = peer->ctx_id != sta_lut->ctx_id ||
			!ether_addr_equal(peer->tx.da, sta_lut->ra) ||
			peer->ht_enable != sta_lut->is_ht_enable ||
			peer->vht_enable != sta_lut->is_vht_enable;
		sc2355_ba_reset_locked(hif, i);
		if (changed)
			memset(peer->time, 0, sizeof(peer->time));
		peer->lut_index = i;
		peer->ctx_id = sta_lut->ctx_id;
		peer->ht_enable = sta_lut->is_ht_enable;
		peer->vht_enable = sta_lut->is_vht_enable;
		hif->tx_num[i] = 0;
		ether_addr_copy(peer->tx.da, sta_lut->ra);
		spin_unlock_bh(&hif->ba_lock);
#ifdef CONFIG_SPRD_WLAN_DEBUG
		wl_info("ctx_id%d,action%d,lut%d,%pM\n", sta_lut->ctx_id,
			sta_lut->action, i, sta_lut->ra);
#else
		wl_info("ctx_id%d,action%d,lut%d,%x:%x:%x:%x:xx:xx\n",
			sta_lut->ctx_id, sta_lut->action, i, sta_lut->ra[0],
			sta_lut->ra[1], sta_lut->ra[2], sta_lut->ra[3]);
#endif
		/* Stable firmware UPDATEs must not disturb the other clients. */
		if (changed && vif->ctx_id == sta_lut->ctx_id)
			sc2355_ba_topology(vif);
		break;
	default:
		break;
	}
}
