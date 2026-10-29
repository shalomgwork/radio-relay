package dev.radiorelay

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.*
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import io.github.jaredmdobson.concentus.OpusApplication
import io.github.jaredmdobson.concentus.OpusDecoder
import io.github.jaredmdobson.concentus.OpusEncoder
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * Foreground service: keeps the UDP link up, plays radio RX out loud, streams mic while PTT held.
 * Protocol: byte0 = 0x00 keepalive | 0x01 opus frame | 0x02 ptt on | 0x03 ptt off
 */
class RadioService : Service() {
    companion object {
        const val ACTION_START = "start"; const val ACTION_STOP = "stop"
        const val ACTION_TOGGLE_MUTE = "toggle_mute"
        const val ACTION_PTT_DOWN = "ptt_down"; const val ACTION_PTT_UP = "ptt_up"
        const val EXTRA_HOST = "host"
        const val RATE = 48000; const val FRAME = 960; const val PORT = 5000
        private const val CH_ID = "radio"; private const val NOTIF_ID = 1

        @Volatile var running = false
        @Volatile var muted = false
        @Volatile var connected = false
        @Volatile var ptt = false
        var onState: (() -> Unit)? = null

        fun send(ctx: Context, action: String, host: String? = null) {
            val i = Intent(ctx, RadioService::class.java).setAction(action)
            if (host != null) i.putExtra(EXTRA_HOST, host)
            if (action == ACTION_START) ctx.startForegroundService(i) else ctx.startService(i)
        }
    }

    private var socket: DatagramSocket? = null
    private var target: InetSocketAddress? = null
    private val alive = AtomicBoolean(false)
    private var lastRx = 0L
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> start(intent.getStringExtra(EXTRA_HOST) ?: return START_NOT_STICKY)
            ACTION_STOP -> stop()
            ACTION_TOGGLE_MUTE -> { muted = !muted; updateNotification(); onState?.invoke() }
            ACTION_PTT_DOWN -> setPtt(true)
            ACTION_PTT_UP -> setPtt(false)
        }
        return START_STICKY
    }

    private fun start(host: String) {
        if (running) return
        running = true
        createChannel()
        val notif = buildNotification()
        if (Build.VERSION.SDK_INT >= 29)
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        else startForeground(NOTIF_ID, notif)

        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "radiorelay:link").apply { acquire() }

        socket = DatagramSocket().apply { soTimeout = 1000 }
        target = InetSocketAddress(host, PORT)
        alive.set(true)
        thread(name = "rx") { rxLoop() }
        thread(name = "tx") { txLoop() }
        thread(name = "keepalive") { keepaliveLoop() }
        onState?.invoke()
    }

    private fun stop() {
        alive.set(false); running = false; connected = false; ptt = false
        socket?.close(); socket = null
        wakeLock?.let { if (it.isHeld) it.release() }
        onState?.invoke()
        stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
    }

    private fun setPtt(on: Boolean) {
        if (!running || ptt == on) return
        ptt = on
        sendRaw(if (on) 0x02 else 0x03)
        updateNotification(); onState?.invoke()
    }

    private fun sendRaw(type: Int, payload: ByteArray? = null, len: Int = 0) {
        val s = socket ?: return; val t = target ?: return
        val buf = ByteArray(1 + len); buf[0] = type.toByte()
        if (payload != null) System.arraycopy(payload, 0, buf, 1, len)
        try { s.send(DatagramPacket(buf, buf.size, t)) } catch (e: Exception) { Log.w("radio", "send: $e") }
    }

    // ---- keepalive / link state ----
    private fun keepaliveLoop() {
        while (alive.get()) {
            sendRaw(0x00)
            val wasConnected = connected
            connected = System.currentTimeMillis() - lastRx < 6000
            if (wasConnected != connected) { updateNotification(); onState?.invoke() }
            Thread.sleep(2000)
        }
    }

    // ---- server -> speaker ----
    private fun rxLoop() {
        val dec = OpusDecoder(RATE, 1)
        val pcm = ShortArray(FRAME)
        val minBuf = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(AudioFormat.Builder().setSampleRate(RATE).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setBufferSizeInBytes(maxOf(minBuf, FRAME * 2 * 4))
            .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
            .build()
        track.play()
        val buf = ByteArray(2048)
        val pkt = DatagramPacket(buf, buf.size)
        while (alive.get()) {
            val s = socket ?: break
            try { s.receive(pkt) } catch (e: Exception) { continue }
            lastRx = System.currentTimeMillis()
            if (pkt.length < 1) continue
            when (buf[0].toInt()) {
                0x01 -> if (!muted && !ptt) {
                    try {
                        val n = dec.decode(buf, 1, pkt.length - 1, pcm, 0, FRAME, false)
                        track.write(pcm, 0, n)
                    } catch (e: Exception) { Log.w("radio", "decode: $e") }
                }
            }
        }
        track.stop(); track.release()
    }

    // ---- mic -> server (only while PTT) ----
    private fun txLoop() {
        val enc = OpusEncoder(RATE, 1, OpusApplication.OPUS_APPLICATION_VOIP).apply { setBitrate(24000) }
        val minBuf = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuf, FRAME * 2 * 4))
        } catch (e: SecurityException) { Log.e("radio", "no mic permission"); return }
        val pcm = ShortArray(FRAME)
        val out = ByteArray(1024)
        var recording = false
        while (alive.get()) {
            if (ptt) {
                if (!recording) { rec.startRecording(); recording = true }
                val n = rec.read(pcm, 0, FRAME)
                if (n == FRAME) {
                    val len = enc.encode(pcm, 0, FRAME, out, 0, out.size)
                    sendRaw(0x01, out, len)
                }
            } else {
                if (recording) { rec.stop(); recording = false }
                Thread.sleep(20)
            }
        }
        if (recording) rec.stop()
        rec.release()
    }

    // ---- notification ----
    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_ID, "Radio link", NotificationManager.IMPORTANCE_LOW))
    }

    private fun pi(action: String) = PendingIntent.getService(this, action.hashCode(),
        Intent(this, RadioService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun buildNotification(): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val text = when { ptt -> "TRANSMITTING"; !connected -> "Connecting…"; muted -> "Connected · muted"; else -> "Connected · listening" }
        return Notification.Builder(this, CH_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Radio Relay").setContentText(text)
            .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
            .addAction(Notification.Action.Builder(null, if (muted) "Unmute" else "Mute", pi(ACTION_TOGGLE_MUTE)).build())
            .addAction(Notification.Action.Builder(null, "Disconnect", pi(ACTION_STOP)).build())
            .build()
    }

    private fun updateNotification() {
        if (running) getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification())
    }
}
