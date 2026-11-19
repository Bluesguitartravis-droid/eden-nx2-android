// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: restarts the app for the Claude bridge's restart_app tool. Runs in its own process
// (":nx2restart"), ends the main process, then starts the target (the game, or the home screen).
package io.github.zortos293.lichii.nx2

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Process
import java.io.File

class Nx2RestartActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pid = intent.getIntExtra(EXTRA_PID, -1)
        @Suppress("DEPRECATION")
        val target = intent.getParcelableExtra<Intent>(EXTRA_TARGET)
        if (pid > 0 && pid != Process.myPid()) Process.killProcess(pid)
        val handler = Handler(Looper.getMainLooper())
        val startedAt = System.currentTimeMillis()
        val check = object : Runnable {
            override fun run() {
                val alive = pid > 0 && File("/proc/$pid").exists()
                if (alive && System.currentTimeMillis() - startedAt < 5000) {
                    handler.postDelayed(this, 100)
                    return
                }
                if (target != null) runCatching { startActivity(target) }
                finish()
                handler.postDelayed({ Process.killProcess(Process.myPid()) }, 500)
            }
        }
        handler.postDelayed(check, 300)
    }

    companion object {
        private const val EXTRA_PID = "nx2_pid"
        private const val EXTRA_TARGET = "nx2_target"

        fun restart(context: Context, target: Intent, pid: Int) {
            context.startActivity(Intent(context, Nx2RestartActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION)
                putExtra(EXTRA_PID, pid)
                putExtra(EXTRA_TARGET, target)
            })
        }
    }
}
