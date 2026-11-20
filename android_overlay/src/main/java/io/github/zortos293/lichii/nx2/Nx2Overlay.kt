// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: Claude on the game screen (Claude Lab → "Show Claude in game").
// - Messages from Claude (the show_message tool) appear as a bubble at the top for a few seconds.
// - The chat panel (top left) holds the conversation with Claude: Claude's messages (the say
//   tool) and the player's replies (Reply button), read by Claude with wait_for_player. Drag its
//   title to move it, tap the title to fold it; A- / A+ change its size, the half circle its
//   opacity. All of that is remembered.
// - A small "C" button in the corner marks the moment: tap saves a full-size screenshot and a
//   NX2_MARK line in the log; long-press does the same and asks for a note. Claude reads the
//   marks with list_marks / get_mark, so "that glitch just now" can be looked at afterwards.
package io.github.zortos293.lichii.nx2

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.view.MotionEvent
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.LinearLayout
import android.widget.ScrollView
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.PixelCopy
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import io.github.zortos293.lichii.utils.Nx2FlightRecorder
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.lang.ref.WeakReference
import java.util.Locale

object Nx2Overlay {
    private const val PREFS = "nx2_bridge"
    private const val KEY_SHOW = "show_in_game"
    private const val KEY_MARK_KEY = "mark_keycode"
    private val main = Handler(Looper.getMainLooper())
    private var layerRef: WeakReference<FrameLayout>? = null
    private var bubbleRef: WeakReference<TextView>? = null
    private val hideBubble = Runnable { bubbleRef?.get()?.visibility = View.GONE }
    private val captureThread by lazy { HandlerThread("NX2-Mark").also { it.start() } }
    private const val KEY_CHAT_SCALE = "chat_scale"
    private const val KEY_CHAT_ALPHA = "chat_alpha"
    private const val KEY_CHAT_FOLDED = "chat_folded"
    private const val KEY_CHAT_X = "chat_x"
    private const val KEY_CHAT_Y = "chat_y"
    private val ALPHAS = floatArrayOf(1.0f, 0.8f, 0.6f, 0.4f, 0.25f)
    private var panelRef: WeakReference<LinearLayout>? = null
    private var chatTextRef: WeakReference<TextView>? = null
    private var chatScrollRef: WeakReference<ScrollView>? = null
    private var titleRef: WeakReference<TextView>? = null
    private var unread = 0

    fun isShown(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_SHOW, true)

    fun setShown(context: Context, shown: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_SHOW, shown).apply()
        main.post { layerRef?.get()?.visibility = if (shown && Nx2Bridge.isEnabled()) View.VISIBLE else View.GONE }
    }

    fun markKey(context: Context): Int =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(KEY_MARK_KEY, 0)

    fun setMarkKey(context: Context, keyCode: Int) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putInt(KEY_MARK_KEY, keyCode).apply()
    }

    /**
     * EmulationActivity.dispatchKeyEvent (patched): the button chosen in Claude Lab marks the
     * moment, and the game never sees it. True when the event was used.
     */
    fun onKey(activity: Activity, event: android.view.KeyEvent): Boolean {
        val code = markKey(activity)
        if (code == 0 || event.keyCode != code || !Nx2Bridge.isEnabled()) return false
        if (event.action == android.view.KeyEvent.ACTION_DOWN && event.repeatCount == 0) {
            main.post { mark(activity, null) }
        }
        return true
    }

    private fun dp(context: Context, value: Int) = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), context.resources.displayMetrics).toInt()

    /** Called by EmulationActivity.onCreate (patched in by apply-nx2-claude-bridge.py). */
    fun attach(activity: Activity) {
        val layer = FrameLayout(activity).apply {
            isClickable = false
            isFocusable = false
            visibility = if (isShown(activity) && Nx2Bridge.isEnabled()) View.VISIBLE else View.GONE
        }
        val bubble = TextView(activity).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setPadding(dp(activity, 14), dp(activity, 8), dp(activity, 14), dp(activity, 8))
            background = GradientDrawable().apply {
                cornerRadius = dp(activity, 14).toFloat()
                setColor(Color.argb(210, 32, 26, 22))
            }
            visibility = View.GONE
            maxWidth = (activity.resources.displayMetrics.widthPixels * 0.7f).toInt()
        }
        layer.addView(bubble, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.CENTER_HORIZONTAL).apply {
            topMargin = dp(activity, 12)
        })
        val button = TextView(activity).apply {
            text = "C"
            gravity = Gravity.CENTER
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            alpha = 0.55f
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.rgb(217, 119, 87))
            }
            isFocusable = false
            setOnClickListener { mark(activity, null) }
            setOnLongClickListener { askNote(activity); true }
        }
        val size = dp(activity, 34)
        layer.addView(button, FrameLayout.LayoutParams(size, size, Gravity.TOP or Gravity.END).apply {
            topMargin = dp(activity, 10)
            marginEnd = dp(activity, 10)
        })
        addChatPanel(activity, layer)
        activity.addContentView(layer, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT))
        layerRef = WeakReference(layer)
        bubbleRef = WeakReference(bubble)
        Nx2Chat.onChange = { refreshChat(activity) }
        refreshChat(activity)
    }

    private fun chatPrefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun chatButton(context: Context, label: String, onTap: () -> Unit) = TextView(context).apply {
        text = label
        gravity = Gravity.CENTER
        setTextColor(Color.WHITE)
        setPadding(dp(context, 7), dp(context, 3), dp(context, 7), dp(context, 3))
        background = GradientDrawable().apply {
            cornerRadius = dp(context, 8).toFloat()
            setColor(Color.argb(150, 90, 74, 64))
        }
        isFocusable = false
        setOnClickListener { onTap() }
    }

    private fun addChatPanel(activity: Activity, layer: FrameLayout) {
        Nx2Chat.load(activity)
        val prefs = chatPrefs(activity)
        val panel = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 8), dp(activity, 6), dp(activity, 8), dp(activity, 8))
            background = GradientDrawable().apply {
                cornerRadius = dp(activity, 12).toFloat()
                setColor(Color.argb(200, 32, 26, 22))
            }
            alpha = prefs.getFloat(KEY_CHAT_ALPHA, 0.8f)
            translationX = prefs.getFloat(KEY_CHAT_X, dp(activity, 10).toFloat())
            translationY = prefs.getFloat(KEY_CHAT_Y, dp(activity, 56).toFloat())
            isClickable = true
        }
        val header = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val title = TextView(activity).apply {
            text = "Claude"
            setTextColor(Color.rgb(217, 119, 87))
            setTypeface(typeface, Typeface.BOLD)
            setPadding(dp(activity, 2), dp(activity, 3), dp(activity, 8), dp(activity, 3))
        }
        // Drag the title to move the panel; a tap folds or unfolds it.
        var downX = 0f
        var downY = 0f
        var startX = 0f
        var startY = 0f
        var moved = false
        title.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX; downY = event.rawY
                    startX = panel.translationX; startY = panel.translationY
                    moved = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    if (moved || dx * dx + dy * dy > dp(activity, 8).let { it * it }) {
                        moved = true
                        val maxX = (layer.width - panel.width).coerceAtLeast(0).toFloat()
                        val maxY = (layer.height - dp(activity, 40)).coerceAtLeast(0).toFloat()
                        panel.translationX = (startX + dx).coerceIn(0f, maxX)
                        panel.translationY = (startY + dy).coerceIn(0f, maxY)
                    }
                }
                MotionEvent.ACTION_UP -> {
                    if (moved) {
                        prefs.edit().putFloat(KEY_CHAT_X, panel.translationX)
                            .putFloat(KEY_CHAT_Y, panel.translationY).apply()
                    } else {
                        val folded = !prefs.getBoolean(KEY_CHAT_FOLDED, false)
                        prefs.edit().putBoolean(KEY_CHAT_FOLDED, folded).apply()
                        if (!folded) unread = 0
                        applyChatLayout(activity)
                    }
                }
            }
            true
        }
        header.addView(title, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val gap = dp(activity, 4)
        fun addHeaderButton(view: View) = header.addView(view, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = gap })
        addHeaderButton(chatButton(activity, "A\u2212") { changeScale(activity, -0.15f) })
        addHeaderButton(chatButton(activity, "A+") { changeScale(activity, 0.15f) })
        addHeaderButton(chatButton(activity, "\u25D0") {
            val current = panel.alpha
            val index = ALPHAS.indexOfFirst { it <= current + 0.01f }.let { if (it < 0) 0 else it }
            val next = ALPHAS[(index + 1) % ALPHAS.size]
            panel.alpha = next
            prefs.edit().putFloat(KEY_CHAT_ALPHA, next).apply()
        })
        addHeaderButton(chatButton(activity, "Reply") { askReply(activity) })
        panel.addView(header)
        val scroll = ScrollView(activity).apply { isVerticalScrollBarEnabled = true }
        val chatText = TextView(activity).apply {
            setTextColor(Color.WHITE)
            setLineSpacing(0f, 1.1f)
            setPadding(dp(activity, 2), dp(activity, 6), dp(activity, 2), 0)
            text = "Claude's messages appear here. Reply to answer without leaving the game."
        }
        scroll.addView(chatText)
        panel.addView(scroll)
        layer.addView(panel, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.START))
        panelRef = WeakReference(panel)
        chatTextRef = WeakReference(chatText)
        chatScrollRef = WeakReference(scroll)
        titleRef = WeakReference(title)
        applyChatLayout(activity)
    }

    private fun changeScale(context: Context, delta: Float) {
        val prefs = chatPrefs(context)
        val scale = (prefs.getFloat(KEY_CHAT_SCALE, 1f) + delta).coerceIn(0.6f, 2.2f)
        prefs.edit().putFloat(KEY_CHAT_SCALE, scale).apply()
        applyChatLayout(context)
    }

    private fun applyChatLayout(context: Context) {
        val panel = panelRef?.get() ?: return
        val scroll = chatScrollRef?.get() ?: return
        val chatText = chatTextRef?.get() ?: return
        val prefs = chatPrefs(context)
        val scale = prefs.getFloat(KEY_CHAT_SCALE, 1f)
        val folded = prefs.getBoolean(KEY_CHAT_FOLDED, false)
        val metrics = context.resources.displayMetrics
        chatText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f * scale)
        val panelWidth = (metrics.widthPixels * 0.30f * scale).toInt()
            .coerceIn(dp(context, 180), (metrics.widthPixels * 0.9f).toInt())
        val scrollHeight = (metrics.heightPixels * 0.30f * scale).toInt()
            .coerceIn(dp(context, 60), (metrics.heightPixels * 0.85f).toInt())
        panel.layoutParams = panel.layoutParams.apply { width = panelWidth }
        scroll.layoutParams = (scroll.layoutParams as LinearLayout.LayoutParams).apply { height = scrollHeight }
        scroll.visibility = if (folded) View.GONE else View.VISIBLE
        titleRef?.get()?.text = if (folded && unread > 0) "Claude ($unread new)" else "Claude"
        panel.requestLayout()
        scroll.post { scroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun refreshChat(context: Context) {
        val chatText = chatTextRef?.get() ?: return
        val recent = Nx2Chat.recent(40)
        if (recent.isEmpty()) return
        val text = SpannableStringBuilder()
        for (message in recent) {
            if (text.isNotEmpty()) text.append("\n")
            val start = text.length
            text.append(if (message.fromPlayer) "You: " else "Claude: ")
            text.setSpan(ForegroundColorSpan(if (message.fromPlayer) Color.rgb(140, 200, 255) else Color.rgb(217, 119, 87)),
                start, text.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            text.setSpan(StyleSpan(Typeface.BOLD), start, text.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            text.append(message.text)
        }
        chatText.text = text
        val last = recent.last()
        val folded = chatPrefs(context).getBoolean(KEY_CHAT_FOLDED, false)
        if (folded && !last.fromPlayer && System.currentTimeMillis() - last.wallMs < 5000) {
            unread++
            showMessage(last.text, 8)
        }
        applyChatLayout(context)
    }

    private fun askReply(activity: Activity) {
        val input = EditText(activity).apply {
            hint = "Message for Claude"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or
                InputType.TYPE_TEXT_FLAG_MULTI_LINE
            imeOptions = EditorInfo.IME_ACTION_SEND
            maxLines = 4
        }
        val dialog = AlertDialog.Builder(activity)
            .setTitle("Message for Claude")
            .setView(input)
            .setPositiveButton("Send") { _, _ -> Nx2Chat.add(true, input.text.toString()) }
            .setNegativeButton("Cancel", null)
            .create()
        input.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                Nx2Chat.add(true, input.text.toString())
                dialog.dismiss()
                true
            } else false
        }
        dialog.setOnShowListener {
            input.requestFocus()
            dialog.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE)
        }
        // Keep the game visible behind it: no dimming.
        dialog.window?.setDimAmount(0f)
        dialog.show()
    }

    /** For the say tool: adds Claude's message to the chat panel. False when the panel is not on screen. */
    fun say(text: String): Boolean {
        Nx2Chat.add(false, text)
        val layer = layerRef?.get() ?: return false
        return layer.visibility == View.VISIBLE && panelRef?.get() != null
    }

    /** For the show_message tool. Returns false when there is no game screen or it is hidden. */
    fun showMessage(text: String, seconds: Int): Boolean {
        val layer = layerRef?.get() ?: return false
        if (layer.visibility != View.VISIBLE) return false
        main.post {
            val bubble = bubbleRef?.get() ?: return@post
            bubble.text = text
            bubble.visibility = View.VISIBLE
            main.removeCallbacks(hideBubble)
            main.postDelayed(hideBubble, seconds.coerceIn(2, 60) * 1000L)
        }
        return true
    }

    private fun askNote(activity: Activity) {
        val input = EditText(activity).apply {
            hint = "What did you see?"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
        }
        // Capture first, so the picture is the moment of the long-press, not the dialog.
        val id = mark(activity, null)
        AlertDialog.Builder(activity)
            .setTitle("Note for Claude")
            .setView(input)
            .setPositiveButton("Save") { _, _ -> if (id != null) addNote(activity, id, input.text.toString()) }
            .setNegativeButton("Skip", null)
            .show()
    }

    internal fun marksDir(context: Context) = File(File(context.filesDir, "nx2-lab"), "marks")

    private fun mark(activity: Activity, note: String?): String? {
        val dir = marksDir(activity).apply { mkdirs() }
        val id = java.text.SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(java.util.Date())
        runCatching { io.github.zortos293.lichii.utils.Log.info("NX2_MARK $id ${note ?: ""}") }
        val meta = JSONObject().put("id", id).put("wall_ms", System.currentTimeMillis())
            .put("build", Nx2FlightRecorder.buildTag())
        if (note != null) meta.put("note", note)
        File(dir, "$id.json").writeText(meta.toString())
        val view = Nx2FlightRecorder.bridgeSurface()
        if (view != null && view.holder.surface.isValid && view.width > 0 && view.height > 0) {
            val width = view.width.coerceAtMost(1920)
            val height = (width.toLong() * view.height / view.width).toInt().coerceAtLeast(1)
            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            runCatching {
                PixelCopy.request(view, bitmap, { code ->
                    if (code == PixelCopy.SUCCESS) {
                        runCatching {
                            FileOutputStream(File(dir, "$id.jpg")).use { bitmap.compress(Bitmap.CompressFormat.JPEG, 85, it) }
                        }
                    }
                    bitmap.recycle()
                }, Handler(captureThread.looper))
            }.onFailure { bitmap.recycle() }
        }
        Toast.makeText(activity, "Marked for Claude", Toast.LENGTH_SHORT).show()
        Nx2Chat.add(true, "(marked a moment: $id)")
        // Keep the last 30 marks.
        dir.listFiles()?.filter { it.name.endsWith(".json") }?.sortedBy { it.name }?.dropLast(30)?.forEach {
            it.delete()
            File(dir, it.name.removeSuffix(".json") + ".jpg").delete()
        }
        return id
    }

    private fun addNote(context: Context, id: String, note: String) {
        val file = File(marksDir(context), "$id.json")
        if (!file.isFile || note.isBlank()) return
        val meta = JSONObject(file.readText()).put("note", note.trim())
        file.writeText(meta.toString())
        runCatching { io.github.zortos293.lichii.utils.Log.info("NX2_MARK $id note: ${note.trim()}") }
        Nx2Chat.add(true, "(note for mark $id) ${note.trim()}")
    }
}
