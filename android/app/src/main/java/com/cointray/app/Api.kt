package com.cointray.app

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

class Api(private val prefs: Prefs) {
    class HttpError(val code: Int, msg: String) : Exception(msg)

    private fun conn(path: String, method: String): HttpURLConnection {
        val base = prefs.host.trimEnd('/')
        if (base.isBlank()) {
            throw IllegalStateException(NetHints.pairingHelp())
        }
        val url = URL("$base$path")
        val c = url.openConnection() as HttpURLConnection
        c.connectTimeout = 8000
        c.readTimeout = TimeUnit.MINUTES.toMillis(4).toInt()
        c.requestMethod = method
        c.setRequestProperty("Accept", "application/json")
        if (prefs.token.isNotBlank()) {
            c.setRequestProperty("X-Coin-Tray-Token", prefs.token)
        }
        return c
    }

    private fun readAll(c: HttpURLConnection): String {
        val stream = if (c.responseCode in 200..299) c.inputStream else c.errorStream
        val body = BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).use { it.readText() }
        if (c.responseCode == 401) throw HttpError(401, "Bad token — check Settings.")
        if (c.responseCode !in 200..299) {
            throw HttpError(c.responseCode, body.ifBlank { "HTTP ${c.responseCode}" })
        }
        return body
    }

    fun health(): JSONObject {
        val c = conn("/api/health", "GET")
        return try {
            JSONObject(readAll(c))
        } finally {
            c.disconnect()
        }
    }

    fun get(path: String): JSONObject {
        val c = conn(path, "GET")
        return try {
            JSONObject(readAll(c))
        } finally {
            c.disconnect()
        }
    }

    fun post(path: String, body: JSONObject): JSONObject {
        val c = conn(path, "POST")
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        return try {
            OutputStreamWriter(c.outputStream, Charsets.UTF_8).use { it.write(body.toString()) }
            JSONObject(readAll(c))
        } finally {
            c.disconnect()
        }
    }

    fun putTray(tray: JSONArray): JSONObject {
        val payload = JSONObject().put("tray", tray)
        val c = conn("/api/tray", "PUT")
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        return try {
            OutputStreamWriter(c.outputStream, Charsets.UTF_8).use { it.write(payload.toString()) }
            JSONObject(readAll(c))
        } finally {
            c.disconnect()
        }
    }
}
