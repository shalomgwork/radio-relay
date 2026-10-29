package dev.radiorelay

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.Settings
import android.view.KeyEvent
import android.view.MotionEvent
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat

class MainActivity : AppCompatActivity() {
    private lateinit var host: EditText
    private lateinit var status: TextView
    private lateinit var connect: Button
    private lateinit var mute: Button
    private lateinit var ptt: Button

    @SuppressLint("ClickableViewAccessibility")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        host = findViewById(R.id.host); status = findViewById(R.id.status)
        connect = findViewById(R.id.connect); mute = findViewById(R.id.mute); ptt = findViewById(R.id.ptt)

        val prefs = getSharedPreferences("cfg", MODE_PRIVATE)
        host.setText(prefs.getString("host", ""))

        ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS), 1)

        connect.setOnClickListener {
            if (RadioService.running) RadioService.send(this, RadioService.ACTION_STOP)
            else {
                val h = host.text.toString().trim()
                if (h.isEmpty()) return@setOnClickListener
                prefs.edit().putString("host", h).apply()
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    status.text = "Mic permission required"; return@setOnClickListener
                }
                RadioService.send(this, RadioService.ACTION_START, h)
            }
        }
        mute.setOnClickListener { RadioService.send(this, RadioService.ACTION_TOGGLE_MUTE) }
        findViewById<Button>(R.id.keysetup).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        ptt.setOnTouchListener { _, ev ->
            when (ev.actionMasked) {
                MotionEvent.ACTION_DOWN -> RadioService.send(this, RadioService.ACTION_PTT_DOWN)
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> RadioService.send(this, RadioService.ACTION_PTT_UP)
            }
            true
        }
        RadioService.onState = { runOnUiThread { render() } }
        render()
    }

    // Volume-down PTT while the app is in front (no accessibility service needed for this case)
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN && RadioService.running) {
            if (event.repeatCount == 0) RadioService.send(this, RadioService.ACTION_PTT_DOWN); return true
        }
        return super.onKeyDown(keyCode, event)
    }
    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN && RadioService.running) {
            RadioService.send(this, RadioService.ACTION_PTT_UP); return true
        }
        return super.onKeyUp(keyCode, event)
    }

    private fun render() {
        val r = RadioService.running
        connect.text = if (r) "Disconnect" else "Connect"
        mute.text = if (RadioService.muted) "Unmute" else "Mute"
        ptt.isEnabled = r
        ptt.setBackgroundColor(if (RadioService.ptt) 0xFFC62828.toInt() else if (r) 0xFF2E7D32.toInt() else 0xFF333333.toInt())
        status.text = when {
            !r -> "Disconnected"
            RadioService.ptt -> "TX"
            !RadioService.connected -> "Connecting…"
            RadioService.muted -> "Connected · muted"
            else -> "Connected · listening"
        }
    }

    override fun onResume() { super.onResume(); render() }
}
