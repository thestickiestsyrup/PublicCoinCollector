package com.cointray.app

import org.json.JSONArray
import org.json.JSONObject

/** Baggie IDs matching desktop: CA-0007 · U · 1967 dime Ag */
object Catalog {
    val COUNTRY_CODES = listOf(
        "United States" to "US",
        "Canada" to "CA",
        "United Kingdom" to "UK",
        "France" to "FR",
        "Germany" to "DE",
        "Switzerland" to "CH",
        "Soviet Union" to "SU",
        "East Germany" to "DDR",
        "Italy" to "IT",
        "Spain" to "ES",
        "Netherlands" to "NL",
        "Austria" to "AT",
        "Belgium" to "BE",
        "Poland" to "PL",
        "Sweden" to "SE",
        "Norway" to "NO",
        "Denmark" to "DK",
        "Ireland" to "IE",
    )

    private val idRe = Regex("""^([A-Za-z]{2,4})-(\d{1,6})\b""")
    private val tierRe = Regex("""·\s*([CURG])\s*·""", RegexOption.IGNORE_CASE)

    fun countryCode(country: String): String {
        val raw = country.trim()
        if (raw.isEmpty()) return "XX"
        val up = raw.uppercase()
        COUNTRY_CODES.firstOrNull { it.second == up && up != "OTHER" }?.let { return it.second }
        COUNTRY_CODES.firstOrNull { it.first.equals(raw, ignoreCase = true) }?.let { return it.second }
        return "XX"
    }

    fun parseId(name: String): Pair<String?, Int?> {
        val m = idRe.find(name.trim()) ?: return null to null
        return m.groupValues[1].uppercase() to m.groupValues[2].toInt()
    }

    fun nextId(code: String, tray: JSONArray): String {
        var c = code.uppercase()
        if (c == "OTHER" || c == "CT") c = "XX"
        var maxN = 0
        for (i in 0 until tray.length()) {
            val (cc, n) = parseId(tray.getJSONObject(i).optString("catalogName"))
            if (cc == c && n != null && n > maxN) maxN = n
        }
        return "%s-%04d".format(c, maxN + 1)
    }

    fun likelySilver(country: String, year: String, series: String, denom: String): Boolean {
        val y = Regex("""(19|20)\d{2}""").find(year)?.value?.toIntOrNull() ?: return false
        val name = country.trim()
        val blob = "$series $denom".lowercase()
        val centish = Regex("""\b(lincoln|penny|1¢|1\s*cent|one\s*cent|canadian\s*cent)\b""").containsMatchIn(blob)
        val nickel = Regex("""\b(jefferson|nickel|5¢|5\s*cent|canadian\s*nickel)\b""").containsMatchIn(blob)
        val half = Regex("""\b(half|50¢|50\s*c)\b""").containsMatchIn(blob)
        if (name.equals("United States", true)) {
            if (centish || nickel) return false
            if (y <= 1964) return true
            if (half && y in 1965..1970) return true
        }
        if (name.equals("Canada", true)) {
            if (centish || nickel || "loonie" in blob || "toonie" in blob) return false
            if (y <= 1967 && listOf("dime", "10¢", "10c", "10 cent", "quarter", "25¢", "25c",
                    "25 cent", "half", "50¢", "50c", "50 cent", "dollar").any { it in blob }) {
                return true
            }
        }
        if (name.equals("United Kingdom", true) && y <= 1946) {
            if (listOf("shilling", "florin", "crown", "sixpence", "1/-", "2/-").any { it in blob }) {
                return true
            }
        }
        return false
    }

    fun tags(
        country: String, year: String, series: String, denom: String, grade: String,
        specs: JSONObject?,
    ): List<String> {
        val out = mutableListOf<String>()
        val metal = specs?.optString("metal")?.lowercase() ?: ""
        if ("silver" in metal || likelySilver(country, year, series, denom)) out.add("Ag")
        val blob = "$series $denom".lowercase()
        if (listOf("commemorat", "olympic", "jubilee", "memorial").any { it in blob }) out.add("comm")
        if ("proof" in grade.lowercase()) out.add("proof")
        return out.distinct()
    }

    fun build(
        country: String,
        year: String,
        mint: String,
        series: String,
        denom: String,
        grade: String,
        current: String,
        tray: JSONArray,
        tierHint: String? = null,
        specs: JSONObject? = null,
    ): String {
        val code = countryCode(country)
        val (cc, num) = parseId(current)
        val prefix = if (num != null && cc != null && cc != "CT" && cc == code) {
            "%s-%04d".format(code, num)
        } else {
            nextId(code, tray)
        }
        var tier = "C"
        val m = tierRe.find(current)
        if (m != null) tier = m.groupValues[1].uppercase()
        val th = (tierHint ?: "").uppercase()
        if (th in setOf("C", "U", "R", "G")) tier = th
        var mintBit = mint
        if (mintBit.lowercase() in setOf("none", "unclear", "n/a")) mintBit = ""
        var detail = listOf(year, mintBit, series.ifBlank { denom }).filter { it.isNotBlank() }.joinToString(" ")
        val t = tags(country, year, series, denom, grade, specs)
        if (t.isNotEmpty()) detail = "$detail ${t.joinToString(" ")}".trim()
        return if (detail.isBlank()) "$prefix · $tier" else "$prefix · $tier · $detail"
    }
}
