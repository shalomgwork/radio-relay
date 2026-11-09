package dev.radiorelay

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import android.view.KeyEvent

/**
 * Receives the Sonim hardware PTT key when it is assigned to this app in
 * Settings > Programmable Keys > "Select PTT Key app".
 *
 * Sonim's broadcast names come from community documentation (not an official SDK),
 * so every intent is logged under tag "PttKey" to confirm the real format on-device.
 */
class PttKeyReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val keyEvent = intent.getParcelableExtraCompat(Intent.EXTRA_KEY_EVENT)
        Log.i(TAG, "received action=$action keyEvent=$keyEvent extras=${intent.extras?.keySet()?.joinToString()}")
        // Only act while the link is up: the foreground service keeps our uid out of the
        // "background" state, so startService is allowed. If it's not running, the key does nothing
        // (and starting the service from a background receiver would throw IllegalStateException).
        if (!RadioService.running) { Log.i(TAG, "link not running; ignoring"); return }
        try {
            when (action) {
                ACTION_PTT_KEY_DOWN -> RadioService.send(context, RadioService.ACTION_PTT_DOWN)
                ACTION_PTT_KEY_UP -> RadioService.send(context, RadioService.ACTION_PTT_UP)
            }
        } catch (e: IllegalStateException) {
            Log.w(TAG, "could not forward PTT to service", e)
        }
    }

    private fun Intent.getParcelableExtraCompat(name: String): KeyEvent? =
        if (android.os.Build.VERSION.SDK_INT >= 33) getParcelableExtra(name, KeyEvent::class.java)
        else @Suppress("DEPRECATION") getParcelableExtra(name) as? KeyEvent

    companion object {
        const val TAG = "PttKey"
        const val ACTION_PTT_KEY_DOWN = "com.sonim.intent.action.PTT_KEY_DOWN"
        const val ACTION_PTT_KEY_UP = "com.sonim.intent.action.PTT_KEY_UP"

        /** Keycode the XP5900 PTT key sends to the foreground app (logcat: "KEYCODE_PTT ... 289"). */
        val PTT_KEYCODES = setOf(289)
    }
}
