// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: the in-game chat between the player and Claude.
// Claude writes with the `say` tool; the player answers from the chat panel on the game screen
// (Nx2Overlay). Claude reads the answers with `wait_for_player`, which blocks until the player
// writes or marks a moment, so a conversation can go on while the game keeps running.
// The last messages are kept in files/nx2-lab/chat.jsonl so the panel survives a restart.
package io.github.zortos293.lichii.nx2

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object Nx2Chat {
    data class Message(val id: Long, val fromPlayer: Boolean, val text: String, val wallMs: Long)

    private const val KEEP = 200
    private val lock = Object()
    private val messages = ArrayList<Message>()
    private var nextId = 1L
    private var delivered = 0L
    private var file: File? = null

    /** Called on the main thread after every new message (the panel redraws). */
    @Volatile var onChange: (() -> Unit)? = null

    fun load(context: Context) {
        synchronized(lock) {
            if (file != null) return
            val dir = File(context.filesDir, "nx2-lab").apply { mkdirs() }
            val target = File(dir, "chat.jsonl")
            file = target
            if (!target.isFile) return
            runCatching {
                target.readLines().takeLast(KEEP).forEach { line ->
                    val json = JSONObject(line)
                    messages.add(Message(nextId++, json.optBoolean("player"), json.optString("text"),
                        json.optLong("wall_ms")))
                }
            }
            // Messages from an earlier run are history, not something Claude still has to answer.
            delivered = nextId - 1
        }
    }

    fun add(fromPlayer: Boolean, text: String): Message? {
        val clean = text.trim()
        if (clean.isEmpty()) return null
        val message: Message
        synchronized(lock) {
            message = Message(nextId++, fromPlayer, clean, System.currentTimeMillis())
            messages.add(message)
            while (messages.size > KEEP) messages.removeAt(0)
            lock.notifyAll()
            file?.let { target ->
                runCatching {
                    target.appendText(JSONObject().put("player", fromPlayer).put("text", clean)
                        .put("wall_ms", message.wallMs).toString() + "\n")
                    if (target.length() > 256 * 1024) {
                        val keep = target.readLines().takeLast(KEEP)
                        target.writeText(keep.joinToString("\n", postfix = "\n"))
                    }
                }
            }
        }
        runCatching {
            io.github.zortos293.lichii.utils.Log.info(
                "NX2_CHAT ${if (fromPlayer) "player" else "claude"}: ${clean.replace('\n', ' ')}")
        }
        onChange?.let { callback -> android.os.Handler(android.os.Looper.getMainLooper()).post { callback() } }
        return message
    }

    fun recent(count: Int): List<Message> = synchronized(lock) { messages.takeLast(count) }

    /** Player messages Claude has not seen yet; waits up to [timeoutMs] for the first one. */
    fun waitForPlayer(timeoutMs: Long): List<Message> {
        val deadline = System.currentTimeMillis() + timeoutMs
        synchronized(lock) {
            while (true) {
                val unseen = messages.filter { it.fromPlayer && it.id > delivered }
                if (unseen.isNotEmpty()) {
                    // A message often comes in two parts; give the second one a moment to arrive.
                    lock.wait(1500)
                    val all = messages.filter { it.fromPlayer && it.id > delivered }
                    delivered = all.last().id
                    return all
                }
                val left = deadline - System.currentTimeMillis()
                if (left <= 0) return emptyList()
                lock.wait(left)
            }
        }
    }

    fun format(message: Message): String =
        SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(message.wallMs)) +
            (if (message.fromPlayer) " Player: " else " Claude: ") + message.text
}
