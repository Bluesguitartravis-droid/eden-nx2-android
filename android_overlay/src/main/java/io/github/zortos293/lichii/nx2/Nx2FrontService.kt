// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: the Claude connection, in its own process (":nx2lab").
//
// Before build 191 the ngrok tunnel and the MCP server lived inside Eden's own process, so every
// low-memory kill, crash or restart_app cut Claude off ("404 endpoint offline", "Bad Gateway")
// until the player reopened Eden. Now this service owns the tunnel and the public server on
// 127.0.0.1:38617 and forwards each request to the bridge in Eden's process (Nx2Bridge, port
// 38618). While Eden is down it still answers: the tool list, status, last_exit, read_log, marks,
// the notebook and the switches file, and it can start Eden again (restart_app, launch_game).
// When Eden comes back the same connection simply reaches it again: no new chat, no new connector.
package io.github.zortos293.lichii.nx2

import android.app.ActivityManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.util.Log
import io.github.zortos293.lichii.R
import io.github.zortos293.lichii.activities.EmulationActivity
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.IOException
import java.net.ConnectException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors

class Nx2FrontService : Service() {
    @Volatile private var token = ""
    @Volatile private var secret = ""
    private var server: ServerSocket? = null
    private val pool = Executors.newFixedThreadPool(6) { r ->
        Thread(r, "NX2-Front").apply { isDaemon = true }
    }

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
            .setContentTitle("Claude connection is on")
            .setContentText("Stays up while Eden restarts. Tap to open Claude Lab.")
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
            Log.w(TAG, "Claude connection could not stay in the foreground", error)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        intent?.getStringExtra(EXTRA_TOKEN)?.let { token = it }
        intent?.getStringExtra(EXTRA_SECRET)?.let { secret = it }
        if (token.isEmpty() || secret.isEmpty()) {
            // Restarted by Android without the intent: read what Eden saved.
            val prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            if (token.isEmpty()) token = prefs.getString(KEY_TOKEN, "") ?: ""
            if (secret.isEmpty()) secret = prefs.getString(KEY_SECRET, "") ?: ""
        }
        startServer()
        if (intent?.getBooleanExtra(EXTRA_RESTART_TUNNEL, false) == true) Nx2Tunnel.stop()
        Nx2Tunnel.start(this, token, Nx2Bridge.PORT)
        return START_REDELIVER_INTENT
    }

    override fun onDestroy() {
        Nx2Tunnel.stop()
        runCatching { server?.close() }
        server = null
        runCatching { Nx2Tunnel.stateFile(this).delete() }
        super.onDestroy()
    }

    @Synchronized
    private fun startServer() {
        if (server != null) return
        try {
            val socket = ServerSocket(Nx2Bridge.PORT, 16, InetAddress.getByName("127.0.0.1"))
            server = socket
            Thread({
                while (!socket.isClosed) {
                    val client = try { socket.accept() } catch (_: Exception) { break }
                    pool.execute { handle(client) }
                }
            }, "NX2-Front-Accept").apply { isDaemon = true }.start()
            Log.i(TAG, "Front listening on 127.0.0.1:${Nx2Bridge.PORT}")
        } catch (error: Exception) {
            Log.w(TAG, "Front could not start", error)
            server = null
        }
    }

    // ---- requests -----------------------------------------------------------------------------

    private fun handle(client: Socket) {
        client.use { socket ->
            try {
                socket.soTimeout = 30000
                val input = BufferedInputStream(socket.getInputStream())
                val out = socket.getOutputStream()
                val request = Nx2Http.readRequest(input) ?: return
                val path = request.path.substringBefore('?')
                if (secret.isEmpty() || path.trimEnd('/') != "/mcp/$secret") {
                    Nx2Http.respond(out, 404, "Not Found", null)
                    return
                }
                when (request.method) {
                    "POST" -> {
                        val reply = forwardOrAnswer(request)
                        if (reply.status == 202) {
                            Nx2Http.respond(out, 202, "Accepted", null)
                        } else {
                            Nx2Http.respond(out, reply.status, if (reply.status == 200) "OK" else "Error",
                                reply.body, reply.contentType, mapOf("Mcp-Session-Id" to SESSION))
                        }
                    }
                    "DELETE" -> Nx2Http.respond(out, 200, "OK", null)
                    else -> Nx2Http.respond(out, 405, "Method Not Allowed", null, extra = mapOf("Allow" to "POST"))
                }
            } catch (error: Exception) {
                Log.w(TAG, "Request failed", error)
            }
        }
    }

    private class Reply(val status: Int, val body: ByteArray?, val contentType: String? = "application/json")

    private fun forwardOrAnswer(request: Nx2Http.Request): Reply {
        val text = String(request.body, Charsets.UTF_8)
        val messages = parse(text)
        val calls = messages.filter { it.optString("method") == "tools/call" }
            .map { it.optJSONObject("params")?.optString("name") ?: "" }

        // Eden's process is starting (or restarting): give it a moment rather than failing a call.
        var up = backendUp()
        if (!up && calls.isNotEmpty() && edenProcessAlive()) up = waitForBackend(20_000)
        if (up) {
            try {
                return forward(request)
            } catch (_: ConnectException) {
                // Went away between the check and the call: answer here.
            } catch (error: IOException) {
                Log.w(TAG, "Eden went away during a request", error)
                return answer(messages, died = true)
            }
        }
        return answer(messages, died = false)
    }

    private fun parse(text: String): List<JSONObject> = runCatching {
        val trimmed = text.trim()
        if (trimmed.startsWith("[")) {
            val array = JSONArray(trimmed)
            (0 until array.length()).map { array.getJSONObject(it) }
        } else listOf(JSONObject(trimmed))
    }.getOrDefault(emptyList())

    private fun backendUp(): Boolean = runCatching {
        Socket().use { it.connect(InetSocketAddress("127.0.0.1", Nx2Bridge.BACKEND_PORT), 500) }
        true
    }.getOrDefault(false)

    private fun waitForBackend(ms: Long): Boolean {
        val deadline = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < deadline) {
            if (backendUp()) return true
            Thread.sleep(500)
        }
        return false
    }

    /** Eden's own process (named after the package) exists, even if its bridge is not up yet. */
    private fun edenProcessAlive(): Boolean = runCatching {
        getSystemService(ActivityManager::class.java).runningAppProcesses
            ?.any { it.processName == packageName } ?: false
    }.getOrDefault(false)

    /** One request to the bridge in Eden's process. A plain socket, not HttpURLConnection:
     *  Android refuses cleartext HTTP from HttpURLConnection for this app (targetSdk 36, no
     *  network security exception), even to 127.0.0.1, which broke every call in build 193. */
    private fun forward(request: Nx2Http.Request): Reply {
        Socket().use { socket ->
            socket.connect(InetSocketAddress("127.0.0.1", Nx2Bridge.BACKEND_PORT), 2000)
            socket.soTimeout = 180_000
            val head = StringBuilder()
                .append("POST /mcp/").append(secret).append(" HTTP/1.1\r\n")
                .append("Host: 127.0.0.1:").append(Nx2Bridge.BACKEND_PORT).append("\r\n")
                .append("Content-Type: application/json\r\n")
                .append("Accept: application/json, text/event-stream\r\n")
            request.headers["mcp-protocol-version"]?.let {
                head.append("Mcp-Protocol-Version: ").append(it).append("\r\n")
            }
            head.append("Content-Length: ").append(request.body.size).append("\r\n")
                .append("Connection: close\r\n\r\n")
            val out = socket.getOutputStream()
            out.write(head.toString().toByteArray(Charsets.UTF_8))
            out.write(request.body)
            out.flush()
            val response = Nx2Http.readResponse(BufferedInputStream(socket.getInputStream()))
                ?: throw IOException("Eden closed the connection without a reply")
            return Reply(response.status, if (response.status == 202) null else response.body,
                response.headers["content-type"] ?: "application/json")
        }
    }

    // ---- answers while Eden is down -----------------------------------------------------------

    private fun answer(messages: List<JSONObject>, died: Boolean): Reply {
        val why = if (died) "Eden closed during the request" else "Eden is not running"
        val replies = JSONArray()
        for (message in messages) answerOne(message, why)?.let { replies.put(it) }
        if (replies.length() == 0) return Reply(202, null)
        val body = if (messages.size == 1 && replies.length() == 1) replies.getJSONObject(0).toString()
        else replies.toString()
        return Reply(200, body.toByteArray(Charsets.UTF_8))
    }

    private fun answerOne(message: JSONObject, why: String): JSONObject? {
        if (!message.has("id")) return null
        val reply = JSONObject().put("jsonrpc", "2.0").put("id", message.get("id"))
        val params = message.optJSONObject("params") ?: JSONObject()
        when (val method = message.optString("method")) {
            "initialize" -> {
                val asked = params.optString("protocolVersion", "")
                reply.put("result", JSONObject()
                    .put("protocolVersion", if (asked in PROTOCOLS) asked else PROTOCOLS[0])
                    .put("capabilities", JSONObject().put("tools", JSONObject()))
                    .put("serverInfo", JSONObject().put("name", "eden-nx2").put("version", "1"))
                    .put("instructions", Nx2Tools.INSTRUCTIONS))
            }
            "ping" -> reply.put("result", JSONObject())
            "tools/list" -> reply.put("result", JSONObject().put("tools", Nx2Tools.list()))
            "resources/list" -> reply.put("result", JSONObject().put("resources", JSONArray()))
            "prompts/list" -> reply.put("result", JSONObject().put("prompts", JSONArray()))
            "tools/call" -> {
                val name = params.optString("name")
                val args = params.optJSONObject("arguments") ?: JSONObject()
                reply.put("result", when {
                    // Eden ended itself to restart: wait for it rather than starting it twice.
                    name == "restart_app" && why.contains("during") -> Nx2Tools.textResult(
                        if (waitForBackend(25_000)) "Eden restarted; the connector reaches it again."
                        else "Eden is restarting; it is not up yet after 25 s. Try status in a moment.")
                    else -> null
                } ?: when (name) {
                    "restart_app" -> Nx2Tools.textResult(startEden(args.optBoolean("relaunch_game", true), null))
                    "launch_game" -> Nx2Tools.textResult(startEden(true, args.optString("query")))
                    in Nx2Tools.OFFLINE -> Nx2Tools.callOffline(this, name, args, why)
                    else -> Nx2Tools.errorResult("$why, so $name cannot run. " +
                        "Last exit: ${lastExitLine()}. " +
                        "Use restart_app (or launch_game) to start Eden; this connection stays up.")
                })
            }
            else -> reply.put("error", JSONObject().put("code", -32601).put("message", "Unknown method $method"))
        }
        return reply
    }

    private fun lastExitLine(): String = runCatching {
        Nx2Tools.callOffline(this, "last_exit", JSONObject().put("count", 1), "")
            .getJSONArray("content").getJSONObject(0).getString("text").trim()
    }.getOrDefault("unknown")

    /** Start Eden from this process. Android may hold back an activity start from the background,
     *  so a notification the player can tap is posted as well. */
    private fun startEden(withGame: Boolean, query: String?): String {
        val path = when {
            !query.isNullOrBlank() -> findGamePath(query) ?: return "No game matches \"$query\" (see list_games)"
            withGame -> Nx2Bridge.lastGamePath(this)
            else -> null
        }
        val target = if (path != null) {
            Intent(this, EmulationActivity::class.java).apply {
                action = Intent.ACTION_VIEW
                data = Uri.parse(path)
            }
        } else {
            packageManager.getLaunchIntentForPackage(packageName) ?: return "No launch intent"
        }
        target.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        Nx2Wake.prepare(this, target)
        runCatching { startActivity(target) }
        postStartNotification(target)
        val started = waitForBackend(25_000)
        if (started) cancelStartNotification()
        return if (started) {
            "Eden is up again" + (if (path != null) "; the game is booting." else ".")
        } else {
            "Asked Android to start Eden" + (if (path != null) " with the last game" else "") +
                ". It is not up after 25 s: Android may be holding the start back while another app " +
                "is in front. The player can tap the \"Start Eden\" notification."
        }
    }

    private fun findGamePath(query: String): String? = runCatching {
        val result = Nx2Tools.callOffline(this, "list_games", JSONObject(), "")
        val games = JSONArray(result.getJSONArray("content").getJSONObject(0).getString("text"))
        val q = query.trim().lowercase()
        val all = (0 until games.length()).map { games.getJSONObject(it) }
        (all.firstOrNull { it.optString("program_id").lowercase() == q }
            ?: all.firstOrNull { it.optString("title").lowercase().contains(q) })?.optString("path")
    }.getOrNull()

    private fun postStartNotification(target: Intent) {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val tap = PendingIntent.getActivity(this, 1, target,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        manager.notify(START_ID, Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_notification_logo)
            .setContentTitle("Start Eden")
            .setContentText("Claude asked to start Eden. Tap to open it.")
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build())
    }

    private fun cancelStartNotification() {
        runCatching { getSystemService(NotificationManager::class.java)?.cancel(START_ID) }
    }

    companion object {
        private const val TAG = "NX2_FRONT"
        const val PROCESS_SUFFIX = ":nx2lab"
        private const val CHANNEL = "nx2_claude_lab"
        private const val ID = 0x4E59
        private const val START_ID = 0x4E5A
        private const val SESSION = "nx2-eden"
        private const val PREFS = "nx2_bridge"
        private const val KEY_TOKEN = "ngrok_token"
        private const val KEY_SECRET = "secret"
        private const val EXTRA_TOKEN = "nx2_token"
        private const val EXTRA_SECRET = "nx2_secret"
        private const val EXTRA_RESTART_TUNNEL = "nx2_restart_tunnel"
        private val PROTOCOLS = listOf("2025-06-18", "2025-03-26", "2024-11-05")

        fun start(context: Context, token: String, secret: String, restartTunnel: Boolean) {
            try {
                context.startForegroundService(Intent(context, Nx2FrontService::class.java)
                    .putExtra(EXTRA_TOKEN, token)
                    .putExtra(EXTRA_SECRET, secret)
                    .putExtra(EXTRA_RESTART_TUNNEL, restartTunnel))
            } catch (error: Exception) {
                // Android refuses when the app is not in front; the running service keeps its settings.
                Log.w(TAG, "Claude connection service not started", error)
            }
        }

        fun stop(context: Context) {
            runCatching { context.stopService(Intent(context, Nx2FrontService::class.java)) }
            runCatching { File(File(context.filesDir, "nx2-lab"), "tunnel.json").delete() }
        }
    }
}
