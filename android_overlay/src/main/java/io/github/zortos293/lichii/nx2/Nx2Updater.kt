// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: Claude installs a new test build by itself (install_build), from this repository's
// GitHub releases only. Android installs it over the running Eden (same signing key) and ends
// every Eden process; MY_PACKAGE_REPLACED then starts the Claude connection again, so Claude can
// carry on testing without the player.
//
// The first self-install needs the player once: Android asks to allow "Install unknown apps" for
// Eden and to confirm, because Eden is not yet the app that installed itself. After that Eden is
// its own installer and Android 12+ installs its updates without asking (USER_ACTION_NOT_REQUIRED).
package io.github.zortos293.lichii.nx2

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import io.github.zortos293.lichii.R
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

internal object Nx2Updater {
    private const val TAG = "NX2_UPDATE"
    private const val REPO = "Bluesguitartravis-droid/eden-nx2-android"
    private const val TAG_PREFIX = "lichii-test-"
    const val ACTION_STATUS = "io.github.zortos293.lichii.nx2.UPDATE_STATUS"
    private const val CHANNEL = "nx2_claude_lab"
    private const val CONFIRM_ID = 0x4E5B

    fun statusFile(context: Context) = File(File(context.filesDir, "nx2-lab").apply { mkdirs() }, "update.txt")

    fun note(context: Context, line: String) {
        val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
        runCatching {
            val file = statusFile(context)
            val old = if (file.isFile) file.readLines().takeLast(30) else emptyList()
            file.writeText((old + "$stamp $line").joinToString("\n") + "\n")
        }
        Log.i(TAG, line)
    }

    fun status(context: Context): String {
        val file = statusFile(context)
        return if (file.isFile) file.readText() else "(no install has run yet)"
    }

    private fun get(url: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 60_000
            instanceFollowRedirects = true
            setRequestProperty("Accept", "application/vnd.github+json")
            setRequestProperty("User-Agent", "eden-nx2-claude-lab")
        }

    private fun getJson(url: String): String {
        val connection = get(url)
        try {
            if (connection.responseCode != 200) {
                throw IllegalStateException("GitHub answered ${connection.responseCode} for $url")
            }
            return connection.inputStream.bufferedReader().use { it.readText() }
        } finally {
            connection.disconnect()
        }
    }

    /** The newest test release's tag, e.g. lichii-test-215. */
    private fun latestTag(): String {
        val releases = JSONArray(getJson("https://api.github.com/repos/$REPO/releases?per_page=30"))
        return (0 until releases.length()).map { releases.getJSONObject(it).optString("tag_name") }
            .filter { it.startsWith(TAG_PREFIX) }
            .maxByOrNull { it.removePrefix(TAG_PREFIX).toIntOrNull() ?: -1 }
            ?: throw IllegalStateException("No $TAG_PREFIX release found")
    }

    /** Downloads the build's APK from this repository's release and starts the install. */
    fun install(context: Context, build: String): String {
        val wanted = build.trim().removePrefix(TAG_PREFIX)
        val tag = when {
            wanted.isEmpty() || wanted == "latest" -> latestTag()
            wanted.all { it.isDigit() } -> TAG_PREFIX + wanted
            else -> return "build must be a number (e.g. 215) or latest"
        }
        val release = JSONObject(getJson("https://api.github.com/repos/$REPO/releases/tags/$tag"))
        val assets = release.getJSONArray("assets")
        val asset = (0 until assets.length()).map { assets.getJSONObject(it) }
            .firstOrNull { it.optString("name").endsWith(".apk") }
            ?: return "$tag has no APK attached"
        val apk = File(context.cacheDir, "nx2-update.apk")
        note(context, "downloading $tag (${asset.optString("name")}, ${asset.optLong("size") / 1048576} MiB)")
        val connection = get(asset.getString("browser_download_url"))
        connection.setRequestProperty("Accept", "application/octet-stream")
        try {
            if (connection.responseCode != 200) {
                return "Download of $tag failed: HTTP ${connection.responseCode}"
            }
            connection.inputStream.use { input -> apk.outputStream().use { input.copyTo(it, 1 shl 20) } }
        } finally {
            connection.disconnect()
        }
        val expected = asset.optLong("size", -1L)
        if (expected > 0 && apk.length() != expected) {
            return "Download of $tag was cut short (${apk.length()} of $expected bytes)"
        }
        val archive = context.packageManager.getPackageArchiveInfo(apk.path, 0)
        if (archive?.packageName != context.packageName) {
            return "$tag's APK is for ${archive?.packageName ?: "an unreadable package"}, not ${context.packageName}"
        }

        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(apk.length())
            if (Build.VERSION.SDK_INT >= 31) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("base.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out, 1 shl 20) }
                session.fsync(out)
            }
            val status = PendingIntent.getBroadcast(context, id,
                Intent(context, Nx2UpdateReceiver::class.java).setAction(ACTION_STATUS).putExtra("tag", tag),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)
            session.commit(status.intentSender)
        }
        note(context, "install of $tag committed (session $id)")
        return "Downloaded $tag (${apk.length() / 1048576} MiB) and started installing it. Android ends " +
            "Eden and this connection while it installs; the connection comes back by itself about " +
            "20-60 s later (check with status / update_status). If Android needs the player to confirm " +
            "(the first time), a \"Confirm Eden update\" notification appears on the device."
    }

    fun askPlayer(context: Context, confirm: Intent, tag: String) {
        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(confirm) }
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Claude Lab", NotificationManager.IMPORTANCE_LOW))
        val tap = PendingIntent.getActivity(context, 7, confirm,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        manager.notify(CONFIRM_ID, Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_notification_logo)
            .setContentTitle("Confirm Eden update")
            .setContentText("Claude wants to install $tag. Tap to confirm.")
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build())
    }
}

/** Install results, and Eden's own update finishing (MY_PACKAGE_REPLACED): starts the Claude
 *  connection again when the player had it on. */
class Nx2UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Nx2Updater.ACTION_STATUS -> {
                val tag = intent.getStringExtra("tag") ?: "the build"
                val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
                val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: ""
                if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                    @Suppress("DEPRECATION")
                    val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                    Nx2Updater.note(context, "$tag waits for the player to confirm")
                    if (confirm != null) Nx2Updater.askPlayer(context, confirm, tag)
                } else if (status == PackageInstaller.STATUS_SUCCESS) {
                    Nx2Updater.note(context, "$tag installed")
                } else {
                    Nx2Updater.note(context, "$tag install failed: status $status $message")
                }
            }
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                val version = runCatching {
                    context.packageManager.getPackageInfo(context.packageName, 0).versionName
                }.getOrNull()
                Nx2Updater.note(context, "Eden updated (version $version)")
                val prefs = context.getSharedPreferences("nx2_bridge", Context.MODE_PRIVATE)
                if (prefs.getBoolean("enabled", false)) {
                    Nx2FrontService.start(context, "", "", restartTunnel = true)
                }
            }
        }
    }
}
