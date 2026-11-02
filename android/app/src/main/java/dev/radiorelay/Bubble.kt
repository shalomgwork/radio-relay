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
import kotlin.math.hypot

/**
 * Floating pill over other apps while the link is running:
 *   [ PTT ] [ ⋮⋮ grip ]
 * PTT keys on touch-down, unkeys on release — no delay, and dragging can't key.
 * Grip drags the pill; while dragging, an ✕ target appears at the bottom — drop there to hide (onRemove).
 * A small light-blue dot on the PTT lights while a transmission is coming in (setRx).
 * Needs "Display over other apps" (Settings.ACTION_MANAGE_OVERLAY_PERMISSION).
 */
class Bubble(private val ctx: Context, private val onPtt: (Boolean) -> Unit, private val onRemove: () -> Unit) {
    private val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val root = LinearLayout(ctx)
    private val grip = TextView(ctx)
    private val ptt = FrameLayout(ctx)
    private val label = TextView(ctx)
    private val rxDot = View(ctx)
    private val trash = TextView(ctx)
    private val lp = overlayParams().apply { gravity = Gravity.TOP or Gravity.START; x = dp(12); y = dp(220) }
    private val trashLp = overlayParams().apply { gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL; y = dp(48) }
    private var shown = false
    private var trashShown = false

    private fun overlayParams() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
        if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY else WindowManager.LayoutParams.TYPE_PHONE,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
        PixelFormat.TRANSLUCENT)

    @SuppressLint("ClickableViewAccessibility")
    private fun build() {
        val size = dp(128)
        root.orientation = LinearLayout.HORIZONTAL
        root.gravity = Gravity.CENTER_VERTICAL
        root.background = ctx.getDrawable(R.drawable.bubble_pill)
        root.setPadding(dp(8), dp(8), dp(8), dp(8))

        ptt.background = ctx.getDrawable(R.drawable.bubble_bg)
        label.text = "PTT"; label.setTextColor(0xFFFFFFFF.toInt()); label.textSize = 28f; label.setTypeface(null, Typeface.BOLD)
        ptt.addView(label, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        rxDot.background = ctx.getDrawable(R.drawable.rx_dot); rxDot.visibility = View.INVISIBLE
        ptt.addView(rxDot, FrameLayout.LayoutParams(dp(14), dp(14), Gravity.TOP or Gravity.START).apply { setMargins(dp(14), dp(14), 0, 0) })
        root.addView(ptt, LinearLayout.LayoutParams(size, size))

        grip.text = "⋮⋮"; grip.setTextColor(0xFFAAAAAA.toInt()); grip.textSize = 40f; grip.gravity = Gravity.CENTER
        grip.setTypeface(null, Typeface.BOLD)
        root.addView(grip, LinearLayout.LayoutParams(dp(96), size))

        trash.text = "✕"; trash.setTextColor(0xFFFFFFFF.toInt()); trash.textSize = 30f; trash.gravity = Gravity.CENTER
        trash.background = ctx.getDrawable(R.drawable.trash_bg)
        trash.layoutParams = FrameLayout.LayoutParams(dp(80), dp(80))

        // grip: drag only; drop on ✕ to hide
        grip.setOnTouchListener(object : View.OnTouchListener {
            var downX = 0f; var downY = 0f; var startX = 0; var startY = 0
            override fun onTouch(v: View, e: MotionEvent): Boolean {
                when (e.actionMasked) {
                    MotionEvent.ACTION_DOWN -> { downX = e.rawX; downY = e.rawY; startX = lp.x; startY = lp.y; showTrash(true) }
                    MotionEvent.ACTION_MOVE -> {
                        lp.x = (startX + e.rawX - downX).toInt(); lp.y = (startY + e.rawY - downY).toInt()
                        wm.updateViewLayout(root, lp)
                        trash.isActivated = overTrash(e.rawX, e.rawY)
                        trash.scaleX = if (trash.isActivated) 1.25f else 1f; trash.scaleY = trash.scaleX
                    }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        val drop = e.actionMasked == MotionEvent.ACTION_UP && overTrash(e.rawX, e.rawY)
                        showTrash(false)
                        if (drop) { lp.x = startX; lp.y = startY; onRemove() }
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

    private fun overTrash(x: Float, y: Float): Boolean {
        val m = ctx.resources.displayMetrics
        val cx = m.widthPixels / 2f
        val cy = m.heightPixels - trashLp.y - dp(40).toFloat()
        return hypot(x - cx, y - cy) < dp(110)
    }

    private fun showTrash(on: Boolean) {
        if (on && !trashShown) { trash.isActivated = false; trash.scaleX = 1f; trash.scaleY = 1f; wm.addView(trash, trashLp); trashShown = true }
        else if (!on && trashShown) { wm.removeView(trash); trashShown = false }
    }

    fun show() { if (!shown) { if (root.childCount == 0) build(); wm.addView(root, lp); shown = true } }
    fun hide() { showTrash(false); if (shown) { wm.removeView(root); shown = false } }
    fun setTx(on: Boolean) { label.text = if (on) "TX" else "PTT"; ptt.isPressed = on }
    fun setRx(on: Boolean) { rxDot.visibility = if (on) View.VISIBLE else View.INVISIBLE }

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), ctx.resources.displayMetrics).toInt()
}
