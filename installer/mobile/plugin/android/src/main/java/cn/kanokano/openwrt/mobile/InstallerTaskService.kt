package cn.kanokano.openwrt.mobile

import android.app.*
import android.content.Intent
import android.content.Context
import android.content.pm.ServiceInfo
import androidx.core.content.ContextCompat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.IBinder
import android.os.PowerManager

/** The process owns Python; the foreground service keeps active work visible. */
class InstallerTaskService : Service() {
    companion object {
        private const val FINISH = "cn.kanokano.openwrt.FINISH_TASK"
        private var requested = false
        @Synchronized fun beginTask(context: Context, local: Boolean) {
            ContextCompat.startForegroundService(context, Intent(context, InstallerTaskService::class.java).putExtra("local", local))
            requested = true
        }
        @Synchronized fun finishTask(context: Context) {
            if (!requested) return
            // A completion is another ordered start command, never stopService:
            // Android must first receive startForeground for the accepted start.
            context.startService(Intent(context, InstallerTaskService::class.java).setAction(FINISH))
            requested = false
        }
    }
    private var wake: PowerManager.WakeLock? = null
    private val handler = Handler(Looper.getMainLooper())
    private val renew = object : Runnable {
        override fun run() {
            wake?.acquire(10 * 60 * 1000L)
            handler.postDelayed(this, 5 * 60 * 1000L)
        }
    }
    private fun text(zh: String, en: String): String {
        val selected = getSharedPreferences("openwrt-tools", MODE_PRIVATE).getString("language", resources.configuration.locales[0].language)
        return if (selected == "zh") zh else en
    }
    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("openwrt-task", text("OpenWrt 操作进度", "OpenWrt operations"), NotificationManager.IMPORTANCE_LOW))
        wake = (getSystemService(POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "OpenWrt:task")
        wake?.setReferenceCounted(false)
        renew.run()
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = Notification.Builder(this, "openwrt-task")
            .setContentTitle("OpenWrt Tools")
            .setContentText(text("操作进行中，点击返回", "Operation in progress; tap to return"))
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentIntent(PendingIntent.getActivity(this, 0, packageManager.getLaunchIntentForPackage(packageName), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= 29) startForeground(410, notification,
            if (intent?.getBooleanExtra("local", false) == true) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        else startForeground(410, notification)
        // startId protects a newer queued start from an earlier completion.
        if (intent?.action == FINISH) stopSelf(startId)
        return START_NOT_STICKY
    }
    // A local analysis may hit Android's dataSync budget. Never kill Python in
    // the middle of an unrelated device transaction; release service ownership.
    override fun onTimeout(startId: Int, fgsType: Int) { stopSelf() }
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onDestroy() { handler.removeCallbacks(renew); if (wake?.isHeld == true) wake?.release(); super.onDestroy() }
}
