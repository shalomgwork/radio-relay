package dev.radiorelay

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * Saved server profiles, stored in the "cfg" prefs as a JSON array under "servers", with the active
 * one's index under "active". One profile is the active server; connecting and History both use it.
 */
object Servers {
    data class Profile(val name: String, val host: String) {
        val label get() = if (name.isNotBlank()) name else host
    }

    private const val KEY = "servers"
    private const val ACTIVE = "active"
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)

    /** First run after the single-host version: fold the old "host" value into one profile. */
    fun migrate(ctx: Context) {
        val p = prefs(ctx)
        if (p.contains(KEY)) return
        val legacy = p.getString("host", "")?.trim().orEmpty()
        val arr = JSONArray()
        if (legacy.isNotEmpty()) arr.put(JSONObject().put("name", "Default").put("host", legacy))
        p.edit().putString(KEY, arr.toString()).putInt(ACTIVE, 0).apply()
    }

    fun load(ctx: Context): MutableList<Profile> {
        val arr = JSONArray(prefs(ctx).getString(KEY, "[]"))
        return (0 until arr.length()).map {
            val o = arr.getJSONObject(it); Profile(o.optString("name"), o.optString("host"))
        }.toMutableList()
    }

    fun save(ctx: Context, list: List<Profile>) {
        val arr = JSONArray()
        list.forEach { arr.put(JSONObject().put("name", it.name).put("host", it.host)) }
        prefs(ctx).edit().putString(KEY, arr.toString()).apply()
    }

    fun activeIndex(ctx: Context) = prefs(ctx).getInt(ACTIVE, 0).coerceAtLeast(0)

    fun setActive(ctx: Context, i: Int) { prefs(ctx).edit().putInt(ACTIVE, i).apply() }

    fun active(ctx: Context): Profile? = load(ctx).getOrNull(activeIndex(ctx))
}
