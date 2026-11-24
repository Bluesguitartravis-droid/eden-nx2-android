// SPDX-License-Identifier: GPL-3.0-or-later
// Claude Lab: when Claude starts Eden or a game while the player is away, the Odin's screen may have
// timed out and locked. Eden then starts behind the lock screen with no surface: the game boots but
// never draws. The Lab wakes the screen and marks its starts so Eden shows over the lock screen.
// Starts by the player are unchanged (no show-over-lock for them).
package io.github.zortos293.lichii.nx2

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager

object Nx2Wake {
    /** Extra on intents the Lab starts Eden with. */
    const val EXTRA = "nx2_lab_wake"

    /** Turns the screen on for a few seconds if it is off (a started activity keeps it on). */
    @Suppress("DEPRECATION")
    fun wake(context: Context) {
        val power = context.getSystemService(PowerManager::class.java) ?: return
        if (power.isInteractive) return
        runCatching {
            power.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or
                    PowerManager.ON_AFTER_RELEASE,
                "eden:nx2-lab-wake"
            ).acquire(15_000)
        }
    }

    /** Marks an intent as a Lab start and wakes the screen. */
    fun prepare(context: Context, intent: Intent): Intent {
        intent.putExtra(EXTRA, true)
        wake(context)
        return intent
    }

    /** In an activity's onCreate/onNewIntent: show over the lock screen for Lab starts. */
    fun apply(activity: Activity, intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA, false) != true) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            activity.setShowWhenLocked(true)
            activity.setTurnScreenOn(true)
            val keyguard = activity.getSystemService(KeyguardManager::class.java)
            // Dismisses a swipe-only lock screen; a PIN stays, and Eden shows above it.
            runCatching { keyguard?.requestDismissKeyguard(activity, null) }
        }
    }
}
