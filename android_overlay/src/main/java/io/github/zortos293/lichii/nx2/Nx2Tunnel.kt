// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: runs libnx2tunnel.so (tools/nx2tunnel, ngrok) so the Claude bridge on 127.0.0.1 gets a
// public https address. Restarts it with a growing delay if it stops while the bridge is on.
// Runs in the Claude Lab process (":nx2lab", Nx2FrontService). The address and the last error are
// also written to files/nx2-lab/tunnel.json so the Lab screen in Eden's own process can show them.
package io.github.zortos293.lichii.nx2

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File

object Nx2Tunnel {
    private const val TAG = "NX2_TUNNEL"
    @Volatile private var process: java.lang.Process? = null
    @Volatile private var url: String? = null
        set(value) { field = value; writeState() }
    @Volatile var lastError: String? = null
        private set(value) { field = value; writeState() }
    @Volatile private var stateTarget: File? = null
    @Volatile private var wanted = false
    private var generation = 0

    fun publicUrl(): String? = url

    fun isRunning(): Boolean = process?.isAlive == true

    @Synchronized
    fun start(context: Context, token: String, port: Int) {
        wanted = true
        stateTarget = stateFile(context)
        if (token.isBlank()) {
            lastError = "Add your ngrok code in the Lab screen"
            return
        }
        if (isRunning()) return
        val binary = File(context.applicationInfo.nativeLibraryDir, "libnx2tunnel.so")
        if (!binary.isFile) {
            lastError = "Tunnel program missing from this build"
            return
        }
        val myGeneration = ++generation
        Thread({ runLoop(binary, token, port, myGeneration) }, "NX2-Tunnel").apply { isDaemon = true }.start()
    }

    /** files/nx2-lab/tunnel.json: {"url": ..., "error": ...}, written by the Lab process. */
    fun stateFile(context: Context) = File(File(context.filesDir, "nx2-lab"), "tunnel.json")

    /** Address and error as the Lab process last wrote them (for Eden's own process). */
    fun sharedState(context: Context): Pair<String?, String?> {
        val json = runCatching { JSONObject(stateFile(context).readText()) }.getOrNull()
            ?: return null to null
        return json.optString("url").ifEmpty { null } to json.optString("error").ifEmpty { null }
    }

    private fun writeState() {
        val file = stateTarget ?: return
        runCatching {
            file.parentFile?.mkdirs()
            val tmp = File(file.parentFile, file.name + ".tmp")
            tmp.writeText(JSONObject().put("url", url ?: "").put("error", lastError ?: "").toString())
            tmp.renameTo(file)
        }
    }

    @Synchronized
    fun stop() {
        wanted = false
        generation++
        process?.destroy()
        process = null
        url = null
    }

    private fun runLoop(binary: File, token: String, port: Int, myGeneration: Int) {
        var delayMs = 2000L
        while (wanted && myGeneration == generation) {
            try {
                val builder = ProcessBuilder(binary.absolutePath, "http://127.0.0.1:$port")
                    .redirectErrorStream(true)
                builder.environment()["NGROK_AUTHTOKEN"] = token
                val started = builder.start()
                process = started
                lastError = null
                started.inputStream.bufferedReader().forEachLine { line ->
                    when {
                        line.startsWith("URL ") -> {
                            url = line.removePrefix("URL ").trim()
                            delayMs = 2000L
                            Log.i(TAG, "Online: $url")
                        }
                        line.startsWith("ERR ") -> {
                            lastError = line.removePrefix("ERR ").trim()
                            Log.w(TAG, line)
                        }
                    }
                }
                started.waitFor()
            } catch (error: Exception) {
                lastError = error.toString()
                Log.w(TAG, "Tunnel failed", error)
            }
            url = null
            process = null
            if (!wanted || myGeneration != generation) break
            Thread.sleep(delayMs)
            delayMs = (delayMs * 2).coerceAtMost(60_000L)
        }
    }
}
