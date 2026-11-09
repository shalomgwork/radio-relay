package dev.radiorelay

import android.accessibilityservice.AccessibilityService
import android.util.Log
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent

/**
 * Captures the Sonim hardware PTT key the only way that works on this locked firmware.
 *
 * The phone's PhoneWindowManager consumes KEYCODE_PTT before it reaches any app, and only forwards
 * it (as a broadcast) to apps Sonim has cryptographically registered — which we can't be. But an
 * accessibility key-event filter sits earlier in the input pipeline (at InputDispatcher, before the
 * window-policy interception that eats the key), so onKeyEvent() still sees the press.
 *
 * The user must enable this under Settings > Accessibility > Radio Relay. We never consume the event
 * (always return false) so the phone's own handling is unaffected.
 */
class PttAccessibilityService : AccessibilityService() {

    override fun onKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode !in PttKeyReceiver.PTT_KEYCODES) return false
        Log.i(PttKeyReceiver.TAG, "a11y key action=${event.action} code=${event.keyCode} repeat=${event.repeatCount}")
        // Only drive the radio while the link is up; otherwise the key does nothing.
        if (RadioService.running) {
            if (event.repeatCount == 0) when (event.action) {
                KeyEvent.ACTION_DOWN -> RadioService.send(this, RadioService.ACTION_PTT_DOWN)
                KeyEvent.ACTION_UP -> RadioService.send(this, RadioService.ACTION_PTT_UP)
            }
        }
        return false // observe only; let the system keep doing whatever it does with the key
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}
}
