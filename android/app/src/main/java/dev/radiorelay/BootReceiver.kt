package dev.radiorelay

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Starts the radio link automatically after the phone boots.
 *
 * We just start the service; its keepalive loop keeps trying to reach the server until the network
 * (e.g. Tailscale's always-on VPN) comes up, so we don't need to wait for connectivity here — the
 * link turns green on its own once the tunnel is ready.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            "android.intent.action.QUICKBOOT_POWERON",
            "com.htc.intent.action.QUICKBOOT_POWERON" -> {
                val host = context.getSharedPreferences("cfg", Context.MODE_PRIVATE)
                    .getString("host", "")?.trim().orEmpty()
                if (host.isEmpty()) { Log.i(PttKeyReceiver.TAG, "boot: no saved host, not starting"); return }
                Log.i(PttKeyReceiver.TAG, "boot: starting link to $host")
                try {
                    RadioService.send(context, RadioService.ACTION_START, host)
                } catch (e: Exception) {
                    Log.w(PttKeyReceiver.TAG, "boot: could not start service", e)
                }
            }
        }
    }
}
