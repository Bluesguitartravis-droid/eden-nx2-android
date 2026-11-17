// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: the Claude Lab screen. Turns the Claude connection on, takes the ngrok code, shows the
// address to add as a connector in Claude, and the lab notebook Claude keeps on the device.
package io.github.zortos293.lichii.nx2

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.TypedValue
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

class Nx2LabActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var address: TextView
    private lateinit var copy: Button
    private lateinit var markButton: Button
    private var waitingForKey = false
    private val refresher = object : Runnable {
        override fun run() {
            refresh()
            handler.postDelayed(this, 1500)
        }
    }

    private fun dp(value: Int) =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

    private fun text(value: String, size: Float = 15f, bold: Boolean = false) = TextView(this).apply {
        text = value
        setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
        if (bold) setTypeface(typeface, Typeface.BOLD)
        setPadding(0, dp(6), 0, dp(6))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = "Claude Lab"
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(16), dp(20), dp(24))
        }
        column.addView(text("Claude Lab", 24f, true))
        column.addView(text("Lets Claude, in your Claude app chat, read Eden's performance, change " +
            "settings, take screenshots and press buttons while you play. It stays off until you " +
            "turn it on, and only someone with your private address can reach it."))

        column.addView(text("1. ngrok code", 18f, true))
        column.addView(text("Make a free account at ngrok.com, open \"Your Authtoken\" in the " +
            "dashboard, copy it and paste it here."))
        val token = EditText(this).apply {
            hint = "Paste your ngrok authtoken"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            setText(Nx2Bridge.ngrokToken())
            isSingleLine = true
        }
        column.addView(token)
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(Button(this).apply {
            text = "Save code"
            setOnClickListener {
                Nx2Bridge.setNgrokToken(token.text.toString())
                Nx2Bridge.restartTunnel()
                Toast.makeText(this@Nx2LabActivity, "Saved", Toast.LENGTH_SHORT).show()
            }
        })
        row.addView(Button(this).apply {
            text = "Open ngrok"
            setOnClickListener {
                runCatching {
                    startActivity(Intent(Intent.ACTION_VIEW,
                        Uri.parse("https://dashboard.ngrok.com/get-started/your-authtoken")))
                }
            }
        })
        column.addView(row)

        column.addView(text("2. Turn it on", 18f, true))
        column.addView(Switch(this).apply {
            text = "Let Claude connect"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            isChecked = Nx2Bridge.isEnabled()
            setOnCheckedChangeListener { _, on -> Nx2Bridge.setEnabled(on) }
            setPadding(0, dp(8), 0, dp(8))
        })
        status = text("", 14f)
        column.addView(status)
        column.addView(Switch(this).apply {
            text = "Show Claude in game (messages, and a C button to mark moments)"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            isChecked = Nx2Overlay.isShown(this@Nx2LabActivity)
            setOnCheckedChangeListener { _, on -> Nx2Overlay.setShown(this@Nx2LabActivity, on) }
            setPadding(0, dp(4), 0, dp(8))
        })
        column.addView(text("In game, tap the small C in the top corner when you see something " +
            "wrong; hold it to add a note. Claude can then look at that moment.", 13f))
        markButton = Button(this).apply {
            setOnClickListener { chooseMarkKey() }
        }
        column.addView(markButton)

        column.addView(text("3. Add it to Claude (once)", 18f, true))
        column.addView(text("Open claude.ai in your browser (the connector setting may not be in " +
            "the app): Settings → Connectors → Add custom connector. Name it Eden and paste this address:"))
        address = text("", 14f).apply { setTextIsSelectable(true); typeface = Typeface.MONOSPACE }
        column.addView(address)
        copy = Button(this).apply {
            text = "Copy address"
            setOnClickListener {
                val url = Nx2Bridge.connectorUrl() ?: return@setOnClickListener
                getSystemService(ClipboardManager::class.java)
                    ?.setPrimaryClip(ClipData.newPlainText("Eden connector", url))
                Toast.makeText(this@Nx2LabActivity, "Address copied", Toast.LENGTH_SHORT).show()
            }
        }
        column.addView(copy)
        column.addView(text("Keep the address private. If it ever leaks, make a new one (then " +
            "update the connector in Claude):", 13f))
        column.addView(Button(this).apply {
            text = "Make a new address"
            setOnClickListener {
                AlertDialog.Builder(this@Nx2LabActivity)
                    .setMessage("The old address stops working. You'll need to paste the new one into Claude.")
                    .setPositiveButton("Make new") { _, _ -> Nx2Bridge.resetSecret(); refresh() }
                    .setNegativeButton(android.R.string.cancel, null)
                    .show()
            }
        })

        column.addView(text("Lab notebook", 18f, true))
        column.addView(text("Notes Claude saves on this device about what it tested and found.", 13f))
        column.addView(Button(this).apply {
            text = "Show notebook"
            setOnClickListener {
                val file = Nx2Tools.notebook(this@Nx2LabActivity)
                val body = if (file.isFile) file.readText() else "(empty)"
                AlertDialog.Builder(this@Nx2LabActivity)
                    .setTitle("Lab notebook")
                    .setMessage(body.takeLast(20000))
                    .setPositiveButton(android.R.string.ok, null)
                    .show()
            }
        })

        setContentView(ScrollView(this).apply {
            addView(column, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT))
        })
    }

    private fun chooseMarkKey() {
        if (Nx2Overlay.markKey(this) != 0) {
            Nx2Overlay.setMarkKey(this, 0)
            refresh()
            return
        }
        waitingForKey = true
        markButton.text = "Press the button to use now (e.g. a back button)…"
    }

    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean {
        if (waitingForKey && event.action == android.view.KeyEvent.ACTION_DOWN &&
            event.keyCode != android.view.KeyEvent.KEYCODE_BACK
        ) {
            waitingForKey = false
            Nx2Overlay.setMarkKey(this, event.keyCode)
            Toast.makeText(this, "Mark button set: " +
                android.view.KeyEvent.keyCodeToString(event.keyCode).removePrefix("KEYCODE_"),
                Toast.LENGTH_SHORT).show()
            refresh()
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    private fun refresh() {
        if (!waitingForKey) {
            val code = Nx2Overlay.markKey(this)
            markButton.text = if (code == 0) "Mark with a controller button: choose…"
            else "Mark button: " + android.view.KeyEvent.keyCodeToString(code).removePrefix("KEYCODE_") +
                " (tap to remove)"
        }
        val on = Nx2Bridge.isEnabled()
        val url = Nx2Bridge.connectorUrl()
        status.text = when {
            !on -> "Off."
            url != null -> "On and reachable. Claude has made ${Nx2Bridge.requestCount} requests" +
                (if (Nx2Bridge.lastRequestMs > 0) ", last " +
                    ((System.currentTimeMillis() - Nx2Bridge.lastRequestMs) / 1000) + " s ago." else ".")
            Nx2Tunnel.lastError != null -> "Not reachable yet: ${Nx2Tunnel.lastError}"
            else -> "Connecting…"
        }
        address.text = url ?: "(appears once it's on and connected)"
        copy.isEnabled = url != null
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresher)
    }

    override fun onPause() {
        handler.removeCallbacks(refresher)
        super.onPause()
    }
}
