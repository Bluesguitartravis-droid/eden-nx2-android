// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: runs libnx2tunnel.so (tools/nx2tunnel, ngrok) so the Claude bridge on 127.0.0.1 gets a
// public https address. Restarts it with a growing delay if it stops while the bridge is on.
package io.github.zortos293.lichii.nx2

import android.content.Context
import android.util.Log
import java.io.File

object Nx2Tunnel {
    private const val TAG = "NX2_TUNNEL"
    @Volatile private var process: java.lang.Process? = null
    @Volatile private var url: String? = null
    @Volatile var lastError: String? = null
        private set
    @Volatile private var wanted = false
    private var generation = 0

    fun publicUrl(): String? = url

    fun isRunning(): Boolean = process?.isAlive == true

    @Synchronized
    fun start(context: Context, token: String, port: Int) {
        wanted = true
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
