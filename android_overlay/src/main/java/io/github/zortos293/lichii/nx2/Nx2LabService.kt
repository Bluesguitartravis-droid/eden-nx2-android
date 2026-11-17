// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: keeps Eden awake while Claude Lab is on. Without it Android freezes the app when the
// player switches to another app (the claude.ai page, say), the tunnel stops answering and
// Claude gets "404 endpoint offline". Shows a small "Claude Lab is on" notification.
package io.github.zortos293.lichii.nx2

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import io.github.zortos293.lichii.R

class Nx2LabService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        manager?.createNotificationChannel(
            NotificationChannel(CHANNEL, "Claude Lab", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while Claude can connect to Eden"
                setShowBadge(false)
            })
        val open = PendingIntent.getActivity(this, 0, Intent(this, Nx2LabActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_notification_logo)
            .setContentTitle("Claude Lab is on")
            .setContentText("Claude can connect to Eden. Tap to open Claude Lab.")
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(ID, notification, 0x40000000) // FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            } else {
                startForeground(ID, notification)
            }
        } catch (error: Exception) {
            Log.w("NX2_BRIDGE", "Claude Lab could not stay in the foreground", error)
            stopSelf()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    companion object {
        private const val CHANNEL = "nx2_claude_lab"
        private const val ID = 0x4E58

        fun start(context: Context) {
            try {
                context.startForegroundService(Intent(context, Nx2LabService::class.java))
            } catch (error: Exception) {
                // Android refuses when the app is not in front (e.g. started in the background).
                Log.w("NX2_BRIDGE", "Claude Lab service not started", error)
            }
        }

        fun stop(context: Context) {
            runCatching { context.stopService(Intent(context, Nx2LabService::class.java)) }
        }
    }
}
