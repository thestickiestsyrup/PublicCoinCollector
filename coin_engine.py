#!/usr/bin/env python3
"""
Coin Tray engine. Shared by the desktop app and the browser version.

Vision inference runs locally through Ollama. Pricing comes from numismatic
sites over the web. No AI provider is involved.

Python stdlib only.
"""

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

OLLAMA = "http://127.0.0.1:11434"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Sites the price lookup may read. Anything else in the search results is
# discarded, so a random blog can't drive a valuation. Editable in Settings.
SOURCES = [
    "pcgs.com",
    "ngccoin.com",
    "usacoinbook.com",
    "greatcollections.com",
    "ha.com",
    "stacksbowers.com",
    "numista.com",
    "coinvaluechecker.com",
    "coinstudy.com",
    # World / European dealers — Spanish & other foreign silver often listed here
    "ma-shops.com",
    "catawiki.com",
    "delcampe.net",
    "coinarchives.com",
    "ebay.com",
    "ebay.co.uk",
    "ebay.de",
    "etsy.com",
]

DEFAULT_SOURCES = list(SOURCES)


def merge_sources(saved):
    """Keep user Settings list, but ensure new default hosts are present."""
    out = []
    seen = set()
    for host in list(saved or []) + DEFAULT_SOURCES:
        h = (host or "").strip().lower().replace("https://", "").replace("http://", "")
        h = h.replace("www.", "").strip("/")
        if not h or h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out or list(DEFAULT_SOURCES)

def _make_ssl_context():
    """Prefer system/certifi CAs; some Windows Python builds fail strict verify."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    return ssl.create_default_context()


SSL_CTX = _make_ssl_context()
SSL_CTX_INSECURE = ssl._create_unverified_context()

# Process we started with `ollama serve`. Only killed on app exit if we own it.
_ollama_proc = None
_ollama_owned = False

# Never send local Ollama traffic through an HTTP(S)_PROXY.
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OllamaDown(Exception):
    pass


def _ollama_get(path, host=None, timeout=8):
    url = (host or OLLAMA).rstrip("/") + path
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _NO_PROXY.open(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------
# HTML -> text
# --------------------------------------------------------------------------

class Textify(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.depth, self.title, self._t = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        if tag == "title":
            self._t = True
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "td"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth > 0:
            self.depth -= 1
        if tag == "title":
            self._t = False

    def handle_data(self, data):
        if self._t:
            self.title += data
        if self.depth == 0:
            self.parts.append(data)

    def text(self):
        raw = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        return re.sub(r"\n\s*\n+", "\n", raw).strip()


def fetch(url, timeout=20):
    """Fetch HTML. Retries with relaxed SSL if the host cert fails local verify."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    })
    last = None
    for ctx in (SSL_CTX, SSL_CTX_INSECURE):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                charset = r.headers.get_content_charset() or "utf-8"
                return r.read(1_500_000).decode(charset, errors="replace")
        except Exception as e:
            last = e
            continue
    raise last


def page_text(url):
    p = Textify()
    p.feed(fetch(url))
    return p.title.strip(), p.text()


# PCGS US Price Guide — graded dealer asking prices (see https://www.pcgs.com/prices/us)
PCGS_US_PRICES = "https://www.pcgs.com/prices/us"
PCGS_SERIES_URLS = {
    "lincoln wheat": "https://www.pcgs.com/prices/detail/lincoln-cent-wheat-reverse/46/grades-1-20/ms",
    "lincoln memorial": "https://www.pcgs.com/prices/detail/lincoln-cent-modern/47/most-active/ms",
    "lincoln shield": "https://www.pcgs.com/prices/detail/lincoln-cent-modern/47/most-active/ms",
    "lincoln": "https://www.pcgs.com/prices/detail/lincoln-cent-modern/47/most-active/ms",
    "jefferson": "https://www.pcgs.com/prices/detail/jefferson-nickel/84/most-active",
    "buffalo": "https://www.pcgs.com/prices/detail/buffalo-nickel/83/most-active",
    "roosevelt": "https://www.pcgs.com/prices/detail/roosevelt-dime/98/most-active/ms",
    "mercury": "https://www.pcgs.com/prices/detail/mercury-dime/703/most-active/ms",
    "washington": "https://www.pcgs.com/prices/detail/washington-quarter/112/most-active",
    "kennedy": "https://www.pcgs.com/prices/detail/kennedy-half-dollar/125/most-active",
}


def pcgs_price_guide_url(a):
    """Best PCGS Price Guide page for this US attribution, or the US hub."""
    if not _is_united_states(a):
        return None
    series = (a.get("series") or "").lower()
    denom = (a.get("denomination") or "").lower()
    year = _year_int(a)
    blob = series + " " + denom
    if "wheat" in blob or (year and year <= 1958 and ("lincoln" in blob or "cent" in blob)):
        return PCGS_SERIES_URLS["lincoln wheat"]
    for key, url in PCGS_SERIES_URLS.items():
        if key in blob:
            return url
    kind = _coin_kind(a)
    if kind == "cent":
        return PCGS_SERIES_URLS["lincoln"]
    if kind == "nickel":
        return PCGS_SERIES_URLS["jefferson"]
    if kind == "dime":
        return PCGS_SERIES_URLS["roosevelt"]
    if kind == "quarter":
        return PCGS_SERIES_URLS["washington"]
    if kind == "half":
        return PCGS_SERIES_URLS["kennedy"]
    return PCGS_US_PRICES


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def unwrap(href):
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if q.get("uddg"):
            return q["uddg"][0]
    return href


def allowed(url, sources=None):
    host = urllib.parse.urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return any(host == s or host.endswith("." + s) for s in (sources or SOURCES))


def search(query, limit=4, sources=None):
    """Return allowed result URLs (compat). Prefer search_hits for snippets."""
    return [h["url"] for h in search_hits(query, limit=limit, sources=sources)]


def _parse_ddg_hits(html, limit=4, sources=None):
    blocks = re.split(r'<div[^>]+class="[^"]*result[^"]*"', html)
    out, seen = [], set()
    for block in blocks:
        hm = re.search(r'class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"', block)
        if not hm:
            hm = re.search(r'href="(https?://[^"]+)"[^>]*class="[^"]*result__a', block)
        if not hm:
            continue
        href = unwrap(hm.group(1))
        if href in seen or not allowed(href, sources):
            continue
        title_m = re.search(r'class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>', block, re.S)
        snip_m = re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|td|div)',
                           block, re.S | re.I)
        title = re.sub(r"<[^>]+>", " ", title_m.group(1) if title_m else "")
        snip = re.sub(r"<[^>]+>", " ", snip_m.group(1) if snip_m else "")
        title = re.sub(r"\s+", " ", title).strip()
        snip = re.sub(r"\s+", " ", snip).strip()
        seen.add(href)
        out.append({"url": href, "title": title[:120], "snippet": snip[:500]})
        if len(out) >= limit:
            break
    if out:
        return out
    hits = [unwrap(m.group(1)) for m in
            re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"', html)]
    for h in hits:
        if h in seen or not allowed(h, sources):
            continue
        seen.add(h)
        out.append({"url": h, "title": "", "snippet": ""})
        if len(out) >= limit:
            break
    return out


def _parse_bing_hits(html, limit=4, sources=None):
    out, seen = [], set()
    for block in re.finditer(
            r'<li[^>]+class="[^"]*b_algo[^"]*"(.*?)</li>', html, re.S | re.I):
        chunk = block.group(1)
        hm = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"', chunk, re.I)
        if not hm:
            continue
        href = unwrap(hm.group(1))
        if "bing.com" in href and "http" not in href.split("bing.com", 1)[-1][:8]:
            continue
        if href in seen or not allowed(href, sources):
            continue
        title_m = re.search(r'<h2[^>]*>\s*<a[^>]*>(.*?)</a>', chunk, re.S | re.I)
        snip_m = re.search(r'class="[^"]*b_caption[^"]*"[^>]*>.*?<p[^>]*>(.*?)</p>',
                           chunk, re.S | re.I)
        title = re.sub(r"<[^>]+>", " ", title_m.group(1) if title_m else "")
        snip = re.sub(r"<[^>]+>", " ", snip_m.group(1) if snip_m else "")
        seen.add(href)
        out.append({"url": href,
                    "title": re.sub(r"\s+", " ", title).strip()[:120],
                    "snippet": re.sub(r"\s+", " ", snip).strip()[:500]})
        if len(out) >= limit:
            break
    return out


def _clean_market_query(query):
    """Strip site: filters / pricing fluff for marketplace search pages."""
    q = re.sub(r"\bsite:\S+", " ", query or "", flags=re.I)
    # Delcampe's search is picky — drop auction jargon that kills matches
    for w in ("sold", "price", "guide", "value", "worth", "asking", "ebay",
              "numista", "listing", "auction", "realized", "wert", "preis",
              "valor", "prix", "valeur"):
        q = re.sub(r"\b%s\b" % w, " ", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def short_market_queries(attribution, base_queries=None):
    """Short country/denom phrases that marketplace search engines actually match."""
    a = attribution or {}
    year = normalize_year(a.get("year"))
    country = (a.get("country") or "").strip()
    denom = (a.get("denomination") or "").strip()
    series = (a.get("series") or "").strip()
    bits = []
    if country and (denom or series):
        bits.append("%s %s" % (country, denom or series))
    if denom:
        bits.append(denom)
    if series and series.lower() != (denom or "").lower():
        bits.append(series)
    if year and (denom or series):
        bits.append("%s %s" % (year, denom or series))
    for q in base_queries or ():
        c = _clean_market_query(q)
        if c:
            bits.append(c)
    out, seen = [], set()
    for q in bits:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
        if len(out) >= 6:
            break
    return out


def search_delcampe(query, limit=8, sources=None):
    """Search Delcampe collectibles (works when Google/DDG/Bing/eBay block bots)."""
    if sources is not None and not any(
            s == "delcampe.net" or str(s).endswith("delcampe.net")
            for s in sources):
        return []
    q = _clean_market_query(query)
    if not q:
        return []
    url = ("https://www.delcampe.net/en_US/collectibles/search?term="
           + urllib.parse.quote(q))
    try:
        html = fetch(url, timeout=25)
    except Exception:
        return []
    pat = re.compile(
        r'href="([^"]+)"[^>]*>\s*<h2[^>]*class="[^"]*item-title[^"]*"[^>]*>\s*(.*?)\s*</h2>'
        r'.{0,900}?class="[^"]*item-price[^"]*"[^>]*>\s*(.*?)\s*</strong>',
        re.S | re.I,
    )
    out, seen = [], set()
    for m in pat.finditer(html):
        href = m.group(1)
        if href.startswith("/"):
            href = "https://www.delcampe.net" + href
        if href in seen or not allowed(href, sources):
            continue
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        price = re.sub(r"<[^>]+>", " ", m.group(3))
        price = price.replace("\u00b1", "").replace("&plusmn;", "").strip()
        price = re.sub(r"\s+", " ", price)
        if not title:
            continue
        seen.add(href)
        out.append({
            "url": href,
            "title": title[:120],
            "snippet": price[:120],
        })
        if len(out) >= limit:
            break
    return out


def search_hits(query, limit=4, sources=None):
    """Search results with title/snippet. DDG → Bing → Delcampe marketplace."""
    try:
        html = fetch(
            "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query),
            timeout=15)
        out = _parse_ddg_hits(html, limit=limit, sources=sources)
        if out:
            return out
    except Exception:
        pass
    try:
        html = fetch(
            "https://www.bing.com/search?q=" + urllib.parse.quote(query),
            timeout=15)
        out = _parse_bing_hits(html, limit=limit, sources=sources)
        if out:
            return out
    except Exception:
        pass
    # Web search engines often bot-block; Delcampe search pages still work
    return search_delcampe(query, limit=limit, sources=sources)


# USD and euro amounts (European dealers often quote EUR only)
PRICE = re.compile(
    r"(?:"
    r"\$\s?[\d,]+(?:\.\d{2})?"
    r"|€\s?[\d.,]+"
    r"|[\d.,]+\s?€"
    r"|EUR\s?[\d.,]+"
    r"|[\d.,]+\s?EUR"
    r"|[\d.,]+\s?euros?"
    r"|GBP\s?[\d.,]+"
    r"|£\s?[\d.,]+"
    r")",
    re.I,
)

# Rough FX so marketplace euros/pounds can become a USD tray range
EUR_TO_USD = 1.08
GBP_TO_USD = 1.27


def money_to_usd(raw):
    """Parse a PRICE match into a USD float, or None."""
    s = (raw or "").strip()
    if not s:
        return None
    euro = bool(re.search(r"€|eur|euros?", s, re.I))
    gbp = bool(re.search(r"£|gbp", s, re.I))
    num = re.sub(r"[^\d.,]", "", s)
    if not num:
        return None
    # European 12,50 vs US 12.50 / 1,299.00
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        parts = num.split(",")
        num = num.replace(",", ".") if len(parts[-1]) == 2 else num.replace(",", "")
    try:
        val = float(num)
    except ValueError:
        return None
    if euro:
        val *= EUR_TO_USD
    elif gbp:
        val *= GBP_TO_USD
    return val


def amounts_usd(text, lo=0.20, hi=400.0):
    """Sane retail amounts from a snippet (filters shipping / bullion outliers)."""
    vals = []
    for m in PRICE.finditer(text or ""):
        v = money_to_usd(m.group(0))
        if v is not None and lo <= v <= hi:
            vals.append(round(v, 2))
    return vals


def price_windows(text, width=260, cap=12):
    """Keep only the parts of a page that actually mention money."""
    spots = [m.start() for m in PRICE.finditer(text)][:80]
    if not spots:
        return text[:800]
    chunks, last = [], -1
    for s in spots:
        a, b = max(0, s - width // 2), min(len(text), s + width)
        if a <= last:
            chunks[-1] = (chunks[-1][0], b)
        else:
            chunks.append((a, b))
        last = b
        if len(chunks) >= cap:
            break
    return " … ".join(text[a:b].strip() for a, b in chunks)


def gather(queries, max_pages=5, sources=None, say=None):
    hits = []
    seen = set()
    for q in queries:
        if say:
            say("searching: " + q[:58])
        for h in search_hits(q, 5, sources):
            u = h["url"]
            if u in seen:
                continue
            seen.add(u)
            hits.append(h)
        time.sleep(0.4)
    docs = []
    for h in hits[:max_pages]:
        u = h["url"]
        host = urllib.parse.urlparse(u).netloc
        if say:
            say("reading " + host)
        title, text = h.get("title") or "", ""
        try:
            title2, text = page_text(u)
            title = title or title2
        except Exception:
            # eBay/etc often block full page reads — keep DDG snippet with prices
            text = h.get("snippet") or ""
        snip = price_windows(text) if text else (h.get("snippet") or "")
        if not snip.strip() and h.get("snippet"):
            snip = h["snippet"]
        host_l = (host or "").lower()
        if snip.strip():
            docs.append({"url": u, "title": (title or "")[:120], "snippet": snip[:2600]})
        elif any(s in host_l for s in (
                "numista.com", "ma-shops.com", "coinarchives.com", "ebay.",
                "delcampe.net")):
            docs.append({"url": u, "title": (title or "")[:120],
                         "snippet": (text or h.get("snippet") or "")[:2600]})
        time.sleep(0.3)
    return docs


# Marketplace / catalog hosts preferred for Coinoscope-like similar cards
MARKETPLACE_HOSTS = (
    "ebay.com", "ebay.co.uk", "ebay.de", "ma-shops.com", "catawiki.com",
    "delcampe.net", "etsy.com", "greatcollections.com", "ha.com",
    "stacksbowers.com", "coinarchives.com", "numista.com",
    "usacoinbook.com", "coinvaluechecker.com", "coinstudy.com",
)


def _doc_host(url):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_price_guide_stub(d):
    """Skip PCGS/NGC guide pages that are not individual marketplace listings."""
    host = _doc_host(d.get("url"))
    title = (d.get("title") or "").lower()
    url = (d.get("url") or "").lower()
    if "pcgs.com" in host and ("/prices" in url or "price guide" in title):
        return True
    if "ngccoin.com" in host and ("price" in url or "price guide" in title):
        return True
    if "pcgs price guide" in title:
        return True
    return False


def _listing_kind(title, snippet):
    blob = ("%s %s" % (title or "", snippet or "")).lower()
    if any(w in blob for w in (
            " sold", "sold for", "price realized", "prices realized",
            "ended:", "was sold", "sold price", "hammer")):
        return "sold"
    if any(w in blob for w in (
            "buy it now", "asking", "for sale", "or best offer",
            "bidding", "current bid", "add to cart", "in stock")):
        return "asking"
    return "listing"


def condition_from_text(title, snippet):
    """Pull a short grade/condition label from a listing title or snippet."""
    blob = "%s %s" % (title or "", snippet or "")
    if not blob.strip():
        return ""
    # Specific graded labels first
    patterns = [
        (r"\b(?:PCGS|NGC)\s*(?:MS|PR|PF|AU|XF|EF|VF|F|VG|G)[-\s]?\d{1,2}\b", None),
        (r"\b(?:PCGS|NGC)\s*(?:MS|PR|PF|AU|XF|EF|VF|F|VG|G)\b", None),
        (r"\b(?:MS|PR|PF)[-\s]?\d{1,2}\b", None),
        (r"\b(?:AU|XF|EF|VF)[-\s]?\d{1,2}\b", None),
        (r"\b(?:AU|XF|EF|VF|F|VG)\b", None),
        (r"\bUNC\b|\buncirculated\b", "uncirculated"),
        (r"\bBU\b|\bbrilliant uncirculated\b", "BU"),
        (r"\babout uncirculated\b", "AU"),
        (r"\bextremely fine\b|\bextra fine\b", "XF"),
        (r"\bvery fine\b", "VF"),
        (r"\blightly worn\b", "lightly worn"),
        (r"\bheavily worn\b", "heavily worn"),
        (r"\bmoderately worn\b", "moderately worn"),
        (r"\bwell worn\b", "well worn"),
        (r"\bcirculated\b", "circulated"),
        (r"\bgood\b", "Good"),
        (r"\bfine\b", "Fine"),
    ]
    for pat, fixed in patterns:
        m = re.search(pat, blob, re.I)
        if not m:
            continue
        if fixed:
            return fixed
        return re.sub(r"\s+", " ", m.group(0)).strip()
    return ""


def build_price_citations(docs, listings=None, max_n=20):
    """One citation per source URL: price + condition/grade when detectable."""
    by_url = {}
    for card in listings or []:
        u = (card.get("url") or "").split("?")[0].lower()
        if u:
            by_url[u] = card
    out, seen = [], set()
    for d in docs or []:
        url = d.get("url") or ""
        key = url.split("?")[0].lower() if url else ""
        if not key or key in seen:
            continue
        seen.add(key)
        host = _doc_host(url)
        title = (d.get("title") or "").strip() or "source"
        snippet = d.get("snippet") or ""
        card = by_url.get(key) or {}
        price = (card.get("price") or "").strip()
        if not price:
            found = amounts_usd(snippet) or amounts_usd(title)
            if found:
                found.sort()
                price = "$%.2f" % found[len(found) // 2]
        if _is_price_guide_stub(d) or (
                "pcgs.com" in host and "/prices" in (url or "").lower()):
            kind = "guide"
        else:
            kind = (card.get("kind") or "").strip() or _listing_kind(title, snippet)
            if kind == "listing" and _is_marketplace_host(host) and not price:
                kind = "catalog"
        cond = condition_from_text(title, snippet)
        out.append({
            "title": title[:100],
            "url": url,
            "where": host or "web",
            "price": price,
            "condition": cond,
            "kind": kind,
        })
        if len(out) >= max_n:
            break
    return out


def _parse_price_amount(price_str):
    m = re.search(r"[\d.]+", (price_str or "").replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def enrich_price_stats(out):
    """Add average/median, sold vs asking counts, and thin-evidence flags.

    Guards against one ridiculous asking price looking like a real market.
    """
    if not out or out.get("error"):
        return out
    citations = out.get("citations") or out.get("comparables") or []
    amounts = []
    sold = asking = guide = 0
    hosts = set()
    for c in citations:
        kind = (c.get("kind") or "").lower()
        if kind == "sold":
            sold += 1
        elif kind == "asking":
            asking += 1
        elif kind == "guide":
            guide += 1
        where = (c.get("where") or "").strip()
        if where:
            hosts.add(where)
        amt = _parse_price_amount(c.get("price"))
        if amt is not None:
            amounts.append(amt)
    # Also fold similarListings if citations were thin
    if len(amounts) < 2:
        for c in out.get("similarListings") or []:
            amt = _parse_price_amount(c.get("price"))
            if amt is None:
                continue
            amounts.append(amt)
            kind = (c.get("kind") or "").lower()
            if kind == "sold":
                sold += 1
            elif kind == "asking":
                asking += 1
            where = (c.get("where") or "").strip()
            if where:
                hosts.add(where)
    out["soldCount"] = sold
    out["askingCount"] = asking
    out["guideCount"] = guide
    out["pricedCount"] = len(amounts)
    out["sourceHostCount"] = len(hosts)
    out["soldEvidence"] = sold > 0
    if amounts:
        amounts.sort()
        n = len(amounts)
        out["valueAvg"] = round(sum(amounts) / n, 2)
        if n % 2:
            out["valueMedian"] = round(amounts[n // 2], 2)
        else:
            out["valueMedian"] = round(
                (amounts[n // 2 - 1] + amounts[n // 2]) / 2, 2)
        # Prefer multi-comp IQR for the displayed range when we have enough
        if n >= 3 and not out.get("faceValue"):
            lo = amounts[max(0, (n - 1) // 4)]
            hi = amounts[min(n - 1, (3 * n) // 4)]
            if hi < lo:
                hi = lo
            med = out["valueMedian"]
            if med > 0 and hi > med * 6 and n >= 4:
                hi = round(med * 4, 2)
            try:
                cur_lo = float(out.get("valueLow") or 0)
            except (TypeError, ValueError):
                cur_lo = 0
            try:
                cur_hi = float(out.get("valueHigh") or 0)
            except (TypeError, ValueError):
                cur_hi = 0
            if cur_lo <= 0:
                out["valueLow"] = round(lo, 2)
            if cur_hi <= 0 or (med > 0 and cur_hi > med * 8):
                out["valueHigh"] = round(hi, 2)
        elif n == 1 and not (out.get("valueLow") or out.get("valueHigh")):
            out["valueLow"] = out["valueHigh"] = amounts[0]
    # Thin evidence: one listing, or one host only
    thin = (
        out.get("pricedCount", 0) < 2
        or out.get("sourceHostCount", 0) < 2
    )
    out["thinEvidence"] = bool(thin) and not out.get("faceValue")
    bits = []
    if out.get("valueAvg") is not None:
        bits.append("avg $%.2f" % out["valueAvg"])
    if out.get("valueMedian") is not None and out.get("valueMedian") != out.get("valueAvg"):
        bits.append("median $%.2f" % out["valueMedian"])
    if sold or asking:
        bits.append("%d sold / %d asking" % (sold, asking))
    elif guide:
        bits.append("%d guide page(s)" % guide)
    if out.get("sourceHostCount"):
        bits.append("%d site(s)" % out["sourceHostCount"])
    if bits:
        out["marketSummary"] = " · ".join(bits)
    if out["thinEvidence"]:
        warn = "Thin evidence"
        if out.get("pricedCount", 0) < 2:
            warn += " — fewer than 2 priced comps"
        elif out.get("sourceHostCount", 0) < 2:
            warn += " — only one site"
        if sold == 0 and asking:
            warn += "; asking prices only (not confirmed sold)"
        out["evidenceNote"] = warn + ". Check SOURCES before trusting a high ask."
    elif sold:
        out["evidenceNote"] = (
            "Includes %d sold/realized price(s) — stronger than asking-only."
            % sold)
    elif asking:
        out["evidenceNote"] = (
            "Asking prices only — no sold/realized comps found. "
            "Average can still be skewed by optimistic asks."
        )
    return out


def attach_citations(out, docs, listings=None, attribution=None):
    """Stamp full source citations (price + condition) onto a price result."""
    if not out or out.get("error"):
        return out
    citations = build_price_citations(docs, listings=listings, max_n=20)
    if citations:
        out["citations"] = citations
        out["sources"] = [c["url"] for c in citations if c.get("url")]
        comps = []
        for c in citations:
            if not (c.get("price") or c.get("condition") or c.get("url")):
                continue
            comps.append({
                "what": c.get("title") or "source",
                "price": c.get("price") or "",
                "where": c.get("where") or "web",
                "url": c.get("url") or "",
                "condition": c.get("condition") or "",
                "kind": c.get("kind") or "",
            })
        if comps:
            out["comparables"] = comps
        grade = (out.get("grade") or (attribution or {}).get("grade") or "").strip()
        conds = []
        for c in citations:
            cond = (c.get("condition") or "").strip()
            if cond and cond not in conds:
                conds.append(cond)
        bits = []
        if grade:
            bits.append("Estimate grade: %s" % grade)
        if conds:
            bits.append("Source conditions: %s" % ", ".join(conds[:8]))
        elif citations and not any(c.get("condition") for c in citations):
            bits.append("Source conditions not stated — assume as-listed / circulated")
        if bits:
            out["conditionNote"] = " — ".join(bits)
    elif out.get("typicalEstimate") or out.get("faceValue"):
        out.setdefault(
            "conditionNote",
            "Ballpark / face value — few or no live listing citations.",
        )
    return enrich_price_stats(out)


def _is_marketplace_host(host):
    host = (host or "").lower()
    for m in MARKETPLACE_HOSTS:
        if host == m or host.endswith("." + m):
            return True
    if host.startswith("ebay."):
        return True
    return False


def similar_listings_from_docs(docs, limit=8):
    """Build rich similar-coin cards from scraped marketplace / catalog pages.

    Prefers eBay / MA-Shops / Delcampe / Catawiki / Numista; skips PCGS guide stubs.
    """
    ranked = []
    for d in docs or []:
        if _is_price_guide_stub(d):
            continue
        url = d.get("url") or ""
        host = _doc_host(url)
        title = (d.get("title") or "").strip() or "listing"
        snippet = d.get("snippet") or ""
        found = amounts_usd(snippet)
        if not found:
            found = amounts_usd(title)
        if not found:
            # Catalog pages (Numista) without a clear USD price — still useful as a match
            if _is_marketplace_host(host) and title and len(title) > 8:
                ranked.append({
                    "title": title[:100],
                    "price": "",
                    "currency": "USD",
                    "where": host or "web",
                    "url": url,
                    "kind": "catalog",
                    "amount": None,
                    "_pref": 1 if _is_marketplace_host(host) else 0,
                })
            continue
        found.sort()
        pick = found[len(found) // 2]
        kind = _listing_kind(title, snippet)
        pref = 2 if _is_marketplace_host(host) else 0
        if kind == "sold":
            pref += 1
        ranked.append({
            "title": title[:100],
            "price": "$%.2f" % pick,
            "currency": "USD",
            "where": host or "web",
            "url": url,
            "kind": kind,
            "amount": pick,
            "_pref": pref,
        })
    # Marketplace + sold first, then others; drop dup URLs
    ranked.sort(key=lambda x: (-x.get("_pref", 0), -(x.get("amount") or 0)))
    out, seen = [], set()
    for card in ranked:
        u = card.get("url") or ""
        key = u.split("?")[0].lower() if u else card.get("title", "").lower()
        if key in seen:
            continue
        seen.add(key)
        clean = {k: v for k, v in card.items() if not k.startswith("_")}
        # Drop internal amount from public card? keep for sorting done — strip amount
        clean.pop("amount", None)
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def market_estimate_from_docs(docs, attribution=None):
    """Build a sold/asking USD range from scraped marketplace snippets.

    Used when the LLM leaves values at 0, or as a supplement for common world coins.
    """
    listings = similar_listings_from_docs(docs, limit=8)
    vals = []
    comps = []
    for card in listings:
        # Reconstruct amount from price string for range math
        p = card.get("price") or ""
        m = re.search(r"[\d.]+", p.replace(",", ""))
        if not m:
            continue
        try:
            pick = float(m.group(0))
        except ValueError:
            continue
        vals.append(pick)
        if len(comps) < 4:
            comps.append({
                "what": card.get("title") or "listing",
                "price": card.get("price") or "",
                "where": card.get("where") or "web",
                "url": card.get("url") or "",
            })
    # Fallback: scan docs directly if listings had only catalog (no prices)
    if not vals:
        for d in docs or []:
            if _is_price_guide_stub(d):
                continue
            host = _doc_host(d.get("url"))
            found = amounts_usd(d.get("snippet") or "")
            if not found:
                found = amounts_usd(d.get("title") or "")
            if not found:
                continue
            found.sort()
            pick = found[len(found) // 2]
            vals.append(pick)
            if len(comps) < 4:
                comps.append({
                    "what": (d.get("title") or "listing")[:80],
                    "price": "$%.2f" % pick,
                    "where": host or "web",
                    "url": d.get("url") or "",
                })
    if not vals and not listings:
        return None
    if not vals:
        # Catalog-only matches — no dollar range
        label_bits = [
            (attribution or {}).get("year") or "",
            (attribution or {}).get("country") or "",
            (attribution or {}).get("series")
            or (attribution or {}).get("denomination") or "coin",
        ]
        label = " ".join(x for x in label_bits if x).strip()
        return {
            "identification": label or "Coin",
            "valueLow": 0,
            "valueHigh": 0,
            "currency": "USD",
            "grade": (attribution or {}).get("grade") or "circulated",
            "melt": "",
            "comparables": [],
            "similarListings": listings,
            "checks": [
                "Similar catalog matches found — open links to check sold prices.",
            ],
            "notes": ("Similar coins on catalog/marketplace sites. "
                      "No clear USD prices in snippets — check links. "
                      "Not a formal appraisal."),
            "faceValue": False,
            "marketSold": False,
            "sources": [d.get("url") for d in docs if d.get("url")][:8],
        }
    vals.sort()
    lo = vals[max(0, (len(vals) - 1) // 4)]
    hi = vals[min(len(vals) - 1, (3 * len(vals)) // 4)]
    if hi < lo:
        hi = lo
    # Single hit → narrow band around it
    if len(vals) == 1:
        lo = hi = vals[0]
    avg = round(sum(vals) / len(vals), 2)
    n = len(vals)
    if n % 2:
        med = round(vals[n // 2], 2)
    else:
        med = round((vals[n // 2 - 1] + vals[n // 2]) / 2, 2)
    sold_n = sum(1 for c in listings if (c.get("kind") or "") == "sold" and c.get("price"))
    ask_n = sum(1 for c in listings if (c.get("kind") or "") == "asking" and c.get("price"))
    hosts = {(c.get("where") or "") for c in listings if c.get("price")}
    hosts.discard("")
    label_bits = [
        (attribution or {}).get("year") or "",
        (attribution or {}).get("country") or "",
        (attribution or {}).get("series")
        or (attribution or {}).get("denomination") or "coin",
    ]
    label = " ".join(x for x in label_bits if x).strip()
    return {
        "identification": label or "Coin",
        "valueLow": round(lo, 2),
        "valueHigh": round(hi, 2),
        "valueAvg": avg,
        "valueMedian": med,
        "currency": "USD",
        "grade": (attribution or {}).get("grade") or "circulated",
        "melt": "",
        "comparables": comps,
        "similarListings": listings,
        "soldCount": sold_n,
        "askingCount": ask_n,
        "pricedCount": len(vals),
        "sourceHostCount": len(hosts),
        "soldEvidence": sold_n > 0,
        "thinEvidence": len(vals) < 2 or len(hosts) < 2,
        "checks": [
            "Prices from recent sold/asking listings - condition and seller fees vary.",
        ],
        "notes": ("Typical sold/asking range from eBay and dealer listings "
                  "(USD approx). Not a formal appraisal."),
        "faceValue": False,
        "marketSold": True,
        "sources": [d.get("url") for d in docs if d.get("url")][:8],
    }


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------

def models(host=None):
    try:
        data = _ollama_get("/api/tags", host=host, timeout=8)
        return [m["name"] for m in json.loads(data.decode()).get("models", [])]
    except Exception:
        return []


def _model_size_b(name):
    """Parameter size in billions from a tag like qwen3-vl:8b → 8.0."""
    m = re.search(r"(?:[:\-_]|^)(\d+(?:\.\d+)?)b\b", (name or "").lower())
    return float(m.group(1)) if m else 4.0


def _is_fragile_vision_model(name):
    """Models that often 500 / mis-read coin legends on current Ollama builds."""
    n = (name or "").lower()
    return "llama3.2-vision" in n or n.startswith("llama3.2-vision")


def _is_weak_coin_id_model(name):
    """Too small / generic for worn dates and foreign legends."""
    n = (name or "").lower()
    if _is_fragile_vision_model(n):
        return True
    if "moondream" in n:
        return True
    return False


def rank_vision_model(name, purpose="id"):
    """Lower tuple sorts first. purpose: 'id' (accuracy) or 'fast' (pricing JSON)."""
    n = (name or "").lower()
    size = _model_size_b(n)
    if _is_fragile_vision_model(n):
        return (90, 0, 0, n)
    if purpose == "fast":
        # Small gemma3 wins for VALUE_PROMPT text; avoid loading a second huge VL
        family = 40
        if "gemma3" in n:
            family = 0
        elif "gemma" in n:
            family = 5
        elif "qwen" in n and "vl" not in n and "vision" not in n:
            family = 10
        elif "qwen" in n:
            family = 20
        elif "moondream" in n:
            family = 30
        return (family, size, n)
    # Identification: OCR-capable VL first; sweet spot ~7–14B
    family = 50
    if "qwen" in n and ("vl" in n or "vision" in n):
        family = 0
    elif "gemma3" in n:
        family = 10
    elif "minicpm" in n and "vision" in n:
        family = 15
    elif "llava" in n:
        family = 25
    elif "moondream" in n:
        family = 80
    elif "vision" in n or "vl" in n:
        family = 35
    size_penalty = 0
    if size >= 27:
        size_penalty = 6
    elif size >= 14:
        size_penalty = 2
    elif size <= 2:
        size_penalty = 8
    elif size <= 3:
        size_penalty = 4
    return (family, size_penalty, -size, n)


def vision_models(host=None):
    """Installed vision-capable tags, best identification models first."""
    all_m = models(host)
    seen = [m for m in all_m if re.search(
        r"vl|vision|llava|minicpm|gemma3|moondream|gemma", m, re.I)]
    pool = seen or all_m
    return sorted(pool, key=lambda m: rank_vision_model(m, purpose="id"))


def pick_vision_model(available, preferred=None, purpose="id"):
    """Best model for coin photo ID (or fast text) from an installed list."""
    pool = list(available or [])
    if not pool:
        return (preferred or "").strip()
    ranked = sorted(pool, key=lambda m: rank_vision_model(m, purpose=purpose))
    pref = (preferred or "").strip()
    if pref and pref in pool:
        # Keep the user's pick unless it's fragile/weak and a better ID model exists
        if purpose == "id" and _is_weak_coin_id_model(pref):
            better = [m for m in ranked if not _is_weak_coin_id_model(m)]
            if better:
                return better[0]
        if purpose == "id" and _is_fragile_vision_model(pref):
            alt = [m for m in ranked if not _is_fragile_vision_model(m)]
            if alt:
                return alt[0]
        return pref
    return ranked[0]


def pick_pricing_model(available, id_model=None):
    """Smaller/faster model for VALUE_PROMPT (text-only JSON)."""
    pool = list(available or [])
    if not pool:
        return id_model or ""
    return pick_vision_model(pool, preferred=None, purpose="fast")


def order_vision_models(available, preferred=None, purpose="id"):
    """Try-order for Read: best ID model first, fragile tags last."""
    pool = list(available or [])
    if not pool:
        return []
    first = pick_vision_model(pool, preferred=preferred, purpose=purpose)
    rest = sorted(
        [m for m in pool if m != first],
        key=lambda m: rank_vision_model(m, purpose=purpose),
    )
    # Never burn time on llama3.2-vision before known-good tags
    good = [m for m in ([first] + rest) if not _is_fragile_vision_model(m)]
    fragile = [m for m in ([first] + rest) if _is_fragile_vision_model(m)]
    out, seen = [], set()
    for m in good + fragile:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def warmup_model(model, host=None, say=None):
    """Load a model into VRAM so the first Read isn't a cold start."""
    if not model:
        return False
    if say:
        say("warming %s…" % model)
    try:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user",
                          "content": 'Reply with JSON only: {"ok": true}'}],
            "stream": False,
            "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 24, "num_ctx": 512},
        }).encode()
        req = urllib.request.Request(
            (host or OLLAMA).rstrip("/") + "/api/chat",
            data=body, headers={"Content-Type": "application/json"})
        with _NO_PROXY.open(req, timeout=180) as r:
            r.read(4096)
        return True
    except Exception as e:
        _log("warmup failed for %s: %s" % (model, e))
        return False


def find_ollama_bin():
    found = shutil.which("ollama")
    if found:
        return found
    for path in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
        r"C:\Program Files\Ollama\ollama.exe",
        "/usr/local/bin/ollama",
        os.path.expanduser("~/bin/ollama"),
    ):
        if path and os.path.isfile(path):
            return path
    return None


def _log(msg):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "coin_tray_ollama.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _hidden_popen(args, cwd=None, env=None, log_file=None):
    """Start a process without a console flash on Windows."""
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    log_fh = None
    if log_file:
        try:
            log_fh = open(log_file, "a", encoding="utf-8")
            log_fh.write("\n--- spawn %s ---\n" % " ".join(args))
            log_fh.flush()
            stdout = log_fh
            stderr = subprocess.STDOUT
        except Exception:
            log_fh = None
    kw = dict(stdout=stdout, stderr=stderr, cwd=cwd, env=env)
    if sys.platform == "win32":
        # Prefer detached serve; CREATE_NO_WINDOW alone can abort ollama.exe.
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
        kw["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        kw["close_fds"] = False
    return subprocess.Popen(args, **kw)


def ensure_ollama(host=None, wait=90):
    """Bring the local Ollama API up. Starts `ollama serve` only if needed.

    Returns (ok, owned, detail). `owned` is True when this process started
    the server and should stop it on exit.
    """
    global _ollama_proc, _ollama_owned
    if _api_up(host):
        _log("already running")
        return True, _ollama_owned, "already running"

    binary = find_ollama_bin()
    if not binary:
        _log("binary not found")
        return False, False, "Ollama not found. Install from ollama.com"

    odir = os.path.dirname(binary)
    env = os.environ.copy()
    env["PATH"] = odir + os.pathsep + env.get("PATH", "")
    env["NO_PROXY"] = "127.0.0.1,localhost,::1," + env.get("NO_PROXY", "")
    env["no_proxy"] = env["NO_PROXY"]
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("http_proxy", None)
    env.pop("https_proxy", None)

    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "coin_tray_ollama_spawn.log")
    last_err = "Couldn't start Ollama"
    # serve first; on Windows also try launching the app binary (no args)
    attempts = [[binary, "serve"]]
    if sys.platform == "win32":
        attempts.append([binary])

    for attempt in range(1, 4):
        for args in attempts:
            _log("start attempt %d via %s" % (attempt, " ".join(args)))
            try:
                _ollama_proc = _hidden_popen(args, cwd=odir, env=env,
                                             log_file=log_path)
                _ollama_owned = True
            except Exception as e:
                last_err = "Couldn't start Ollama: %s" % e
                _log(last_err)
                _ollama_proc = None
                _ollama_owned = False
                continue

            deadline = time.time() + max(20, wait // 2)
            while time.time() < deadline:
                if _api_up(host):
                    _log("api up (owned)")
                    return True, True, "started"
                if _ollama_proc.poll() is not None:
                    last_err = "Ollama exited while starting (code %s)" % (
                        _ollama_proc.returncode,)
                    _log(last_err)
                    _ollama_owned = False
                    _ollama_proc = None
                    break
                time.sleep(0.35)
            else:
                if _api_up(host):
                    return True, True, "started"
                last_err = "Timed out waiting for Ollama"
                _log(last_err)
                # leave process; might still come up
        time.sleep(1.0)
    return False, bool(_ollama_proc), last_err


def _api_up(host=None):
    try:
        _ollama_get("/api/tags", host=host, timeout=3)
        return True
    except Exception as e:
        _log("api down: %s" % e)
        return False


def stop_owned_ollama():
    """Stop Ollama only if Coin Tray started it.

    On Windows we leave the server running by default — killing it made the
    next launch unreliable. Call with force via env COIN_TRAY_STOP_OLLAMA=1.
    """
    global _ollama_proc, _ollama_owned
    if not _ollama_owned or _ollama_proc is None:
        return
    if os.environ.get("COIN_TRAY_STOP_OLLAMA", "").strip() not in ("1", "true", "yes"):
        _log("leaving ollama running (set COIN_TRAY_STOP_OLLAMA=1 to kill on exit)")
        _ollama_proc = None
        _ollama_owned = False
        return
    pid = _ollama_proc.pid
    _log("stopping owned ollama pid=%s" % pid)
    try:
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags, check=False)
        else:
            _ollama_proc.terminate()
            try:
                _ollama_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _ollama_proc.kill()
    except Exception as e:
        _log("stop failed: %s" % e)
    _ollama_proc = None
    _ollama_owned = False


def ollama(model, prompt, images=None, host=None, timeout=900,
           num_predict=None, num_ctx=None):
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = images
    # Vision ID JSON is short; keep ctx smaller for speed. Pricing needs more room.
    if num_ctx is None:
        num_ctx = 2048 if images else 4096
    if num_predict is None:
        num_predict = 640 if images else 900
    body = json.dumps({
        "model": model,
        "messages": [msg],
        "stream": False,
        "format": "json",
        "keep_alive": "30m",
        "options": {
            "temperature": 0.1,
            "num_ctx": int(num_ctx),
            "num_predict": int(num_predict),
        },
    }).encode()
    req = urllib.request.Request((host or OLLAMA).rstrip("/") + "/api/chat",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with _NO_PROXY.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            detail = str(e.reason)
        raise OllamaDown("Ollama HTTP %s for %s: %s" % (e.code, model, detail))
    except urllib.error.URLError as e:
        raise OllamaDown("Ollama isn't answering at %s (%s). Is it running?"
                         % (host or OLLAMA, e.reason))
    except TimeoutError:
        raise OllamaDown("Ollama timed out. The model may still be loading — try again.")
    except Exception as e:
        raise OllamaDown("Ollama request failed: %s" % e)


def loads(raw):
    try:
        return json.loads(raw)
    except Exception:
        pass
    t = raw.strip().strip("`")
    i = t.find("{")
    if i < 0:
        return None
    t = t[i:]
    for end in range(len(t), 0, -1):
        if t[end - 1] != "}":
            continue
        try:
            return json.loads(t[:end])
        except Exception:
            continue
    return None


READ_PROMPT = """You are looking at one face of a coin: the {side}.

Report only what you can actually see. Prefer reading the lettering on the coin.
If something is worn away or unreadable, say "unclear" rather than inventing it.

Do NOT assume the coin is from the United States. Read the country or kingdom name
on the coin when visible (UNITED STATES, GEORGIVS / EDWARDVS, REPUBLIQUE FRANCAISE,
DEUTSCHES REICH, DEUTSCHE DEMOKRATISCHE REPUBLIK / DDR (East Germany, historic),
REPUBLIK OSTERREICH, CCCP / SSSR / USSR, HELVETIA, BELGIQUE, PORTUGUESA, MAGYAR,
SVERIGE, NORGE, DANMARK, EIRE, KONINGRIJK DER NEDERLANDEN, REGNO D ITALIA,
ESPANA / PESETAS, CANADA / maple leaf, HELLENIC REPUBLIC / HELLAS /
ΒΑΣΙΛΕΙΟΝ ΤΗΣ ΕΛΛΑΔΟΣ / DRACHMAI / LEPTA (Greece), etc.). Keep Cyrillic or Greek
letters if that is what you see (СССР, КОПЕЕК, ΔΡΑΧΜΑΙ) — do not invent Latin USA
text instead.
Historic countries still count: DDR (East Germany), USSR, Czechoslovakia — report
those names even though the states no longer exist.

US coins — do not confuse these:
- Franklin D. Roosevelt + torch between olive and oak branches = ONE DIME.
- George Washington = QUARTER DOLLAR (2022+ often facing right).
- Thomas Jefferson + Monticello = FIVE CENTS.
- Abraham Lincoln = ONE CENT (penny).
- Never invent denomination text that is not readable.

European / world — name legends and reverse carefully:
- CANADA + maple leaf (or beaver / caribou / loon) = Canada — not a US cent/dime.
  DEI GRATIA REGINA with CANADA is Canadian; DEI GRA / BRITT / Britannia is UK.
- Britannia, GEORGIVS, DEI GRA / BRITT = United Kingdom.
- Marianne / Liberte / Republique Francaise = France.
- Deutsches Reich / Kaiserreich = Germany (Empire / pre-DDR).
- DEUTSCHE DEMOKRATISCHE REPUBLIK / DDR / hammer+compass emblem = East Germany (historic).
- Republik Osterreich / Osterreich / OHNE KRIEGE + GROSCHEN or SCHILLING = Austria.
- CCCP / SSSR / hammer and sickle + KOPEEK / KOPEKS = Soviet Union (USSR).
- HELVETIA = Switzerland. BELGIQUE / BELGIE = Belgium. PORTUGUESA = Portugal.
- MAGYAR = Hungary. SVERIGE / NORGE / DANMARK = Scandinavia. EIRE = Ireland.
- HELLENIC REPUBLIC / HELLAS / Greece + DRACHMA / DRACHMAI / LEPTA = Greece.
- Name the reverse object: torch, Monticello, Britannia, maple leaf, eagle, globe, crown, etc.
- CRITICAL: If either face shows a non-US country (CANADA, DDR, Osterreich, CCCP,
  KOPEEK, Helvetia, Georgivs, Republique, Peseta, Hellenic, Drachma, etc.), do NOT
  invent UNITED STATES, Liberty torch, Monticello, or Lincoln on the other face.
  A large numeral alone is not a US dime.

Pay special attention to:
- Country / kingdom / republic lettering (including Cyrillic)
- Value text (ONE DIME, 15 KOPEEK, 1 FRANC, 10 GROSCHEN, PESETAS, etc.)
- The four-digit date and any mint mark

Reply with JSON only:
{{"legends": "every word and letter you can read, in order",
  "date": "the four digit year, or unclear",
  "mintMark": "the small letter beside the date or design, or none, or unclear",
  "design": "what is pictured, in one sentence (name the person/building if clear)",
  "denomination": "value text if visible, or unclear",
  "metal": "colour and appearance: copper brown, silver grey, nickel grey, brass yellow, etc",
  "wear": "how worn the high points look, one short phrase",
  "damage": "scratches, cleaning marks, holes, bends, or none"}}"""

VALUE_PROMPT = """You are pricing a coin from marketplace and price-guide source material.

The coin, as confirmed by its owner:
{attribution}

Source material, each block from the URL shown (may include eBay sold/asking, MA-Shops, Numista):

{docs}

Use only prices supported by the source material. Do not invent figures.
For United States coins, prefer PCGS Price Guide figures when present (dealer asking
prices for PCGS-graded coins). Note in notes that raw/uncertified circulated coins
usually sell for less than PCGS grades.
For world coins, prefer recent sold or asking prices (eBay, dealers).
Convert EUR/GBP amounts to approximate USD in valueLow/valueHigh.
Match the grade estimate above to the closest grade the sources price.
In each comparable, copy the source URL and note the condition/grade that source
implies (VF, XF, AU, MS60, circulated, etc.) when the text says it.
Common copper/aluminum world coins often sell for under $5 — still report that range, do not use 0.
Only set both values to 0 if the sources truly show no prices at all.

Reply with JSON only:
{{"identification": "short catalogue name",
  "valueLow": 0, "valueHigh": 0, "currency": "USD",
  "grade": "the grade these values correspond to (must match source conditions)",
  "melt": "intrinsic metal value if silver or gold, else empty string",
  "comparables": [{{"what": "", "price": "", "where": "domain name", "url": "",
                   "condition": "grade/condition from that source or empty"}}],
  "checks": ["things to verify in hand that would change the value a lot"],
  "notes": "under 40 words; mention which conditions the range covers"}}

Include every source URL that supports the range (up to 12 comparables). Limit checks to 3."""

LOOK_PROMPT = """You are looking at coin photo(s). The date and lettering may be worn away
or missing. Identify it from the DESIGN and metal colour only.

Guess the most likely country and type. Prefer common circulated pieces people find
in mixed boxes. Do not invent a four-digit date if you cannot see one.

Reply with JSON only:
{"countryGuess": "country or historic state, or unclear",
 "denominationGuess": "likely value text, or unclear",
 "seriesGuess": "likely series/type name, or unclear",
 "era": "rough period e.g. mid-20th century, or unclear",
 "searchTerms": ["4 to 6 short web search phrases to find sold prices"],
 "confidence": "low or medium or high",
 "notes": "under 30 words"}"""


def build_queries(a):
    year = normalize_year(a.get("year"))
    mint = (a.get("mintMark") or "").strip()
    if mint.lower() in ("unclear", "none", "n/a", "na"):
        mint = ""
    series = (a.get("series") or "").strip()
    denom = (a.get("denomination") or "").strip()
    country = (a.get("country") or "").strip()
    # Always keep country for foreign / silver searches
    core = " ".join(x for x in [year, mint, country, series or denom]
                    if x and x.lower() not in ("unclear", "none", "n/a"))
    core = re.sub(r"\s+", " ", core).strip()
    # Extra design / look-assist terms (undated or worn pieces)
    extras = []
    for t in (a.get("searchTerms") or []):
        t = re.sub(r"\s+", " ", str(t)).strip()
        if t:
            extras.append(t if "coin" in t.lower() else t + " coin value")
    design = design_keywords(a.get("observed"))
    if design and not year:
        extras.append(design + " coin value sold")
        extras.append(design + " coin worth")
    if not core and not extras:
        return []
    short = " ".join(x for x in [year, country, series or denom]
                     if x and x.lower() not in ("unclear", "none", "n/a"))
    foreign = country and not _is_united_states(a)
    bits = list(extras)
    if not core:
        # Design-only / look-assist path
        bits.append((short or design or "world coin") + " sold site:ebay.com")
        out, seen = [], set()
        for q in bits:
            q = re.sub(r"\s+", " ", q).strip()
            if q and q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
            if len(out) >= 7:
                break
        return out
    if foreign:
        bits = extras + [
            f"{short} coin sold site:ebay.com",
            f"{short} coin value price guide",
            f"{short} sold site:ebay.de",
            f"{short} coin value site:numista.com",
            f"{short} site:ma-shops.com",
        ]
        c_l = country.lower()
        d_l = (denom or series or "").lower()
        if "spain" in c_l or "peseta" in d_l:
            bits.append(f"{year} {denom or 'peseta'} valor precio".strip())
        elif "france" in c_l or "franc" in d_l:
            bits.append(f"{year} {denom or 'franc'} valeur prix".strip())
        elif ("east germany" in c_l or "ddr" in c_l or "gdr" in c_l
              or "demokratische" in (series or "").lower()):
            bits.append(f"{year} {denom or 'mark'} ddr east germany value".strip())
            bits.append(f"{year} {denom or 'mark'} ddr wert preis".strip())
        elif "germany" in c_l or "mark" in d_l or "pfennig" in d_l:
            bits.append(f"{year} {denom or 'pfennig'} wert preis ebay".strip())
        elif ("austria" in c_l or "österreich" in c_l or "osterreich" in c_l
              or "groschen" in d_l or "schilling" in d_l):
            bits.append(f"{year} {denom or 'groschen'} osterreich wert preis".strip())
        elif ("soviet" in c_l or "ussr" in c_l or "cccp" in c_l
              or "kopek" in d_l or "kopeek" in d_l):
            bits.append(f"{year} {denom or 'kopek'} ussr cccp value".strip())
            bits.append(f"{year} {denom or 'kopeek'} цена".strip())
        elif "switzerland" in c_l or "helvetia" in d_l or "rappen" in d_l:
            bits.append(f"{year} {denom or 'franc'} helvetia wert preis".strip())
        elif "belgium" in c_l or "belgique" in c_l:
            bits.append(f"{year} {denom or 'franc'} belgique valeur".strip())
        elif "portugal" in c_l or "escudo" in d_l:
            bits.append(f"{year} {denom or 'escudo'} portuguesa valor".strip())
        elif "poland" in c_l or "zloty" in d_l or "groszy" in d_l:
            bits.append(f"{year} {denom or 'zloty'} polska value".strip())
        elif "hungary" in c_l or "forint" in d_l:
            bits.append(f"{year} {denom or 'forint'} magyar value".strip())
        elif ("greece" in c_l or "hellenic" in c_l or "hellas" in c_l
              or "drachm" in d_l or "lepta" in d_l or "lepton" in d_l):
            bits.append(f"{year} {denom or 'drachma'} greece coin".strip())
            bits.append(f"{year} {denom or 'drachmai'} greek value".strip())
        elif ("canada" in c_l or "canadian" in c_l or "loonie" in d_l
              or "toonie" in d_l or "maple" in (series or "").lower()):
            bits.append(f"{year} {denom or series or 'cent'} canada coin".strip())
            bits.append(f"{short} canadian coin value".strip())
        else:
            bits.append(f"{core} coin sold price")
    else:
        # US: PCGS Price Guide first (https://www.pcgs.com/prices/us)
        bits = extras + [
            f"{short} price guide site:pcgs.com/prices",
            f"{short} site:pcgs.com",
            f"{short} coin value price guide",
            f"{short} coin sold site:ebay.com",
            f"{core} sold auction price realized",
        ]
    bits.append(f"{core} coin worth")
    # Cap so DuckDuckGo stays snappy
    out, seen = [], set()
    for q in bits:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
        if len(out) >= 7:
            break
    return out


def normalize_year(year):
    """Blank / N/A / unclear → empty string (do not put N/A in DATE)."""
    s = str(year or "").strip()
    if s.lower() in ("", "n/a", "na", "n.a.", "none", "unclear", "unknown",
                     "?", "-", "--", "—"):
        return ""
    return s


def design_keywords(sides):
    """Short design phrase from vision notes for undated / worn coins."""
    stop = {
        "the", "a", "an", "and", "with", "of", "on", "in", "to", "for", "is",
        "unclear", "none", "coin", "design", "shows", "showing", "large",
        "small", "worn", "metal", "colour", "color", "grey", "gray", "brown",
    }
    parts = []
    for side in ("obverse", "reverse"):
        s = (sides or {}).get(side) or {}
        parts.append(str(s.get("design") or ""))
        parts.append(str(s.get("legends") or ""))
    blob = _norm_blob(" ".join(parts))
    words = [w for w in blob.split() if len(w) > 2 and w not in stop]
    # Prefer distinctive tokens
    keep = []
    for w in words:
        if w not in keep:
            keep.append(w)
        if len(keep) >= 8:
            break
    return " ".join(keep)


def needs_look_assist(a):
    """True when identity is too thin for marketplace search without another vision pass.

    After a successful Read (country + denom/series), skip look_assist even if DATE
    is blank — that second vision call is the usual PRICE/FIND SIMILAR slowdown.
    """
    year = normalize_year(a.get("year"))
    denom = (a.get("denomination") or "").strip()
    series = (a.get("series") or "").strip()
    country = (a.get("country") or "").strip()
    if country and (denom or series):
        return False
    if year and (denom or series):
        return False
    if not denom and not series:
        return True
    blob = _sides_blob(a.get("observed"))
    if len(blob) < 24:
        return True
    return False


def look_assist(model, images_b64, host=None, say=None):
    """Vision guess from design when date/markings are missing (local Ollama)."""
    imgs = [i for i in (images_b64 or []) if i][:2]
    if not imgs:
        return {}
    if say:
        say("looking up by design with %s…" % model)
    raw = ollama(model, LOOK_PROMPT, images=imgs, host=host,
                 num_predict=480, num_ctx=2048)
    data = loads(raw) or {}
    out = {
        "countryGuess": (data.get("countryGuess") or "").strip(),
        "denominationGuess": (data.get("denominationGuess") or "").strip(),
        "seriesGuess": (data.get("seriesGuess") or "").strip(),
        "era": (data.get("era") or "").strip(),
        "searchTerms": list(data.get("searchTerms") or [])[:8],
        "confidence": (data.get("confidence") or "low").strip().lower(),
        "notes": (data.get("notes") or "").strip(),
    }
    # Drop useless guesses
    for k in ("countryGuess", "denominationGuess", "seriesGuess"):
        if (out.get(k) or "").lower() in ("unclear", "none", "n/a", "unknown", "?"):
            out[k] = ""
    return out


def apply_look_assist(attribution, look):
    """Fill empty attribution fields from a look_assist result."""
    a = dict(attribution or {})
    look = look or {}
    if not (a.get("country") or "").strip() and look.get("countryGuess"):
        a["country"] = look["countryGuess"]
    if not (a.get("denomination") or "").strip() and look.get("denominationGuess"):
        a["denomination"] = look["denominationGuess"]
    if not (a.get("series") or "").strip() and look.get("seriesGuess"):
        a["series"] = look["seriesGuess"]
    terms = list(a.get("searchTerms") or [])
    for t in look.get("searchTerms") or []:
        if t and t not in terms:
            terms.append(t)
    if look.get("era") and look["era"].lower() not in ("unclear", "none"):
        terms.append("%s coin value" % look["era"])
    a["searchTerms"] = terms[:8]
    a["lookAssist"] = look
    return a


def find_similar(model, attribution, images_b64=None, host=None, sources=None, say=None):
    """In-app lookalike search: design guess + marketplace listings (no browser).

    Returns a price-shaped result with similarListings for the desktop SIMILAR COINS panel.
    """
    attribution = dict(attribution or {})
    imgs = [i for i in (images_b64 or []) if i][:2]
    look = {}
    # Only spend a vision call when country/type are still empty (not merely undated)
    if imgs and needs_look_assist(attribution):
        try:
            look = look_assist(model, imgs, host=host, say=say) or {}
            if look:
                attribution = apply_look_assist(attribution, look)
                if say and look.get("notes"):
                    say("design guess: %s" % look["notes"][:80])
        except Exception as e:
            if say:
                say("design guess skipped (%s)" % type(e).__name__)
            look = {}

    queries = build_queries(attribution)
    if not queries:
        design = design_keywords(attribution.get("observed"))
        if design:
            queries = [
                design + " coin sold site:ebay.com",
                design + " coin value",
                design + " site:numista.com",
            ]
        for t in attribution.get("searchTerms") or []:
            t = re.sub(r"\s+", " ", str(t)).strip()
            if t:
                queries.append(t if "coin" in t.lower() else t + " coin value")
    # Dedupe
    seen_q, clean_q = set(), []
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen_q:
            seen_q.add(q.lower())
            clean_q.append(q)
        if len(clean_q) >= 7:
            break
    queries = clean_q
    if not queries:
        return {"error": "Need a photo or country/type to find similar coins."}

    if say:
        say("searching similar listings…")
    docs = gather(queries, max_pages=8, sources=sources, say=say)
    # Direct marketplace pass when web search engines return nothing
    if len(docs) < 3:
        if say:
            say("trying Delcampe marketplace…")
        seen_u = {d.get("url") for d in docs}
        for q in short_market_queries(attribution, queries):
            for h in search_delcampe(q, limit=6, sources=sources):
                if h["url"] in seen_u:
                    continue
                seen_u.add(h["url"])
                docs.append({
                    "url": h["url"],
                    "title": h.get("title") or "",
                    "snippet": h.get("snippet") or "",
                })
            if len(docs) >= 8:
                break
    listings = similar_listings_from_docs(docs, limit=8)
    market = market_estimate_from_docs(docs, attribution)
    auth = authenticity_for_attribution(attribution)

    if market and (market.get("valueHigh") or 0) > 0:
        out = dict(market)
    else:
        tip = typical_circulated_fallback(attribution, auth)
        if tip:
            out = tip
            if say:
                say("typical common range (few live listings)")
        elif market:
            out = dict(market)
        else:
            label_bits = [
                attribution.get("year") or "",
                attribution.get("country") or "",
                attribution.get("series") or attribution.get("denomination") or "coin",
            ]
            out = {
                "identification": " ".join(x for x in label_bits if x).strip() or "Coin",
                "valueLow": 0,
                "valueHigh": 0,
                "currency": "USD",
                "grade": attribution.get("grade") or "circulated",
                "melt": "",
                "comparables": [],
                "checks": [],
                "notes": ("Similar listing search finished. "
                          "Open a row below to check the price on that site."),
                "faceValue": False,
                "marketSold": False,
                "sources": [d.get("url") for d in docs if d.get("url")][:8],
            }

    if listings:
        out["similarListings"] = listings
    out["faceValue"] = False
    if not out.get("tier"):
        out["tier"] = value_tier(out)
    if look:
        out["lookAssist"] = look
        out["lookFilled"] = {
            "country": attribution.get("country") or "",
            "denomination": attribution.get("denomination") or "",
            "series": attribution.get("series") or "",
        }
    if auth:
        if auth.get("specs"):
            out.setdefault("specs", auth["specs"])
        if auth.get("authChecks"):
            out.setdefault("authChecks", auth["authChecks"])
    # Soft success: typical range and/or design guess still useful when scrapes are empty
    if not listings and not docs:
        tip = typical_circulated_fallback(attribution, auth)
        if tip:
            tip["lookAssist"] = look or tip.get("lookAssist")
            if look:
                tip["lookFilled"] = out.get("lookFilled")
            if say:
                say("no live listings — typical range for this type")
            return attach_citations(tip, docs, listings=listings, attribution=attribution)
        return {"error": "No similar listings found. Try READ first or fix country/type."}
    if say:
        say("found %d similar listing(s)" % len(listings))
    return attach_citations(out, docs, listings=listings, attribution=attribution)


def _year_int(a):
    # Include 16xx–18xx (Spanish / European silver) as well as 19xx–20xx
    s = normalize_year(a.get("year") if isinstance(a, dict) else a)
    m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", s)
    return int(m.group(0)) if m else None


def _is_united_states(a):
    c = (a.get("country") or "").strip().lower()
    if not c:
        # US type series without country still counts as US for face value
        series = (a.get("series") or "").lower()
        return any(x in series for x in (
            "lincoln", "jefferson", "roosevelt", "washington", "kennedy",
            "buffalo", "mercury"))
    return c in ("united states", "usa", "us", "u.s.", "u.s.a.", "america")


def _coin_kind(a):
    """Return nickel|cent|dime|quarter|half|None from series/denomination text."""
    series = (a.get("series") or "").lower()
    denom = (a.get("denomination") or "").lower()
    if ("nickel" in series or "nickel" in denom
            or "five cent" in denom or denom.startswith("five")):
        return "nickel"
    if "dime" in series or "dime" in denom:
        return "dime"
    if "quarter" in series or "quarter" in denom:
        return "quarter"
    if "half" in series or "half" in denom:
        return "half"
    if ("cent" in series or "cent" in denom or "penny" in denom
            or "lincoln" in series):
        return "cent"
    return None


def face_value_dollars(a):
    """Exact face value for ordinary modern US coins, else None (needs research)."""
    if not _is_united_states(a):
        return None
    year = _year_int(a)
    series = (a.get("series") or "").lower()
    kind = _coin_kind(a)
    if kind == "nickel":
        if year and 1942 <= year <= 1945:
            return None
        return 0.05
    if kind == "cent":
        if "wheat" in series or (year and year < 1959):
            return None
        return 0.01
    if kind == "dime":
        if year and year < 1965:
            return None
        return 0.10
    if kind == "quarter":
        if year and year < 1965:
            return None
        return 0.25
    if kind == "half":
        if year and year < 1971:
            return None
        return 0.50
    return None


def value_tier(entry):
    """C common / U uncommon / R rare / G gem from face flag or mid value."""
    if entry.get("faceValue"):
        return "C"
    try:
        lo = float(entry.get("valueLow") or 0)
        hi = float(entry.get("valueHigh") or 0)
    except (TypeError, ValueError):
        return "C"
    if lo <= 0 and hi <= 0:
        return "C"
    mid = (lo + hi) / 2.0 if hi else lo
    if mid < 1:
        return "C"
    if mid < 25:
        return "U"
    if mid < 250:
        return "R"
    return "G"


TIER_NAMES = {"C": "Common", "U": "Uncommon", "R": "Rare", "G": "Gem"}


def common_circulated_fallback(a):
    """Price everyday modern US coins at exact face value.

    Keeps commons visually distinct from researched / premium pieces in the tray.
    """
    face = face_value_dollars(a)
    if face is None:
        return None
    year = _year_int(a)
    mint = (a.get("mintMark") or "").strip()
    mint = "" if mint.lower() in ("", "none", "unclear", "p", "n/a") else mint
    label_bits = [str(year) if year else "", mint,
                  a.get("series") or a.get("denomination") or "US coin"]
    label = " ".join(x for x in label_bits if x).strip()
    out = {
        "identification": label or "US coin",
        "valueLow": face,
        "valueHigh": face,
        "currency": "USD",
        "grade": a.get("grade") or "circulated",
        "melt": "",
        "comparables": [],
        "checks": [
            "Errors, varieties, and brilliant uncirculated pieces can be worth more.",
        ],
        "notes": ("Common piece - listed at face value so it stands apart from "
                  "scarcer coins in your tray."),
        "faceValue": True,
        "sources": [],
    }
    out["tier"] = value_tier(out)
    return attach_pcgs_guide(out, a)


def typical_circulated_fallback(a, auth=None):
    """Typical sold/asking band for known common world types when web search fails.

    Not a live quote — a ballpark so the tray does not show an empty Common ---.
    """
    entry = lookup_type_entry(
        a.get("country") or "", a.get("series") or "", a.get("denomination") or "")
    tip = None
    if entry and entry.get("typical_usd"):
        tip = entry["typical_usd"]
    elif not _is_united_states(a):
        metal = ""
        if entry and entry.get("specs"):
            metal = (entry["specs"].get("metal") or "").lower()
        if metal in ("aluminum", "aluminium", "copper", "zinc", "bronze",
                     "brass", "copper-nickel", "cupronickel"):
            tip = (0.25, 2.50)
    if not tip:
        return None
    lo, hi = float(tip[0]), float(tip[1])
    year = _year_int(a)
    label_bits = [str(year) if year else "", a.get("country") or "",
                  a.get("series") or a.get("denomination") or "coin"]
    label = " ".join(x for x in label_bits if x).strip()
    out = {
        "identification": label or "Coin",
        "valueLow": lo,
        "valueHigh": hi,
        "currency": "USD",
        "grade": a.get("grade") or "circulated",
        "melt": "",
        "comparables": [],
        "checks": [
            "Ballpark from common sold listings - check eBay sold for this exact date.",
        ],
        "notes": ("Typical sold/asking range for common circulated examples "
                  "(about $%.2f-$%.2f). Not a live quote - verify on eBay sold."
                  % (lo, hi)),
        "faceValue": False,
        "marketSold": True,
        "typicalEstimate": True,
        "sources": [],
        "researchNeeded": False,
    }
    if auth and auth.get("specs"):
        out["specs"] = auth["specs"]
    if auth and auth.get("authChecks"):
        out["authChecks"] = auth["authChecks"]
        out["checks"] = list(auth["authChecks"])[:6] + out["checks"]
    out["tier"] = value_tier(out)
    return out


def attach_pcgs_guide(out, a):
    """Stamp the matching PCGS Price Guide URL onto a price result (US only)."""
    url = pcgs_price_guide_url(a)
    if not url or not out:
        return out
    out["pcgsGuideUrl"] = url
    srcs = list(out.get("sources") or [])
    if url not in srcs:
        srcs.insert(0, url)
    out["sources"] = srcs
    return out


# Offline cue catalog: distinctive design beats hallucinated value text.
# strong = almost decisive; cues = supporting; weak = denom words alone (not enough);
# block = veto this type when present.
COMMON_US_COINS = [
    {
        "series": "Roosevelt Dime",
        "denomination": "One dime",
        "country": "United States",
        "strong": [
            "roosevelt",
            ("torch", "olive"),
            ("torch", "oak"),
            ("torch", "branch"),
            ("olive", "oak"),
            "olive branch",
            "oak branch",
        ],
        "cues": ["torch", "one dime", "olive", "oak"],
        "weak": ["dime"],
        "block": ["monticello", "jefferson", "lincoln memorial", "wheat cent"],
    },
    {
        "series": "Mercury Dime",
        "denomination": "One dime",
        "country": "United States",
        "strong": ["mercury", "fasces", "winged liberty", "winged head"],
        "cues": ["one dime"],
        "weak": ["dime"],
        "block": ["roosevelt", "torch", "monticello", "washington"],
    },
    {
        "series": "Lincoln Cent",
        "denomination": "One cent",
        "country": "United States",
        "strong": [
            "lincoln", "wheat", "memorial", "penny", "one cent",
            "lincoln memorial", "wheat ears",
        ],
        "cues": ["copper", "bronze", "brown"],
        "weak": ["cent"],
        "block": ["jefferson", "monticello", "roosevelt", "washington", "torch"],
    },
    {
        "series": "Jefferson Nickel",
        "denomination": "Five cents",
        "country": "United States",
        "strong": ["jefferson", "monticello"],
        "cues": ["five cents", "five cent"],
        "weak": ["nickel"],
        "block": ["washington", "lincoln", "roosevelt", "torch", "quarter dollar"],
    },
    {
        "series": "Buffalo Nickel",
        "denomination": "Five cents",
        "country": "United States",
        "strong": ["buffalo", "indian head", "indian-head"],
        "cues": ["five cents", "bison"],
        "weak": ["nickel"],
        "block": ["jefferson", "monticello", "roosevelt", "washington"],
    },
    {
        "series": "Washington Quarter",
        "denomination": "Quarter dollar",
        "country": "United States",
        "strong": [
            "washington", "george washington", "quarter dollar",
            "american women", "crossing the delaware", "facing right",
        ],
        "cues": [],
        "weak": ["quarter"],
        "block": ["roosevelt", "torch", "olive", "oak", "monticello", "lincoln"],
    },
    {
        "series": "Kennedy Half Dollar",
        "denomination": "Half dollar",
        "country": "United States",
        "strong": ["kennedy", "half dollar"],
        "cues": ["half-dollar"],
        "weak": ["half"],
        "block": ["washington", "roosevelt", "lincoln", "monticello"],
    },
]

# European / UK types common in mixed boxes (early 1900s silver & bronze).
# specs are typical; varieties exist — always verify in hand.
COMMON_EU_COINS = [
    {
        "series": "UK Penny",
        "denomination": "One penny",
        "country": "United Kingdom",
        "strong": ["britannia", "one penny", ("georgivs", "penny"), ("edwardvs", "penny")],
        "cues": ["georgivs", "edwardvs", "dei gra", "britt"],
        "weak": ["penny"],
        "block": ["lincoln", "united states", "franc", "mark"],
        "specs": {"diameter_mm": 30.8, "weight_g": 9.4, "metal": "bronze",
                  "edge": "plain"},
    },
    {
        "series": "UK Halfpenny",
        "denomination": "Half penny",
        "country": "United Kingdom",
        "strong": ["half penny", "halfpenny", ("britannia", "half")],
        "cues": ["britannia", "georgivs", "edwardvs"],
        "weak": [],
        "block": ["one penny", "lincoln", "franc"],
        "specs": {"diameter_mm": 25.5, "weight_g": 5.7, "metal": "bronze",
                  "edge": "plain"},
    },
    {
        "series": "UK Shilling",
        "denomination": "One shilling",
        "country": "United Kingdom",
        "strong": ["one shilling", "shilling", ("georgivs", "shilling")],
        "cues": ["georgivs", "edwardvs", "dei gra", "fid def"],
        "weak": [],
        "block": ["florin", "five shillings", "lincoln", "franc"],
        "specs": {"diameter_mm": 23.6, "weight_g": 5.65, "metal": "silver",
                  "edge": "reeded", "note": "Pre-1920 usually .925; 1920-1946 often .500"},
    },
    {
        "series": "UK Florin",
        "denomination": "Two shillings",
        "country": "United Kingdom",
        "strong": ["one florin", "florin", "two shillings"],
        "cues": ["georgivs", "edwardvs", "dei gra"],
        "weak": [],
        "block": ["one shilling", "lincoln", "franc"],
        "specs": {"diameter_mm": 28.5, "weight_g": 11.3, "metal": "silver",
                  "edge": "reeded"},
    },
    {
        "series": "UK Crown",
        "denomination": "Crown",
        "country": "United Kingdom",
        "strong": ["five shillings", "one crown", ("st george", "dragon")],
        "cues": ["st george", "georgivs", "edwardvs"],
        "weak": ["crown"],
        "block": ["one shilling", "one penny", "lincoln", "washington"],
        "specs": {"diameter_mm": 38.6, "weight_g": 28.3, "metal": "silver",
                  "edge": "reeded"},
    },
    {
        "series": "French Franc",
        "denomination": "1 franc",
        "country": "France",
        "strong": [
            "republique francaise", "republique française", "1 franc", "un franc",
            "marianne",
        ],
        "cues": ["liberte", "egalite", "fraternite", "franc"],
        "weak": [],
        "block": ["georgivs", "deutsches", "lincoln", "united states"],
        "specs": {"diameter_mm": 23.0, "weight_g": 5.0, "metal": "silver",
                  "edge": "reeded", "note": "Third Republic silver francs ~0.835 fine"},
    },
    {
        "series": "French 50 Centimes",
        "denomination": "50 centimes",
        "country": "France",
        "strong": ["50 centimes", "cinquante centimes"],
        "cues": ["republique francaise", "marianne", "liberte"],
        "weak": ["centimes"],
        "block": ["georgivs", "lincoln", "deutsches"],
        "specs": {"diameter_mm": 18.0, "weight_g": 2.5, "metal": "silver",
                  "edge": "reeded"},
    },
    {
        "series": "German Mark",
        "denomination": "1 mark",
        "country": "Germany",
        "strong": ["deutsches reich", "1 mark", "eine mark", "kaiserreich"],
        "cues": ["reich", "mark"],
        "weak": [],
        "block": ["georgivs", "francaise", "lincoln", "united states", "ddr",
                  "demokratische"],
        "specs": {"diameter_mm": 24.0, "weight_g": 5.5, "metal": "silver",
                  "edge": "reeded", "note": "Empire silver marks ~0.900 fine"},
    },
    {
        "series": "German Pfennig",
        "denomination": "Pfennig",
        "country": "Germany",
        "strong": ["pfennig", "deutsches reich"],
        "cues": ["reich"],
        "weak": [],
        "block": ["1 mark", "eine mark", "lincoln", "ddr", "demokratische"],
        "specs": {"diameter_mm": 21.5, "weight_g": 2.0, "metal": "copper",
                  "edge": "plain"},
        # Typical eBay sold/asking for common circulated (USD approx)
        "typical_usd": (0.50, 3.00),
    },
    # East Germany (DDR) — historic state; still identify as East Germany
    {
        "series": "DDR 5 Mark",
        "denomination": "5 mark",
        "country": "East Germany",
        "strong": [
            "5 mark", "xx jahre ddr", "jahre ddr",
            ("ddr", "5"), ("demokratische", "5"), ("ddr", "mark"),
            "deutsche demokratische republik",
        ],
        "cues": ["ddr", "demokratische", "republik", "mark", "compass", "hammer"],
        "weak": [],
        "block": ["deutsches reich", "kaiserreich", "lincoln", "united states",
                  "kopeek", "cccp"],
        "specs": {"diameter_mm": 29.0, "weight_g": 9.7, "metal": "copper-nickel",
                  "edge": "reeded",
                  "note": "GDR / East Germany commemorative and circulation 5 Mark"},
        "typical_usd": (1.00, 8.00),
    },
    {
        "series": "DDR 1 Mark",
        "denomination": "1 mark",
        "country": "East Germany",
        "strong": [
            "1 mark", ("ddr", "1"), ("demokratische", "mark"),
            "deutsche demokratische republik",
        ],
        "cues": ["ddr", "demokratische", "mark"],
        "weak": [],
        "block": ["5 mark", "deutsches reich", "lincoln", "cccp", "kopeek"],
        "specs": {"diameter_mm": 25.0, "weight_g": 7.0, "metal": "aluminum",
                  "edge": "reeded"},
        "typical_usd": (0.50, 3.00),
    },
    {
        "series": "DDR 10 Pfennig",
        "denomination": "10 pfennig",
        "country": "East Germany",
        "strong": ["10 pfennig", ("ddr", "pfennig"), ("ddr", "10")],
        "cues": ["ddr", "demokratische", "pfennig"],
        "weak": [],
        "block": ["deutsches reich", "lincoln", "cccp"],
        "specs": {"diameter_mm": 21.0, "weight_g": 1.5, "metal": "aluminum",
                  "edge": "plain"},
        "typical_usd": (0.25, 1.50),
    },
    {
        "series": "DDR 20 Pfennig",
        "denomination": "20 pfennig",
        "country": "East Germany",
        "strong": ["20 pfennig", ("ddr", "20")],
        "cues": ["ddr", "demokratische", "pfennig"],
        "weak": [],
        "block": ["deutsches reich", "lincoln", "cccp"],
        "specs": {"diameter_mm": 22.2, "weight_g": 1.8, "metal": "brass",
                  "edge": "plain"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Dutch Guilder",
        "denomination": "1 gulden",
        "country": "Netherlands",
        "strong": ["koninkrijk der nederlanden", "koningrijk", "1 gulden", "gulden"],
        "cues": ["nederlanden", "wilhelmina"],
        "weak": [],
        "block": ["georgivs", "francaise", "lincoln"],
        "specs": {"diameter_mm": 28.0, "weight_g": 10.0, "metal": "silver",
                  "edge": "reeded"},
    },
    {
        "series": "Italian Lira",
        "denomination": "1 lira",
        "country": "Italy",
        "strong": ["regno d italia", "regno d'italia", "1 lira", "una lira"],
        "cues": ["italia", "vittorio"],
        "weak": ["lira"],
        "block": ["georgivs", "francaise", "lincoln"],
        "specs": {"diameter_mm": 23.0, "weight_g": 5.0, "metal": "silver",
                  "edge": "reeded"},
    },
    {
        "series": "Spanish Peseta",
        "denomination": "1 peseta",
        "country": "Spain",
        "strong": ["una peseta", "1 peseta", ("espana", "peseta"), "alfonso"],
        "cues": ["espana", "españa", "reino", "peseta"],
        "weak": [],
        "block": ["50 pesetas", "georgivs", "lincoln", "francaise"],
        "specs": {"diameter_mm": 23.0, "weight_g": 5.0, "metal": "silver",
                  "edge": "reeded"},
    },
    {
        "series": "Spanish 50 Pesetas",
        "denomination": "50 pesetas",
        "country": "Spain",
        "strong": ["50 pesetas", "cincuenta pesetas", ("50", "pesetas")],
        "cues": ["espana", "pesetas"],
        "weak": [],
        "block": ["una peseta", "1 peseta", "lincoln", "georgivs"],
        "specs": {"diameter_mm": 30.0, "weight_g": 12.0, "metal": "silver",
                  "edge": "reeded"},
    },
    {
        "series": "Austrian 10 Groschen",
        "denomination": "10 groschen",
        "country": "Austria",
        "strong": [
            "10 groschen",
            "ohne kriege",
            ("ohne", "kriege"),
            ("osterreich", "groschen"),
            ("republik", "groschen"),
            ("osterreich", "10"),
            ("kriege", "10"),
        ],
        "cues": ["osterreich", "republik osterreich", "groschen", "kriege"],
        "weak": [],
        "block": ["lincoln", "roosevelt", "torch", "georgivs", "peseta", "franc"],
        "specs": {"diameter_mm": 20.0, "weight_g": 1.1, "metal": "aluminum",
                  "edge": "plain",
                  "note": "Second Republic aluminum 10 Groschen; common circulated"},
        "typical_usd": (0.25, 1.50),
    },
    {
        "series": "Austrian 5 Groschen",
        "denomination": "5 groschen",
        "country": "Austria",
        "strong": ["5 groschen", ("osterreich", "5 groschen")],
        "cues": ["osterreich", "groschen"],
        "weak": [],
        "block": ["10 groschen", "lincoln", "united states"],
        "specs": {"diameter_mm": 19.0, "weight_g": 2.5, "metal": "zinc",
                  "edge": "plain"},
        "typical_usd": (0.25, 1.00),
    },
    {
        "series": "Austrian Schilling",
        "denomination": "1 schilling",
        "country": "Austria",
        "strong": [
            "1 schilling", "ein schilling", "schilling",
            ("osterreich", "schilling"),
        ],
        "cues": ["osterreich", "republik osterreich"],
        "weak": [],
        "block": ["groschen", "lincoln", "united states", "georgivs"],
        "specs": {"diameter_mm": 22.5, "weight_g": 4.2, "metal": "aluminum-bronze",
                  "edge": "reeded"},
        "typical_usd": (0.50, 3.00),
    },
    # --- Soviet Union (CCCP) kopeks — very common in mixed European boxes ---
    {
        "series": "Soviet 15 Kopeks",
        "denomination": "15 kopeks",
        "country": "Soviet Union",
        "strong": [
            "15 kopeks", "15 kopeek", "15 kopek",
            ("cccp", "15"), ("sssr", "15"), ("kopeek", "15"), ("kopek", "15"),
        ],
        "cues": ["cccp", "sssr", "ussr", "kopeek", "kopek", "kopeks",
                 "hammer", "sickle"],
        "weak": [],
        "block": ["lincoln", "roosevelt", "torch", "united states", "groschen",
                  "ddr", "demokratische"],
        "specs": {"diameter_mm": 23.5, "weight_g": 2.5, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Soviet 10 Kopeks",
        "denomination": "10 kopeks",
        "country": "Soviet Union",
        "strong": [
            "10 kopeks", "10 kopeek", "10 kopek",
            ("cccp", "10"), ("sssr", "10"), ("kopeek", "10"),
        ],
        "cues": ["cccp", "sssr", "kopeek", "kopek", "hammer", "sickle"],
        "weak": [],
        "block": ["15 kopek", "15 kopeek", "lincoln", "united states"],
        "specs": {"diameter_mm": 17.3, "weight_g": 1.6, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 1.50),
    },
    {
        "series": "Soviet 20 Kopeks",
        "denomination": "20 kopeks",
        "country": "Soviet Union",
        "strong": [
            "20 kopeks", "20 kopeek", "20 kopek",
            ("cccp", "20"), ("sssr", "20"), ("kopeek", "20"),
        ],
        "cues": ["cccp", "sssr", "kopeek", "kopek"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 22.0, "weight_g": 3.4, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Soviet 5 Kopeks",
        "denomination": "5 kopeks",
        "country": "Soviet Union",
        "strong": [
            "5 kopeks", "5 kopeek", "5 kopek",
            ("cccp", "5"), ("sssr", "5"), ("kopeek", "5"),
        ],
        "cues": ["cccp", "sssr", "kopeek", "kopek"],
        "weak": [],
        "block": ["15 kopek", "lincoln", "united states"],
        "specs": {"diameter_mm": 25.0, "weight_g": 5.0, "metal": "brass",
                  "edge": "reeded"},
        "typical_usd": (0.25, 1.50),
    },
    {
        "series": "Soviet 3 Kopeks",
        "denomination": "3 kopeks",
        "country": "Soviet Union",
        "strong": ["3 kopeks", "3 kopeek", "3 kopek", ("cccp", "3"), ("kopeek", "3")],
        "cues": ["cccp", "sssr", "kopeek", "kopek"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 22.0, "weight_g": 3.0, "metal": "brass",
                  "edge": "reeded"},
        "typical_usd": (0.25, 1.50),
    },
    {
        "series": "Soviet 2 Kopeks",
        "denomination": "2 kopeks",
        "country": "Soviet Union",
        "strong": ["2 kopeks", "2 kopeek", "2 kopek", ("cccp", "2"), ("kopeek", "2")],
        "cues": ["cccp", "sssr", "kopeek", "kopek"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 18.0, "weight_g": 2.0, "metal": "brass",
                  "edge": "reeded"},
        "typical_usd": (0.25, 1.25),
    },
    {
        "series": "Soviet 1 Kopek",
        "denomination": "1 kopek",
        "country": "Soviet Union",
        "strong": ["1 kopek", "1 kopeek", "one kopek", ("cccp", "1"), ("kopeek", "1")],
        "cues": ["cccp", "sssr", "kopeek", "kopek"],
        "weak": [],
        "block": ["lincoln", "united states", "ruble", "rouble"],
        "specs": {"diameter_mm": 15.0, "weight_g": 1.0, "metal": "brass",
                  "edge": "reeded"},
        "typical_usd": (0.25, 1.00),
    },
    {
        "series": "Soviet 1 Ruble",
        "denomination": "1 ruble",
        "country": "Soviet Union",
        "strong": [
            "1 ruble", "1 rouble", "one ruble", "один рубль",
            ("cccp", "ruble"), ("sssr", "ruble"), ("cccp", "rouble"),
            ("рубль", "1"),
        ],
        "cues": ["cccp", "sssr", "ruble", "rouble", "рубль", "commemorative"],
        "weak": ["olympic", "olympiad"],
        "block": ["lincoln", "united states", "kopek", "kopeek"],
        "specs": {"diameter_mm": 31.0, "weight_g": 12.8, "metal": "copper-nickel",
                  "edge": "reeded",
                  "note": "Cupro-nickel commemorative / circulation rubles — "
                          "not silver unless marked proof / Ag"},
        "typical_usd": (1.00, 8.00),
    },
    {
        "series": "Soviet Olympic Ruble",
        "denomination": "1 ruble",
        "country": "Soviet Union",
        "strong": [
            ("olympic", "ruble"), ("olympiad", "ruble"),
            ("москва", "1980"), ("moscow", "1980"),
            ("cccp", "olympic"), ("sssr", "olympic"),
        ],
        "cues": ["olympic", "olympiad", "1980", "moscow", "москва", "cccp",
                 "ruble", "commemorative"],
        "weak": [],
        "block": ["lincoln", "united states", "kopek"],
        "specs": {"diameter_mm": 31.0, "weight_g": 12.8, "metal": "copper-nickel",
                  "edge": "reeded",
                  "note": "1980 Moscow Olympics commemorative — common CN; "
                          "silver proofs exist separately"},
        "typical_usd": (2.00, 15.00),
    },
    {
        "series": "Soviet Anniversary Ruble",
        "denomination": "1 ruble",
        "country": "Soviet Union",
        "strong": [
            ("anniversary", "ruble"), ("jubilee", "ruble"),
            ("ленин", "рубль"), ("lenin", "ruble"),
            ("victory", "ruble"), ("победа", "рубль"),
        ],
        "cues": ["anniversary", "jubilee", "lenin", "ленин", "commemorative",
                 "cccp", "ruble", "рубль"],
        "weak": [],
        "block": ["lincoln", "united states", "kopek", "olympic"],
        "specs": {"diameter_mm": 31.0, "weight_g": 12.8, "metal": "copper-nickel",
                  "edge": "reeded",
                  "note": "Anniversary / jubilee commemorative ruble — usually CN"},
        "typical_usd": (1.50, 12.00),
    },
    # --- Other high-frequency European commons ---
    {
        "series": "Swiss Franc",
        "denomination": "1 franc",
        "country": "Switzerland",
        "strong": ["helvetia", "1 franc", "confoederatio", ("helvetia", "franc")],
        "cues": ["helvetia", "swiss", "franc"],
        "weak": [],
        "block": ["lincoln", "united states", "belgique", "francaise"],
        "specs": {"diameter_mm": 23.2, "weight_g": 4.4, "metal": "copper-nickel",
                  "edge": "reeded",
                  "note": "Pre-1968 often .835 silver; later cupro-nickel"},
        "typical_usd": (0.50, 3.00),
    },
    {
        "series": "Swiss 1/2 Franc",
        "denomination": "1/2 franc",
        "country": "Switzerland",
        "strong": ["1/2 franc", "half franc", ("helvetia", "1/2")],
        "cues": ["helvetia", "franc"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 18.2, "weight_g": 2.2, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Swiss 20 Rappen",
        "denomination": "20 rappen",
        "country": "Switzerland",
        "strong": ["20 rappen", "20 rp", ("helvetia", "20")],
        "cues": ["helvetia", "rappen"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 21.0, "weight_g": 4.0, "metal": "copper-nickel",
                  "edge": "plain"},
        "typical_usd": (0.25, 1.50),
    },
    {
        "series": "Belgian Franc",
        "denomination": "1 franc",
        "country": "Belgium",
        "strong": ["belgique", "belgie", "1 franc", ("belgique", "franc")],
        "cues": ["belgique", "belgie", "belgica"],
        "weak": ["franc"],
        "block": ["helvetia", "francaise", "lincoln"],
        "specs": {"diameter_mm": 21.0, "weight_g": 4.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Belgian 5 Francs",
        "denomination": "5 francs",
        "country": "Belgium",
        "strong": ["5 francs", "5 frank", ("belgique", "5"), ("belgie", "5")],
        "cues": ["belgique", "belgie"],
        "weak": [],
        "block": ["lincoln", "helvetia"],
        "specs": {"diameter_mm": 24.0, "weight_g": 6.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.50, 3.00),
    },
    {
        "series": "Portuguese Escudo",
        "denomination": "1 escudo",
        "country": "Portugal",
        "strong": ["republica portuguesa", "1 escudo", "escudo", "portuguesa"],
        "cues": ["portugal", "escudo"],
        "weak": [],
        "block": ["lincoln", "peseta", "united states"],
        "specs": {"diameter_mm": 26.0, "weight_g": 8.0, "metal": "bronze",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.50),
    },
    {
        "series": "Portuguese 5 Escudos",
        "denomination": "5 escudos",
        "country": "Portugal",
        "strong": ["5 escudos", ("portuguesa", "5"), ("escudo", "5")],
        "cues": ["portuguesa", "escudos", "portugal"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 24.5, "weight_g": 7.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.50, 3.00),
    },
    {
        "series": "Polish Zloty",
        "denomination": "1 zloty",
        "country": "Poland",
        "strong": ["rzeczpospolita", "1 zloty", "zlote", "zloty"],
        "cues": ["polska", "poland"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 23.0, "weight_g": 5.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Polish 10 Groszy",
        "denomination": "10 groszy",
        "country": "Poland",
        "strong": ["10 groszy", "groszy", ("rzeczpospolita", "10")],
        "cues": ["polska", "groszy"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 17.5, "weight_g": 1.8, "metal": "brass",
                  "edge": "plain"},
        "typical_usd": (0.20, 1.00),
    },
    {
        "series": "Czechoslovak Koruna",
        "denomination": "1 koruna",
        "country": "Czechoslovakia",
        "strong": [
            "ceskoslovenska", "1 koruna", "koruna", "csr",
            "republica ceskoslovenska",
        ],
        "cues": ["czechoslovakia", "korun"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 23.0, "weight_g": 5.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Hungarian Forint",
        "denomination": "1 forint",
        "country": "Hungary",
        "strong": ["magyar", "1 forint", "forint", "magyarorszag"],
        "cues": ["hungary", "forint"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 23.0, "weight_g": 5.0, "metal": "brass",
                  "edge": "reeded"},
        "typical_usd": (0.25, 1.50),
    },
    {
        "series": "Hungarian 2 Forint",
        "denomination": "2 forint",
        "country": "Hungary",
        "strong": ["2 forint", ("magyar", "2")],
        "cues": ["magyar", "forint"],
        "weak": [],
        "block": ["lincoln", "united states"],
        "specs": {"diameter_mm": 25.0, "weight_g": 6.0, "metal": "brass",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Swedish Krona",
        "denomination": "1 krona",
        "country": "Sweden",
        "strong": ["sverige", "1 krona", "krona"],
        "cues": ["sweden", "ore"],
        "weak": [],
        "block": ["norge", "danmark", "lincoln"],
        "specs": {"diameter_mm": 25.0, "weight_g": 7.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Norwegian Krone",
        "denomination": "1 krone",
        "country": "Norway",
        "strong": ["norge", "1 krone", "krone"],
        "cues": ["norway", "ore"],
        "weak": [],
        "block": ["sverige", "danmark", "lincoln"],
        "specs": {"diameter_mm": 25.0, "weight_g": 7.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Danish Krone",
        "denomination": "1 krone",
        "country": "Denmark",
        "strong": ["danmark", "1 krone", "denmark"],
        "cues": ["ore", "krone"],
        "weak": [],
        "block": ["sverige", "norge", "lincoln"],
        "specs": {"diameter_mm": 24.5, "weight_g": 6.8, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.25, 2.00),
    },
    {
        "series": "Irish Penny",
        "denomination": "One penny",
        "country": "Ireland",
        "strong": ["eire", "one penny", ("eire", "penny")],
        "cues": ["ireland", "eire"],
        "weak": ["penny"],
        "block": ["georgivs", "lincoln", "united states"],
        "specs": {"diameter_mm": 30.9, "weight_g": 9.5, "metal": "bronze",
                  "edge": "plain"},
        "typical_usd": (0.25, 2.00),
    },
    # Greece — Kingdom / Hellenic Republic drachma & lepta (pre-euro)
    {
        "series": "Greek 1 Drachma",
        "denomination": "1 drachma",
        "country": "Greece",
        "strong": [
            "1 drachma", "one drachma", "drachme",
            ("hellenic", "drachma"), ("greece", "drachma"),
            ("hellas", "drachma"),
        ],
        "cues": ["drachma", "drachmai", "hellenic", "hellas", "greece",
                 "basileion", "kingdom of greece"],
        "weak": [],
        "block": ["lincoln", "united states", "euro", "peseta"],
        "specs": {"diameter_mm": 21.0, "weight_g": 4.0, "metal": "copper-nickel",
                  "edge": "reeded",
                  "note": "Common mid-20th-c. cupro-nickel; earlier issues may be silver"},
        "typical_usd": (0.50, 4.00),
    },
    {
        "series": "Greek 2 Drachmai",
        "denomination": "2 drachmai",
        "country": "Greece",
        "strong": ["2 drachmai", "2 drachma", "two drachmai", ("hellenic", "2")],
        "cues": ["drachmai", "drachma", "hellenic", "hellas", "greece"],
        "weak": [],
        "block": ["lincoln", "united states", "euro"],
        "specs": {"diameter_mm": 24.0, "weight_g": 6.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.50, 5.00),
    },
    {
        "series": "Greek 5 Drachmai",
        "denomination": "5 drachmai",
        "country": "Greece",
        "strong": ["5 drachmai", "5 drachma", "five drachmai", ("hellenic", "5")],
        "cues": ["drachmai", "drachma", "hellenic", "hellas", "greece"],
        "weak": [],
        "block": ["lincoln", "united states", "euro"],
        "specs": {"diameter_mm": 28.0, "weight_g": 9.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.75, 6.00),
    },
    {
        "series": "Greek 10 Drachmai",
        "denomination": "10 drachmai",
        "country": "Greece",
        "strong": [
            "10 drachmai", "10 drachma", "ten drachmai",
            ("hellenic", "10"), ("greece", "10"),
        ],
        "cues": ["drachmai", "drachma", "hellenic", "hellas", "greece", "atom"],
        "weak": [],
        "block": ["lincoln", "united states", "euro"],
        "specs": {"diameter_mm": 30.0, "weight_g": 10.0, "metal": "copper-nickel",
                  "edge": "reeded",
                  "note": "1959–2000 types common; Democritus/atom reverse on later issues"},
        "typical_usd": (0.50, 5.00),
    },
    {
        "series": "Greek 20 Drachmai",
        "denomination": "20 drachmai",
        "country": "Greece",
        "strong": ["20 drachmai", "20 drachma", ("hellenic", "20")],
        "cues": ["drachmai", "hellenic", "hellas", "greece"],
        "weak": [],
        "block": ["lincoln", "united states", "euro"],
        "specs": {"diameter_mm": 32.0, "weight_g": 11.0, "metal": "copper-nickel",
                  "edge": "reeded"},
        "typical_usd": (0.75, 8.00),
    },
    {
        "series": "Greek 50 Lepta",
        "denomination": "50 lepta",
        "country": "Greece",
        "strong": ["50 lepta", "50 lepton", ("hellenic", "lepta")],
        "cues": ["lepta", "lepton", "hellenic", "hellas", "greece"],
        "weak": [],
        "block": ["lincoln", "united states", "euro", "centimes"],
        "specs": {"diameter_mm": 18.0, "weight_g": 2.5, "metal": "copper-nickel",
                  "edge": "plain"},
        "typical_usd": (0.25, 2.50),
    },
    {
        "series": "Greek 20 Lepta",
        "denomination": "20 lepta",
        "country": "Greece",
        "strong": ["20 lepta", "20 lepton"],
        "cues": ["lepta", "lepton", "hellenic", "hellas", "greece"],
        "weak": [],
        "block": ["lincoln", "united states", "euro"],
        "specs": {"diameter_mm": 16.0, "weight_g": 1.5, "metal": "aluminum",
                  "edge": "plain"},
        "typical_usd": (0.25, 2.00),
    },
    # Canada — Elizabeth II / maple leaf circulation types
    {
        "series": "Canadian Cent",
        "denomination": "1 cent",
        "country": "Canada",
        "strong": [
            ("canada", "1 cent"), ("canada", "one cent"),
            ("maple", "cent"), ("canada", "cent"),
        ],
        "cues": ["canada", "maple", "cent", "elizabeth"],
        "weak": [],
        "block": ["lincoln", "united states", "britannia", "georgivs", "edwardvs"],
        "specs": {"diameter_mm": 19.1, "weight_g": 2.35, "metal": "copper",
                  "edge": "plain",
                  "note": "Pre-1997 mostly copper; later copper-plated zinc/steel"},
        "typical_usd": (0.05, 1.50),
    },
    {
        "series": "Canadian Nickel",
        "denomination": "5 cents",
        "country": "Canada",
        "strong": [
            ("canada", "5 cents"), ("canada", "five cents"),
            ("beaver", "canada"), ("canada", "nickel"),
        ],
        "cues": ["canada", "beaver", "5 cents", "elizabeth"],
        "weak": [],
        "block": ["jefferson", "monticello", "united states", "britannia"],
        "specs": {"diameter_mm": 21.2, "weight_g": 3.95, "metal": "nickel",
                  "edge": "plain"},
        "typical_usd": (0.10, 2.00),
    },
    {
        "series": "Canadian Dime",
        "denomination": "10 cents",
        "country": "Canada",
        "strong": [
            ("canada", "10 cents"), ("canada", "ten cents"),
            ("bluenose", "canada"), ("schooner", "canada"),
        ],
        "cues": ["canada", "10 cents", "bluenose", "schooner", "elizabeth"],
        "weak": [],
        "block": ["roosevelt", "torch", "united states", "britannia"],
        "specs": {"diameter_mm": 18.0, "weight_g": 1.75, "metal": "nickel",
                  "edge": "reeded",
                  "note": "1967 and earlier often .800 silver; 1968 transitional; "
                          "later nickel"},
        "typical_usd": (0.15, 3.00),
    },
    {
        "series": "Canadian Quarter",
        "denomination": "25 cents",
        "country": "Canada",
        "strong": [
            ("canada", "25 cents"), ("canada", "twenty-five"),
            ("caribou", "canada"), ("canada", "quarter"),
        ],
        "cues": ["canada", "25 cents", "caribou", "elizabeth"],
        "weak": [],
        "block": ["washington", "united states", "britannia", "quarter dollar"],
        "specs": {"diameter_mm": 23.9, "weight_g": 4.4, "metal": "nickel",
                  "edge": "reeded",
                  "note": "1967 and earlier often .800 silver; 1968 transitional; "
                          "later nickel"},
        "typical_usd": (0.25, 4.00),
    },
    {
        "series": "Canadian Half Dollar",
        "denomination": "50 cents",
        "country": "Canada",
        "strong": [
            ("canada", "50 cents"), ("canada", "fifty cents"),
            ("coat of arms", "canada"),
        ],
        "cues": ["canada", "50 cents", "elizabeth"],
        "weak": [],
        "block": ["kennedy", "united states", "half dollar"],
        "specs": {"diameter_mm": 27.1, "weight_g": 8.1, "metal": "nickel",
                  "edge": "reeded",
                  "note": "Pre-1968 often .800 silver; later nickel"},
        "typical_usd": (0.50, 6.00),
    },
    {
        "series": "Canadian Loonie",
        "denomination": "1 dollar",
        "country": "Canada",
        "strong": [
            ("canada", "1 dollar"), ("canada", "one dollar"),
            "loonie", ("loon", "canada"),
        ],
        "cues": ["canada", "dollar", "loon", "loonie", "elizabeth"],
        "weak": [],
        "block": ["united states", "susan b", "sacagawea", "eisenhower"],
        "specs": {"diameter_mm": 26.5, "weight_g": 7.0, "metal": "aureate bronze",
                  "edge": "eleven-sided"},
        "typical_usd": (0.75, 3.00),
    },
    {
        "series": "Canadian Toonie",
        "denomination": "2 dollars",
        "country": "Canada",
        "strong": [
            ("canada", "2 dollars"), ("canada", "two dollars"),
            "toonie", ("polar bear", "canada"),
        ],
        "cues": ["canada", "2 dollars", "toonie", "polar bear", "elizabeth"],
        "weak": [],
        "block": ["united states", "loonie"],
        "specs": {"diameter_mm": 28.0, "weight_g": 7.3, "metal": "bi-metallic",
                  "edge": "interrupted serrations"},
        "typical_usd": (1.50, 4.00),
    },
]


def _norm_blob(text):
    blob = (text or "").lower()
    # Latin accents
    for a, b in (("é", "e"), ("è", "e"), ("à", "a"), ("ñ", "n"), ("ü", "u"),
                 ("ö", "o"), ("ä", "a"), ("ß", "ss"), ("ç", "c")):
        blob = blob.replace(a, b)
    # Greek coin legends → Latin before non-ascii strip
    for a, b in (
            ("δραχμαί", "drachmai"), ("δραχμαι", "drachmai"),
            ("δραχμή", "drachma"), ("δραχμη", "drachma"),
            ("λεπτά", "lepta"), ("λεπτα", "lepta"),
            ("λεπτόν", "lepton"), ("λεπτον", "lepton"),
            ("ελλάς", "hellas"), ("ελλας", "hellas"),
            ("ελλάδος", "hellados"), ("ελλαδος", "hellados"),
            ("ελληνική", "hellenic"), ("ελληνικη", "hellenic"),
            ("δημοκρατία", "republic"), ("δημοκρατια", "republic"),
            ("βασίλειον", "basileion"), ("βασιλειον", "basileion"),
    ):
        blob = blob.replace(a, b)
    # Cyrillic lookalikes → Latin so СССР / КОПЕЕК match cccp / kopeek
    cyr = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "c", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        # Uppercase Cyrillic often survives .lower() on some builds — map both
        "А": "a", "В": "v", "Е": "e", "К": "k", "М": "m", "Н": "n", "О": "o",
        "Р": "r", "С": "c", "Т": "t", "У": "u", "Х": "h",
    }
    for a, b in cyr.items():
        blob = blob.replace(a, b)
    # СССР phonetic map yields "cccr" (Cyrillic Р = r); collectors write CCCP
    blob = blob.replace("cccr", "cccp")
    blob = re.sub(r"\bc\s*c\s*c\s*[pr]\b", "cccp", blob)
    return re.sub(r"[^a-z0-9\s]", " ", blob)


def _side_blob(side):
    s = side or {}
    return _norm_blob(" ".join(str(s.get(k) or "") for k in
                               ("legends", "design", "denomination", "metal")))


def _sides_blob(sides):
    parts = []
    for side in ("obverse", "reverse"):
        parts.append(_side_blob((sides or {}).get(side)))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


# Foreign legends that mean the coin is NOT a US type — even if the other face
# was hallucinated as Liberty / torch / UNITED STATES.
_FOREIGN_MARKERS = (
    "osterreich", "ohne kriege", "kriege", "groschen", "schilling",
    "georgivs", "edwardvs", "britannia", "dei gra",
    "republique", "francaise", "marianne",
    "deutsches reich", "kaiserreich", "pfennig",
    "ddr", "demokratische", "deutsche demokratische",
    "nederland", "koninkrijk", "italia", "peseta", "espana", "alfonso",
    "cccp", "sssr", "ussr", "kopeek", "kopek", "kopeks", "hammer", "sickle",
    "helvetia", "confoederatio", "belgique", "belgie", "portuguesa", "escudo",
    "rzeczpospolita", "zloty", "groszy", "ceskoslovenska", "koruna",
    "magyar", "forint", "sverige", "norge", "danmark", "eire", "krona", "krone",
    "hellenic", "hellas", "greece", "drachma", "drachmai", "drachme",
    "lepta", "lepton", "basileion",
    "canada", "canadian", "maple", "loonie", "toonie", "bluenose", "caribou",
)

_US_HALLUCINATION_PHRASES = (
    "united states of america", "united states", "e pluribus unum",
    "in god we trust", "one dime", "one cent", "five cents",
    "quarter dollar", "half dollar", "liberty",
    "roosevelt", "jefferson", "lincoln", "washington", "kennedy",
    "torch", "olive", "oak", "monticello", "memorial",
)


def _has_foreign_markers(blob):
    return any(_cue_hit(blob, m) for m in _FOREIGN_MARKERS)


def _strip_us_hallucinations(blob):
    """Drop US stock phrases when a foreign legend is already present."""
    out = blob
    for p in _US_HALLUCINATION_PHRASES:
        out = re.sub(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(p), " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _id_blob(sides):
    """Blob for catalog matching; suppresses US hallucination on foreign coins."""
    blob = _sides_blob(sides)
    if _has_foreign_markers(blob):
        return _strip_us_hallucinations(blob)
    # Per-side conflict: one face foreign, the other US-looking → keep foreign side
    o = _side_blob((sides or {}).get("obverse"))
    r = _side_blob((sides or {}).get("reverse"))
    fo, fr = _has_foreign_markers(o), _has_foreign_markers(r)
    us_o = any(_cue_hit(o, p) for p in ("united states", "torch", "monticello", "lincoln"))
    us_r = any(_cue_hit(r, p) for p in ("united states", "torch", "monticello", "lincoln"))
    if fo and us_r and not fr:
        return o
    if fr and us_o and not fo:
        return r
    return blob


def _cue_hit(blob, cue):
    """Whole-token match so 'crown' does not fire inside unrelated words."""
    if isinstance(cue, (tuple, list)):
        return all(_cue_hit(blob, p) for p in cue)
    c = str(cue).lower().strip()
    if not c:
        return False
    return bool(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(c), blob))


def _score_catalog_entry(blob, entry):
    """Return (score, design_hits) or (-1, 0) if blocked."""
    for b in entry.get("block") or ():
        if _cue_hit(blob, b):
            return -1, 0
    score = 0
    design = 0
    for c in entry.get("strong") or ():
        if _cue_hit(blob, c):
            score += 10
            design += 1
    for c in entry.get("cues") or ():
        if _cue_hit(blob, c):
            score += 3
            design += 1
    for c in entry.get("weak") or ():
        if _cue_hit(blob, c):
            score += 1
    return score, design


def _refine_lincoln_series(blob, series):
    if "lincoln" not in series.lower():
        return series
    if "wheat" in blob:
        return "Lincoln Wheat Cent"
    if "memorial" in blob:
        return "Lincoln Memorial Cent"
    if "shield" in blob:
        return "Lincoln Shield Cent"
    return "Lincoln Cent"


def all_common_coins():
    return list(COMMON_US_COINS) + list(COMMON_EU_COINS)


def match_common_coin(sides):
    """Best COMMON_US_COINS / COMMON_EU_COINS hit for vision text, or None."""
    blob = _id_blob(sides)
    if not blob.strip():
        return None
    foreign = _has_foreign_markers(_sides_blob(sides))
    best = None
    best_score = 0
    best_design = 0
    for entry in all_common_coins():
        # Never pick a US type when a foreign legend was read on either face
        if foreign and (entry.get("country") or "") == "United States":
            continue
        sc, design = _score_catalog_entry(blob, entry)
        if sc < 0 or design < 1:
            continue
        if sc > best_score or (sc == best_score and design > best_design):
            best = entry
            best_score = sc
            best_design = design
    if not best or best_score < 3:
        return None
    series = _refine_lincoln_series(blob, best["series"])
    out = {
        "country": best["country"],
        "denomination": best["denomination"],
        "series": series,
        "catalogHit": True,
    }
    if best.get("specs"):
        out["specs"] = dict(best["specs"])
    return out


def match_common_us_coin(sides):
    """Backward-compatible alias."""
    return match_common_coin(sides)


def lookup_type_entry(country="", series="", denomination=""):
    """Find catalog entry by series (preferred) or country+denom."""
    series_l = (series or "").strip().lower()
    country_l = (country or "").strip().lower()
    denom_l = (denomination or "").strip().lower()
    for entry in all_common_coins():
        if series_l and entry.get("series", "").lower() == series_l:
            return entry
    for entry in all_common_coins():
        if country_l and entry.get("country", "").lower() == country_l:
            if denom_l and denom_l in (entry.get("denomination") or "").lower():
                return entry
            if series_l and series_l in (entry.get("series") or "").lower():
                return entry
    return None


def authenticity_for_attribution(a, catalog_hit=None):
    """Specs + hand-check list for foreign / old / silver-looking pieces.

    Photos cannot prove genuineness — this is a screening checklist only.
    """
    country = (a.get("country") or "").strip()
    series = (a.get("series") or "").strip()
    denom = (a.get("denomination") or "").strip()
    year = _year_int(a)
    metal_hint = ""
    if catalog_hit and catalog_hit.get("specs"):
        specs = dict(catalog_hit["specs"])
        entry = None
    else:
        entry = lookup_type_entry(country, series, denom)
        specs = dict(entry["specs"]) if entry and entry.get("specs") else None

    foreign = country and not _is_united_states(a)
    old = year is not None and year < 1965
    silverish = False
    if specs and (specs.get("metal") or "").lower() == "silver":
        silverish = True
    metal_blob = (a.get("metal") or "").lower()
    obs = a.get("observed") or {}
    if isinstance(obs, dict):
        for side in ("obverse", "reverse"):
            metal_blob += " " + str((obs.get(side) or {}).get("metal") or "").lower()
    if "silver" in metal_blob:
        silverish = True

    if not (specs or foreign or silverish or (old and foreign) or (old and silverish)):
        # Modern US commons: no authenticity panel
        if _is_united_states(a) and not old:
            return None
        if _is_united_states(a) and old and not silverish and not specs:
            if not (year and year < 1920):
                return None
        elif not foreign and not silverish and not specs:
            return None

    checks = [
        "Weigh on a 0.01 g scale and compare to the expected weight.",
        "Measure diameter in mm with calipers.",
        "Magnet test: most silver and bronze are not magnetic - stickiness is a red flag.",
        "Look for cast seams, bubbles, mushy lettering, or a wrong edge.",
        "Photo ID is provisional - confirm specs in hand before treating as genuine.",
    ]
    if silverish:
        checks.insert(
            0,
            "Silver check: non-magnetic; clear ring when lightly rung; weight must match.",
        )
    if specs:
        meta = specs.get("metal") or "?"
        checks.insert(0, "Expected metal: %s." % meta)
        if specs.get("edge"):
            checks.insert(1, "Expected edge: %s." % specs["edge"])
        if specs.get("note"):
            checks.append(str(specs["note"]))

    out = {
        "authChecks": checks,
        "authNote": "Photo ID is provisional - confirm specs in hand.",
    }
    if specs:
        out["specs"] = specs
    return out


def face_skip_estimate(a, auth=None):
    """Bag as common / face without web search (FACE SKIP button).

    US uses exact face when known; world commons use the low end of the type
    catalog ballpark so the tray stays sorted without a full PRICE IT.
    """
    face = common_circulated_fallback(a)
    if face:
        face = dict(face)
        face["faceSkip"] = True
        face["notes"] = (
            "Face skip — no web search. "
            + (face.get("notes") or "Common piece at face value."))
        return face
    tip = typical_circulated_fallback(a, auth=auth)
    if tip:
        tip = dict(tip)
        lo = float(tip.get("valueLow") or 0.25)
        tip["valueLow"] = tip["valueHigh"] = round(lo, 2)
        tip["faceValue"] = True
        tip["faceSkip"] = True
        tip["typicalEstimate"] = False
        tip["marketSold"] = False
        tip["tier"] = "C"
        tip["notes"] = (
            "Face skip — bagged as common without web search "
            "(nominal ~$%.2f). Use PRICE IT if you want live comps." % lo)
        return tip
    year = _year_int(a)
    label_bits = [str(year) if year else "", a.get("country") or "",
                  a.get("series") or a.get("denomination") or "coin"]
    label = " ".join(x for x in label_bits if x).strip()
    out = {
        "identification": label or "Coin",
        "valueLow": 0.25,
        "valueHigh": 0.25,
        "currency": "USD",
        "grade": a.get("grade") or "circulated",
        "melt": "",
        "comparables": [],
        "checks": ["Face skip — confirm type in hand if unsure."],
        "notes": "Face skip — nominal common without web search.",
        "faceValue": True,
        "faceSkip": True,
        "sources": [],
    }
    if auth and auth.get("specs"):
        out["specs"] = auth["specs"]
    if auth and auth.get("authChecks"):
        out["authChecks"] = auth["authChecks"]
    out["tier"] = "C"
    return out


def research_needed_result(a, auth=None):
    """When web search fails for foreign / non-face coins."""
    label_bits = [a.get("year") or "", a.get("country") or "",
                  a.get("series") or a.get("denomination") or "coin"]
    label = " ".join(x for x in label_bits if x).strip()
    out = {
        "identification": label or "Unidentified coin",
        "valueLow": 0,
        "valueHigh": 0,
        "currency": "USD",
        "grade": a.get("grade") or "circulated",
        "melt": "",
        "comparables": [],
        "checks": (auth or {}).get("authChecks") or [
            "Verify authenticity in hand before paying a premium.",
        ],
        "notes": ("Research needed - verify authenticity and comparables before "
                  "paying. Paste a price-guide URL if you have one."),
        "faceValue": False,
        "sources": [],
        "researchNeeded": True,
    }
    if auth and auth.get("specs"):
        out["specs"] = auth["specs"]
    if auth and auth.get("authChecks"):
        out["authChecks"] = auth["authChecks"]
    out["tier"] = value_tier(out)
    return out


def propose_identity(sides):
    """Soft ID from legends/design after a vision read. Still editable by the user.

    Uses offline US + European cue catalogs so design beats hallucinated denom text.
    """
    hit = match_common_coin(sides)
    if hit:
        return hit

    # Prefer foreign-cleaned blob so a hallucinated US reverse can't force USA
    blob = _id_blob(sides)
    raw = _sides_blob(sides)
    foreign = _has_foreign_markers(raw)
    out = {}
    if not foreign and any(w in blob for w in (
            "united states", "e pluribus", "jefferson", "lincoln",
            "washington", "roosevelt", "kennedy")):
        out["country"] = "United States"
    elif any(w in blob for w in (
            "canada", "canadian", "maple leaf", "loonie", "toonie",
            "bluenose", "caribou")) or (
            "maple" in blob and "cent" in blob):
        out["country"] = "Canada"
    elif any(w in blob for w in ("georgivs", "edwardvs", "britannia", "britt")):
        out["country"] = "United Kingdom"
    elif any(w in blob for w in ("republique francaise", "marianne", "liberte")):
        out["country"] = "France"
    elif any(w in blob for w in (
            "ddr", "demokratische", "deutsche demokratische", "jahre ddr")):
        out["country"] = "East Germany"
    elif any(w in blob for w in ("deutsches reich", "kaiserreich")) or (
            "pfennig" in blob and "ddr" not in blob):
        out["country"] = "Germany"
    elif any(w in blob for w in (
            "osterreich", "ohne kriege", "kriege", "groschen", "schilling")):
        out["country"] = "Austria"
    elif any(w in blob for w in (
            "cccp", "sssr", "ussr", "kopeek", "kopek", "kopeks",
            "hammer and sickle", "soviet", "ruble", "rouble", "рубль")) and "ddr" not in blob:
        out["country"] = "Soviet Union"
    elif "helvetia" in blob or "confoederatio" in blob:
        out["country"] = "Switzerland"
    elif "belgique" in blob or "belgie" in blob:
        out["country"] = "Belgium"
    elif "portuguesa" in blob or "escudo" in blob:
        out["country"] = "Portugal"
    elif "rzeczpospolita" in blob or "zloty" in blob or "groszy" in blob:
        out["country"] = "Poland"
    elif "ceskoslovenska" in blob or "koruna" in blob:
        out["country"] = "Czechoslovakia"
    elif "magyar" in blob or "forint" in blob:
        out["country"] = "Hungary"
    elif "sverige" in blob:
        out["country"] = "Sweden"
    elif "norge" in blob:
        out["country"] = "Norway"
    elif "danmark" in blob:
        out["country"] = "Denmark"
    elif "eire" in blob:
        out["country"] = "Ireland"
    elif "nederland" in blob:
        out["country"] = "Netherlands"
    elif "italia" in blob:
        out["country"] = "Italy"
    elif "peseta" in blob or "espana" in blob:
        out["country"] = "Spain"
    elif any(w in blob for w in (
            "hellenic", "hellas", "greece", "drachma", "drachmai", "drachme",
            "lepta", "lepton", "basileion")):
        out["country"] = "Greece"

    if out.get("country") == "Canada" or "canada" in blob or "loonie" in blob:
        if "toonie" in blob or "2 dollar" in blob or "two dollar" in blob:
            out["denomination"] = "2 dollars"
            out.setdefault("series", "Canadian Toonie")
        elif "loonie" in blob or "1 dollar" in blob or "one dollar" in blob:
            out["denomination"] = "1 dollar"
            out.setdefault("series", "Canadian Loonie")
        elif "50 cent" in blob or "fifty cent" in blob:
            out["denomination"] = "50 cents"
            out.setdefault("series", "Canadian Half Dollar")
        elif "25 cent" in blob or "twenty five" in blob or "twenty-five" in blob:
            out["denomination"] = "25 cents"
            out.setdefault("series", "Canadian Quarter")
        elif "10 cent" in blob or "ten cent" in blob:
            out["denomination"] = "10 cents"
            out.setdefault("series", "Canadian Dime")
        elif "5 cent" in blob or "five cent" in blob or "beaver" in blob:
            out["denomination"] = "5 cents"
            out.setdefault("series", "Canadian Nickel")
        elif "1 cent" in blob or "one cent" in blob or (
                "cent" in blob and "maple" in blob):
            out["denomination"] = "1 cent"
            out.setdefault("series", "Canadian Cent")
        else:
            out.setdefault("denomination", "25 cents")
            out.setdefault("series", "Canadian Quarter")
    elif "one cent" in blob or ("penny" in blob and out.get("country") == "United States"):
        out["denomination"] = "One cent"
        out.setdefault("series", "Lincoln Cent")
    elif "one dime" in blob:
        out["denomination"] = "One dime"
        out.setdefault("series", "Roosevelt Dime")
    elif "quarter dollar" in blob:
        out["denomination"] = "Quarter dollar"
        out.setdefault("series", "Washington Quarter")
    elif "five cents" in blob or "five cent" in blob:
        out["denomination"] = "Five cents"
        out.setdefault("series", "Jefferson Nickel")
    elif "half dollar" in blob:
        out["denomination"] = "Half dollar"
        out.setdefault("series", "Kennedy Half Dollar")
    elif out.get("country") == "Soviet Union" or "kopeek" in blob or "kopek" in blob \
            or "ruble" in blob or "rouble" in blob or "рубль" in blob:
        if any(w in blob for w in ("olympic", "olympiad", "moscow 1980", "москва")):
            out["denomination"] = "1 ruble"
            out.setdefault("series", "Soviet Olympic Ruble")
        elif any(w in blob for w in (
                "anniversary", "jubilee", "lenin", "ленин", "victory", "победа",
                "commemorative")) and (
                "ruble" in blob or "rouble" in blob or "рубль" in blob):
            out["denomination"] = "1 ruble"
            out.setdefault("series", "Soviet Anniversary Ruble")
        elif "ruble" in blob or "rouble" in blob or "рубль" in blob:
            out["denomination"] = "1 ruble"
            out.setdefault("series", "Soviet 1 Ruble")
        else:
            for n in (20, 15, 10, 5, 3, 2, 1):
                token = "%d" % n
                if token in blob and ("kopek" in blob or "kopeek" in blob or "cccp" in blob):
                    label = "%d kopek%s" % (n, "" if n == 1 else "s")
                    out["denomination"] = label
                    out.setdefault("series", "Soviet %d Kopek%s" % (n, "" if n == 1 else "s"))
                    break
            else:
                out.setdefault("denomination", "15 kopeks")
                out.setdefault("series", "Soviet 15 Kopeks")
    elif "10 groschen" in blob or (
            "groschen" in blob and "10" in blob and out.get("country") == "Austria"):
        out["denomination"] = "10 groschen"
        out.setdefault("series", "Austrian 10 Groschen")
    elif "5 groschen" in blob:
        out["denomination"] = "5 groschen"
        out.setdefault("series", "Austrian 5 Groschen")
    elif "groschen" in blob and out.get("country") == "Austria":
        out.setdefault("denomination", "10 groschen")
        out.setdefault("series", "Austrian 10 Groschen")
    elif "schilling" in blob and out.get("country") == "Austria":
        out.setdefault("denomination", "1 schilling")
        out.setdefault("series", "Austrian Schilling")
    elif out.get("country") == "East Germany" or "ddr" in blob:
        if "5" in blob and "mark" in blob:
            out["denomination"] = "5 mark"
            out.setdefault("series", "DDR 5 Mark")
        elif "20" in blob and "pfennig" in blob:
            out["denomination"] = "20 pfennig"
            out.setdefault("series", "DDR 20 Pfennig")
        elif "10" in blob and "pfennig" in blob:
            out["denomination"] = "10 pfennig"
            out.setdefault("series", "DDR 10 Pfennig")
        elif "mark" in blob:
            out.setdefault("denomination", "1 mark")
            out.setdefault("series", "DDR 1 Mark")
        else:
            out.setdefault("denomination", "5 mark")
            out.setdefault("series", "DDR 5 Mark")
    elif "helvetia" in blob and "franc" in blob:
        out.setdefault("denomination", "1 franc")
        out.setdefault("series", "Swiss Franc")
    elif "one penny" in blob or ("penny" in blob and out.get("country") == "United Kingdom"):
        out["denomination"] = "One penny"
        out.setdefault("series", "UK Penny")
    elif "shilling" in blob:
        out["denomination"] = "One shilling"
        out.setdefault("series", "UK Shilling")
    elif "franc" in blob and out.get("country") == "Belgium":
        out.setdefault("denomination", "1 franc")
        out.setdefault("series", "Belgian Franc")
    elif "franc" in blob:
        out.setdefault("denomination", "1 franc")
        out.setdefault("series", "French Franc")
    elif ("mark" in blob and "denmark" not in blob and "danmark" not in blob
          and "ddr" not in blob and out.get("country") != "East Germany"):
        out.setdefault("denomination", "1 mark")
        out.setdefault("series", "German Mark")
    elif out.get("country") == "Greece" or "drachm" in blob or "lepta" in blob:
        if "20" in blob and "drachm" in blob:
            out["denomination"] = "20 drachmai"
            out.setdefault("series", "Greek 20 Drachmai")
        elif "10" in blob and "drachm" in blob:
            out["denomination"] = "10 drachmai"
            out.setdefault("series", "Greek 10 Drachmai")
        elif "5" in blob and "drachm" in blob:
            out["denomination"] = "5 drachmai"
            out.setdefault("series", "Greek 5 Drachmai")
        elif "2" in blob and "drachm" in blob:
            out["denomination"] = "2 drachmai"
            out.setdefault("series", "Greek 2 Drachmai")
        elif "50" in blob and "lept" in blob:
            out["denomination"] = "50 lepta"
            out.setdefault("series", "Greek 50 Lepta")
        elif "20" in blob and "lept" in blob:
            out["denomination"] = "20 lepta"
            out.setdefault("series", "Greek 20 Lepta")
        elif "drachm" in blob:
            out.setdefault("denomination", "1 drachma")
            out.setdefault("series", "Greek 1 Drachma")
        else:
            out.setdefault("denomination", "10 drachmai")
            out.setdefault("series", "Greek 10 Drachmai")
    return out


# --------------------------------------------------------------------------
# The two steps
# --------------------------------------------------------------------------

def read_side(model, side, image_b64, host=None):
    raw = ollama(model, READ_PROMPT.format(side=side), images=[image_b64],
                 host=host, num_predict=560, num_ctx=2048)
    return loads(raw) or {"legends": raw[:400], "date": "unclear"}


def price(model, attribution, extra_urls=(), host=None, sources=None, say=None,
          images_b64=None, text_model=None):
    attribution = dict(attribution or {})
    id_model = model
    text_model = text_model or model
    # Worn / undated with no country/type: one design vision pass, then search
    if needs_look_assist(attribution) and images_b64:
        try:
            look = look_assist(id_model, images_b64, host=host, say=say)
            if look:
                attribution = apply_look_assist(attribution, look)
                if say and look.get("notes"):
                    say("design guess: %s" % look["notes"][:80])
        except Exception as e:
            if say:
                say("design guess skipped (%s)" % type(e).__name__)

    # Filled after scrape; stamp_result attaches citations on every success path
    docs = []
    listings = []

    def stamp_result(out):
        """Attach look-assist, similar cards, and full source citations."""
        if not out or out.get("error"):
            return out
        look = attribution.get("lookAssist")
        if look:
            out["lookAssist"] = look
            out["lookFilled"] = {
                "country": attribution.get("country") or "",
                "denomination": attribution.get("denomination") or "",
                "series": attribution.get("series") or "",
            }
            if not normalize_year(attribution.get("year")):
                n = (out.get("notes") or "").strip()
                tip = "Ballpark from design match (date unread)."
                if tip not in n:
                    out["notes"] = (n + " " + tip).strip() if n else tip
        if listings:
            out["similarListings"] = listings
        # Engine citations win over model-limited comparables
        return attach_citations(out, docs, listings=listings, attribution=attribution)

    queries = build_queries(attribution)
    if not queries:
        return {"error": "Not enough detail to search on. Capture both sides "
                         "(or fill country + denomination). Worn coins use a "
                         "design guess when photos are present."}

    auth = authenticity_for_attribution(attribution)
    extra = [u for u in list(extra_urls)[:3] if u.startswith("http")]
    # US commons only → face value (still attach PCGS guide link)
    if not extra:
        face = common_circulated_fallback(attribution)
        if face:
            if say:
                say("common coin — listing at face value")
            return stamp_result(face)
    pcgs = pcgs_price_guide_url(attribution)
    if pcgs:
        if say:
            say("PCGS price guide")
        try:
            title, text = page_text(pcgs)
            snip = price_windows(text)
            docs.append({
                "url": pcgs,
                "title": (title or "PCGS Price Guide")[:120],
                "snippet": (snip or text or "")[:2600] or (
                    "PCGS Price Guide dealer asking prices for PCGS-graded coins."),
            })
        except Exception:
            # PCGS often returns HTTP 403 to bots — keep the deep link for the UI
            docs.append({
                "url": pcgs,
                "title": "PCGS Price Guide (open in browser)",
                "snippet": (
                    "PCGS Price Guide at pcgs.com/prices/us — average dealer asking "
                    "prices for PCGS-graded coins. This app could not download the "
                    "table automatically; open the link for current grades. Raw "
                    "circulated coins usually sell for less than graded guide prices."
                ),
            })

    max_pages = 8 if not _is_united_states(attribution) else 6
    docs.extend(gather(queries, max_pages=max_pages, sources=sources, say=say))
    for u in extra:
        if say:
            say("reading your source")
        try:
            title, text = page_text(u)
            docs.append({"url": u, "title": title[:120],
                         "snippet": price_windows(text)[:2600]})
        except Exception:
            pass
    # When Google/DDG/Bing/eBay block bots, Delcampe search still returns priced rows
    market_docs = [d for d in docs if not _is_price_guide_stub(d)]
    if len(market_docs) < 3:
        if say:
            say("trying Delcampe marketplace…")
        seen_u = {d.get("url") for d in docs}
        for q in short_market_queries(attribution, queries):
            for h in search_delcampe(q, limit=6, sources=sources):
                if h["url"] in seen_u:
                    continue
                seen_u.add(h["url"])
                docs.append({
                    "url": h["url"],
                    "title": h.get("title") or "",
                    "snippet": h.get("snippet") or "",
                })
            if len([d for d in docs if not _is_price_guide_stub(d)]) >= 8:
                break

    listings = similar_listings_from_docs(docs, limit=8)
    market = market_estimate_from_docs(docs, attribution)

    if not docs:
        fallback = common_circulated_fallback(attribution)
        if fallback:
            if say:
                say("common modern coin — using face value")
            return stamp_result(fallback)
        if market:
            if say:
                say("using marketplace sold/asking prices")
            out = dict(market)
            out["tier"] = value_tier(out)
            if auth:
                if auth.get("specs"):
                    out["specs"] = auth["specs"]
                if auth.get("authChecks"):
                    out["authChecks"] = auth["authChecks"]
                    out["checks"] = list(auth["authChecks"])[:8]
            return stamp_result(attach_pcgs_guide(out, attribution))
        tip = typical_circulated_fallback(attribution, auth)
        if tip:
            if say:
                say("typical common range (no live listings)")
            return stamp_result(tip)
        if say:
            say("no pages — authenticity checklist only")
        return stamp_result(attach_pcgs_guide(
            research_needed_result(attribution, auth), attribution))

    # Fast path: enough priced lookalikes → skip a second LLM pass
    priced_n = sum(1 for x in listings if x.get("price"))
    if market and (market.get("valueHigh") or 0) > 0 and priced_n >= 3:
        if say:
            say("using %d marketplace comps (skip model re-rank)" % priced_n)
        out = dict(market)
        out["tier"] = value_tier(out)
        if auth:
            if auth.get("specs"):
                out["specs"] = auth["specs"]
            if auth.get("authChecks"):
                out["authChecks"] = auth["authChecks"]
                out["checks"] = list(auth["authChecks"])[:8]
        out["sources"] = [d["url"] for d in docs]
        out["faceValue"] = False
        return stamp_result(attach_pcgs_guide(out, attribution))

    if say:
        say("weighing %d sources with %s…" % (len(docs), text_model))
    rendered = "\n\n".join("--- %s (%s)\n%s" % (d["url"], d["title"], d["snippet"])
                           for d in docs)
    raw = ollama(text_model, VALUE_PROMPT.format(
        attribution=json.dumps(attribution, indent=1), docs=rendered[:14000]),
                 host=host, num_predict=900, num_ctx=4096)
    out = loads(raw)
    if not out:
        if market:
            if say:
                say("model blank — using marketplace prices")
            out = dict(market)
        else:
            tip = typical_circulated_fallback(attribution, auth)
            if tip:
                if say:
                    say("typical common range (parse failed)")
                return stamp_result(tip)
            return {"error": "The model's answer didn't parse. Try again."}
    try:
        lo = float(out.get("valueLow") or 0)
        hi = float(out.get("valueHigh") or 0)
    except (TypeError, ValueError):
        lo = hi = 0
    # Model often returns 0 for common foreign copper — fill from eBay/dealer scrapes
    if (lo <= 0 and hi <= 0) and market:
        if say:
            say("filling sold/asking range from listings")
        out["valueLow"] = market["valueLow"]
        out["valueHigh"] = market["valueHigh"]
        out["marketSold"] = True
        if not out.get("comparables"):
            out["comparables"] = market.get("comparables") or []
        notes = (out.get("notes") or "").strip()
        tip = market.get("notes") or ""
        out["notes"] = (notes + " " + tip).strip() if notes else tip
        if not out.get("identification"):
            out["identification"] = market.get("identification")
        lo, hi = out["valueLow"], out["valueHigh"]
    if (lo <= 0 and hi <= 0):
        tip = typical_circulated_fallback(attribution, auth)
        if tip:
            if say:
                say("typical common range (sources empty)")
            tip["sources"] = [d["url"] for d in docs]
            return stamp_result(tip)
    out["sources"] = [d["url"] for d in docs]
    out["faceValue"] = False
    out["tier"] = value_tier(out)
    if auth:
        if auth.get("specs"):
            out["specs"] = auth["specs"]
        if auth.get("authChecks"):
            out["authChecks"] = auth["authChecks"]
            existing = list(out.get("checks") or [])
            for c in auth["authChecks"]:
                if c not in existing:
                    existing.append(c)
            out["checks"] = existing[:8]
    return stamp_result(attach_pcgs_guide(out, attribution))
