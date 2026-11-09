package dev.radiorelay

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.ListView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

/**
 * The server profiles. D-pad up/down moves through the list; OK opens a menu to use, edit or delete
 * the highlighted server. "+ Add" creates one. The active server has a green dot and is what the
 * home screen connects to.
 */
class ServersActivity : AppCompatActivity() {
    private lateinit var list: ListView
    private lateinit var empty: TextView
    private var items = listOf<Servers.Profile>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_servers)
        list = findViewById(R.id.list); empty = findViewById(R.id.empty)
        findViewById<View>(R.id.back).setOnClickListener { finish() }
        findViewById<View>(R.id.add).setOnClickListener { edit(-1) }
        list.adapter = adapter
        list.setOnItemClickListener { _, _, i, _ -> rowMenu(i) }
    }

    override fun onResume() { super.onResume(); refresh() }

    private fun refresh() {
        items = Servers.load(this)
        empty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
        adapter.notifyDataSetChanged()
    }

    private fun rowMenu(i: Int) {
        val p = items[i]
        AlertDialog.Builder(this)
            .setTitle(p.label)
            .setItems(arrayOf("Use this server", "Edit", "Delete")) { _, which ->
                when (which) {
                    0 -> use(i)
                    1 -> edit(i)
                    2 -> delete(i)
                }
            }.show()
    }

    private fun use(i: Int) {
        Servers.setActive(this, i)
        val host = items[i].host
        if (RadioService.running && host.isNotBlank()) {    // switch the live link to the new server
            RadioService.send(this, RadioService.ACTION_STOP)
            RadioService.send(this, RadioService.ACTION_START, host)
        }
        Toast.makeText(this, "Using ${items[i].label}", Toast.LENGTH_SHORT).show()
        refresh()
    }

    private fun delete(i: Int) {
        val list = Servers.load(this)
        if (i !in list.indices) return
        AlertDialog.Builder(this)
            .setMessage("Delete ${list[i].label}?")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Delete") { _, _ ->
                val active = Servers.activeIndex(this)
                list.removeAt(i)
                Servers.save(this, list)
                // keep the active pointer sensible
                val newActive = when {
                    list.isEmpty() -> 0
                    i < active -> active - 1
                    active >= list.size -> list.size - 1
                    else -> active
                }
                Servers.setActive(this, newActive)
                refresh()
            }.show()
    }

    private fun edit(i: Int) {
        startActivity(Intent(this, ServerEditActivity::class.java).putExtra("index", i))
    }

    private val adapter = object : BaseAdapter() {
        override fun getCount() = items.size
        override fun getItem(i: Int) = items[i]
        override fun getItemId(i: Int) = i.toLong()
        override fun getView(i: Int, convert: View?, parent: ViewGroup): View {
            val v = convert ?: LayoutInflater.from(parent.context).inflate(R.layout.row_server, parent, false)
            val p = items[i]
            v.findViewById<TextView>(R.id.name).text = p.label
            v.findViewById<TextView>(R.id.host).text = p.host
            v.findViewById<View>(R.id.activeDot).visibility =
                if (i == Servers.activeIndex(this@ServersActivity)) View.VISIBLE else View.INVISIBLE
            return v
        }
    }
}
