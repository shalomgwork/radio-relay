package dev.radiorelay

import android.app.DownloadManager
import android.media.AudioAttributes
import android.net.Uri
import android.os.Environment
import android.widget.Toast
import android.media.MediaPlayer
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.ListView
import android.widget.SeekBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** Recorded transmissions from the server, grouped into conversations (gap > GAP_S starts a new one).
 *  Tap one to play; playback continues to the next (newer) one until paused. ⤓ saves a copy to Downloads. */
class HistoryActivity : AppCompatActivity() {
    data class Rec(val name: String, val kind: String, val start: Long, val dur: Double)

    private var items = listOf<Rec>()          // newest first
    private var heads = mapOf<Int, String>()   // row index -> conversation header
    private val GAP_S = 15 * 60
    private var playing = -1
    private var player: MediaPlayer? = null
    private var prepared = false
    private lateinit var list: ListView
    private lateinit var empty: TextView
    private lateinit var bar: View
    private lateinit var nowText: TextView
    private lateinit var playBtn: TextView
    private lateinit var seek: SeekBar
    private val ui = Handler(Looper.getMainLooper())
    private val host by lazy { Servers.active(this)?.host ?: "" }
    private val base get() = "http://$host:${RadioService.PORT + 1}/recordings"
    private val timeFmt = SimpleDateFormat("h:mm a", Locale.getDefault())
    private val dayFmt = SimpleDateFormat("EEE, MMM d", Locale.getDefault())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_history)
        list = findViewById(R.id.list); empty = findViewById(R.id.empty); bar = findViewById(R.id.bar)
        nowText = findViewById(R.id.now); playBtn = findViewById(R.id.playBtn); seek = findViewById(R.id.seek)
        findViewById<View>(R.id.back).setOnClickListener { finish() }
        findViewById<View>(R.id.refresh).setOnClickListener { load() }
        list.adapter = adapter
        list.setOnItemClickListener { _, _, i, _ -> if (i == playing) togglePause() else play(i) }
        playBtn.setOnClickListener { togglePause() }
        findViewById<View>(R.id.saveBtn).setOnClickListener { items.getOrNull(playing)?.let { download(it) } }
        seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar, p: Int, fromUser: Boolean) { if (fromUser && prepared) player?.seekTo(p) }
            override fun onStartTrackingTouch(sb: SeekBar) {}
            override fun onStopTrackingTouch(sb: SeekBar) {}
        })
        load()
    }

    private fun load() {
        empty.text = "Loading…"; empty.visibility = View.VISIBLE
        Thread {
            val result = try {
                val c = URL(base).openConnection() as HttpURLConnection
                c.connectTimeout = 4000; c.readTimeout = 4000
                val arr = JSONArray(c.inputStream.bufferedReader().readText())
                (0 until arr.length()).map { arr.getJSONObject(it) }
                    .map { Rec(it.getString("name"), it.getString("kind"), it.getLong("start"), it.getDouble("dur")) }
            } catch (e: Exception) { null }
            runOnUiThread {
                if (result == null) { empty.text = "Can't reach server at $host"; return@runOnUiThread }
                items = result; heads = group(result)
                empty.text = "No recordings yet"
                empty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
                adapter.notifyDataSetChanged()
            }
        }.start()
    }

    /** Conversation header for the first (newest) row of each group: "Today · 3:42 PM – 3:58 PM". */
    private fun group(list: List<Rec>): Map<Int, String> {
        val h = HashMap<Int, String>()
        var i = 0
        while (i < list.size) {
            var j = i
            while (j + 1 < list.size && list[j].start - list[j + 1].start <= GAP_S) j++
            val from = timeFmt.format(Date(list[j].start * 1000)); val to = timeFmt.format(Date(list[i].start * 1000))
            h[i] = dayOf(list[i].start) + " · " + if (i == j) to else "$from – $to"
            i = j + 1
        }
        return h
    }

    private fun download(r: Rec) {
        val req = DownloadManager.Request(Uri.parse("$base/${r.name}"))
            .setTitle(r.name).setMimeType("audio/wav")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "RadioRelay/${r.name}")
        getSystemService(DownloadManager::class.java).enqueue(req)
        Toast.makeText(this, "Saving to Downloads/RadioRelay", Toast.LENGTH_SHORT).show()
    }

    private fun play(i: Int) {
        release()
        playing = i; prepared = false
        val r = items[i]
        nowText.text = label(r); playBtn.text = "❚❚"; seek.progress = 0; seek.max = (r.dur * 1000).toInt()
        bar.visibility = View.VISIBLE
        adapter.notifyDataSetChanged()
        player = MediaPlayer().apply {
            setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            setDataSource("$base/${r.name}")
            setOnPreparedListener { prepared = true; seek.max = duration; it.start(); tick() }
            setOnCompletionListener { if (playing > 0) play(playing - 1) else stop() }   // next = the newer one
            setOnErrorListener { _, _, _ -> stop(); true }
            prepareAsync()
        }
    }

    private fun togglePause() {
        val p = player ?: return
        if (!prepared) return
        if (p.isPlaying) { p.pause(); playBtn.text = "▶" } else { p.start(); playBtn.text = "❚❚"; tick() }
        adapter.notifyDataSetChanged()
    }

    private fun stop() {
        release(); playing = -1
        bar.visibility = View.GONE
        adapter.notifyDataSetChanged()
    }

    private fun release() {
        ui.removeCallbacksAndMessages(null)
        player?.let { try { it.stop() } catch (e: Exception) {}; it.release() }
        player = null; prepared = false
    }

    private fun tick() {
        val p = player ?: return
        if (prepared && p.isPlaying) { seek.progress = p.currentPosition; ui.postDelayed({ tick() }, 250) }
    }

    private fun dayOf(t: Long): String {
        val d = Calendar.getInstance().apply { time = Date(t * 1000) }
        val now = Calendar.getInstance()
        val y = now.get(Calendar.YEAR) == d.get(Calendar.YEAR)
        return when {
            y && now.get(Calendar.DAY_OF_YEAR) == d.get(Calendar.DAY_OF_YEAR) -> "Today"
            y && now.get(Calendar.DAY_OF_YEAR) - d.get(Calendar.DAY_OF_YEAR) == 1 -> "Yesterday"
            else -> dayFmt.format(d.time)
        }
    }

    private fun label(r: Rec) = (if (r.kind == "tx") "You" else "Radio") + " · " + dayOf(r.start) + " " + timeFmt.format(Date(r.start * 1000))

    private val adapter = object : BaseAdapter() {
        override fun getCount() = items.size
        override fun getItem(i: Int) = items[i]
        override fun getItemId(i: Int) = i.toLong()
        override fun getView(i: Int, convert: View?, parent: ViewGroup): View {
            val v = convert ?: LayoutInflater.from(parent.context).inflate(R.layout.row_rec, parent, false)
            val r = items[i]
            val tx = r.kind == "tx"
            v.findViewById<TextView>(R.id.kind).apply { text = if (tx) "YOU" else "RX"; setTextColor(if (tx) 0xFFE53935.toInt() else 0xFF43A047.toInt()) }
            v.findViewById<TextView>(R.id.time).text = timeFmt.format(Date(r.start * 1000))
            v.findViewById<TextView>(R.id.day).apply { text = heads[i] ?: ""; visibility = if (heads.containsKey(i)) View.VISIBLE else View.GONE }
            v.findViewById<TextView>(R.id.dur).text = "${r.dur.toInt()}s"
            val isPlaying = i == playing
            v.findViewById<TextView>(R.id.state).text = if (isPlaying && player?.isPlaying == true) "❚❚" else if (isPlaying) "▶" else ""
            v.alpha = if (isPlaying) 1f else 0.85f
            v.setBackgroundColor(if (isPlaying) 0xFF26262A.toInt() else 0)
            return v
        }
    }

    override fun onDestroy() { release(); super.onDestroy() }
}
