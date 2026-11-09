package dev.radiorelay

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat

/**
 * D-pad-first home screen for the keypad Sonim (240x320, no touchscreen, hardware PTT).
 * Three focusable rows: Connect/Disconnect, the active Server (opens the profile list), and History.
 * No on-screen PTT (the hardware key handles it), no mute, no volume (hardware volume keys do that).
 */
class MainActivity : AppCompatActivity() {
    private lateinit var status: TextView
    private lateinit var dot: View
    private lateinit var rxDot: View
    private lateinit var connectLabel: TextView
    private lateinit var connectDot: View
    private lateinit var serverValue: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Servers.migrate(this)
        setContentView(R.layout.activity_main)
        status = findViewById(R.id.status); dot = findViewById(R.id.dot); rxDot = findViewById(R.id.rxDot)
        connectLabel = findViewById(R.id.connectLabel); connectDot = findViewById(R.id.connectDot)
        serverValue = findViewById(R.id.serverValue)

        ActivityCompat.requestPermissions(this,
            arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS), 1)

        findViewById<View>(R.id.connect).setOnClickListener { toggleConnect() }
        findViewById<View>(R.id.servers).setOnClickListener { startActivity(Intent(this, ServersActivity::class.java)) }
        findViewById<View>(R.id.history).setOnClickListener {
            if (Servers.active(this)?.host.isNullOrBlank()) {
                Toast.makeText(this, "Add a server first", Toast.LENGTH_SHORT).show()
                startActivity(Intent(this, ServersActivity::class.java))
            } else startActivity(Intent(this, HistoryActivity::class.java))
        }

        RadioService.onState = { runOnUiThread { render() } }
        findViewById<View>(R.id.connect).requestFocus()
        render()
    }

    private fun toggleConnect() {
        if (RadioService.running) { RadioService.send(this, RadioService.ACTION_STOP); render(); return }
        val p = Servers.active(this)
        if (p == null || p.host.isBlank()) {
            Toast.makeText(this, "Add a server first", Toast.LENGTH_SHORT).show()
            startActivity(Intent(this, ServersActivity::class.java)); return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            Toast.makeText(this, "Mic permission required", Toast.LENGTH_SHORT).show(); return
        }
        RadioService.send(this, RadioService.ACTION_START, p.host)
        render()
    }

    private fun render() {
        val r = RadioService.running
        rxDot.visibility = if (r && RadioService.rxActive) View.VISIBLE else View.INVISIBLE
        serverValue.text = Servers.active(this)?.label ?: "None"
        connectLabel.text = if (r) "Disconnect" else "Connect"
        val (text, color) = when {
            !r -> "Off" to 0xFF777777.toInt()
            RadioService.ptt -> "Transmitting" to 0xFFE53935.toInt()
            !RadioService.connected -> "Connecting…" to 0xFFFFB300.toInt()
            else -> "Listening" to 0xFF43A047.toInt()
        }
        status.text = text
        dot.background.mutate().setTint(color)
        connectDot.background.mutate().setTint(if (r) 0xFF43A047.toInt() else 0xFF777777.toInt())
    }

    override fun onResume() { super.onResume(); render() }

    /** Hardware PTT key when the app is in front; the accessibility service covers the background. */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode in PttKeyReceiver.PTT_KEYCODES) {
            if (event.repeatCount == 0 && RadioService.running) when (event.action) {
                KeyEvent.ACTION_DOWN -> RadioService.send(this, RadioService.ACTION_PTT_DOWN)
                KeyEvent.ACTION_UP -> RadioService.send(this, RadioService.ACTION_PTT_UP)
            }
            return true
        }
        return super.dispatchKeyEvent(event)
    }
}
