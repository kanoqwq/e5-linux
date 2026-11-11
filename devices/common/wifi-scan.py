#!/usr/bin/env python3
"""SC2355 scan request ownership; retain the E5 scan/abort protocol."""
from pathlib import Path


CANCEL = '''static bool sc2355_cancel_scan(struct sprd_vif *vif,
                               struct cfg80211_scan_request *request)
{
	struct sprd_priv *priv = vif->priv;
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;
	u8 fw_ver = (api + CMD_SCAN)->fw_version;
	bool cancelled = false;

	/* Wait outside scan_lock; a completed request cannot also be rejected. */
	timer_delete_sync(&priv->scan_timer);
	spin_lock_bh(&priv->scan_lock);
	if (priv->scan_request == request && priv->scan_vif == vif) {
		if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE)
			fw_ver = min(fw_ver, (api + CMD_SCAN)->drv_version);
		if (!list_empty(&vif->scan_head_ptr))
			scan_clean_list(vif);
		if (vif->mode == SPRD_MODE_AP && fw_ver == 1)
			clean_survey_info_list(vif);
		priv->scan_request = NULL;
		priv->scan_vif = NULL;
		cancelled = true;
	}
	spin_unlock_bh(&priv->scan_lock);
	return cancelled;
}
'''

GUARD = '''	/* Preserve the single firmware scan context and its existing owner. */
	spin_lock_bh(&priv->scan_lock);
	ret = priv->scan_request ? -EBUSY : 0;
	spin_unlock_bh(&priv->scan_lock);
	if (ret)
		return ret;'''

FAILURE = '''		if (!sc2355_cancel_scan(vif, request))
			return 0; /* Completion or timeout won the reply race. */'''


def apply(kernel):
    path = Path(kernel)/'drivers/unisoc_platform/sprd_wlan_combo/sc2355/scan.c'
    text = path.read_text()
    original = text
    if CANCEL not in text:
        start = text.index('static void sc2355_cancel_scan(struct sprd_vif *vif)')
        end = text.index('\nstatic void sc2355_cancel_sched_scan', start)
        old = text[start:end]
        if old.count('cfg80211_scan_done(') != 1 or old.count('timer_delete_sync(') != 1:
            raise ValueError('Unexpected SC2355 rejected-scan cleanup')
        text = text[:start] + CANCEL + text[end:]
    old = '''	if (priv->scan_request)
		netdev_err(vif->ndev, "%s error scan %p running [%p, %p]\\n",
			   __func__, priv->scan_request, priv->scan_vif, vif);'''
    for before, after in ((old, GUARD), ('\t\tsc2355_cancel_scan(vif);', FAILURE)):
        if after in text:
            continue
        if text.count(before) != 1:
            raise ValueError('Unexpected SC2355 scan callback')
        text = text.replace(before, after, 1)
    if text != original:
        path.write_text(text)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
