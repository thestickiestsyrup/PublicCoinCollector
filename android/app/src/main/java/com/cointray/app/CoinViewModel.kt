package com.cointray.app

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.min

class CoinViewModel(app: Application) : AndroidViewModel(app) {
    val prefs = Prefs(app)
    private val api = Api(prefs)

    var host by mutableStateOf(prefs.host)
    var token by mutableStateOf(prefs.token)
    var idMode by mutableStateOf(prefs.idMode)
    var status by mutableStateOf(NetHints.pairingHelp())
    var busy by mutableStateOf(false)
    var ollamaOk by mutableStateOf(false)
    var idModel by mutableStateOf("")

    var obverseJpeg by mutableStateOf<ByteArray?>(null)
    var reverseJpeg by mutableStateOf<ByteArray?>(null)

    var year by mutableStateOf("")
    var mint by mutableStateOf("")
    var country by mutableStateOf("")
    var denom by mutableStateOf("")
    var series by mutableStateOf("")
    var grade by mutableStateOf("")
    var catalogName by mutableStateOf("")
    var disposition by mutableStateOf("Keep")

    var result by mutableStateOf<JSONObject?>(null)
    var tray by mutableStateOf(JSONArray())
    var captureSide by mutableStateOf("obverse")
    var lastCapturedSide by mutableStateOf("")
    var flashGreen by mutableStateOf(false)

    init {
        try {
            tray = JSONArray(prefs.trayJson())
        } catch (_: Exception) {
            tray = JSONArray()
        }
        if (host.isBlank()) host = NetHints.suggestedHost()
    }

    fun saveSettings() {
        prefs.host = host
        prefs.token = token
        prefs.idMode = idMode
        status = "Saved. " + NetHints.pairingHelp()
    }

    fun setPhoto(side: String, jpeg: ByteArray) {
        if (side == "reverse") reverseJpeg = jpeg else obverseJpeg = jpeg
        lastCapturedSide = side
        flashGreen = true
        val label = if (side == "reverse") "Back" else "Front"
        status = "$label captured — check the thumbnail. Tap it to retake."
    }

    fun clearFlash() {
        flashGreen = false
    }

    fun ping() = work("Pinging PC…") {
        requireHost()
        if (idMode != "companion") {
            status = "On-device ID is not installed yet — switch to Companion (PC)."
            return@work
        }
        val h = api.health()
        ollamaOk = h.optBoolean("ollama", false)
        idModel = h.optString("idModel")
        val detail = h.optString("detail")
        status = if (ollamaOk) {
            "PC reachable. Ollama model: ${idModel.ifBlank { "ready" }}"
        } else {
            "PC reachable but Ollama is down. $detail"
        }
        pullTraySilent()
    }

    fun readCoin() = work("Reading on PC (Ollama)…") {
        requireHost()
        requireCompanion()
        val body = JSONObject()
        val obv = obverseJpeg
        val rev = reverseJpeg
        if (obv != null) body.put("obverse", b64Jpeg(obv))
        if (rev != null) body.put("reverse", b64Jpeg(rev))
        if (!body.has("obverse") && !body.has("reverse")) {
            status = "Capture a photo first."
            return@work
        }
        val out = api.post("/api/read", body)
        if (out.has("error") && out.optString("error").isNotBlank()) {
            status = out.optString("error")
            return@work
        }
        val guess = out.optJSONObject("guess") ?: JSONObject()
        if (country.isBlank()) country = guess.optString("country")
        if (denom.isBlank()) denom = guess.optString("denomination")
        if (series.isBlank()) series = guess.optString("series")
        val sides = out.optJSONObject("sides") ?: JSONObject()
        val o = sides.optJSONObject("obverse") ?: JSONObject()
        val v = sides.optJSONObject("reverse") ?: JSONObject()
        if (year.isBlank()) year = pickField(o.optString("date"), v.optString("date"))
        if (mint.isBlank()) mint = pickField(o.optString("mintMark"), v.optString("mintMark"))
        if (grade.isBlank()) grade = pickField(o.optString("wear"), v.optString("wear"))
        refreshCatalog(announce = false)
        status = "Read done. $catalogName. Fix DATE if needed, then Price it."
    }

    fun priceCoin() = work("Pricing on PC…") {
        requireHost()
        requireCompanion()
        val out = api.post("/api/price", priceBody())
        applyPrice(out, "Priced.")
    }

    fun faceSkip() = work("Face skip…") {
        requireHost()
        requireCompanion()
        val out = api.post("/api/face-skip", JSONObject().put("attribution", attribution()))
        applyPrice(out, "Bagged as face / common (no web search).")
        disposition = "Face"
    }

    fun priceAndNext() = work("Pricing on PC…") {
        requireHost()
        requireCompanion()
        val out = api.post("/api/price", priceBody())
        if (applyPrice(out, "Priced.")) nextCoin()
    }

    fun deleteTrayAt(index: Int) {
        if (index < 0 || index >= tray.length()) return
        val next = JSONArray()
        for (i in 0 until tray.length()) {
            if (i != index) next.put(tray.getJSONObject(i))
        }
        tray = next
        prefs.saveTrayJson(next.toString())
        status = "Removed from tray. Push to PC to sync."
    }

    fun pullTray() = work("Pulling tray from PC…") {
        requireHost()
        requireCompanion()
        pullTraySilent()
        status = "Tray: ${tray.length()} coin(s) from PC."
    }

    fun pushTray() = work("Pushing tray to PC…") {
        requireHost()
        requireCompanion()
        api.putTray(tray)
        prefs.saveTrayJson(tray.toString())
        status = "Pushed ${tray.length()} coin(s) to PC."
    }

    fun refreshCatalog(tierHint: String? = null, specs: JSONObject? = null, announce: Boolean = true) {
        catalogName = Catalog.build(
            country = country,
            year = year,
            mint = mint,
            series = series,
            denom = denom,
            grade = grade,
            current = catalogName,
            tray = tray,
            tierHint = tierHint,
            specs = specs ?: result?.optJSONObject("specs"),
        )
        if (announce) status = "Catalog: $catalogName"
    }

    fun nextCoin() {
        obverseJpeg = null
        reverseJpeg = null
        year = ""; mint = ""; country = ""; denom = ""; series = ""; grade = ""
        catalogName = ""
        result = null
        disposition = "Keep"
        captureSide = "obverse"
        lastCapturedSide = ""
        flashGreen = false
        status = "Ready for the next coin."
    }

    private fun applyPrice(out: JSONObject, okMsg: String): Boolean {
        if (out.has("error") && out.optString("error").isNotBlank()) {
            status = out.optString("error")
            return false
        }
        val res = out.optJSONObject("result") ?: out
        result = res
        val entry = JSONObject(res.toString())
        entry.put("id", System.currentTimeMillis())
        entry.put("year", year)
        entry.put("mintMark", mint)
        entry.put("country", country)
        entry.put("denomination", denom)
        entry.put("series", series)
        entry.put("grade", grade.ifBlank { res.optString("grade") })
        val tier = res.optString("tier")
        refreshCatalog(tier.ifBlank { null }, res.optJSONObject("specs"), announce = false)
        res.put("catalogName", catalogName)
        result = res
        entry.put("disposition", disposition)
        entry.put("catalogName", catalogName.ifBlank { res.optString("identification") })
        if (tier.isNotBlank()) entry.put("tier", tier)
        val next = JSONArray()
        next.put(entry)
        for (i in 0 until tray.length()) next.put(tray.getJSONObject(i))
        tray = next
        prefs.saveTrayJson(tray.toString())
        status = okMsg + " " + entry.optString("catalogName")
        return true
    }

    private fun pullTraySilent() {
        val t = api.get("/api/tray").optJSONArray("tray") ?: JSONArray()
        tray = t
        prefs.saveTrayJson(t.toString())
    }

    private fun attribution(): JSONObject = JSONObject()
        .put("year", year)
        .put("mintMark", mint)
        .put("country", country)
        .put("denomination", denom)
        .put("series", series)
        .put("grade", grade)

    private fun priceBody(): JSONObject {
        val b = JSONObject().put("attribution", attribution())
        val obv = obverseJpeg
        val rev = reverseJpeg
        if (obv != null) b.put("obverse", b64Jpeg(obv))
        if (rev != null) b.put("reverse", b64Jpeg(rev))
        return b
    }

    private fun b64Jpeg(jpeg: ByteArray): String =
        Base64.encodeToString(jpeg, Base64.NO_WRAP)

    private fun pickField(a: String, b: String): String {
        val bad = setOf("", "unclear", "none", "n/a")
        if (a.lowercase() !in bad) return a
        if (b.lowercase() !in bad) return b
        return ""
    }

    private fun requireHost() {
        if (prefs.host.isBlank()) {
            throw IllegalStateException(NetHints.pairingHelp())
        }
    }

    private fun requireCompanion() {
        if (idMode != "companion") {
            throw IllegalStateException("On-device ID is not ready. Use Companion (PC) in Settings.")
        }
    }

    private fun work(msg: String, block: () -> Unit) {
        if (busy) return
        busy = true
        status = msg
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { block() }
            } catch (e: Exception) {
                status = NetHints.friendlyError(e)
            } finally {
                busy = false
            }
        }
    }

    companion object {
        /** Overlay radius as a fraction of the preview’s shorter side (desktop GUIDE_FRAC/2). */
        const val GUIDE_RADIUS_FRAC = 0.20f
        const val GUIDE_PAD = 0.12f

        fun jpegFromFile(path: String, viewW: Int = 0, viewH: Int = 0): ByteArray {
            val bmp = BitmapFactory.decodeFile(path) ?: error("Could not decode photo")
            return toJpeg(cropToGuide(bmp, viewW, viewH))
        }

        /**
         * Square crop around the on-screen guide circle, matching desktop crop_roi.
         * [viewW]/[viewH] are the PreviewView size (FILL_CENTER).
         */
        fun cropToGuide(bmp: Bitmap, viewW: Int, viewH: Int): Bitmap {
            val bw = bmp.width
            val bh = bmp.height
            val vw = viewW.coerceAtLeast(1).toFloat()
            val vh = viewH.coerceAtLeast(1).toFloat()
            val useView = viewW > 8 && viewH > 8
            val visW: Int
            val visH: Int
            val ox: Int
            val oy: Int
            val r: Float
            if (useView) {
                val s = max(vw / bw, vh / bh)
                visW = (vw / s).toInt().coerceIn(1, bw)
                visH = (vh / s).toInt().coerceIn(1, bh)
                ox = (bw - visW) / 2
                oy = (bh - visH) / 2
                r = GUIDE_RADIUS_FRAC * min(vw, vh) / s
            } else {
                visW = bw
                visH = bh
                ox = 0
                oy = 0
                r = min(bw, bh) * GUIDE_RADIUS_FRAC
            }
            val half = (r * (1f + GUIDE_PAD)).toInt().coerceAtLeast(32)
            val cx = ox + visW / 2
            val cy = oy + visH / 2
            val x0 = (cx - half).coerceAtLeast(0)
            val y0 = (cy - half).coerceAtLeast(0)
            val x1 = (cx + half).coerceAtMost(bw)
            val y1 = (cy + half).coerceAtMost(bh)
            val side = min(x1 - x0, y1 - y0).coerceAtLeast(32)
            val cropped = Bitmap.createBitmap(bmp, x0, y0, side, side)
            val maxPx = 1280
            if (side <= maxPx) return cropped
            return Bitmap.createScaledBitmap(cropped, maxPx, maxPx, true)
        }

        fun jpegToBitmap(jpeg: ByteArray?): Bitmap? {
            if (jpeg == null || jpeg.isEmpty()) return null
            return BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)
        }

        fun toJpeg(bmp: Bitmap, quality: Int = 86): ByteArray {
            val out = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, quality, out)
            return out.toByteArray()
        }
    }
}
