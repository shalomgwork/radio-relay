package dev.radiorelay

import android.accessibilityservice.AccessibilityService
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent

/**
 * Global Volume-Down = PTT, from any screen, while the radio link is running.
 * Enable once under Settings > Accessibility > Radio Relay. Sideloaded app, so no Play policy issues.
 * When the link is not running, the key behaves normally.
 */
class PttKeyService : AccessibilityService() {
    override fun onKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode != KeyEvent.KEYCODE_VOLUME_DOWN || !RadioService.running) return false
        when (event.action) {
            KeyEvent.ACTION_DOWN -> if (event.repeatCount == 0) RadioService.send(this, RadioService.ACTION_PTT_DOWN)
            KeyEvent.ACTION_UP -> RadioService.send(this, RadioService.ACTION_PTT_UP)
        }
        return true // consume: no volume change
    }
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}
}
