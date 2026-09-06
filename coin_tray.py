#!/usr/bin/env python3
"""
Coin Tray — browser UI + phone LAN API.

Desktop: python coin_desktop.py
Phone companion: python coin_tray.py   then point the Android app at the printed URL.

Bind 0.0.0.0 so an Android phone on the same Wi‑Fi can reach Ollama through this PC.
Auth uses the X-Coin-Tray-Token header only (query-string tokens are rejected).
"""

import json
import os
import secrets
import socket
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import coin_engine as E

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("COIN_TRAY_PORT", "8722"))
TRAY_FILE = os.path.join(HERE, "coin_tray_data.json")
CONF_FILE = os.path.join(HERE, "coin_tray_config.json")


def load_conf():
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_conf(conf):
    try:
        with open(CONF_FILE, "w", encoding="utf-8") as f:
            json.dump(conf, f, indent=1)
    except Exception:
        pass


def ensure_phone_token(conf):
    token = (conf.get("phoneToken") or os.environ.get("COIN_TRAY_TOKEN") or "").strip()
    if not token:
        token = secrets.token_urlsafe(12)
        conf["phoneToken"] = token
        save_conf(conf)
    return token


def lan_ipv4():
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip in ips:
                continue
            ips.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip not in ips and not ip.startswith("127."):
            ips.insert(0, ip)
    except Exception:
        pass
    return ips


CONF = load_conf()
PHONE_TOKEN = ensure_phone_token(CONF)
E.SOURCES = E.merge_sources(CONF.get("sources") or E.DEFAULT_SOURCES)


class Handler(BaseHTTPRequestHandler):
    server_version = "CoinTrayPhone/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Coin-Tray-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")

    def reply(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        if not raw:
            return {}
        return json.loads(raw)

    def authorized(self):
        # Header only — never accept ?token= (leaks into proxies / access logs).
        got = (self.headers.get("X-Coin-Tray-Token") or "").strip()
        return bool(got) and got == PHONE_TOKEN

    def pick_models(self, preferred=None):
        found = E.vision_models()
        id_model = E.pick_vision_model(found, preferred=preferred, purpose="id")
        text_model = E.pick_pricing_model(found, id_model) if found else None
        return found, id_model, text_model

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if route in ("/", "/index.html"):
            path = os.path.join(HERE, "index.html")
            if not os.path.exists(path):
                return self.reply(500, {"error": "index.html is missing from this folder."})
            with open(path, "rb") as f:
                return self.reply(200, f.read(), "text/html; charset=utf-8")
        if route == "/api/health":
            # Health is unauthenticated so the phone can discover a live host,
            # but it does not return the token.
            found, id_model, text_model = self.pick_models(CONF.get("model"))
            ok, _owned, detail = E.ensure_ollama()
            return self.reply(200, {
                "ok": True,
                "ollama": bool(ok),
                "detail": detail or "",
                "models": found,
                "idModel": id_model,
                "priceModel": text_model,
                "needsToken": True,
            })
        if not self.authorized():
            return self.reply(401, {"error": "Bad or missing X-Coin-Tray-Token."})
        if route == "/api/models":
            found, id_model, text_model = self.pick_models(CONF.get("model"))
            return self.reply(200, {
                "models": found,
                "idModel": id_model,
                "priceModel": text_model,
                "error": None if found else "Ollama isn't answering. Start it and retry.",
            })
        if route == "/api/tray":
            try:
                with open(TRAY_FILE, encoding="utf-8") as f:
                    tray = json.load(f)
            except Exception:
                tray = []
            if not isinstance(tray, list):
                tray = []
            return self.reply(200, {"tray": tray, "count": len(tray)})
        return self.reply(404, {"error": "No such page."})

    def do_PUT(self):
        return self.do_POST()

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        if not self.authorized():
            return self.reply(401, {"error": "Bad or missing X-Coin-Tray-Token."})
        try:
            if route == "/api/read":
                b = self.body()
                found, id_model, _text = self.pick_models(
                    b.get("model") or CONF.get("model"))
                model = id_model or b.get("model") or "gemma3:4b"
                if not found:
                    return self.reply(200, {"error": "Ollama has no vision model."})
                sides = {s: E.read_side(model, s, b[s])
                         for s in ("obverse", "reverse") if b.get(s)}
                if not sides:
                    return self.reply(200, {"error": "No photo came through."})
                guess = E.propose_identity(sides) or {}
                return self.reply(200, {
                    "sides": sides,
                    "guess": guess,
                    "model": model,
                })

            if route in ("/api/value", "/api/price"):
                b = self.body()
                found, id_model, text_model = self.pick_models(
                    b.get("model") or CONF.get("model"))
                model = id_model or b.get("model") or "gemma3:4b"
                text = text_model or model
                imgs = []
                for k in ("obverse", "reverse"):
                    if b.get(k):
                        imgs.append(b[k])
                out = E.price(
                    model,
                    b.get("attribution", {}),
                    b.get("urls") or [],
                    images_b64=imgs or None,
                    text_model=text,
                )
                if out.get("error"):
                    return self.reply(200, {"error": out["error"]})
                return self.reply(200, {"result": out, "model": model})

            if route == "/api/face-skip":
                b = self.body()
                attr = b.get("attribution") or {}
                auth = E.authenticity_for_attribution(attr)
                out = E.face_skip_estimate(attr, auth=auth)
                return self.reply(200, {"result": out})

            if route == "/api/tray":
                data = self.body().get("tray", [])
                if not isinstance(data, list):
                    return self.reply(400, {"error": "tray must be a list"})
                tmp = TRAY_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=1)
                os.replace(tmp, TRAY_FILE)
                return self.reply(200, {"saved": len(data)})
        except E.OllamaDown as e:
            return self.reply(200, {"error": str(e)})
        except Exception as e:
            return self.reply(200, {"error": "%s: %s" % (type(e).__name__, e)})
        return self.reply(404, {"error": "No such page."})


def main():
    host = os.environ.get("COIN_TRAY_BIND", "0.0.0.0")
    ips = lan_ipv4()
    print("\n  Coin Tray phone / browser server")
    print("  Local   http://127.0.0.1:%d" % PORT)
    for ip in ips:
        print("  Phone   http://%s:%d" % (ip, PORT))
    print("  Token   %s" % PHONE_TOKEN)
    print("  Header  X-Coin-Tray-Token")
    print("  Ollama  %s" % E.OLLAMA)
    print("  Tray    %s" % TRAY_FILE)
    print("\n  Same Wi‑Fi as the S24. Allow Python in Windows Firewall if prompted.")
    print("  Ctrl-C to stop.\n")
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
