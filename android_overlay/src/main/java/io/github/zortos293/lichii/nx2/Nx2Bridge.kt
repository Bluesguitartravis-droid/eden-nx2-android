// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: Claude bridge. An MCP server (Streamable HTTP, JSON responses) inside the app, so
// Claude can read performance, change settings and switches, take screenshots and press buttons.
// Off until the player turns it on in the Lab screen. It listens on 127.0.0.1:38618 only. The
// public side (ngrok tunnel and the server on 38617) runs in its own process, Nx2FrontService
// (":nx2lab"), which forwards requests here and keeps answering while Eden is closed or restarting.
// Every request must carry the secret path.
package io.github.zortos293.lichii.nx2

import android.app.ActivityManager
import android.app.Application
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Debug
import android.os.Handler
import android.os.HandlerThread
import android.os.PowerManager
import android.os.Process
import android.util.Base64
import android.view.PixelCopy
import androidx.preference.PreferenceManager
import io.github.zortos293.lichii.NativeLibrary
import io.github.zortos293.lichii.activities.EmulationActivity
import io.github.zortos293.lichii.features.input.NativeInput
import io.github.zortos293.lichii.features.input.model.NativeAnalog
import io.github.zortos293.lichii.features.input.model.NativeButton
import io.github.zortos293.lichii.features.settings.model.BooleanSetting
import io.github.zortos293.lichii.features.settings.model.ByteSetting
import io.github.zortos293.lichii.features.settings.model.FloatSetting
import io.github.zortos293.lichii.features.settings.model.IntSetting
import io.github.zortos293.lichii.features.settings.model.LongSetting
import io.github.zortos293.lichii.features.settings.model.ShortSetting
import io.github.zortos293.lichii.features.settings.model.StringSetting
import io.github.zortos293.lichii.utils.GameHelper
import io.github.zortos293.lichii.utils.NativeConfig
import io.github.zortos293.lichii.utils.Nx2FlightRecorder
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.io.RandomAccessFile
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.SecureRandom
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

object Nx2Bridge {
    private const val TAG = "NX2_BRIDGE"
    /** The Claude Lab front server (":nx2lab" process), behind the tunnel. */
    const val PORT = 38617
    /** This bridge, in Eden's own process; only the front server talks to it. */
    const val BACKEND_PORT = 38618
    private const val PREFS = "nx2_bridge"
    private const val KEY_ENABLED = "enabled"
    private const val KEY_SECRET = "secret"
    private const val KEY_TOKEN = "ngrok_token"
    private const val KEY_LAST_GAME = "last_game_path"
    private val PROTOCOLS = listOf("2025-06-18", "2025-03-26", "2024-11-05")

    private lateinit var app: Application
    private var server: ServerSocket? = null
    private val pool = Executors.newFixedThreadPool(4) { r ->
        Thread(r, "NX2-Bridge").apply { isDaemon = true }
    }
    private val inputPool = Executors.newSingleThreadExecutor { r ->
        Thread(r, "NX2-Bridge-Input").apply { isDaemon = true }
    }
    @Volatile var lastRequestMs = 0L
        private set
    @Volatile var requestCount = 0
        private set

    /** Frame statistics since the last call (NX2 Claude Lab window in nx2_session.h). */
    @JvmStatic external fun nativeLiveFrames(): String

    private fun prefs(): SharedPreferences = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** True in the restart helper's process (":nx2restart"), where the app must not start up. */
    fun isHelperProcess(application: Application): Boolean {
        val name = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) Application.getProcessName()
        else runCatching { File("/proc/self/cmdline").readText().trimEnd('\u0000') }.getOrDefault("")
        return name.endsWith(":nx2restart") || name.endsWith(Nx2FrontService.PROCESS_SUFFIX)
    }

    fun initialize(application: Application) {
        app = application
        if (isEnabled()) start()
    }

    fun isEnabled(): Boolean = prefs().getBoolean(KEY_ENABLED, false)

    fun setEnabled(enabled: Boolean) {
        prefs().edit().putBoolean(KEY_ENABLED, enabled).apply()
        if (enabled) start() else stop()
    }

    fun ngrokToken(): String = prefs().getString(KEY_TOKEN, "") ?: ""

    fun setNgrokToken(token: String) {
        prefs().edit().putString(KEY_TOKEN, token.trim()).apply()
    }

    /** The secret path segment: random, made once, kept until the player resets it. */
    fun secret(): String {
        prefs().getString(KEY_SECRET, null)?.let { if (it.isNotEmpty()) return it }
        return resetSecret()
    }

    fun resetSecret(): String {
        val bytes = ByteArray(24)
        SecureRandom().nextBytes(bytes)
        val value = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        prefs().edit().putString(KEY_SECRET, value).apply()
        // The front server checks the secret itself: give it the new one.
        if (::app.isInitialized && isEnabled()) Nx2FrontService.start(app, ngrokToken(), value, false)
        return value
    }

    /** The address to paste into Claude's connector settings, or null while the tunnel is down. */
    fun connectorUrl(): String? =
        Nx2Tunnel.sharedState(app).first?.let { it.trimEnd('/') + "/mcp/" + secret() }

    /** Why the tunnel is not up, as the Lab process reported it. */
    fun tunnelError(): String? = Nx2Tunnel.sharedState(app).second

    /** Remembered by EmulationActivity so restart_app can bring the same game back. */
    fun rememberGame(path: String?) {
        if (!::app.isInitialized || path.isNullOrEmpty()) return
        prefs().edit().putString(KEY_LAST_GAME, path).apply()
        // For the Lab process, whose copy of these preferences is not refreshed.
        runCatching { lastGameFile(app).apply { parentFile?.mkdirs() }.writeText(path) }
    }

    internal fun lastGameFile(context: Context) = File(File(context.filesDir, "nx2-lab"), "last_game.txt")

    /** The last game, readable from either process. */
    internal fun lastGamePath(context: Context): String? =
        runCatching { lastGameFile(context).readText().trim() }.getOrNull()?.ifEmpty { null }
            ?: context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_LAST_GAME, null)

    @Synchronized
    private fun start() {
        if (server != null) return
        try {
            val socket = ServerSocket(BACKEND_PORT, 16, InetAddress.getByName("127.0.0.1"))
            server = socket
            Thread({ acceptLoop(socket) }, "NX2-Bridge-Accept").apply { isDaemon = true }.start()
            android.util.Log.i(TAG, "Bridge listening on 127.0.0.1:$BACKEND_PORT")
        } catch (error: Exception) {
            android.util.Log.w(TAG, "Bridge could not start", error)
            server = null
        }
        Nx2LabService.start(app)
        Nx2FrontService.start(app, ngrokToken(), secret(), false)
    }

    @Synchronized
    private fun stop() {
        Nx2FrontService.stop(app)
        Nx2LabService.stop(app)
        runCatching { server?.close() }
        server = null
    }

    fun restartTunnel() {
        if (!isEnabled()) return
        Nx2FrontService.start(app, ngrokToken(), secret(), true)
    }

    private fun acceptLoop(socket: ServerSocket) {
        while (!socket.isClosed) {
            val client = try {
                socket.accept()
            } catch (_: Exception) {
                break
            }
            pool.execute { handle(client) }
        }
    }

    private fun handle(client: Socket) {
        client.use { socket ->
            try {
                socket.soTimeout = 30000
                val input = BufferedInputStream(socket.getInputStream())
                val out = socket.getOutputStream()
                val request = Nx2Http.readRequest(input) ?: return
                val path = request.path.substringBefore('?')
                if (path.trimEnd('/') != "/mcp/" + secret()) {
                    Nx2Http.respond(out, 404, "Not Found", null)
                    return
                }
                when (request.method) {
                    "POST" -> {
                        lastRequestMs = System.currentTimeMillis()
                        requestCount++
                        val reply = handleRpcBody(String(request.body, Charsets.UTF_8))
                        if (reply == null) {
                            Nx2Http.respond(out, 202, "Accepted", null)
                        } else {
                            Nx2Http.respond(out, 200, "OK", reply.toByteArray(Charsets.UTF_8),
                                "application/json", mapOf("Mcp-Session-Id" to "nx2-eden"))
                        }
                    }
                    "DELETE" -> Nx2Http.respond(out, 200, "OK", null)
                    else -> Nx2Http.respond(out, 405, "Method Not Allowed", null, extra = mapOf("Allow" to "POST"))
                }
            } catch (error: Exception) {
                android.util.Log.w(TAG, "Request failed", error)
            }
        }
    }

    // ---- JSON-RPC / MCP ---------------------------------------------------------------------

    private fun handleRpcBody(body: String): String? {
        val trimmed = body.trim()
        if (trimmed.startsWith("[")) {
            val batch = JSONArray(trimmed)
            val replies = JSONArray()
            for (i in 0 until batch.length()) {
                handleRpc(batch.getJSONObject(i))?.let { replies.put(it) }
            }
            return if (replies.length() == 0) null else replies.toString()
        }
        return handleRpc(JSONObject(trimmed))?.toString()
    }

    private fun handleRpc(message: JSONObject): JSONObject? {
        val id = if (message.has("id")) message.get("id") else null
        val method = message.optString("method", "")
        if (id == null) return null // notification (notifications/initialized, cancelled, ...)
        val reply = JSONObject().put("jsonrpc", "2.0").put("id", id)
        try {
            when (method) {
                "initialize" -> {
                    val asked = message.optJSONObject("params")?.optString("protocolVersion", "") ?: ""
                    reply.put("result", JSONObject()
                        .put("protocolVersion", if (asked in PROTOCOLS) asked else PROTOCOLS[0])
                        .put("capabilities", JSONObject().put("tools", JSONObject()))
                        .put("serverInfo", JSONObject().put("name", "eden-nx2").put("version", "1"))
                        .put("instructions", Nx2Tools.INSTRUCTIONS))
                }
                "ping" -> reply.put("result", JSONObject())
                "tools/list" -> reply.put("result", JSONObject().put("tools", Nx2Tools.list()))
                "tools/call" -> {
                    val params = message.optJSONObject("params") ?: JSONObject()
                    reply.put("result", Nx2Tools.call(app, params.optString("name"),
                        params.optJSONObject("arguments") ?: JSONObject()))
                }
                "resources/list" -> reply.put("result", JSONObject().put("resources", JSONArray()))
                "prompts/list" -> reply.put("result", JSONObject().put("prompts", JSONArray()))
                else -> reply.put("error", JSONObject().put("code", -32601).put("message", "Unknown method $method"))
            }
        } catch (error: Exception) {
            reply.remove("result")
            reply.put("error", JSONObject().put("code", -32603).put("message", error.toString()))
        }
        return reply
    }

    // ---- helpers used by the tools ----------------------------------------------------------

    internal fun lastGamePath(): String? = lastGamePath(app)

    internal fun inputExecutor() = inputPool
}

/** The tools Claude sees. Each returns MCP content (text, or an image for screenshots). */
internal object Nx2Tools {
    @Volatile private var gpuLine: String? = null
    const val INSTRUCTIONS =
        "Eden NX2 (Switch 2 emulator) on the player's AYN Odin 3. Use status first. Performance " +
        "lines come from the native log every 10 s (NX2_FRAMES, NX2_GPU, NX2_THERMAL); the frame " +
        "trace needs the EDEN_DKB_FRAME_TRACE=1 switch. Switches and most settings need " +
        "restart_app. The player is holding the device: keep inputs short and say what you are " +
        "about to do. Never delete saves, keys or user data."

    private fun schema(props: JSONObject = JSONObject(), required: List<String> = emptyList()) =
        JSONObject().put("type", "object").put("properties", props)
            .put("required", JSONArray(required))

    private fun prop(type: String, description: String) =
        JSONObject().put("type", type).put("description", description)

    private fun tool(name: String, description: String, input: JSONObject, readOnly: Boolean) =
        JSONObject().put("name", name).put("description", description).put("inputSchema", input)
            .put("annotations", JSONObject().put("readOnlyHint", readOnly))

    fun list(): JSONArray = JSONArray()
        .put(tool("status", "Build, whether a game is running and which, GPU driver, the switches " +
            "in effect and in the switches file, key graphics settings.", schema(), true))
        .put(tool("performance", "Live memory, heat and GPU load, plus the latest performance lines " +
            "from the native log (frame rate and frame times every 10 s).",
            schema(JSONObject().put("seconds", prop("integer", "How far back in the log, default 30"))), true))
        .put(tool("read_log", "Lines from the end of the native log, optionally filtered. " +
            "file: \"current\" (default), \"previous\" (the run before; Eden rotates the log at " +
            "start, so after a crash and relaunch the crashed run is here) or \"crash\" " +
            "(nx2_crash.txt: the native crash handler's report, written as Eden dies).",
            schema(JSONObject()
                .put("file", prop("string", "current, previous or crash"))
                .put("pattern", prop("string", "Regular expression lines must match (optional)"))
                .put("max_lines", prop("integer", "Default 200, at most 2000"))
                .put("search_bytes", prop("integer", "How much of the log end to search, default 4 MiB"))), true))
        .put(tool("frame_trace_summary", "GPU time per frame from the frame trace (needs the " +
            "EDEN_DKB_FRAME_TRACE=1 switch): busy and timed medians and the costliest shaders.",
            schema(JSONObject()
                .put("seconds", prop("integer", "Window, default 30"))
                .put("top", prop("integer", "How many shaders, default 15"))), true))
        .put(tool("list_settings", "Every setting key with its type and current value.",
            schema(JSONObject().put("filter", prop("string", "Substring of the key (optional)"))), true))
        .put(tool("set_setting", "Change a setting by key (the game's own settings when a game with " +
            "custom settings is running, else the global ones) and save it. Says whether it needs a restart.",
            schema(JSONObject()
                .put("key", prop("string", "Setting key, e.g. resolution_setup"))
                .put("value", prop("string", "New value (number, true/false or text)")),
                listOf("key", "value")), false))
        .put(tool("get_switches", "The EDEN_NX2_* switches file and the switches in effect now.", schema(), true))
        .put(tool("set_switches", "Replace the switches file (NAME=VALUE lines; empty clears it). " +
            "They take effect after restart_app.",
            schema(JSONObject().put("lines", prop("string", "File contents")), listOf("lines")), false))
        .put(tool("list_games", "Games in the library (title, program id, path).", schema(), true))
        .put(tool("launch_game", "Start a game by title or program id (closes the running one).",
            schema(JSONObject().put("query", prop("string", "Part of the title, or the program id")),
                listOf("query")), false))
        .put(tool("restart_app", "Restart Eden (switches and restart-only settings take effect), " +
            "then start the last game again unless relaunch_game is false.",
            schema(JSONObject().put("relaunch_game", prop("boolean", "Default true"))), false))
        .put(tool("pause", "Pause or resume emulation.",
            schema(JSONObject().put("paused", prop("boolean", "true pauses, false resumes")),
                listOf("paused")), false))
        .put(tool("screenshot", "The game's picture as a JPEG.",
            schema(JSONObject().put("width", prop("integer", "Width in pixels, default 960, at most 1920"))), true))
        .put(tool("press", "Hold buttons together, then release. Buttons: A B X Y L R ZL ZR Plus " +
            "Minus DUp DDown DLeft DRight LStick RStick Home Capture.",
            schema(JSONObject()
                .put("buttons", JSONObject().put("type", "array").put("items", JSONObject().put("type", "string")))
                .put("hold_ms", prop("integer", "Default 120, at most 10000")),
                listOf("buttons")), false))
        .put(tool("move_stick", "Hold a stick at x,y (-1..1, y up is positive), then center it.",
            schema(JSONObject()
                .put("stick", prop("string", "left or right"))
                .put("x", prop("number", "-1..1")).put("y", prop("number", "-1..1"))
                .put("hold_ms", prop("integer", "Default 500, at most 10000")),
                listOf("stick", "x", "y")), false))
        .put(tool("input_sequence", "Run steps in order. Each step: {buttons?: [..], stick?: " +
            "left|right, x?, y?, hold_ms?, wait_ms?}. At most 60 s in total.",
            schema(JSONObject().put("steps", JSONObject().put("type", "array")
                .put("items", JSONObject().put("type", "object"))), listOf("steps")), false))
        .put(tool("show_message", "Show a short message on the game screen for a few seconds " +
            "(needs \"Show Claude in game\" on in Claude Lab).",
            schema(JSONObject()
                .put("text", prop("string", "The message, one or two short lines"))
                .put("seconds", prop("integer", "How long, default 6, 2..60")),
                listOf("text")), false))
        .put(tool("say", "Write to the player in the chat panel on the game screen (they read it " +
            "while playing and can reply there). Keep it short. Use wait_for_player to read replies.",
            schema(JSONObject().put("text", prop("string", "The message")), listOf("text")), false))
        .put(tool("wait_for_player", "Wait up to 50 s for the player to write in the in-game chat " +
            "or mark a moment; returns what they wrote (nothing if they did not). Call it again and " +
            "again to keep the conversation going while they play.",
            schema(JSONObject().put("seconds", prop("integer", "5..50, default 45"))), true))
        .put(tool("chat_history", "The last messages of the in-game chat, oldest first.",
            schema(JSONObject().put("count", prop("integer", "Default 20, at most 200"))), true))
        .put(tool("watch", "Watch the game for up to 50 s: frame rate, frame times, GPU load and " +
            "shader building every second, pictures of hitches or drops (up to two), marks the " +
            "player made meanwhile, and a screenshot at the end. Call it again and again to keep " +
            "watching while the player plays.",
            schema(JSONObject()
                .put("seconds", prop("integer", "5..50, default 30"))
                .put("screenshot", prop("boolean", "Screenshot at the end, default true"))), true))
        .put(tool("memory_breakdown", "Where Eden's memory is: /proc/self/smaps grouped by " +
            "mapping (native heap, guest memory, GPU, Java, code...), resident and private MiB, plus " +
            "the native heap and Java heap totals. Takes about a second.",
            schema(JSONObject().put("top", prop("integer", "How many groups, default 25"))), true))
        .put(tool("last_exit", "How Eden's previous runs ended, as Android recorded it (low " +
            "memory, crash, user closed it, ...), with memory use at the time.",
            schema(JSONObject().put("count", prop("integer", "How many, default 3"))), true))
        .put(tool("recent_frames", "The game frames Eden saved in the last half minute (one every " +
            "2.5 s, 480x270), newest first, with their age. For \"what just happened\".",
            schema(JSONObject().put("count", prop("integer", "How many, default 4, at most 12"))), true))
        .put(tool("list_marks", "Moments the player marked with the C button in game (newest " +
            "first): id, time, note.", schema(), true))
        .put(tool("get_mark", "A marked moment's full-size screenshot and note.",
            schema(JSONObject().put("id", prop("string", "Mark id from list_marks")), listOf("id")), true))
        .put(tool("notebook_read", "The lab notebook kept on the device between sessions.", schema(), true))
        .put(tool("notebook_append", "Add a dated entry to the lab notebook (findings, measurements, " +
            "suggestions for code changes).",
            schema(JSONObject().put("text", prop("string", "Entry")), listOf("text")), false))

    fun call(context: Context, name: String, args: JSONObject): JSONObject = try {
        when (name) {
            "status" -> text(status(context))
            "performance" -> text(performance(context, args.optInt("seconds", 30)))
            "read_log" -> text(readLog(context, args.optString("file", "current"),
                args.optString("pattern", ""),
                args.optInt("max_lines", 200).coerceIn(1, 2000),
                args.optLong("search_bytes", 4L shl 20).coerceIn(4096L, 32L shl 20)))
            "frame_trace_summary" -> text(frameTrace(context, args.optInt("seconds", 30),
                args.optInt("top", 15).coerceIn(1, 60)))
            "list_settings" -> text(listSettings(args.optString("filter", "")))
            "set_setting" -> text(setSetting(args.getString("key"), args.get("value").toString()))
            "get_switches" -> text(getSwitches())
            "set_switches" -> text(setSwitches(args.getString("lines")))
            "list_games" -> text(listGames(context).toString(2))
            "launch_game" -> text(launchGame(context, args.getString("query")))
            "restart_app" -> text(restartApp(context, args.optBoolean("relaunch_game", true)))
            "pause" -> text(pause(args.getBoolean("paused")))
            "screenshot" -> screenshot(args.optInt("width", 960).coerceIn(160, 1920))
            "press" -> text(press(args.getJSONArray("buttons"), args.optLong("hold_ms", 120)))
            "move_stick" -> text(moveStick(args.getString("stick"), args.getDouble("x"),
                args.getDouble("y"), args.optLong("hold_ms", 500)))
            "input_sequence" -> text(sequence(args.getJSONArray("steps")))
            "show_message" -> text(if (Nx2Overlay.showMessage(args.getString("text"), args.optInt("seconds", 6)))
                "Shown" else "Not shown: no game on screen, or \"Show Claude in game\" is off")
            "say" -> text(if (Nx2Overlay.say(args.getString("text"))) "Shown in the chat panel"
                else "Saved in the chat, but the panel is not on screen (no game running, or " +
                    "\"Show Claude in game\" is off)")
            "wait_for_player" -> {
                Nx2Chat.load(context)
                val seconds = args.optInt("seconds", 45).coerceIn(5, 50)
                val got = Nx2Chat.waitForPlayer(seconds * 1000L)
                text(if (got.isEmpty()) "Nothing from the player in $seconds s"
                    else got.joinToString("\n") { Nx2Chat.format(it) })
            }
            "chat_history" -> {
                Nx2Chat.load(context)
                val recent = Nx2Chat.recent(args.optInt("count", 20).coerceIn(1, 200))
                text(if (recent.isEmpty()) "(no messages yet)" else recent.joinToString("\n") { Nx2Chat.format(it) })
            }
            "watch" -> watch(context, args.optInt("seconds", 30).coerceIn(5, 50), args.optBoolean("screenshot", true))
            "memory_breakdown" -> text(memoryBreakdown(args.optInt("top", 25).coerceIn(5, 80)))
            "last_exit" -> text(lastExit(context, args.optInt("count", 3).coerceIn(1, 10)))
            "recent_frames" -> recentFrames(args.optInt("count", 4).coerceIn(1, 12))
            "list_marks" -> text(listMarks(context))
            "get_mark" -> getMark(context, args.getString("id"))
            "notebook_read" -> text(notebook(context).let { if (it.isFile) it.readText() else "(empty)" })
            "notebook_append" -> text(notebookAppend(context, args.getString("text")))
            else -> error("Unknown tool $name")
        }
    } catch (failure: Exception) {
        error(failure.toString())
    }

    /** Tools the Lab process (":nx2lab") answers by itself while Eden's own process is down:
     *  they only read or write files and settings, never the emulator. */
    val OFFLINE = setOf("status", "read_log", "last_exit", "list_marks", "get_mark", "notebook_read",
        "notebook_append", "get_switches", "set_switches", "list_games")

    fun callOffline(context: Context, name: String, args: JSONObject, why: String): JSONObject = try {
        when (name) {
            "status" -> text(offlineStatus(context, why))
            "get_switches" -> text("file ${switchesFile().path}:\n" +
                (if (switchesFile().isFile) switchesFile().readText() else "(none)") +
                "\n(Eden is not running, so nothing is in effect now)")
            in OFFLINE -> call(context, name, args)
            else -> error("Unknown tool $name")
        }
    } catch (failure: Exception) {
        error(failure.toString())
    }

    fun offlineStatus(context: Context, why: String): String {
        val out = StringBuilder()
        out.append("app_build=").append(Nx2FlightRecorder.buildTag()).append('\n')
        out.append("eden_running=false (").append(why).append(")\n")
        out.append("game_running=false\n")
        out.append("last_game=").append(Nx2Bridge.lastGamePath(context) ?: "-").append('\n')
        out.append("last_exit=").append(runCatching { lastExit(context, 1) }.getOrDefault("?")).append('\n')
        out.append("switches_file=").append(switchesFile().let {
            if (it.isFile) it.readText().trim().replace('\n', ' ') else "(none)" }).append('\n')
        out.append("Answering from the Claude Lab process. restart_app or launch_game start Eden again.\n")
        return out.toString()
    }

    internal fun textResult(value: String) = text(value)
    internal fun errorResult(value: String) = error(value)

    private fun text(value: String) = JSONObject()
        .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", value)))

    private fun error(value: String) = text(value).put("isError", true)

    // ---- status / performance / logs --------------------------------------------------------

    private fun nativeLog(context: Context): File? = logFile(context, "eden_log.txt")

    private fun logFile(context: Context, name: String): File? =
        context.getExternalFilesDir(null)?.let { File(File(it, "log"), name) }

    private fun tail(file: File?, bytes: Long): String {
        if (file == null || !file.isFile) return ""
        RandomAccessFile(file, "r").use { source ->
            val start = (source.length() - bytes).coerceAtLeast(0L)
            source.seek(start)
            val data = ByteArray((source.length() - start).toInt())
            source.readFully(data)
            val text = String(data, Charsets.UTF_8)
            return if (start > 0) text.substringAfter('\n') else text
        }
    }

    /** The renderer's "Driver:" and "Device:" lines from the start of the current log. */
    private fun gpuFromLog(context: Context): String? {
        val file = nativeLog(context) ?: return null
        if (!file.isFile) return null
        val head = runCatching {
            RandomAccessFile(file, "r").use { source ->
                val data = ByteArray(minOf(source.length(), 4L shl 20).toInt())
                source.readFully(data)
                String(data, Charsets.UTF_8)
            }
        }.getOrNull() ?: return null
        var device: String? = null
        var driver: String? = null
        for (line in head.lineSequence()) {
            if (device == null) line.substringAfter(":Report: Device: ", "").takeIf { it.isNotEmpty() }?.let { device = it.trim() }
            if (driver == null) line.substringAfter(":Report: Driver: ", "").takeIf { it.isNotEmpty() }?.let { driver = it.trim() }
            if (device != null && driver != null) break
        }
        if (device == null) return null
        return "gpu=" + device + " driver=" + (driver ?: "?")
    }

    /** The log's own clock: "[  123.456789]" at the start of each line. */
    private fun logTime(line: String): Double? {
        val open = line.indexOf('[')
        val close = line.indexOf(']')
        if (open != 0 || close < 0) return null
        return line.substring(1, close).trim().toDoubleOrNull()
    }

    private fun lastSeconds(lines: List<String>, seconds: Int): List<String> {
        val end = lines.asReversed().firstNotNullOfOrNull { logTime(it) } ?: return lines
        return lines.filter { line -> logTime(line)?.let { it >= end - seconds } ?: false }
    }

    private fun running(): Boolean = runCatching { NativeLibrary.isRunning() }.getOrDefault(false)

    private fun status(context: Context): String {
        val out = StringBuilder()
        out.append("app_build=").append(Nx2FlightRecorder.buildTag()).append('\n')
        val isRunning = running()
        out.append("game_running=").append(isRunning)
        if (isRunning) out.append(" paused=").append(runCatching { NativeLibrary.isPaused() }.getOrDefault(false))
        out.append('\n')
        out.append("last_game=").append(Nx2Bridge.lastGamePath() ?: "-").append('\n')
        // Builds 193/194/198: asking NativeLibrary for these loads Android's own Vulkan driver
        // and creates an instance in Eden's process. With the game on Turnip, Eden crashed a few
        // seconds later every time (198: twice in the pipeline-cache serialization thread). The
        // renderer logs its device and driver at boot ("Driver: ...", "Device: ..."): read those.
        if (isRunning) gpuFromLog(context)?.let { gpuLine = it }
        out.append(gpuLine ?: "gpu=(read while a game runs)").append('\n')
        out.append("per_game_settings_loaded=").append(runCatching { NativeConfig.isPerGameConfigLoaded() }.getOrDefault(false)).append('\n')
        for (key in listOf("resolution_setup", "oot_anti_aliasing", "anti_aliasing", "scaling_filter",
            "vram_usage_mode", "precache_shaders", "use_vsync", "gpu_accuracy", "use_disk_shader_cache")) {
            out.append(key).append('=').append(getSetting(key) ?: "?").append('\n')
        }
        out.append("switches_in_effect=").append(System.getenv().filterKeys {
            it.startsWith("EDEN_NX2_") && it != "EDEN_NX2_ANDROID_SWITCHES_SOURCE" || it.startsWith("EDEN_DKB")
        }.entries.joinToString(" ") { it.key + "=" + it.value }).append('\n')
        out.append("switches_file=").append(switchesFile().let { if (it.isFile) it.readText().trim().replace('\n', ' ') else "(none)" }).append('\n')
        out.append("connector_requests=").append(Nx2Bridge.requestCount).append('\n')
        return out.toString()
    }

    private fun readSys(path: String): String? =
        runCatching { File(path).readText().trim() }.getOrNull()?.takeIf { it.isNotEmpty() }

    private fun performance(context: Context, seconds: Int): String {
        val out = StringBuilder()
        val memory = Debug.MemoryInfo()
        Debug.getMemoryInfo(memory)
        val manager = context.getSystemService(ActivityManager::class.java)
        val system = ActivityManager.MemoryInfo()
        manager.getMemoryInfo(system)
        val power = context.getSystemService(PowerManager::class.java)
        out.append("app_pss_mib=").append(memory.totalPss / 1024)
            .append(" system_available_mib=").append(system.availMem / 1048576)
            .append(" system_low_memory=").append(system.lowMemory).append('\n')
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            out.append("thermal_status=").append(power?.currentThermalStatus ?: -1)
                .append(" (0 none .. 3 severe .. 6 shutdown) headroom_10s=")
                .append(runCatching { power?.getThermalHeadroom(10) }.getOrNull()).append('\n')
        }
        out.append("gpu_busy_now=").append(readSys("/sys/class/kgsl/kgsl-3d0/gpu_busy_percentage") ?: "?")
            .append(" gpu_clock=").append(readSys("/sys/class/kgsl/kgsl-3d0/gpuclk") ?: "?")
            .append(" gpu_temp=").append(readSys("/sys/class/kgsl/kgsl-3d0/temp") ?: "?").append('\n')
        out.append("game_running=").append(running()).append('\n')
        val lines = tail(nativeLog(context), 3L shl 20).lines()
        val wanted = Regex("NX2_FRAMES|NX2_GPU |NX2_THERMAL|NX2_CPU|NX2_THREADS|NX2_HANG|Runtime pipelines|Building")
        out.append("--- log, last ").append(seconds).append(" s ---\n")
        lastSeconds(lines, seconds).filter { wanted.containsMatchIn(it) }.takeLast(60)
            .forEach { out.append(it.take(400)).append('\n') }
        return out.toString()
    }

    private fun readLog(context: Context, which: String, pattern: String, maxLines: Int,
                        bytes: Long): String {
        val file = when (which.lowercase()) {
            "", "current" -> nativeLog(context)
            "previous" -> logFile(context, "eden_log.txt.old.txt")
            "crash" -> logFile(context, "nx2_crash.txt")
            else -> return "Unknown file \"$which\": current, previous or crash"
        }
        if (file == null || !file.isFile) return "(no ${which.lowercase()} log file)"
        val modified = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
            .format(java.util.Date(file.lastModified()))
        val regex = if (pattern.isEmpty()) null else Regex(pattern)
        val lines = tail(file, bytes).lines()
            .filter { regex == null || regex.containsMatchIn(it) }
            .takeLast(maxLines)
        val text = lines.joinToString("\n") { it.take(1000) }
        val body = if (text.length > 120_000) text.takeLast(120_000) else text.ifEmpty { "(no matching lines)" }
        return if (which.lowercase() in setOf("", "current")) body
        else "${file.name}, last written $modified:\n$body"
    }

    private fun frameTrace(context: Context, seconds: Int, top: Int): String {
        val lines = lastSeconds(tail(nativeLog(context), 12L shl 20).lines(), seconds)
        val busy = ArrayList<Double>()
        val timed = ArrayList<Double>()
        val compute = ArrayList<Double>()
        val totals = HashMap<String, Double>()
        val counts = HashMap<String, Int>()
        val sizes = HashMap<String, String>()
        val busyRe = Regex("gpu_busy_ms=([\\d.]+)")
        val timedRe = Regex("timed_sum_ms=([\\d.]+)")
        val entryRe = Regex(" (s[\\w:+]+)=([\\d.]+)ms/(\\d+)x\\(max [\\d.]+ms size ([\\dx]+)\\)")
        var frames = 0
        for (line in lines) {
            if (line.contains("DKB gpu_frame")) busyRe.find(line)?.let { busy.add(it.groupValues[1].toDouble()) }
            if (!line.contains("DKB gpu_work")) continue
            frames++
            timedRe.find(line)?.let { timed.add(it.groupValues[1].toDouble()) }
            var c = 0.0
            for (m in entryRe.findAll(line)) {
                val key = m.groupValues[1]
                val ms = m.groupValues[2].toDouble()
                totals[key] = (totals[key] ?: 0.0) + ms
                counts[key] = (counts[key] ?: 0) + m.groupValues[3].toInt()
                sizes.putIfAbsent(key, m.groupValues[4])
                if (key.startsWith("s6:")) c += ms
            }
            compute.add(c)
        }
        if (frames == 0) return "No frame trace lines in the last $seconds s. Is EDEN_DKB_FRAME_TRACE=1 set " +
            "(set_switches, then restart_app) and is a game running?"
        fun median(values: List<Double>) = if (values.isEmpty()) "?" else
            String.format(Locale.US, "%.1f", values.sorted()[values.size / 2])
        val out = StringBuilder()
        out.append("frames=").append(frames).append(" gpu_busy_ms_median=").append(median(busy))
            .append(" timed_ms_median=").append(median(timed)).append(" compute_ms_median=")
            .append(median(compute)).append('\n')
        out.append("untimed (busy - timed) is time between draws: clears, copies, render pass changes\n")
        out.append("ms/frame  draws/frame  shader stages  first size\n")
        totals.entries.sortedByDescending { it.value }.take(top).forEach { (key, ms) ->
            out.append(String.format(Locale.US, "%7.2f  %7.1f  %s  %s\n", ms / frames,
                (counts[key] ?: 0).toDouble() / frames, key, sizes[key] ?: ""))
        }
        return out.toString()
    }

    // ---- settings ---------------------------------------------------------------------------

    private data class Known(val type: String, val get: () -> String, val set: (String) -> Unit)

    private fun known(key: String): Known? {
        BooleanSetting.entries.firstOrNull { it.key == key }?.let {
            return Known("boolean", { NativeConfig.getBoolean(key, false).toString() },
                { NativeConfig.setBoolean(key, parseBool(it)) })
        }
        IntSetting.entries.firstOrNull { it.key == key }?.let {
            return Known("int", { NativeConfig.getInt(key, false).toString() },
                { v -> NativeConfig.setInt(key, v.trim().toInt()) })
        }
        ByteSetting.entries.firstOrNull { it.key == key }?.let {
            return Known("byte", { NativeConfig.getByte(key, false).toString() },
                { v -> NativeConfig.setByte(key, v.trim().toInt().toByte()) })
        }
        ShortSetting.entries.firstOrNull { it.key == key }?.let {
            return Known("short", { NativeConfig.getShort(key, false).toString() },
                { v -> NativeConfig.setShort(key, v.trim().toInt().toShort()) })
        }
        FloatSetting.entries.firstOrNull { it.key == key }?.let {
            return Known("float", { NativeConfig.getFloat(key, false).toString() },
                { v -> NativeConfig.setFloat(key, v.trim().toFloat()) })
        }
        LongSetting.entries.firstOrNull { it.key == key }?.let {
            return Known("long", { NativeConfig.getLong(key, false).toString() },
                { v -> NativeConfig.setLong(key, v.trim().toLong()) })
        }
        StringSetting.entries.firstOrNull { it.key == key }?.let {
            return Known("string", { NativeConfig.getString(key, false) },
                { v -> NativeConfig.setString(key, v) })
        }
        return null
    }

    private fun parseBool(value: String) = when (value.trim().lowercase(Locale.US)) {
        "true", "1", "on", "yes" -> true
        "false", "0", "off", "no" -> false
        else -> throw IllegalArgumentException("Not a boolean: $value")
    }

    private fun getSetting(key: String): String? = runCatching { known(key)?.get?.invoke() }.getOrNull()

    private fun listSettings(filter: String): String {
        val keys = (BooleanSetting.entries.map { it.key } + IntSetting.entries.map { it.key } +
            ByteSetting.entries.map { it.key } + ShortSetting.entries.map { it.key } +
            FloatSetting.entries.map { it.key } + LongSetting.entries.map { it.key } +
            StringSetting.entries.map { it.key }).distinct().sorted()
        return keys.filter { filter.isEmpty() || it.contains(filter, ignoreCase = true) }.joinToString("\n") { key ->
            val k = known(key)
            val restart = runCatching { !NativeConfig.getIsRuntimeModifiable(key) }.getOrDefault(true)
            "$key (${k?.type}) = ${getSetting(key)}" + if (restart) " [restart]" else ""
        }
    }

    private fun setSetting(key: String, value: String): String {
        val k = known(key) ?: return "Unknown setting key: $key (see list_settings)"
        val perGame = runCatching { NativeConfig.isPerGameConfigLoaded() }.getOrDefault(false)
        if (perGame && runCatching { NativeConfig.getIsSwitchable(key) }.getOrDefault(false)) {
            NativeConfig.setGlobal(key, false)
        }
        val before = k.get()
        k.set(value)
        if (perGame) NativeConfig.savePerGameConfig() else NativeConfig.saveGlobalConfig()
        val runtime = runCatching { NativeConfig.getIsRuntimeModifiable(key) }.getOrDefault(false)
        if (running() && runtime) runCatching { NativeLibrary.applySettings() }
        return "$key: $before -> ${k.get()} (${if (perGame) "this game's settings" else "global settings"}); " +
            if (runtime) "applied now" else "takes effect after restart_app"
    }

    // ---- switches ---------------------------------------------------------------------------

    /** The first path nx2_switches.cpp tries: the app's own folder (no storage permission needed). */
    private fun switchesFile(): File =
        File("/storage/emulated/0/Android/data/dev.eden.eden_emulator/files/nx2_switches.txt")

    private fun getSwitches(): String {
        val file = switchesFile()
        val effect = System.getenv().filterKeys { it.startsWith("EDEN_NX2_") || it.startsWith("EDEN_DKB") }
        return "file ${file.path}:\n" + (if (file.isFile) file.readText() else "(none)") +
            "\nin effect now:\n" + effect.entries.joinToString("\n") { it.key + "=" + it.value } +
            "\n(Download/nx2_switches.txt is only read when this file does not exist)"
    }

    private fun setSwitches(lines: String): String {
        val file = switchesFile()
        file.parentFile?.mkdirs()
        if (lines.isBlank()) {
            file.delete()
            return "Switches file removed; restart_app to apply."
        }
        file.writeText(lines.trim() + "\n")
        return "Switches file written (${lines.trim().lines().size} lines); restart_app to apply."
    }

    // ---- games and lifecycle ----------------------------------------------------------------

    private fun listGames(context: Context): JSONArray {
        val stored = PreferenceManager.getDefaultSharedPreferences(context)
            .getStringSet(GameHelper.KEY_GAMES, emptySet()) ?: emptySet()
        val games = JSONArray()
        for (entry in stored) {
            runCatching {
                val game = JSONObject(entry)
                games.put(JSONObject().put("title", game.optString("title"))
                    .put("program_id", game.optString("programId"))
                    .put("path", game.optString("path")))
            }
        }
        return games
    }

    private fun findGame(context: Context, query: String): JSONObject? {
        val games = listGames(context)
        val q = query.trim().lowercase(Locale.US)
        for (i in 0 until games.length()) {
            val g = games.getJSONObject(i)
            if (g.optString("program_id").lowercase(Locale.US) == q) return g
        }
        for (i in 0 until games.length()) {
            val g = games.getJSONObject(i)
            if (g.optString("title").lowercase(Locale.US).contains(q)) return g
        }
        return null
    }

    private fun gameIntent(context: Context, path: String) =
        Intent(context, EmulationActivity::class.java).apply {
            action = Intent.ACTION_VIEW
            data = Uri.parse(path)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }

    private fun launchGame(context: Context, query: String): String {
        val game = findGame(context, query) ?: return "No game matches \"$query\" (see list_games)"
        context.startActivity(gameIntent(context, game.getString("path")))
        Nx2Bridge.rememberGame(game.getString("path"))
        return "Launching ${game.optString("title")}"
    }

    private fun restartApp(context: Context, relaunch: Boolean): String {
        val path = Nx2Bridge.lastGamePath()
        val target = if (relaunch && path != null) gameIntent(context, path)
        else context.packageManager.getLaunchIntentForPackage(context.packageName)
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ?: return "No launch intent"
        // Stop the game the normal way first (saves and caches are flushed), then restart.
        if (running()) {
            runCatching { NativeLibrary.stopEmulation() }
            val deadline = System.currentTimeMillis() + 8000
            while (running() && System.currentTimeMillis() < deadline) Thread.sleep(100)
        }
        Nx2RestartActivity.restart(context, target, Process.myPid())
        return "Restarting Eden" + (if (relaunch && path != null) " and the last game" else "") +
            ". The connector is back in about 10 s; the game needs longer to boot."
    }

    private fun pause(paused: Boolean): String {
        if (!running()) return "No game running"
        if (paused) NativeLibrary.pauseEmulation() else NativeLibrary.unpauseEmulation()
        return if (paused) "Paused" else "Resumed"
    }

    // ---- screenshot -------------------------------------------------------------------------

    private val captureThread by lazy { HandlerThread("NX2-Bridge-Capture").also { it.start() } }

    /** The game's picture as MCP image content, or the reason there is none. */
    private fun capture(width: Int): Pair<JSONObject?, String?> {
        val view = Nx2FlightRecorder.bridgeSurface()
            ?: return null to "No game screen: no game has been started."
        if (!view.holder.surface.isValid || view.width <= 0 || view.height <= 0) {
            return null to "The game screen is not visible right now (Eden is in the background, " +
                "e.g. the player is in this chat). Ask them to switch back to Eden."
        }
        val height = (width.toLong() * view.height / view.width).toInt().coerceAtLeast(1)
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val result = AtomicReference(-1)
        val done = CountDownLatch(1)
        try {
            PixelCopy.request(view, bitmap, { code -> result.set(code); done.countDown() },
                Handler(captureThread.looper))
        } catch (failure: Exception) {
            bitmap.recycle()
            return null to "Capture failed: $failure"
        }
        if (!done.await(5, TimeUnit.SECONDS) || result.get() != PixelCopy.SUCCESS) {
            bitmap.recycle()
            return null to "Capture failed (code ${result.get()})"
        }
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 80, out)
        bitmap.recycle()
        return JSONObject().put("type", "image").put("mimeType", "image/jpeg")
            .put("data", Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)) to null
    }

    private fun screenshot(width: Int): JSONObject {
        val (picture, problem) = capture(width)
        return if (picture != null) JSONObject().put("content", JSONArray().put(picture))
        else error(problem ?: "Capture failed")
    }

    // ---- watch: second-by-second while the player plays -----------------------------------

    private val fpsRe = Regex("^([\\d.]+) fps")
    private val maxRe = Regex("max (\\d+)")
    private val p50Re = Regex("p50 (\\d+)")

    private fun watch(context: Context, seconds: Int, withScreenshot: Boolean): JSONObject {
        if (!running()) return error("No game running")
        runCatching { Nx2Bridge.nativeLiveFrames() } // start a fresh window
        val marksBefore = Nx2Overlay.marksDir(context).list()?.toSet() ?: emptySet()
        val content = JSONArray()
        val timeline = StringBuilder("sec  fps   p50ms maxms  gpu%  shaders\n")
        val fpsSeen = ArrayList<Double>()
        var worstMs = 0
        var shaderSeconds = 0
        var anomalyPictures = 0
        var silent = 0
        for (second in 1..seconds) {
            Thread.sleep(1000)
            val frames = runCatching { Nx2Bridge.nativeLiveFrames() }.getOrDefault("")
            val fps = fpsRe.find(frames)?.groupValues?.get(1)?.toDoubleOrNull() ?: 0.0
            val maxMs = maxRe.find(frames)?.groupValues?.get(1)?.toIntOrNull() ?: 0
            val p50 = p50Re.find(frames)?.groupValues?.get(1)?.toIntOrNull() ?: 0
            val gpu = readSys("/sys/class/kgsl/kgsl-3d0/gpu_busy_percentage")?.substringBefore(' ') ?: "?"
            val shaders = runCatching { NativeLibrary.getShadersBuilding() }.getOrDefault(0)
            if (frames.isEmpty()) silent++
            if (shaders > 0) shaderSeconds++
            timeline.append(String.format(Locale.US, "%3d %5.1f %6d %5d %5s %8d\n", second, fps, p50, maxMs, gpu, shaders))
            val typical = fpsSeen.sorted().let { if (it.isEmpty()) 0.0 else it[it.size / 2] }
            val hitch = maxMs > 0 && p50 > 0 && maxMs >= 3 * p50 && maxMs >= 50
            val drop = typical > 0 && fps > 0 && fps < typical * 0.7
            if ((hitch || drop) && anomalyPictures < 2) {
                capture(640).first?.let { picture ->
                    anomalyPictures++
                    content.put(JSONObject().put("type", "text").put("text",
                        "Second $second: " + (if (hitch) "hitch, a $maxMs ms frame" else "drop to ${"%.1f".format(fps)} fps") +
                            (if (shaders > 0) " while $shaders shaders were building" else "")))
                    content.put(picture)
                }
            }
            if (fps > 0) fpsSeen.add(fps)
            worstMs = maxOf(worstMs, maxMs)
        }
        val sorted = fpsSeen.sorted()
        val summary = StringBuilder()
        summary.append("Watched $seconds s. ")
        if (sorted.isEmpty()) summary.append("No frames presented (paused, loading or in the background). ")
        else summary.append(String.format(Locale.US, "fps median %.1f, lowest %.1f; worst frame %d ms; ",
            sorted[sorted.size / 2], sorted.first(), worstMs))
        summary.append("shaders building in $shaderSeconds s; no frames in $silent s.\n")
        val memory = Debug.MemoryInfo().also { Debug.getMemoryInfo(it) }
        summary.append("memory ${memory.totalPss / 1024} MiB")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            summary.append(", thermal status ")
                .append(context.getSystemService(PowerManager::class.java)?.currentThermalStatus ?: -1)
        }
        summary.append(", gpu temp ").append(readSys("/sys/class/kgsl/kgsl-3d0/temp") ?: "?").append('\n')
        val newMarks = (Nx2Overlay.marksDir(context).list()?.toSet() ?: emptySet()) - marksBefore
        if (newMarks.isNotEmpty()) {
            summary.append("The player marked ${newMarks.count { it.endsWith(".json") }} moment(s): ")
                .append(newMarks.filter { it.endsWith(".json") }.joinToString { it.removeSuffix(".json") })
                .append(" (get_mark)\n")
        }
        val ordered = JSONArray().put(JSONObject().put("type", "text").put("text", summary.toString() + timeline))
        for (i in 0 until content.length()) ordered.put(content.get(i))
        val result = ordered
        if (withScreenshot) {
            val (picture, problem) = capture(960)
            if (picture != null) result.put(picture)
            else result.put(JSONObject().put("type", "text").put("text", "No final screenshot: $problem"))
        }
        return JSONObject().put("content", result)
    }

    // ---- input ------------------------------------------------------------------------------

    private fun button(name: String): NativeButton =
        NativeButton.entries.firstOrNull { it.name.equals(name.trim(), ignoreCase = true) }
            ?: throw IllegalArgumentException("Unknown button $name")

    private fun stick(name: String): NativeAnalog = when (name.trim().lowercase(Locale.US)) {
        "left", "l", "lstick" -> NativeAnalog.LStick
        "right", "r", "rstick" -> NativeAnalog.RStick
        else -> throw IllegalArgumentException("Unknown stick $name")
    }

    private fun runInput(block: () -> Unit) {
        if (!running()) throw IllegalStateException("No game running")
        inputLock.lock()
        try { block() } finally { inputLock.unlock() }
    }

    private val inputLock = java.util.concurrent.locks.ReentrantLock()

    // The player's controller may be the Odin's built-in gamepad (Android engine) or the touch
    // overlay (virtual gamepad), so every input goes to both: the mapping of player 1 for the
    // button or stick as a physical event, and the overlay's virtual gamepad. The unused one is
    // ignored by the game.
    private fun setButton(button: NativeButton, pressed: Boolean) {
        val action = if (pressed) NativeInput.ButtonState.PRESSED else NativeInput.ButtonState.RELEASED
        runCatching {
            val param = NativeInput.getButtonParam(0, button)
            if (param.get("engine", "") == "android") {
                val guid = param.get("guid", "")
                val port = param.get("port", 0)
                val axis = param.get("axis", -1)
                if (axis >= 0) {
                    val direction = if (param.get("direction", "+") == "-") -1f else 1f
                    NativeInput.onGamePadAxisEvent(guid, port, axis, if (pressed) direction else 0f)
                } else {
                    val code = param.get("button", -1)
                    if (code >= 0) NativeInput.onGamePadButtonEvent(guid, port, code, action)
                }
            }
        }
        NativeInput.onOverlayButtonEvent(0, button, action)
    }

    /** x right and y up positive, -1..1. */
    private fun setStick(which: NativeAnalog, x: Float, y: Float) {
        runCatching {
            val param = NativeInput.getStickParam(0, which)
            if (param.get("engine", "") == "android") {
                val guid = param.get("guid", "")
                val port = param.get("port", 0)
                // As the physical stick reports it: Android's Y axis is positive downwards.
                NativeInput.onGamePadAxisEvent(guid, port, param.get("axis_x", 0), x)
                NativeInput.onGamePadAxisEvent(guid, port, param.get("axis_y", 1), -y)
            }
        }
        NativeInput.onOverlayJoystickEvent(0, which, x, y)
    }

    private fun pressNow(buttons: List<NativeButton>, holdMs: Long) {
        buttons.forEach { setButton(it, true) }
        try { Thread.sleep(holdMs) } finally {
            buttons.forEach { setButton(it, false) }
        }
    }

    private fun stickNow(which: NativeAnalog, x: Double, y: Double, holdMs: Long) {
        setStick(which, x.coerceIn(-1.0, 1.0).toFloat(), y.coerceIn(-1.0, 1.0).toFloat())
        try { Thread.sleep(holdMs) } finally {
            setStick(which, 0f, 0f)
        }
    }

    private fun press(names: JSONArray, holdMs: Long): String {
        val buttons = (0 until names.length()).map { button(names.getString(it)) }
        runInput { pressNow(buttons, holdMs.coerceIn(16, 10_000)) }
        return "Pressed ${buttons.joinToString("+")} for ${holdMs.coerceIn(16, 10_000)} ms"
    }

    private fun moveStick(name: String, x: Double, y: Double, holdMs: Long): String {
        val which = stick(name)
        runInput { stickNow(which, x, y, holdMs.coerceIn(16, 10_000)) }
        return "Held ${which.name} at ($x, $y) for ${holdMs.coerceIn(16, 10_000)} ms"
    }

    private fun sequence(steps: JSONArray): String {
        var total = 0L
        for (i in 0 until steps.length()) {
            val s = steps.getJSONObject(i)
            total += s.optLong("hold_ms", 120).coerceIn(0, 10_000) + s.optLong("wait_ms", 0).coerceIn(0, 10_000)
        }
        if (total > 60_000) return "Sequence too long (${total} ms, at most 60000)"
        runInput {
            for (i in 0 until steps.length()) {
                val s = steps.getJSONObject(i)
                val hold = s.optLong("hold_ms", 120).coerceIn(0, 10_000)
                val buttons = s.optJSONArray("buttons")?.let { a -> (0 until a.length()).map { button(a.getString(it)) } } ?: emptyList()
                val which = if (s.has("stick")) stick(s.getString("stick")) else null
                if (which != null) {
                    setStick(which, s.optDouble("x", 0.0).coerceIn(-1.0, 1.0).toFloat(),
                        s.optDouble("y", 0.0).coerceIn(-1.0, 1.0).toFloat())
                }
                buttons.forEach { setButton(it, true) }
                try { Thread.sleep(hold) } finally {
                    buttons.forEach { setButton(it, false) }
                    if (which != null) setStick(which, 0f, 0f)
                }
                Thread.sleep(s.optLong("wait_ms", 0).coerceIn(0, 10_000))
            }
        }
        return "Ran ${steps.length()} steps (${total} ms)"
    }

    /** Mapping name with numbers and addresses folded, so e.g. 900 "[anon:scudo:primary]" pieces add up. */
    private fun groupName(name: String): String {
        if (name.isEmpty()) return "[anonymous, unnamed]"
        val trimmed = name.substringBefore(" (deleted)")
        return when {
            trimmed.startsWith("/") -> {
                val base = trimmed.substringAfterLast('/')
                if (trimmed.contains("/data/app/") || trimmed.endsWith(".so")) "file: $base"
                else "file: " + trimmed.replace(Regex("[0-9]{3,}"), "#")
            }
            else -> trimmed.replace(Regex("[0-9a-fx]{6,}"), "#").replace(Regex("[0-9]+"), "#")
        }
    }

    private class MemGroup(var rss: Long = 0, var pss: Long = 0, var priv: Long = 0, var swap: Long = 0,
                           var size: Long = 0, var count: Int = 0)

    private fun memoryBreakdown(top: Int): String {
        val groups = HashMap<String, MemGroup>()
        var current: MemGroup? = null
        val header = Regex("^[0-9a-f]+-[0-9a-f]+ \\S+ \\S+ \\S+ \\S+\\s*(.*)$")
        java.io.BufferedReader(java.io.FileReader("/proc/self/smaps"), 1 shl 16).use { reader ->
            while (true) {
                val line = reader.readLine() ?: break
                val first = line.firstOrNull() ?: continue
                if (first.isDigit() || first in 'a'..'f') {
                    val m = header.find(line)
                    if (m != null) {
                        current = groups.getOrPut(groupName(m.groupValues[1].trim())) { MemGroup() }
                        current!!.count++
                        continue
                    }
                }
                val group = current ?: continue
                val colon = line.indexOf(':')
                if (colon <= 0) continue
                val key = line.substring(0, colon)
                if (key != "Size" && key != "Rss" && key != "Pss" && key != "Private_Dirty" &&
                    key != "Private_Clean" && key != "Swap") continue
                val kb = line.substring(colon + 1).trim().substringBefore(' ').toLongOrNull() ?: continue
                when (key) {
                    "Size" -> group.size += kb
                    "Rss" -> group.rss += kb
                    "Pss" -> group.pss += kb
                    "Private_Dirty", "Private_Clean" -> group.priv += kb
                    "Swap" -> group.swap += kb
                }
            }
        }
        val total = MemGroup()
        groups.values.forEach { total.rss += it.rss; total.pss += it.pss; total.priv += it.priv; total.swap += it.swap }
        val out = StringBuilder()
        out.append(String.format(Locale.US, "total: rss %d MiB, pss %d MiB, private %d MiB, swap %d MiB, %d groups\n",
            total.rss / 1024, total.pss / 1024, total.priv / 1024, total.swap / 1024, groups.size))
        out.append(String.format(Locale.US, "native heap allocated %d MiB, Java heap %d of %d MiB\n",
            Debug.getNativeHeapAllocatedSize() / 1048576,
            (Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory()) / 1048576,
            Runtime.getRuntime().maxMemory() / 1048576))
        out.append("    rss     pss    priv    swap  reserved  maps  name (MiB)\n")
        groups.entries.sortedByDescending { it.value.pss }.take(top).forEach { (name, g) ->
            out.append(String.format(Locale.US, "%7d %7d %7d %7d %9d %5d  %s\n", g.rss / 1024, g.pss / 1024,
                g.priv / 1024, g.swap / 1024, g.size / 1024, g.count, name.take(90)))
        }
        return out.toString()
    }

    private fun lastExit(context: Context, count: Int): String {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return "Needs Android 11"
        val manager = context.getSystemService(ActivityManager::class.java)
        val reasons = manager.getHistoricalProcessExitReasons(context.packageName, 0, count + 4)
            .filter { !it.processName.endsWith(":nx2restart") &&
                !it.processName.endsWith(Nx2FrontService.PROCESS_SUFFIX) }.take(count)
        if (reasons.isEmpty()) return "Android has no record of earlier runs"
        val names = mapOf(0 to "unknown", 1 to "exit (the app ended itself)", 2 to "signaled",
            3 to "LOW MEMORY (Android closed it for memory)", 4 to "CRASH (Java)", 5 to "CRASH (native)",
            6 to "not responding (ANR)", 7 to "initialization failure", 8 to "permission change",
            9 to "excessive resource use", 10 to "user requested (closed from recents)",
            11 to "user stopped", 12 to "dependency died", 13 to "other", 14 to "frozen",
            15 to "package state change", 16 to "package updated")
        val format = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        return reasons.joinToString("\n") { info ->
            "${format.format(java.util.Date(info.timestamp))}  ${names[info.reason] ?: info.reason}" +
                "  pss=${info.pss / 1024} MiB rss=${info.rss / 1024} MiB importance=${info.importance}" +
                (info.description?.let { "  ($it)" } ?: "")
        }
    }

    // ---- recent frames and marks ------------------------------------------------------------

    private fun image(file: File, mime: String): JSONObject = JSONObject().put("type", "image")
        .put("data", Base64.encodeToString(file.readBytes(), Base64.NO_WRAP)).put("mimeType", mime)

    private fun recentFrames(count: Int): JSONObject {
        val dir = Nx2FlightRecorder.bridgeSessionDir()?.let { File(it, "frames") }
            ?: return error("No game session yet")
        val frames = dir.listFiles()?.filter { it.isFile && it.name.endsWith(".webp") }
            ?.sortedByDescending { it.lastModified() }?.take(count) ?: emptyList()
        if (frames.isEmpty()) return error("No saved frames yet (they are saved while the game is on screen)")
        val now = System.currentTimeMillis()
        val content = JSONArray()
        for (frame in frames) {
            content.put(JSONObject().put("type", "text").put("text",
                "${(now - frame.lastModified()) / 1000} s ago (${frame.name})"))
            content.put(image(frame, "image/webp"))
        }
        return JSONObject().put("content", content)
    }

    private fun listMarks(context: Context): String {
        val files = Nx2Overlay.marksDir(context).listFiles()?.filter { it.name.endsWith(".json") }
            ?.sortedByDescending { it.name } ?: emptyList()
        if (files.isEmpty()) return "No marks yet. In game, tap the C button (long-press to add a note)."
        val now = System.currentTimeMillis()
        return files.joinToString("\n") { file ->
            val meta = runCatching { JSONObject(file.readText()) }.getOrNull() ?: JSONObject()
            val age = (now - meta.optLong("wall_ms", now)) / 1000
            "${meta.optString("id")}  ${age} s ago  ${meta.optString("build")}  " +
                (if (File(file.parentFile, file.name.removeSuffix(".json") + ".jpg").isFile) "picture" else "no picture") +
                "  ${meta.optString("note", "")}"
        }
    }

    private fun getMark(context: Context, id: String): JSONObject {
        if (!Regex("[0-9-]{8,20}").matches(id)) return error("Bad mark id")
        val dir = Nx2Overlay.marksDir(context)
        val meta = File(dir, "$id.json")
        if (!meta.isFile) return error("No mark $id (see list_marks)")
        val content = JSONArray().put(JSONObject().put("type", "text").put("text", meta.readText()))
        val picture = File(dir, "$id.jpg")
        if (picture.isFile) content.put(image(picture, "image/jpeg"))
        return JSONObject().put("content", content)
    }

    // ---- notebook ---------------------------------------------------------------------------

    internal fun notebook(context: Context) = File(File(context.filesDir, "nx2-lab"), "notebook.md")

    private fun notebookAppend(context: Context, entry: String): String {
        val file = notebook(context)
        file.parentFile?.mkdirs()
        val stamp = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(java.util.Date())
        file.appendText("\n## $stamp (build ${Nx2FlightRecorder.buildTag()})\n${entry.trim()}\n")
        return "Saved (${file.length()} bytes in the notebook)"
    }
}
