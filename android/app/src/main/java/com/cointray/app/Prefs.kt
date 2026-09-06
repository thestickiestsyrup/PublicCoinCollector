package com.cointray.app

import android.content.Context
import android.os.Build

object NetHints {
    fun isEmulator(): Boolean {
        val fp = Build.FINGERPRINT.lowercase()
        val model = Build.MODEL.lowercase()
        val product = Build.PRODUCT.lowercase()
        val hw = Build.HARDWARE.lowercase()
        return fp.contains("generic") || fp.contains("emulator") ||
            model.contains("sdk") || model.contains("emulator") ||
            product.contains("sdk") || product.contains("emulator") ||
            hw.contains("goldfish") || hw.contains("ranchu") ||
            Build.DEVICE.lowercase().contains("generic") ||
            Build.BRAND.lowercase().startsWith("generic")
    }

    fun suggestedHost(): String =
        if (isEmulator()) "http://10.0.2.2:8722" else ""

    fun pairingHelp(): String = if (isEmulator()) {
        "Emulator: use http://10.0.2.2:8722 (that is this PC). Start python coin_tray.py, paste the Token, then Save & ping."
    } else {
        "Phone: same Wi‑Fi as the PC. Run python coin_tray.py, paste the printed Phone URL and Token, then Save & ping."
    }

    fun friendlyError(e: Throwable): String {
        val m = (e.message ?: e.javaClass.simpleName).lowercase()
        val timeout = "timed out" in m || "timeout" in m || "failed to connect" in m ||
            "econnrefused" in m || "connection refused" in m || "unable to resolve" in m ||
            "network is unreachable" in m
        if (timeout) {
            return "Can't reach the PC. " + pairingHelp() + " Is coin_tray.py running? Allow Python in Windows Firewall."
        }
        return e.message ?: e.javaClass.simpleName
    }
}

class Prefs(ctx: Context) {
    private val p = ctx.getSharedPreferences("coin_tray", Context.MODE_PRIVATE)

    var host: String
        get() {
            val saved = (p.getString("host", "") ?: "").trim().trimEnd('/')
            if (saved.isBlank()) return NetHints.suggestedHost()
            // Emulator cannot reach a real LAN IP; remap common home-LAN mistypes
            if (NetHints.isEmulator() && Regex("""192\.168\.\d+\.\d+""").containsMatchIn(saved)) {
                return "http://10.0.2.2:8722"
            }
            return saved
        }
        set(v) {
            p.edit().putString("host", v.trim().trimEnd('/')).apply()
        }

    var token: String
        get() = p.getString("token", "") ?: ""
        set(v) { p.edit().putString("token", v.trim()).apply() }

    var idMode: String
        get() = p.getString("idMode", "companion") ?: "companion"
        set(v) { p.edit().putString("idMode", v).apply() }

    fun trayJson(): String = p.getString("trayJson", "[]") ?: "[]"
    fun saveTrayJson(s: String) { p.edit().putString("trayJson", s).apply() }
}
