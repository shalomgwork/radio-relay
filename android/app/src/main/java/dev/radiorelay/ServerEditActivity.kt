package dev.radiorelay

import android.os.Bundle
import android.view.View
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

/** Add or edit one server profile. index == -1 means a new one. */
class ServerEditActivity : AppCompatActivity() {
    private var index = -1
    private lateinit var name: EditText
    private lateinit var host: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_server_edit)
        index = intent.getIntExtra("index", -1)
        name = findViewById(R.id.name); host = findViewById(R.id.host)
        val delete = findViewById<TextView>(R.id.delete)

        val list = Servers.load(this)
        if (index in list.indices) {
            findViewById<TextView>(R.id.title).text = "Edit server"
            name.setText(list[index].name); host.setText(list[index].host)
            delete.visibility = View.VISIBLE
            delete.setOnClickListener { confirmDelete() }
        }

        findViewById<View>(R.id.back).setOnClickListener { finish() }
        findViewById<View>(R.id.save).setOnClickListener { save() }
        (if (index == -1) host else name).requestFocus()
    }

    private fun save() {
        val h = host.text.toString().trim()
        if (h.isEmpty()) { Toast.makeText(this, "Enter a server address", Toast.LENGTH_SHORT).show(); return }
        val profile = Servers.Profile(name.text.toString().trim(), h)
        val list = Servers.load(this)
        if (index in list.indices) list[index] = profile
        else { list.add(profile); if (list.size == 1) Servers.setActive(this, 0) }
        Servers.save(this, list)
        finish()
    }

    private fun confirmDelete() {
        AlertDialog.Builder(this)
            .setMessage("Delete this server?")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Delete") { _, _ ->
                val list = Servers.load(this)
                if (index in list.indices) {
                    val active = Servers.activeIndex(this)
                    list.removeAt(index)
                    Servers.save(this, list)
                    Servers.setActive(this, when {
                        list.isEmpty() -> 0
                        index < active -> active - 1
                        active >= list.size -> list.size - 1
                        else -> active
                    })
                }
                finish()
            }.show()
    }
}
