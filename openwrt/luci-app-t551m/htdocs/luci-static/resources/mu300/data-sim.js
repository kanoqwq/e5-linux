'use strict';
'require baseclass';
'require mu300.common as M';

function attach(root) {
	root.className = 'mud-data-sim';
	root.innerHTML = '<div class="mud-data-sim-controls"><label for="mud-data-sim-select">上网 SIM 卡</label>' +
		'<select id="mud-data-sim-select" disabled><option value="0">SIM 1</option><option value="1">SIM 2</option></select>' +
		'<button type="button" class="mud-btn" disabled>切换上网卡</button></div>' +
		'<div class="mud-sub" data-current>--</div><div class="mud-sub" role="status" aria-live="polite" data-result></div>' +
		'<small class="mud-sub">切换会重新连接蜂窝网络，并保存为开机使用的卡槽。</small>';
	M.localize(root);
	var select = root.querySelector('select'), button = root.querySelector('button');
	var currentLine = root.querySelector('[data-current]'), result = root.querySelector('[data-result]');
	var current = null, saved = null, pending = false, disposed = false, timer, expires = 0;
	var storageKey = 'modem-data-sim-job';
	function controls() {
		select.disabled = pending || current == null;
		button.disabled = pending || current == null || (select.value === current && select.value === saved);
	}
	function message(text, error) {
		result.textContent = text;
		result.style.color = error ? 'var(--danger,#dc3545)' : '';
	}
	function finish(error, job) {
		if (disposed) return;
		if (!error && job) M.followDataSim(job.target, true);
		pending = false; clearTimeout(timer); sessionStorage.removeItem(storageKey);
		M.busy(button, false); controls();
		message(M.translate(error ? '切换失败：' : '上网卡已切换，正在刷新连接状态。') + M.translate(error || ''), !!error);
		// Reconcile with the actual card, including a switch from another UI.
		M.callSimGet().then(function(info) { if (!disposed) update(info); }).catch(function() {});
	}
	function watch(id) {
		if (disposed) return;
		if (Date.now() > expires) { finish(M.translate('切换等待超时，请刷新实际状态')); return; }
		M.callSimStatus(id).then(function(job) {
			if (disposed) return;
			if (job && job.state === 'done' && job.ok) finish(null, job);
			else if (!job || job.state !== 'running') finish(job && job.error || M.translate('无法读取切换结果'));
			else timer = setTimeout(function() { watch(id); }, 1000);
		}).catch(function() {
			// Brief HTTP/network loss during reconnection must not resubmit a switch.
			if (!disposed) timer = setTimeout(function() { watch(id); }, 1000);
		});
	}
	button.onclick = function() {
		if (pending || current == null || (select.value === current && select.value === saved)) return;
		var target = select.value, previous = current;
		pending = true; controls();
		M.confirmBox(M.translate('切换上网卡') + ' → SIM ' + (Number(target) + 1),
			M.translate('切换会重新连接蜂窝网络，并保存为开机使用的卡槽。')).then(function(yes) {
			if (disposed) return;
			if (!yes) { pending = false; controls(); return; }
			M.busy(button, true);
			message(M.translate('正在切换上网卡…'));
			return M.callSimSet(target, previous, true).then(function(job) {
				if (disposed) return;
				if (!job || !job.ok) { finish(job && job.error || M.translate('提交失败')); return; }
				if (job.state === 'done') { finish(null, job); return; }
				if (!/^[a-f0-9]{32}$/.test(job.id || '')) { finish(M.translate('无法读取切换结果')); return; }
				sessionStorage.setItem(storageKey, job.id); expires = Date.now() + 210000; watch(job.id);
			});
		}).catch(function(error) { finish(error.message || String(error)); });
	};
	select.onchange = controls;
	function update(modem) {
		if (disposed) return;
		var value = modem && modem.data_sim;
		var next = value === 0 || value === 1 ? String(value) : null;
		if (current == null || select.value === current) select.value = next || '0';
		current = next;
		saved = modem && [0,1].includes(modem.saved_sim) ? String(modem.saved_sim) : null;
		currentLine.textContent = M.translate('当前上网卡') + '：' + (current == null ? '--' : 'SIM ' + (Number(current) + 1)) +
			' · ' + M.translate(modem && modem.data_up ? '已连接' : '未连接') +
			' · ' + M.translate('开机使用') + '：' + (modem && [0,1].includes(modem.saved_sim) ? 'SIM ' + (modem.saved_sim + 1) : '--');
		controls();
	}
	var existing = sessionStorage.getItem(storageKey);
	if (/^[a-f0-9]{32}$/.test(existing || '')) {
		pending = true; M.busy(button, true); message(M.translate('正在切换上网卡…'));
		expires = Date.now() + 210000; watch(existing);
	}
	return { update: update, dispose: function() { disposed = true; clearTimeout(timer); } };
}

return baseclass.extend({ attach: attach });
