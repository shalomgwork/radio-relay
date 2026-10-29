package dev.radiorelay

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.os.Build
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Floating pill over other apps while the link is running:
 *   [ ⋮⋮ grip ] [ PTT ]
 * Grip drags the pill. PTT keys on touch-down, unkeys on release — no delay, and dragging can't key.
 * Needs "Display over other apps" (Settings.ACTION_MANAGE_OVERLAY_PERMISSION).
 */
class Bubble(private val ctx: Context, private val onPtt: (Boolean) -> Unit) {
    private val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val root = LinearLayout(ctx)
    private val grip = TextView(ctx)
    private val ptt = FrameLayout(ctx)
    private val label = TextView(ctx)
    private val lp = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
        if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY else WindowManager.LayoutParams.TYPE_PHONE,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.TRANSLUCENT
    ).apply { gravity = Gravity.TOP or Gravity.START; x = dp(12); y = dp(220) }
    private var shown = false

    @SuppressLint("ClickableViewAccessibility")
    private fun build() {
        root.orientation = LinearLayout.HORIZONTAL
        root.gravity = Gravity.CENTER_VERTICAL
        root.background = ctx.getDrawable(R.drawable.bubble_pill)
        root.setPadding(dp(6), dp(6), dp(6), dp(6))

        grip.text = "⋮⋮"; grip.setTextColor(0xFFAAAAAA.toInt()); grip.textSize = 20f; grip.gravity = Gravity.CENTER
        grip.setTypeface(null, Typeface.BOLD)
        root.addView(grip, LinearLayout.LayoutParams(dp(40), dp(64)))

        ptt.background = ctx.getDrawable(R.drawable.bubble_bg)
        label.text = "PTT"; label.setTextColor(0xFFFFFFFF.toInt()); label.textSize = 15f; label.setTypeface(null, Typeface.BOLD)
        ptt.addView(label, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        root.addView(ptt, LinearLayout.LayoutParams(dp(64), dp(64)))

        // grip: drag only
        grip.setOnTouchListener(object : View.OnTouchListener {
            var downX = 0f; var downY = 0f; var startX = 0; var startY = 0
            override fun onTouch(v: View, e: MotionEvent): Boolean {
                when (e.actionMasked) {
                    MotionEvent.ACTION_DOWN -> { downX = e.rawX; downY = e.rawY; startX = lp.x; startY = lp.y }
                    MotionEvent.ACTION_MOVE -> {
                        lp.x = (startX + e.rawX - downX).toInt(); lp.y = (startY + e.rawY - downY).toInt()
                        wm.updateViewLayout(root, lp)
                    }
                }
                return true
            }
        })

        // ptt: key on touch, release on lift — immediate
        ptt.setOnTouchListener { v, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> { v.isPressed = true; label.text = "TX"; onPtt(true) }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { v.isPressed = false; label.text = "PTT"; onPtt(false) }
            }
            true
        }
    }

    fun show() { if (!shown) { if (root.childCount == 0) build(); wm.addView(root, lp); shown = true } }
    fun hide() { if (shown) { wm.removeView(root); shown = false } }
    fun setTx(on: Boolean) { label.text = if (on) "TX" else "PTT"; ptt.isPressed = on }

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), ctx.resources.displayMetrics).toInt()
}
