package dev.radiorelay

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.MotionEvent
import android.view.View
import android.widget.EditText
import android.widget.SeekBar
import android.widget.Switch
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat

class MainActivity : AppCompatActivity() {
    private lateinit var host: EditText
    private lateinit var status: TextView
    private lateinit var dot: View
    private lateinit var link: Switch
    private lateinit var mute: TextView
    private lateinit var ptt: View
    private lateinit var pttLabel: TextView
    private lateinit var volume: SeekBar
    private lateinit var bubbleSw: Switch
    private val prefs by lazy { getSharedPreferences("cfg", MODE_PRIVATE) }

    @SuppressLint("ClickableViewAccessibility")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        host = findViewById(R.id.host); status = findViewById(R.id.status); dot = findViewById(R.id.dot)
        link = findViewById(R.id.link); mute = findViewById(R.id.mute)
        ptt = findViewById(R.id.ptt); pttLabel = findViewById(R.id.pttLabel); volume = findViewById(R.id.volume)
        bubbleSw = findViewById(R.id.bubble)

        host.setText(prefs.getString("host", ""))
        volume.progress = prefs.getInt("volume", 80)
        RadioService.volume = volume.progress / 100f
        bubbleSw.isChecked = prefs.getBoolean("bubble", true)
        RadioService.bubbleEnabled = bubbleSw.isChecked
        bubbleSw.setOnCheckedChangeListener { _, on -> onBubbleToggle(on) }

        ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS), 1)

        link.setOnCheckedChangeListener { _, on -> onToggle(on) }
        mute.setOnClickListener { RadioService.send(this, RadioService.ACTION_TOGGLE_MUTE) }
        findViewById<TextView>(R.id.history).setOnClickListener {
            val h = host.text.toString().trim()
            if (h.isEmpty()) { status.text = "Enter a server"; return@setOnClickListener }
            prefs.edit().putString("host", h).apply()
            startActivity(Intent(this, HistoryActivity::class.java))
        }
        findViewById<TextView>(R.id.overlay).setOnClickListener {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }

        volume.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar, p: Int, fromUser: Boolean) {
                if (!fromUser) return
                RadioService.volume = p / 100f
                prefs.edit().putInt("volume", p).apply()
                if (RadioService.running) startService(Intent(this@MainActivity, RadioService::class.java)
                    .setAction(RadioService.ACTION_SET_VOLUME).putExtra(RadioService.EXTRA_VOLUME, p / 100f))
            }
            override fun onStartTrackingTouch(sb: SeekBar) {}
            override fun onStopTrackingTouch(sb: SeekBar) {}
        })

        ptt.setOnTouchListener { v, ev ->
            when (ev.actionMasked) {
                MotionEvent.ACTION_DOWN -> { v.isPressed = true; RadioService.send(this, RadioService.ACTION_PTT_DOWN) }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { v.isPressed = false; RadioService.send(this, RadioService.ACTION_PTT_UP) }
            }
            true
        }
        RadioService.onState = { runOnUiThread { render() } }
        render()
    }

    private fun onBubbleToggle(on: Boolean) {
        if (on && !Settings.canDrawOverlays(this)) {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        prefs.edit().putBoolean("bubble", on).apply()
        RadioService.bubbleEnabled = on
        if (RadioService.running) startService(Intent(this, RadioService::class.java)
            .setAction(RadioService.ACTION_SET_BUBBLE).putExtra(RadioService.EXTRA_ENABLED, on))
    }

    /** Toggle ON: lock the server field and start the link. OFF: stop it and unlock. */
    private fun onToggle(on: Boolean) {
        if (on) {
            val h = host.text.toString().trim()
            if (h.isEmpty()) { setChecked(false); status.text = "Enter a server"; return }
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                setChecked(false); status.text = "Mic permission required"; return
            }
            prefs.edit().putString("host", h).apply()
            if (!RadioService.running) RadioService.send(this, RadioService.ACTION_START, h)
        } else if (RadioService.running) {
            RadioService.send(this, RadioService.ACTION_STOP)
        }
        render()
    }

    private fun setChecked(v: Boolean) {           // change the switch without re-triggering onToggle
        link.setOnCheckedChangeListener(null); link.isChecked = v
        link.setOnCheckedChangeListener { _, on -> onToggle(on) }
    }

    private fun render() {
        val r = RadioService.running
        if (link.isChecked != r) setChecked(r)      // e.g. stopped from the notification
        if (bubbleSw.isChecked != RadioService.bubbleEnabled) {   // hidden from the notification or dropped on ✕
            bubbleSw.setOnCheckedChangeListener(null); bubbleSw.isChecked = RadioService.bubbleEnabled
            prefs.edit().putBoolean("bubble", bubbleSw.isChecked).apply()
            bubbleSw.setOnCheckedChangeListener { _, on -> onBubbleToggle(on) }
        }
        findViewById<View>(R.id.rxDot).visibility = if (r && RadioService.rxActive) View.VISIBLE else View.INVISIBLE
        host.isEnabled = !r
        host.alpha = if (r) 0.6f else 1f
        ptt.isEnabled = r
        pttLabel.text = if (RadioService.ptt) "TX" else "PTT"
        pttLabel.alpha = if (r) 1f else 0.4f
        mute.text = if (RadioService.muted) "Unmute" else "Mute"
        val (text, color) = when {
            !r -> "Off" to 0xFF777777.toInt()
            RadioService.ptt -> "Transmitting" to 0xFFE53935.toInt()
            !RadioService.connected -> "Connecting…" to 0xFFFFB300.toInt()
            RadioService.muted -> "Muted" to 0xFF4FC3F7.toInt()
            else -> "Listening" to 0xFF43A047.toInt()
        }
        status.text = text
        dot.background.mutate().setTint(color)
        findViewById<TextView>(R.id.overlay).text = if (Settings.canDrawOverlays(this))
            "Bubble over other apps" else "Tap to allow display over other apps ›"
    }

    override fun onResume() { super.onResume(); render() }
}
