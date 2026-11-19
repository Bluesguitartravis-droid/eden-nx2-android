// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: Claude on the game screen (Claude Lab → "Show Claude in game").
// - Messages from Claude (the show_message tool) appear as a bubble at the top for a few seconds.
// - A small "C" button in the corner marks the moment: tap saves a full-size screenshot and a
//   NX2_MARK line in the log; long-press does the same and asks for a note. Claude reads the
//   marks with list_marks / get_mark, so "that glitch just now" can be looked at afterwards.
package io.github.zortos293.lichii.nx2

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
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
        activity.addContentView(layer, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT))
        layerRef = WeakReference(layer)
        bubbleRef = WeakReference(bubble)
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
    }
}
