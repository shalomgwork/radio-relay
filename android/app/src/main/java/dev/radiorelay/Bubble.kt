package dev.radiorelay

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.os.Build
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.TextView
import kotlin.math.abs

/**
 * Floating hold-to-talk bubble shown over other apps while the link is running.
 * Short press-and-hold = PTT; drag moves it. Needs "Display over other apps" (Settings.ACTION_MANAGE_OVERLAY_PERMISSION).
 */
class Bubble(private val ctx: Context, private val onPtt: (Boolean) -> Unit) {
    private val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val view: FrameLayout = FrameLayout(ctx)
    private val label = TextView(ctx)
    private val lp = WindowManager.LayoutParams(
        dp(72), dp(72),
        if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY else WindowManager.LayoutParams.TYPE_PHONE,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.TRANSLUCENT
    ).apply { gravity = Gravity.TOP or Gravity.START; x = dp(16); y = dp(200) }
    private var shown = false

    init {
        view.background = ctx.getDrawable(R.drawable.bubble_bg)
        label.text = "PTT"; label.setTextColor(0xFFFFFFFF.toInt()); label.textSize = 15f
        label.setTypeface(null, android.graphics.Typeface.BOLD)
        view.addView(label, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        view.setOnTouchListener(touch())
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun touch() = object : View.OnTouchListener {
        val slop = ViewConfiguration.get(ctx).scaledTouchSlop
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0
        var dragging = false; var keyed = false
        override fun onTouch(v: View, e: MotionEvent): Boolean {
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX; downY = e.rawY; startX = lp.x; startY = lp.y; dragging = false
                    keyed = true; v.isPressed = true; label.text = "TX"; onPtt(true)   // key immediately: hold = talk
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX; val dy = e.rawY - downY
                    if (!dragging && (abs(dx) > slop * 3 || abs(dy) > slop * 3)) {
                        dragging = true
                        if (keyed) { keyed = false; v.isPressed = false; label.text = "PTT"; onPtt(false) }  // a drag is not a transmission
                    }
                    if (dragging) { lp.x = (startX + dx).toInt(); lp.y = (startY + dy).toInt(); wm.updateViewLayout(view, lp) }
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (keyed) { keyed = false; onPtt(false) }
                    v.isPressed = false; label.text = "PTT"
                }
            }
            return true
        }
    }

    fun show() { if (!shown) { wm.addView(view, lp); shown = true } }
    fun hide() { if (shown) { wm.removeView(view); shown = false } }
    fun setTx(on: Boolean) { label.text = if (on) "TX" else "PTT" }

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), ctx.resources.displayMetrics).toInt()
}
