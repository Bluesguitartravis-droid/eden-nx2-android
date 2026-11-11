// SPDX-License-Identifier: GPL-3.0-or-later
// NX2 Android flight recorder: bounded, local, opt-in diagnostic export.
package org.yuzu.yuzu_emu.utils

import android.app.Activity
import android.app.ActivityManager
import android.app.AlertDialog
import android.app.Application
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.os.Build
import android.os.Debug
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.os.PowerManager
import android.util.Log
import android.view.PixelCopy
import android.view.SurfaceView
import android.widget.Toast
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.lang.ref.WeakReference
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * Flight Recorder v1. Captures 12 recent low-resolution game frames (one every 2.5s),
 * Android memory/thermal samples, lifecycle events and Android's previous-process exit
 * reason. Native NX2 diagnostics and the previous native crash file are copied into a
 * ZIP only when the player chooses Share. No server, root or capture permissions.
 *
 * It cannot record an arbitrary native GPU crash synchronously: evidence is saved
 * BEFORE a failure and Android exit data is recovered on the NEXT app launch.
 */
object Nx2FlightRecorder {
    private const val TAG = "NX2_FLIGHT"
    private const val FRAME_COUNT = 12
    private const val CAPTURE_INTERVAL_MS = 2500L
    private const val MAX_EVENTS_BYTES = 2L * 1024L * 1024L
    private const val MAX_NATIVE_LOG_BYTES = 4L * 1024L * 1024L
    private val mainHandler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "NX2-Flight-IO").apply { isDaemon = true }
    }
    private val captureInProgress = AtomicBoolean(false)
    private var captureThread: HandlerThread? = null
    private var captureHandler: Handler? = null
    private var captureRunnable: Runnable? = null
    private var lastCapturedIndex = 0
    private var app: Application? = null
    private var baseDir: File? = null
    @Volatile private var currentSession: File? = null
    private var previousUnexpectedSession: File? = null
    private var surfaceViewRef: WeakReference<SurfaceView>? = null
    private var activityRef: WeakReference<Activity>? = null
    private var initialized = false

    @Synchronized
    fun initialize(application: Application) {
        if (initialized) return
        initialized = true
        app = application
        val base = File(application.filesDir, "nx2-flight-recorder")
        base.mkdirs()
        baseDir = base
        recoverPreviousProcess(application, base)
        worker.scheduleAtFixedRate({
            try {
                sampleMemory(application)
            } catch (error: Exception) {
                Log.w(TAG, "Memory sample failed", error)
            }
        }, 10, 10, TimeUnit.SECONDS)
        Log.i(TAG, "Flight recorder initialized (local-only)")
    }

    private fun recoverPreviousProcess(context: Context, base: File) {
        val marker = File(base, "active_session.txt")
        if (!marker.exists()) return
        val name = runCatching { marker.readText().trim() }.getOrDefault("")
        // Only accept our own generated folder names, never arbitrary file paths.
        if (!name.matches(Regex("[0-9]+-[0-9a-f]{8}"))) {
            marker.delete()
            return
        }
        val old = File(base, name)
        marker.delete()
        if (!old.isDirectory) return
        previousUnexpectedSession = old
        val activeSince = old.lastModified()
        val exit = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            try {
                val manager = context.getSystemService(ActivityManager::class.java)
                manager.getHistoricalProcessExitReasons(context.packageName, 0, 8)
                    .firstOrNull {
                        it.timestamp >= activeSince - 10000L &&
                            it.processName.startsWith(context.packageName)
                    }
            } catch (error: Exception) {
                Log.w(TAG, "Historical exit reason unavailable", error)
                null
            }
        } else null
        worker.execute {
            val details = JSONObject()
                .put("recovered_at_ms", System.currentTimeMillis())
                .put("previous_session", name)
                .put("exit_reason", exit?.reason ?: -1)
                .put("exit_status", exit?.status ?: -1)
                .put("exit_importance", exit?.importance ?: -1)
                .put("exit_timestamp_ms", exit?.timestamp ?: 0L)
                .put("description", exit?.description?.take(400) ?: "unavailable")
            runCatching { File(old, "process_exit.json").writeText(details.toString(2)) }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && exit != null) {
                try {
                    exit.traceInputStream?.use { input ->
                        FileOutputStream(File(old, "android_exit_trace.txt")).use { out ->
                            val bytes = ByteArray(16384)
                            var remaining = MAX_NATIVE_LOG_BYTES
                            while (remaining > 0) {
                                val n = input.read(bytes, 0, minOf(bytes.size.toLong(), remaining).toInt())
                                if (n <= 0) break
                                out.write(bytes, 0, n)
                                remaining -= n
                            }
                        }
                    }
                } catch (error: Exception) {
                    Log.w(TAG, "Android exit trace unavailable", error)
                }
            }
        }
    }

    @Synchronized
    fun start(activity: Activity) {
        if (!initialized) {
            val application = activity.application
            initialize(application)
        }
        activityRef = WeakReference(activity)
        if (currentSession == null) {
            val base = baseDir ?: return
            val id = System.currentTimeMillis().toString() + "-" +
                UUID.randomUUID().toString().substring(0, 8)
            val dir = File(base, id)
            File(dir, "frames").mkdirs()
            currentSession = dir
            val meta = JSONObject()
                .put("schema", 1)
                .put("session", id)
                .put("started_ms", System.currentTimeMillis())
                .put("started_elapsed_ns", SystemClock.elapsedRealtimeNanos())
                .put("android_api", Build.VERSION.SDK_INT)
                .put("device_manufacturer", Build.MANUFACTURER)
                .put("device_model", Build.MODEL)
                .put("app_package", activity.packageName)
                .put("app_version", try {
                    activity.packageManager.getPackageInfo(activity.packageName, 0).versionName
                } catch (_: Exception) { "unknown" })
            // Small synchronous marker: survives a SIGKILL even if the writer thread dies.
            runCatching {
                File(dir, "session.json").writeText(meta.toString(2))
                FileOutputStream(File(base, "active_session.txt")).use { out ->
                    out.write(id.toByteArray(Charsets.UTF_8))
                    out.fd.sync()
                }
            }
            worker.execute { pruneOldSessions(base, dir) }
            event("session_start")
        } else {
            event("activity_recreated")
        }
    }

    fun attachSurface(view: SurfaceView) {
        surfaceViewRef = WeakReference(view)
    }

    fun resume(activity: Activity) {
        activityRef = WeakReference(activity)
        event("activity_resumed")
        if (captureRunnable != null) return
        if (captureThread == null) {
            captureThread = HandlerThread("NX2-Flight-Capture").also { it.start() }
            captureHandler = Handler(captureThread!!.looper)
        }
        val ticker = object : Runnable {
            override fun run() {
                if (captureRunnable !== this) return
                if (screenshotsEnabled(activity)) captureFrame()
                mainHandler.postDelayed(this, CAPTURE_INTERVAL_MS)
            }
        }
        captureRunnable = ticker
        mainHandler.postDelayed(ticker, CAPTURE_INTERVAL_MS)
    }

    fun pause() {
        captureRunnable?.let { mainHandler.removeCallbacks(it) }
        captureRunnable = null
        event("activity_paused")
    }

    @Synchronized
    fun stop(activity: Activity) {
        pause()
        event("session_clean_stop")
        val dir = currentSession
        val marker = baseDir?.let { File(it, "active_session.txt") }
        if (dir != null && marker != null && marker.exists()) {
            val same = runCatching { marker.readText().trim() == dir.name }.getOrDefault(false)
            if (same) marker.delete()
        }
        currentSession = null
        surfaceViewRef = null
        activityRef = null
    }

    fun screenshotsEnabled(context: Context): Boolean =
        context.getSharedPreferences("nx2_flight", Context.MODE_PRIVATE)
            .getBoolean("screenshots", true)

    fun toggleScreenshots(context: Context): Boolean {
        val enabled = !screenshotsEnabled(context)
        context.getSharedPreferences("nx2_flight", Context.MODE_PRIVATE)
            .edit().putBoolean("screenshots", enabled).apply()
        event("screenshots_toggle", if (enabled) "on" else "off")
        return enabled
    }

    /** Stored as a small JSON line on a dedicated worker, never on the render thread. */
    fun event(name: String, detail: String = "") {
        val dir = currentSession ?: return
        val data = JSONObject()
            .put("event", name.take(70))
            .put("wall_ms", System.currentTimeMillis())
            .put("elapsed_ns", SystemClock.elapsedRealtimeNanos())
            .put("pid", Process.myPid())
            .put("detail", detail.take(300))
        worker.execute { appendEvent(dir, data.toString()) }
    }

    private fun appendEvent(dir: File, line: String) {
        try {
            val file = File(dir, "events.jsonl")
            val encoded = (line + "\n").toByteArray(Charsets.UTF_8)
            if (file.length() + encoded.size > MAX_EVENTS_BYTES) {
                val previous = File(dir, "events.previous.jsonl")
                previous.delete()
                file.renameTo(previous)
            }
            FileOutputStream(file, true).use { out ->
                out.write(encoded)
                out.fd.sync()
            }
        } catch (error: Exception) {
            Log.w(TAG, "Unable to write flight-recorder event", error)
        }
    }

    private fun sampleMemory(context: Context) {
        if (currentSession == null) return
        val memory = Debug.MemoryInfo()
        Debug.getMemoryInfo(memory)
        val manager = context.getSystemService(ActivityManager::class.java)
        val system = ActivityManager.MemoryInfo()
        manager.getMemoryInfo(system)
        val thermal = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            context.getSystemService(PowerManager::class.java)?.currentThermalStatus ?: -1
        } else -1
        val values = JSONObject()
            .put("pss_kib", memory.totalPss)
            .put("private_dirty_kib", memory.totalPrivateDirty)
            .put("system_available_mib", system.availMem / 1048576)
            .put("system_low_memory", system.lowMemory)
            .put("java_heap_used_mib",
                (Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory()) / 1048576)
            .put("threads", Thread.activeCount())
            .put("thermal_status", thermal)
        event("memory_sample", values.toString())
    }

    private fun captureFrame() {
        if (!captureInProgress.compareAndSet(false, true)) return
        val activity = activityRef?.get()
        val dir = currentSession
        val callbackHandler = captureHandler
        if (activity == null || dir == null || callbackHandler == null ||
            activity.isFinishing || activity.isDestroyed
        ) {
            captureInProgress.set(false)
            return
        }
        val bitmap = try {
            Bitmap.createBitmap(480, 270, Bitmap.Config.ARGB_8888)
        } catch (_: OutOfMemoryError) {
            captureInProgress.set(false)
            return
        }
        val callback = PixelCopy.OnPixelCopyFinishedListener { result ->
            if (result != PixelCopy.SUCCESS) {
                bitmap.recycle()
                captureInProgress.set(false)
                return@OnPixelCopyFinishedListener
            }
            val slot = lastCapturedIndex++ % FRAME_COUNT
            worker.execute {
                try {
                    val frames = File(dir, "frames")
                    frames.mkdirs()
                    val filename = "frame-" + slot.toString().padStart(2, '0') + ".webp"
                    val target = File(frames, filename)
                    val temp = File(frames, filename + ".tmp")
                    FileOutputStream(temp).use { out ->
                        bitmap.compress(Bitmap.CompressFormat.WEBP_LOSSY, 55, out)
                        out.fd.sync()
                    }
                    if (!temp.renameTo(target)) {
                        target.delete()
                        temp.renameTo(target)
                    }
                    appendEvent(dir, JSONObject()
                        .put("event", "frame_saved")
                        .put("wall_ms", System.currentTimeMillis())
                        .put("elapsed_ns", SystemClock.elapsedRealtimeNanos())
                        .put("slot", slot)
                        .toString())
                } catch (error: Exception) {
                    Log.w(TAG, "Frame capture could not be saved", error)
                } finally {
                    bitmap.recycle()
                    captureInProgress.set(false)
                }
            }
        }
        try {
            val surface = surfaceViewRef?.get()
            if (surface != null && surface.holder.surface.isValid) {
                PixelCopy.request(surface, bitmap, callback, callbackHandler)
            } else {
                PixelCopy.request(activity.window, bitmap, callback, callbackHandler)
            }
        } catch (error: Exception) {
            Log.w(TAG, "PixelCopy unavailable", error)
            bitmap.recycle()
            captureInProgress.set(false)
        }
    }

    fun shareReport(activity: Activity, recovered: Boolean = false) {
        val dir = if (recovered) previousUnexpectedSession else currentSession
            ?: previousUnexpectedSession
        if (dir == null || !dir.isDirectory) {
            Toast.makeText(activity, "No NX2 diagnostic session found", Toast.LENGTH_LONG).show()
            return
        }
        event("report_requested", if (recovered) "recovered" else "manual")
        Toast.makeText(activity, "Preparing NX2 diagnostic ZIP...", Toast.LENGTH_SHORT).show()
        worker.execute {
            try {
                val zip = buildReport(activity.applicationContext, dir)
                activity.runOnUiThread {
                    if (activity.isFinishing || activity.isDestroyed) return@runOnUiThread
                    try {
                        val uri = FileProvider.getUriForFile(
                            activity, activity.packageName + ".provider", zip)
                        val intent = Intent(Intent.ACTION_SEND).apply {
                            type = "application/zip"
                            putExtra(Intent.EXTRA_STREAM, uri)
                            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        }
                        activity.startActivity(Intent.createChooser(intent, "Share NX2 diagnostics"))
                    } catch (error: Exception) {
                        Log.e(TAG, "Could not share diagnostic ZIP", error)
                        Toast.makeText(activity, "Could not share diagnostic ZIP",
                            Toast.LENGTH_LONG).show()
                    }
                }
            } catch (error: Exception) {
                Log.e(TAG, "Could not build diagnostic ZIP", error)
                activity.runOnUiThread {
                    if (!activity.isFinishing && !activity.isDestroyed) {
                        Toast.makeText(activity, "Diagnostic export failed: " +
                            (error.message ?: "unknown"), Toast.LENGTH_LONG).show()
                    }
                }
            }
        }
    }

    /** Only offer this after an unexpectedly interrupted session, once per recovery. */
    fun offerRecovery(activity: Activity) {
        val dir = previousUnexpectedSession ?: return
        val prefs = activity.getSharedPreferences("nx2_flight", Context.MODE_PRIVATE)
        if (prefs.getString("recovery_offered", "") == dir.name) return
        prefs.edit().putString("recovery_offered", dir.name).apply()
        AlertDialog.Builder(activity)
            .setTitle("NX2 diagnostic report available")
            .setMessage("The previous emulation session ended without a clean stop. " +
                "Its recent frames and diagnostics were preserved. Share a local ZIP?")
            .setPositiveButton("Share report") { _, _ -> shareReport(activity, true) }
            .setNegativeButton("Not now", null)
            .show()
    }

    private fun pruneOldSessions(base: File, current: File) {
        val keep = previousUnexpectedSession
        val folders = base.listFiles()?.filter { it.isDirectory }?.sortedByDescending {
            it.lastModified()
        } ?: return
        var kept = 0
        for (folder in folders) {
            if (folder == current || folder == keep) continue
            if (++kept > 3) folder.deleteRecursively()
        }
    }

    private fun buildReport(context: Context, dir: File): File {
        val reports = File(context.cacheDir, "nx2-flight-reports")
        reports.mkdirs()
        val archive = File(reports, "nx2-flight-" + dir.name + ".zip")
        ZipOutputStream(FileOutputStream(archive)).use { zip ->
            for (name in listOf("session.json", "events.jsonl",
                "events.previous.jsonl", "process_exit.json", "android_exit_trace.txt")) {
                val file = File(dir, name)
                if (file.isFile) addZipFile(zip, name, file, MAX_NATIVE_LOG_BYTES)
            }
            File(dir, "frames").listFiles()?.filter {
                it.isFile && it.name.endsWith(".webp")
            }?.sortedBy { it.lastModified() }?.forEach { frame ->
                addZipFile(zip, "frames/" + frame.name, frame, 1024L * 1024L)
            }
            val logDir = context.getExternalFilesDir(null)?.let { File(it, "log") }
            if (logDir != null) {
                for (name in listOf("eden_log.txt", "eden_log.old.txt",
                    "nx2_crash.txt")) {
                    val file = File(logDir, name)
                    if (file.isFile) {
                        addZipTail(zip, "logs/" + name, file, MAX_NATIVE_LOG_BYTES)
                    }
                }
            }
            val explanation = "NX2 Flight Recorder v1\n" +
                "Session: " + dir.name + "\n" +
                "Screenshots are sampled at 480x270, every 2.5 seconds, 12 frames retained.\n" +
                "Events are bounded to 2x2 MiB; native logs contain at most their last 4 MiB.\n" +
                "Android termination metadata is added after relaunch when available.\n" +
                "These are diagnostic samples, not a deterministic guest/GPU replay.\n" +
                "Review before sharing: logs may contain game titles and local file paths.\n"
            zip.putNextEntry(ZipEntry("README.txt"))
            zip.write(explanation.toByteArray(Charsets.UTF_8))
            zip.closeEntry()
        }
        return archive
    }

    private fun addZipFile(zip: ZipOutputStream, name: String, file: File, max: Long) {
        if (file.length() > max) return
        zip.putNextEntry(ZipEntry(name))
        file.inputStream().use { it.copyTo(zip, 16384) }
        zip.closeEntry()
    }

    private fun addZipTail(zip: ZipOutputStream, name: String, file: File, max: Long) {
        zip.putNextEntry(ZipEntry(name))
        RandomAccessFile(file, "r").use { source ->
            source.seek((source.length() - max).coerceAtLeast(0L))
            val buffer = ByteArray(16384)
            var remaining = max
            while (remaining > 0) {
                val n = source.read(buffer, 0, minOf(remaining, buffer.size.toLong()).toInt())
                if (n <= 0) break
                zip.write(buffer, 0, n)
                remaining -= n
            }
        }
        zip.closeEntry()
    }
}
