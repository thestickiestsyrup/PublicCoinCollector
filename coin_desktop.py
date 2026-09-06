#!/usr/bin/env python3
"""
Coin Tray - desktop app.

Double-click, or:  python3 coin_desktop.py

Needs Ollama with a vision model, and Pillow (pip install pillow).
Everything else is stdlib.
"""

import base64
import csv
import html
import io
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, font as tkfont, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk
except ImportError:
    print("Coin Tray needs Pillow for image handling.\n\n    pip install pillow\n")
    sys.exit(1)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    _DND = False
    print("Note: drag-and-drop disabled. Install with:\n"
          "    python -m pip install tkinterdnd2\n"
          "(use the same python that runs this app)", file=sys.stderr)

import coin_engine as E

try:
    import coin_camera as Cam
except ImportError:
    Cam = None

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_Base = TkinterDnD.Tk if _DND else tk.Tk

HERE = os.path.dirname(os.path.abspath(__file__))
TRAY_FILE = os.path.join(HERE, "coin_tray_data.json")
TRAY_TMP = TRAY_FILE + ".tmp"
BACKUP_DIR = os.path.join(HERE, "coin_tray_backups")
BACKUP_KEEP = 20
CONF_FILE = os.path.join(HERE, "coin_tray_config.json")
CATALOG_ID_RE = re.compile(r"^([A-Za-z]{2,4})-(\d{1,6})\b")

BOARD, DARK, LINE = "#1B4A5A", "#0F2E39", "#2C6478"
CREAM, DIM, BRASS = "#EFE7D5", "#9FB3B8", "#C08F3C"
OXIDE, ALARM, BRASSD = "#7FA894", "#D2735B", "#7A5A24"

WELL = 168


def serif():
    for f in ("Iowan Old Style", "Palatino", "Palatino Linotype", "Georgia", "Times"):
        if f in tkfont.families():
            return f
    return "Times"


def mono():
    for f in ("SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"):
        if f in tkfont.families():
            return f
    return "Courier"


def sans():
    for f in ("Inter", "Segoe UI", "Helvetica Neue", "Helvetica", "DejaVu Sans"):
        if f in tkfont.families():
            return f
    return "Helvetica"


def money(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n == 0:
        return "—"
    return "$" + format(int(round(n)), ",") if n >= 100 else "$%.2f" % n


def money_span(lo, hi, face=False):
    """Single face value, or a low–high span for researched coins."""
    if face or (lo is not None and hi is not None and float(lo or 0) == float(hi or 0)
                and float(lo or 0) > 0):
        return money(lo if lo is not None else hi)
    return "%s–%s" % (money(lo), money(hi))


def entry_tier(c):
    """Prefer stored tier; recompute for older tray rows."""
    t = (c.get("tier") or "").upper()
    if t in ("C", "U", "R", "G"):
        return t
    return E.value_tier(c)


# Two rows so the bar stays usable with Eastern / Nordic Europe
COUNTRY_CHIPS_ROW1 = (
    ("US", "United States"),
    ("CA", "Canada"),
    ("UK", "United Kingdom"),
    ("FR", "France"),
    ("DE", "Germany"),
    ("AT", "Austria"),
    ("NL", "Netherlands"),
    ("IT", "Italy"),
    ("ES", "Spain"),
    ("GR", "Greece"),
)
COUNTRY_CHIPS_ROW2 = (
    ("SU", "Soviet Union"),
    ("DDR", "East Germany"),
    ("CH", "Switzerland"),
    ("BE", "Belgium"),
    ("PT", "Portugal"),
    ("PL", "Poland"),
    ("CZ", "Czechoslovakia"),
    ("HU", "Hungary"),
    ("SE", "Sweden"),
    ("NO", "Norway"),
    ("DK", "Denmark"),
    ("IE", "Ireland"),
    ("OTHER", "Other"),
)
COUNTRY_CHIPS = COUNTRY_CHIPS_ROW1 + COUNTRY_CHIPS_ROW2

# label, denomination, series — shown under TYPE for the active country
TYPE_CHIPS = {
    "United States": (
        ("1¢", "One cent", "Lincoln Cent"),
        ("5¢", "Five cents", "Jefferson Nickel"),
        ("10¢", "One dime", "Roosevelt Dime"),
        ("25¢", "Quarter dollar", "Washington Quarter"),
        ("50¢", "Half dollar", "Kennedy Half Dollar"),
    ),
    "Canada": (
        ("1¢", "1 cent", "Canadian Cent"),
        ("5¢", "5 cents", "Canadian Nickel"),
        ("10¢", "10 cents", "Canadian Dime"),
        ("25¢", "25 cents", "Canadian Quarter"),
        ("50¢", "50 cents", "Canadian Half Dollar"),
        ("$1", "1 dollar", "Canadian Loonie"),
        ("$2", "2 dollars", "Canadian Toonie"),
    ),
    "United Kingdom": (
        ("1d", "One penny", "UK Penny"),
        ("½d", "Half penny", "UK Halfpenny"),
        ("1/-", "One shilling", "UK Shilling"),
        ("2/-", "Two shillings", "UK Florin"),
        ("5/-", "Crown", "UK Crown"),
    ),
    "France": (
        ("1 Fr", "1 franc", "French Franc"),
        ("50c", "50 centimes", "French 50 Centimes"),
    ),
    "Germany": (
        ("1 Mk", "1 mark", "German Mark"),
        ("1 Pf", "Pfennig", "German Pfennig"),
    ),
    "East Germany": (
        ("1 Mk", "1 mark", "DDR 1 Mark"),
        ("5 Mk", "5 mark", "DDR 5 Mark"),
        ("10 Pf", "10 pfennig", "DDR 10 Pfennig"),
        ("20 Pf", "20 pfennig", "DDR 20 Pfennig"),
    ),
    "Austria": (
        ("10 Gr", "10 groschen", "Austrian 10 Groschen"),
        ("5 Gr", "5 groschen", "Austrian 5 Groschen"),
        ("1 S", "1 schilling", "Austrian Schilling"),
    ),
    "Netherlands": (
        ("1 G", "1 gulden", "Dutch Guilder"),
    ),
    "Italy": (
        ("1 L", "1 lira", "Italian Lira"),
    ),
    "Spain": (
        ("1 Pts", "1 peseta", "Spanish Peseta"),
        ("50 Pts", "50 pesetas", "Spanish 50 Pesetas"),
    ),
    "Greece": (
        ("1 Dr", "1 drachma", "Greek 1 Drachma"),
        ("2 Dr", "2 drachmai", "Greek 2 Drachmai"),
        ("5 Dr", "5 drachmai", "Greek 5 Drachmai"),
        ("10 Dr", "10 drachmai", "Greek 10 Drachmai"),
        ("20 Dr", "20 drachmai", "Greek 20 Drachmai"),
        ("50 Lp", "50 lepta", "Greek 50 Lepta"),
        ("20 Lp", "20 lepta", "Greek 20 Lepta"),
    ),
    "Soviet Union": (
        ("1k", "1 kopek", "Soviet 1 Kopek"),
        ("2k", "2 kopeks", "Soviet 2 Kopeks"),
        ("3k", "3 kopeks", "Soviet 3 Kopeks"),
        ("5k", "5 kopeks", "Soviet 5 Kopeks"),
        ("10k", "10 kopeks", "Soviet 10 Kopeks"),
        ("15k", "15 kopeks", "Soviet 15 Kopeks"),
        ("20k", "20 kopeks", "Soviet 20 Kopeks"),
        ("1r", "1 ruble", "Soviet 1 Ruble"),
        ("Oly", "1 ruble", "Soviet Olympic Ruble"),
        ("Ann", "1 ruble", "Soviet Anniversary Ruble"),
    ),
    "Switzerland": (
        ("1 Fr", "1 franc", "Swiss Franc"),
        ("½ Fr", "1/2 franc", "Swiss 1/2 Franc"),
        ("20 Rp", "20 rappen", "Swiss 20 Rappen"),
    ),
    "Belgium": (
        ("1 Fr", "1 franc", "Belgian Franc"),
        ("5 Fr", "5 francs", "Belgian 5 Francs"),
    ),
    "Portugal": (
        ("1$00", "1 escudo", "Portuguese Escudo"),
        ("5$00", "5 escudos", "Portuguese 5 Escudos"),
    ),
    "Poland": (
        ("1 zł", "1 zloty", "Polish Zloty"),
        ("10 gr", "10 groszy", "Polish 10 Groszy"),
    ),
    "Czechoslovakia": (
        ("1 Kčs", "1 koruna", "Czechoslovak Koruna"),
    ),
    "Hungary": (
        ("1 Ft", "1 forint", "Hungarian Forint"),
        ("2 Ft", "2 forint", "Hungarian 2 Forint"),
    ),
    "Sweden": (
        ("1 kr", "1 krona", "Swedish Krona"),
    ),
    "Norway": (
        ("1 kr", "1 krone", "Norwegian Krone"),
    ),
    "Denmark": (
        ("1 kr", "1 krone", "Danish Krone"),
    ),
    "Ireland": (
        ("1d", "One penny", "Irish Penny"),
    ),
    "Other": (),
}


def normalize_country_name(country):
    """Map free-text COUNTRY field to a COUNTRY_CHIPS value, or ''."""
    c = (country or "").strip().lower()
    if not c:
        return ""
    aliases = {
        "united states": "United States", "usa": "United States", "us": "United States",
        "u.s.": "United States", "u.s.a.": "United States", "america": "United States",
        "canada": "Canada", "canadian": "Canada", "ca": "Canada",
        "united kingdom": "United Kingdom", "uk": "United Kingdom", "britain": "United Kingdom",
        "england": "United Kingdom", "great britain": "United Kingdom",
        "france": "France", "germany": "Germany", "austria": "Austria",
        "osterreich": "Austria", "österreich": "Austria",
        "netherlands": "Netherlands", "holland": "Netherlands",
        "italy": "Italy", "spain": "Spain", "other": "Other",
        "greece": "Greece", "hellas": "Greece", "hellenic": "Greece",
        "hellenic republic": "Greece", "gr": "Greece",
        "east germany": "East Germany", "ddr": "East Germany", "gdr": "East Germany",
        "deutsche demokratische republik": "East Germany",
        "german democratic republic": "East Germany",
        "soviet union": "Soviet Union", "ussr": "Soviet Union", "cccp": "Soviet Union",
        "sssr": "Soviet Union", "soviet": "Soviet Union", "su": "Soviet Union",
        "russia": "Soviet Union",  # CCCP-era pieces in mixed boxes
        "switzerland": "Switzerland", "helvetia": "Switzerland", "swiss": "Switzerland",
        "belgium": "Belgium", "belgique": "Belgium", "belgie": "Belgium",
        "portugal": "Portugal", "portuguesa": "Portugal",
        "poland": "Poland", "polska": "Poland",
        "czechoslovakia": "Czechoslovakia", "czech": "Czechoslovakia",
        "hungary": "Hungary", "magyar": "Hungary",
        "sweden": "Sweden", "sverige": "Sweden",
        "norway": "Norway", "norge": "Norway",
        "denmark": "Denmark", "danmark": "Denmark",
        "ireland": "Ireland", "eire": "Ireland",
    }
    if c in aliases:
        return aliases[c]
    for _label, name in COUNTRY_CHIPS:
        if name.lower() == c:
            return name
    return ""


_CODE_BY_NAME = {name: code for code, name in COUNTRY_CHIPS}
_KNOWN_CODES = {code.upper() for code, _ in COUNTRY_CHIPS}


def country_code(country):
    """Chip code for baggie IDs (US, CA, SU, …). Unknown → XX."""
    raw = (country or "").strip()
    if not raw:
        return "XX"
    up = raw.upper()
    if up in _KNOWN_CODES and up != "OTHER":
        return up
    name = normalize_country_name(raw)
    if not name or name == "Other":
        return "XX"
    return _CODE_BY_NAME.get(name, "XX")


def parse_catalog_id(name):
    """Return (CODE, n) from 'CA-0007 · …' or legacy 'CT-0003'; else (None, None)."""
    m = CATALOG_ID_RE.match((name or "").strip())
    if not m:
        return None, None
    return m.group(1).upper(), int(m.group(2))


def catalog_sort_key(c):
    """Country code, then sequence, then id (for tray Country sort)."""
    code, num = parse_catalog_id(c.get("catalogName") or "")
    if not code:
        code = country_code(c.get("country") or "")
        num = 10 ** 9
    return (code, num if num is not None else 10 ** 9, c.get("id") or 0)


def likely_silver_era(country, year, series="", denom=""):
    """Cheap heuristics for Ag bag tags on common silver cutovers."""
    try:
        y = int(re.search(r"(19|20)\d{2}", str(year or "") or "").group(0))
    except (AttributeError, ValueError, TypeError):
        return False
    name = normalize_country_name(country) or (country or "")
    blob = ("%s %s" % (series or "", denom or "")).lower()
    is_centish = bool(re.search(
        r"\b(lincoln|penny|1¢|1\s*cent|one\s*cent|canadian\s*cent)\b", blob))
    is_nickel = bool(re.search(
        r"\b(jefferson|nickel|5¢|5\s*cent|canadian\s*nickel)\b", blob))
    is_half = bool(re.search(r"\b(half|50¢|50\s*c)\b", blob))
    if name == "United States":
        if is_centish or is_nickel:
            return False
        if y <= 1964:
            return True
        if is_half and 1965 <= y <= 1970:
            return True
    if name == "Canada":
        if is_centish or is_nickel or "loonie" in blob or "toonie" in blob:
            return False
        if y <= 1967 and any(x in blob for x in (
                "dime", "10¢", "10c", "10 cent", "quarter", "25¢", "25c", "25 cent",
                "half", "50¢", "50c", "50 cent", "dollar")):
            return True
    if name == "United Kingdom" and y <= 1946:
        if any(x in blob for x in ("shilling", "florin", "crown", "sixpence", "1/-", "2/-")):
            return True
    return False


def unusual_catalog_tags(country="", year="", series="", denom="", grade="", melt="",
                         specs=None):
    """Short unusual markers for bag IDs: Ag, comm, proof."""
    tags = []
    melt_l = (melt or "").lower()
    metal = ""
    if isinstance(specs, dict):
        metal = (specs.get("metal") or "").lower()
    if ("silver" in melt_l or re.search(r"\bag\b", melt_l)
            or "silver" in metal
            or likely_silver_era(country, year, series, denom)):
        tags.append("Ag")
    blob = ("%s %s" % (series or "", denom or "")).lower()
    if any(x in blob for x in ("commemorat", "olympic", "jubilee", "memorial")):
        tags.append("comm")
    if "proof" in (grade or "").lower():
        tags.append("proof")
    # de-dupe preserve order
    out, seen = [], set()
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def baggie_label_lines(c):
    """Short lines suitable for a 2x2\" flip bag slip (tier letter is separate)."""
    cat = (c.get("catalogName") or "").strip()
    ident = (c.get("identification") or "").strip()
    grade = (c.get("grade") or "").strip()
    face = bool(c.get("faceValue"))
    tier = entry_tier(c)
    val = money_span(c.get("valueLow"), c.get("valueHigh"), face=face)
    disp = normalize_disposition(c.get("disposition"), face_value=face)
    lines = []
    if cat:
        lines.append(cat)
    if ident and ident.lower() not in (cat or "").lower():
        lines.append(ident)
    lines.append("%s · %s" % (disp.upper(), "FACE %s" % val if face else val))
    if grade:
        lines.append(grade[:40])
    if entry_is_ag(c):
        lines.append("Ag — verify silver")
    if c.get("authChecks") or c.get("specs"):
        lines.append("VERIFY IN HAND")
    return tier, lines


def encode_for_model(im):
    """JPEG for Ollama: gentle contrast so dates/mint marks read better."""
    big = im.convert("RGB").copy()
    big.thumbnail((768, 768))
    try:
        big = ImageOps.autocontrast(big, cutoff=1)
        big = big.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
    except Exception:
        pass
    buf = io.BytesIO()
    big.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def make_tray_thumb(b64, size=72):
    """Tiny JPEG for tray storage/display (keeps coin_tray_data.json lean)."""
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64)
        # Already small enough
        if len(raw) <= 4500:
            return b64
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=55, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return b64


def slim_entry_thumbs(entry):
    """Shrink oversized thumbs in place. Returns True if changed."""
    thumbs = entry.get("thumbs") or []
    if not thumbs:
        return False
    new = []
    changed = False
    for t in thumbs:
        nt = make_tray_thumb(t)
        if nt and nt != t:
            changed = True
        if nt:
            new.append(nt)
    if changed or len(new) != len(thumbs):
        entry["thumbs"] = new
        return True
    return False


DISPOSITIONS = ("Face", "Keep", "Sell", "Check")
DISPOSITION_CODES = {"Face": "F", "Keep": "K", "Sell": "S", "Check": "C"}


def entry_is_ag(c):
    """True if catalog/melt/specs/era say silver."""
    if c.get("likelyAg"):
        return True
    cat = c.get("catalogName") or ""
    if re.search(r"\bAg\b", cat):
        return True
    melt = (c.get("melt") or "").lower()
    if "silver" in melt or re.search(r"\bag\b", melt):
        return True
    specs = c.get("specs") or {}
    if "silver" in (specs.get("metal") or "").lower():
        return True
    return likely_silver_era(
        c.get("country"), c.get("year"),
        c.get("series") or "", c.get("denomination") or "")


def normalize_disposition(value, face_value=False):
    v = (value or "").strip().title()
    if v in DISPOSITIONS:
        return v
    if face_value:
        return "Face"
    return "Keep"


def tray_search_blob(c):
    tier = entry_tier(c)
    return " ".join([
        c.get("catalogName") or "",
        c.get("identification") or "",
        c.get("grade") or "",
        tier,
        E.TIER_NAMES.get(tier, ""),
        str(c.get("year") or ""),
        str(c.get("mintMark") or ""),
        str(c.get("series") or ""),
        str(c.get("denomination") or ""),
        str(c.get("country") or ""),
        str(c.get("notes") or ""),
        str(c.get("disposition") or ""),
        "ag" if entry_is_ag(c) else "",
        "face" if c.get("faceValue") else "",
    ]).lower()


def tray_matches(c, query):
    q = (query or "").strip().lower()
    if not q:
        return True
    blob = tray_search_blob(c)
    return all(part in blob for part in q.split())


class CoinTray(_Base):
    def __init__(self):
        super().__init__()
        self.title("Coin Tray")
        self.geometry("1080x860")
        self.minsize(920, 720)
        self.configure(bg=BOARD)

        self.conf = self.load_conf()
        # Merge so new EU/world hosts appear even if Settings saved an older list
        E.SOURCES = E.merge_sources(self.conf.get("sources") or E.DEFAULT_SOURCES)
        self.conf["sources"] = E.SOURCES

        self.SERIF, self.MONO, self.SANS = serif(), mono(), sans()
        self.q = queue.Queue()
        self.images = {"obverse": None, "reverse": None}   # base64 full size
        self.thumbs = {"obverse": None, "reverse": None}   # PhotoImage refs
        self.sides = None
        self._tray_recovered_from = None
        self.tray = self.load_tray()
        self.busy = False
        self.ollama_owned = False
        self.cap = None
        self._cam_photo = None
        self._cam_frame = None
        self._cam_detected = None
        self._cam_prev_detect = None
        self._cam_tick = None
        self._cam_fail = 0
        self.camera_labels = []
        # Auto-capture: wait_front | need_clear | wait_back | done
        self.auto_phase = "wait_front"
        self._hold_started = None
        self._clear_started = None
        self._hold_progress = 0.0
        self.HOLD_SECS = 2.0
        self.CLEAR_SECS = 0.4
        self.DROP_GRACE = 0.2
        self._tray_selected_id = None
        self._tray_query = ""
        self._tray_filter_country = None   # e.g. "SU" or None
        self._tray_filter_flags = set()    # Ag, RG, Face, Keep, Sell, Check
        self._filter_chip_btns = {}
        self._disposition = "Keep"
        self._disposition_btns = {}
        self._price_then_next = False
        self._suppress_live_catalog = False
        self._current_likely_ag = False

        self.style()
        self.build()
        self.enable_dnd()
        self.draw_tray()
        self.after(120, self.pump)
        self.after(200, self.boot_ollama)
        self.after(300, self.init_camera)
        self.after(800, self._maybe_slim_tray)
        if self._tray_recovered_from:
            bak = os.path.basename(self._tray_recovered_from)
            self.after(
                900,
                lambda b=bak: self.say(
                    "Tray recovered from backup %s (main file was missing or corrupt)."
                    % b, ALARM))

    # ---------------------------------------------------------------- config
    def load_conf(self):
        try:
            with open(CONF_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def save_conf(self):
        self.conf["sources"] = E.SOURCES
        self.conf["model"] = self.model.get() if hasattr(self, "model") else ""
        if hasattr(self, "cam_pick"):
            try:
                self.conf["cameraIndex"] = int(self.cam_pick.get().split()[-1])
            except Exception:
                pass
        if hasattr(self, "auto_var"):
            self.conf["autoCapture"] = bool(self.auto_var.get())
        try:
            with open(CONF_FILE, "w") as f:
                json.dump(self.conf, f, indent=1)
        except Exception:
            pass

    def _read_tray_file(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else None
        except Exception:
            return None

    def _backup_paths(self):
        if not os.path.isdir(BACKUP_DIR):
            return []
        paths = []
        for name in os.listdir(BACKUP_DIR):
            if name.startswith("coin_tray_") and name.endswith(".json"):
                paths.append(os.path.join(BACKUP_DIR, name))
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return paths

    def _prune_backups(self):
        paths = self._backup_paths()
        for old in paths[BACKUP_KEEP:]:
            try:
                os.remove(old)
            except Exception:
                pass

    def load_tray(self):
        data = self._read_tray_file(TRAY_FILE)
        if data is not None:
            return data
        for path in self._backup_paths():
            data = self._read_tray_file(path)
            if data is not None:
                self._tray_recovered_from = path
                return data
        return []

    def save_tray(self):
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            with open(TRAY_TMP, "w", encoding="utf-8") as f:
                json.dump(self.tray, f, indent=1)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
            if os.path.isfile(TRAY_FILE):
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                bak = os.path.join(BACKUP_DIR, "coin_tray_%s.json" % stamp)
                try:
                    shutil.copy2(TRAY_FILE, bak)
                    self._prune_backups()
                except Exception:
                    pass
            os.replace(TRAY_TMP, TRAY_FILE)
        except Exception as e:
            self.say("Couldn't save the tray: %s" % e, ALARM)

    def open_backups_folder(self):
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(BACKUP_DIR)
            elif sys.platform == "darwin":
                os.system('open "%s"' % BACKUP_DIR)
            else:
                os.system('xdg-open "%s"' % BACKUP_DIR)
            self.say("Opened backups folder.")
        except Exception as e:
            self.say("Couldn't open backups: %s" % e, ALARM)

    def restore_tray_backup(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = filedialog.askopenfilename(
            title="Restore tray backup",
            initialdir=BACKUP_DIR,
            filetypes=[("Tray backup", "coin_tray_*.json"), ("JSON", "*.json"),
                       ("All", "*.*")],
        )
        if not path:
            return
        data = self._read_tray_file(path)
        if data is None:
            return self.say("Couldn't read that backup.", ALARM)
        n = len(data)
        if not messagebox.askyesno(
                "Restore tray backup",
                "Replace the current tray (%d coin%s) with\n%s\n(%d coin%s)?"
                % (len(self.tray), "" if len(self.tray) == 1 else "s",
                   os.path.basename(path), n, "" if n == 1 else "s")):
            return
        # Snapshot current tray into backups before replacing
        if self.tray:
            self.save_tray()
        self.tray = data
        self._tray_selected_id = None
        self.save_tray()
        self.draw_tray()
        self.say("Restored %d coin%s from %s." % (
            n, "" if n == 1 else "s", os.path.basename(path)))

    def _maybe_slim_tray(self):
        """One-time shrink of fat thumbs left from older saves."""
        changed = False
        for c in self.tray:
            if slim_entry_thumbs(c):
                changed = True
        if changed:
            self.save_tray()
            self.draw_tray()
            self.say("Trimmed tray photo thumbs to keep the file small.")

    # ----------------------------------------------------------------- style
    def style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BOARD, foreground=CREAM, borderwidth=0)
        s.configure("TFrame", background=BOARD)
        s.configure("Dark.TFrame", background=DARK)
        s.configure("TLabel", background=BOARD, foreground=CREAM,
                    font=(self.SANS, 11))
        s.configure("Dark.TLabel", background=DARK, foreground=CREAM,
                    font=(self.SANS, 11))
        s.configure("Field.TLabel", background=DARK, foreground=OXIDE,
                    font=(self.MONO, 8))
        s.configure("Go.TButton", background=BRASS, foreground=DARK,
                    font=(self.MONO, 10, "bold"), padding=(14, 10), borderwidth=0)
        s.map("Go.TButton", background=[("disabled", BRASSD), ("active", "#D9A44F")],
              foreground=[("disabled", "#4A5F68")])
        s.configure("Ghost.TButton", background=BOARD, foreground=DIM,
                    font=(self.MONO, 9), padding=(10, 7), borderwidth=1)
        s.map("Ghost.TButton", background=[("active", DARK)])
        s.configure("ChipOn.TButton", background=BRASS, foreground=DARK,
                    font=(self.MONO, 9, "bold"), padding=(10, 7), borderwidth=1)
        s.map("ChipOn.TButton", background=[("active", "#D9A44F")],
              foreground=[("active", DARK)])
        s.configure("TEntry", fieldbackground=DARK, foreground=CREAM,
                    insertcolor=BRASS, bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, padding=5)
        s.configure("TCombobox", fieldbackground=DARK, background=DARK,
                    foreground=CREAM, arrowcolor=BRASS, bordercolor=LINE)
        s.configure("Horizontal.TProgressbar", background=BRASS, troughcolor=DARK,
                    bordercolor=DARK, lightcolor=BRASS, darkcolor=BRASS)
        self.option_add("*TCombobox*Listbox.background", DARK)
        self.option_add("*TCombobox*Listbox.foreground", CREAM)
        self.option_add("*TCombobox*Listbox.selectBackground", BRASS)
        self.option_add("*TCombobox*Listbox.selectForeground", DARK)

    # ----------------------------------------------------------------- build
    def build(self):
        self.menus()

        # Status first so the expanding body can't cover it (Tk pack order).
        bar2 = tk.Frame(self, bg=DARK)
        bar2.pack(fill="x", side="bottom")
        self.status = tk.Label(bar2, text="Ready.", bg=DARK, fg=DIM, anchor="w",
                               font=(self.SANS, 10), padx=14, pady=6)
        self.status.pack(side="left", fill="x", expand=True)
        self.retry_btn = tk.Label(bar2, text="Retry", bg=DARK, fg=BRASS,
                                  font=(self.MONO, 9), cursor="hand2", padx=10, pady=6)
        self.retry_btn.bind("<Button-1>", lambda e: self.boot_ollama())
        self.bar = ttk.Progressbar(bar2, mode="indeterminate", length=120)
        self.ollama_ok = False

        head = ttk.Frame(self, padding=(20, 16, 20, 0))
        head.pack(fill="x")
        tk.Label(head, text="Coin Tray", bg=BOARD, fg=CREAM,
                 font=(self.SERIF, 24)).pack(side="left")
        self.model = ttk.Combobox(head, state="readonly", width=24,
                                  font=(self.MONO, 9))
        self.model.pack(side="right")
        tk.Label(head, text="MODEL", bg=BOARD, fg=OXIDE,
                 font=(self.MONO, 8)).pack(side="right", padx=(0, 8))
        tk.Frame(self, bg=BRASS, height=2).pack(fill="x", padx=20, pady=(10, 0))

        body = ttk.Frame(self, padding=(20, 14, 20, 0))
        body.pack(fill="both", expand=True)

        # ---- left: the tray
        left = ttk.Frame(body, width=300)
        left.pack(side="left", fill="y", padx=(0, 18))
        left.pack_propagate(False)
        row = ttk.Frame(left)
        row.pack(fill="x")
        tk.Label(row, text="Tray", bg=BOARD, fg=CREAM,
                 font=(self.SERIF, 16)).pack(side="left")
        self.total = tk.Label(row, text="", bg=BOARD, fg=BRASS, font=(self.MONO, 9))
        self.total.pack(side="right")
        tk.Frame(left, bg=LINE, height=1).pack(fill="x", pady=(8, 0))

        find = tk.Frame(left, bg=BOARD)
        find.pack(fill="x", pady=(10, 8))
        self.tray_find = ttk.Entry(find, font=(self.SANS, 10))
        self.tray_find.pack(side="left", fill="x", expand=True)
        self.hint(self.tray_find, "Find CA-0007, SU, R…")
        self.tray_find.bind("<KeyRelease>", lambda e: self.on_tray_filter())
        self.tray_sort = ttk.Combobox(
            find, state="readonly", width=8, font=(self.MONO, 8),
            values=("Country", "Newest", "Value", "Tier"))
        self.tray_sort.set("Country")
        self.tray_sort.pack(side="left", padx=(6, 0))
        self.tray_sort.bind("<<ComboboxSelected>>", lambda e: self.draw_tray())

        # Quick filters: country / Ag / keepers / disposition
        filters = tk.Frame(left, bg=BOARD)
        filters.pack(fill="x", pady=(0, 8))
        self._filter_chip_btns = {}
        filter_specs = [
            ("all", "All"),
            ("SU", "SU"), ("CA", "CA"), ("US", "US"),
            ("Ag", "Ag"), ("RG", "R+G"),
            ("Keep", "Keep"), ("Sell", "Sell"),
            ("Check", "Check"), ("Face", "Face"),
        ]
        for i, (key, label) in enumerate(filter_specs):
            btn = ttk.Button(
                filters, text=label, style="Ghost.TButton", width=5,
                command=lambda k=key: self.toggle_tray_filter(k),
            )
            btn.grid(row=i // 5, column=i % 5, padx=2, pady=2, sticky="ew")
            self._filter_chip_btns[key] = btn
        for col in range(5):
            filters.grid_columnconfigure(col, weight=1)
        self.sync_filter_chips()

        wrap = tk.Frame(left, bg=BOARD)
        wrap.pack(fill="both", expand=True)
        self.tray_canvas = tk.Canvas(wrap, bg=BOARD, highlightthickness=0,
                                     bd=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.tray_canvas.yview)
        self.tray_box = tk.Frame(self.tray_canvas, bg=BOARD)
        self.tray_box.bind("<Configure>", lambda e: self.tray_canvas.configure(
            scrollregion=self.tray_canvas.bbox("all")))
        self._tray_win = self.tray_canvas.create_window(
            (0, 0), window=self.tray_box, anchor="nw", width=276)
        self.tray_canvas.bind(
            "<Configure>",
            lambda e: self.tray_canvas.itemconfig(self._tray_win, width=max(e.width - 4, 240)))
        self.tray_canvas.configure(yscrollcommand=bar.set)
        self.tray_canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        btns = tk.Frame(left, bg=BOARD)
        btns.pack(fill="x", pady=(10, 4))
        for i, (label, cmd) in enumerate((
                ("CSV", self.export_csv),
                ("JSON", self.export_json),
                ("Labels", self.print_baggie_labels),
        )):
            ttk.Button(btns, text=label, style="Ghost.TButton",
                       command=cmd).pack(
                side="left", expand=True, fill="x",
                padx=(0 if i == 0 else 5, 0))

        # ---- right: workspace, scrollable
        right = tk.Frame(body, bg=BOARD)
        right.pack(side="left", fill="both", expand=True)
        self.work_canvas = tk.Canvas(right, bg=BOARD, highlightthickness=0)
        wbar = ttk.Scrollbar(right, orient="vertical", command=self.work_canvas.yview)
        self.work = tk.Frame(self.work_canvas, bg=BOARD)
        self.work.bind("<Configure>", lambda e: self.work_canvas.configure(
            scrollregion=self.work_canvas.bbox("all")))
        self._wwin = self.work_canvas.create_window((0, 0), window=self.work,
                                                    anchor="nw")
        self.work_canvas.bind("<Configure>",
                              lambda e: self.work_canvas.itemconfig(self._wwin,
                                                                    width=e.width))
        self.work_canvas.configure(yscrollcommand=wbar.set)
        self.work_canvas.pack(side="left", fill="both", expand=True)
        wbar.pack(side="right", fill="y")
        self._scroll_canvases = (self.tray_canvas, self.work_canvas)
        self.bind_mousewheel()

        self.workflow_strip()
        self.camera_panel()
        self.wells()
        btn_row = tk.Frame(self.work, bg=BOARD)
        btn_row.pack(pady=(14, 0))
        self.read_btn = ttk.Button(btn_row, text="READ THE COIN", style="Go.TButton",
                                   command=self.read_coin, state="disabled", width=28)
        self.read_btn.pack()

        self.seen = tk.Frame(self.work, bg=DARK, padx=16, pady=14)
        self.fields_panel()
        self.seen.pack(fill="x", pady=(18, 0))

        self.card = tk.Frame(self.work, bg=DARK)

    def workflow_strip(self):
        strip = tk.Frame(self.work, bg=BOARD)
        strip.pack(fill="x", pady=(0, 8))
        self._step_labels = {}
        parts = [(1, "1 Capture"), (2, "2 Read"),
                 (3, "3 Fix DATE & fields"), (4, "4 Price")]
        for i, (n, text) in enumerate(parts):
            if i:
                tk.Label(strip, text="→", bg=BOARD, fg=DIM,
                         font=(self.MONO, 9)).pack(side="left", padx=4)
            lab = tk.Label(strip, text=text, bg=BOARD, fg=DIM, font=(self.MONO, 9))
            lab.pack(side="left")
            self._step_labels[n] = lab
        self.set_workflow_step(1)

    def set_workflow_step(self, n):
        for k, lab in getattr(self, "_step_labels", {}).items():
            lab.configure(fg=BRASS if k == n else DIM)

    def reveal_details(self, focus_year=True):
        """Scroll Coin details into view; optionally focus DATE for corrections."""
        self.update_idletasks()
        try:
            top = self.seen.winfo_y()
            total = max(self.work.winfo_height(), 1)
            self.work_canvas.yview_moveto(min(max(top / float(total), 0.0), 1.0))
        except Exception:
            self.work_canvas.yview_moveto(0.55)
        if not focus_year:
            return
        e = self.f.get("year")
        if not e:
            return
        e.focus_set()
        if not getattr(e, "_empty", True):
            e.selection_range(0, "end")
        lab = getattr(self, "year_label", None)
        if lab is not None:
            lab.configure(fg=BRASS)
            self.after(900, lambda: lab.configure(fg=OXIDE))

    def bind_mousewheel(self):
        def on_wheel(e):
            target = None
            for c in self._scroll_canvases:
                if self.under(c):
                    target = c
                    break
            if target is None:
                return
            if getattr(e, "delta", 0):
                d = -1 * (e.delta // 120)
            else:
                d = 1 if e.num == 5 else -1
            target.yview_scroll(d, "units")
        self.bind_all("<MouseWheel>", on_wheel)
        self.bind_all("<Button-4>", on_wheel)
        self.bind_all("<Button-5>", on_wheel)

    def under(self, canvas):
        w = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        while w is not None:
            if w is canvas:
                return True
            w = getattr(w, "master", None)
        return False

    def set_ollama_status(self, ok, detail=""):
        self.ollama_ok = ok
        if ok:
            self.retry_btn.pack_forget()
            if Cam and Cam.AVAILABLE:
                tip = " Place a coin under the camera and Capture."
            elif _DND:
                tip = " Drop photos onto the wells."
            else:
                tip = ""
            self.say(detail or ("Ready." + tip))
        else:
            self.say(detail or "Ollama isn’t running. Click Retry.", ALARM)
            self.retry_btn.pack(side="right", padx=(0, 8))

    def has_model(self):
        m = (self.model.get() or "").strip()
        return bool(m) and m not in ("", "no model found")

    def set_busy(self, on):
        self.busy = on
        if on:
            self.bar.pack(side="right", padx=14)
            self.bar.start(12)
            self.read_btn.configure(state="disabled")
            self.price_btn.configure(state="disabled")
            if hasattr(self, "price_next_btn"):
                self.price_next_btn.configure(state="disabled")
            if hasattr(self, "face_skip_btn"):
                self.face_skip_btn.configure(state="disabled")
        else:
            self.bar.stop()
            self.bar.pack_forget()
            self.read_btn.configure(
                state="normal" if any(self.images.values()) else "disabled")
            self.price_btn.configure(state="normal")
            if hasattr(self, "price_next_btn"):
                self.price_next_btn.configure(state="normal")
            if hasattr(self, "face_skip_btn"):
                self.face_skip_btn.configure(state="normal")

    def friendly_error(self, err):
        text = str(err)
        ollama_ish = (
            isinstance(err, E.OllamaDown)
            or "Ollama isn't answering" in text
            or "Ollama timed out" in text
            or "Ollama request failed" in text
            or "10061" in text
        )
        if ollama_ish:
            # Re-check — a single failed chat must not permanently mark us down
            # if the API is actually healthy.
            if E._api_up():
                self.ollama_ok = True
                self.retry_btn.pack_forget()
                return ("Model request failed. Wait a few seconds and try Read again "
                        "(first load can be slow).")
            self.ollama_ok = False
            self.retry_btn.pack(side="right", padx=(0, 8))
            return "Ollama isn’t reachable. Click Retry to start it."
        return text

    def menus(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Open front photo…", accelerator="Ctrl+1",
                      command=lambda: self.pick("obverse"))
        f.add_command(label="Open back photo…", accelerator="Ctrl+2",
                      command=lambda: self.pick("reverse"))
        f.add_command(label="Capture front from camera", accelerator="Ctrl+Shift+1",
                      command=lambda: self.capture_side("obverse"))
        f.add_command(label="Capture back from camera", accelerator="Ctrl+Shift+2",
                      command=lambda: self.capture_side("reverse"))
        f.add_separator()
        f.add_command(label="Export CSV…", command=self.export_csv)
        f.add_command(label="Export JSON…", command=self.export_json)
        f.add_command(label="Print baggie labels…", command=self.print_baggie_labels)
        f.add_separator()
        f.add_command(label="Restore tray backup…", command=self.restore_tray_backup)
        f.add_command(label="Open backups folder", command=self.open_backups_folder)
        f.add_separator()
        f.add_command(label="Settings…", command=self.settings)
        f.add_command(label="Quit", accelerator="Ctrl+Q", command=self.destroy)
        m.add_cascade(label="File", menu=f)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Setup help", command=self.help)
        m.add_cascade(label="Help", menu=h)
        self.config(menu=m)
        self.bind("<Control-Key-1>", lambda e: self.pick("obverse"))
        self.bind("<Control-Key-2>", lambda e: self.pick("reverse"))
        self.bind("<Control-Shift-Key-1>", lambda e: self.capture_side("obverse"))
        self.bind("<Control-Shift-Key-2>", lambda e: self.capture_side("reverse"))
        self.bind("<Control-q>", lambda e: self.destroy())
        self.bind("<Control-Shift-f>", lambda e: self.face_skip_coin())
        self.bind("<Control-f>", lambda e: self.face_skip_coin())

    def camera_panel(self):
        wrap = tk.Frame(self.work, bg=BOARD)
        wrap.pack(pady=(0, 4))
        pw = Cam.PREVIEW_W if Cam else 560
        ph = Cam.PREVIEW_H if Cam else 420
        self.cam_canvas = tk.Canvas(wrap, width=pw, height=ph, bg=DARK,
                                    highlightthickness=2, highlightbackground=LINE)
        self.cam_canvas.pack()
        self.cam_canvas.create_text(pw // 2, ph // 2, text="Finding cameras…",
                                    fill=DIM, font=(self.SANS, 12), tags="msg")

        self.cam_hint = tk.Label(self.work, text="Hold a coin in the green circle…",
                                 bg=BOARD, fg=BRASS, font=(self.SANS, 12))
        self.cam_hint.pack(pady=(6, 0))

        self.hold_bar = ttk.Progressbar(self.work, mode="determinate",
                                        maximum=100, length=pw)
        self.hold_bar.pack(pady=(6, 0))

        controls = tk.Frame(self.work, bg=BOARD)
        controls.pack(pady=(10, 4))
        self.cap_front_btn = ttk.Button(controls, text="CAPTURE FRONT",
                                        style="Ghost.TButton",
                                        command=lambda: self.capture_side("obverse"))
        self.cap_front_btn.pack(side="left", padx=(0, 6))
        self.cap_back_btn = ttk.Button(controls, text="CAPTURE BACK",
                                       style="Ghost.TButton",
                                       command=lambda: self.capture_side("reverse"))
        self.cap_back_btn.pack(side="left", padx=(0, 10))
        self.auto_var = tk.BooleanVar(value=self.conf.get("autoCapture", True))
        self.auto_chk = tk.Checkbutton(
            controls, text="Auto (2s)", variable=self.auto_var,
            command=self.on_auto_toggle, bg=BOARD, fg=CREAM,
            activebackground=BOARD, activeforeground=CREAM,
            selectcolor=DARK, highlightthickness=0, font=(self.SANS, 10))
        self.auto_chk.pack(side="left", padx=(0, 10))
        self.next_btn = ttk.Button(controls, text="NEXT COIN", style="Ghost.TButton",
                                   command=self.next_coin)
        self.next_btn.pack(side="left", padx=(0, 12))
        tk.Label(controls, text="CAM", bg=BOARD, fg=OXIDE,
                 font=(self.MONO, 8)).pack(side="left", padx=(0, 4))
        self.cam_pick = ttk.Combobox(controls, state="readonly", width=12,
                                     font=(self.MONO, 9))
        self.cam_pick.pack(side="left")
        self.cam_pick.bind("<<ComboboxSelected>>", lambda e: self.switch_camera())

    def set_cam_hint(self, text, color=None):
        self.cam_hint.configure(text=text, fg=color or BRASS)

    def on_auto_toggle(self):
        self.conf["autoCapture"] = bool(self.auto_var.get())
        self._hold_started = None
        self._hold_progress = 0.0
        self.hold_bar["value"] = 0
        if self.auto_var.get() and self.auto_phase == "done":
            self.set_cam_hint("Auto on — click Next coin for another")
        elif self.auto_var.get():
            self._sync_auto_hint()
        else:
            self.set_cam_hint("Auto off — use Capture front / back")

    def _sync_auto_hint(self):
        if self.auto_phase == "wait_front":
            self.set_cam_hint("Hold coin steady for 2s to capture the front. "
                             "Soft even light — avoid glare on the date.")
        elif self.auto_phase == "need_clear":
            self.set_cam_hint("Front saved — lift the coin, then flip it")
        elif self.auto_phase == "wait_back":
            self.set_cam_hint("Hold the back steady for 2s")
        elif self.auto_phase == "done":
            self.set_cam_hint("Both sides captured — hit Read, or Next coin. "
                             "Even side light helps the date.")

    def next_coin(self):
        """Clear wells and re-arm auto-capture for another coin."""
        self.images = {"obverse": None, "reverse": None}
        self.sides = None
        for side in ("obverse", "reverse"):
            self.blank(side)
        self.read_btn.configure(state="disabled")
        self.auto_phase = "wait_front"
        self._hold_started = None
        self._clear_started = None
        self._hold_progress = 0.0
        self.hold_bar["value"] = 0
        self._cam_prev_detect = None
        self._suppress_live_catalog = True
        try:
            for key in ("catalog", "year", "mint", "country", "denom",
                        "series", "grade", "url"):
                if key in self.f:
                    self.put(key, "")
        finally:
            self._suppress_live_catalog = False
        self.rebuild_type_chips("United States")
        self.sync_identity_chips()
        if hasattr(self, "auth_frame"):
            for w in self.auth_frame.winfo_children():
                w.destroy()
            self.auth_frame.pack_forget()
            self._auth_bundle = None
        self.saw.configure(state="normal")
        self.saw.delete("1.0", "end")
        self.saw.insert("1.0", "Load both sides, then Read. The model’s notes will show here.")
        self.saw.configure(state="disabled")
        for w in self.card.winfo_children():
            w.destroy()
        self.card.pack_forget()
        if self.auto_var.get():
            self._sync_auto_hint()
        else:
            self.set_cam_hint("Wells cleared — capture front and back")
        self.set_workflow_step(1)
        self.say("Ready for the next coin.")

    def init_camera(self):
        if Cam is None or not Cam.AVAILABLE:
            self.cam_canvas.delete("msg")
            self.cam_canvas.create_text(
                Cam.PREVIEW_W // 2 if Cam else 280,
                Cam.PREVIEW_H // 2 if Cam else 210,
                text="Install OpenCV for live camera\n  pip install opencv-python-headless",
                fill=DIM, font=(self.SANS, 11), justify="center", tags="msg")
            self.cap_front_btn.configure(state="disabled")
            self.cap_back_btn.configure(state="disabled")
            self.set_cam_hint("Camera unavailable — drop photos onto the wells", DIM)
            return
        self.set_cam_hint("Finding cameras…", DIM)

        def job():
            try:
                cams = Cam.list_cameras()
                self.q.put(("cameras", cams))
            except Exception as e:
                self.q.put(("cameras", e))
        threading.Thread(target=job, daemon=True).start()

    def _finish_camera_list(self, cams):
        if isinstance(cams, Exception):
            self.set_cam_hint("Camera scan failed: %s" % cams, ALARM)
            return
        self.camera_labels = ["Camera %d" % i for i, _ in cams] or []
        if not self.camera_labels:
            self.cam_canvas.delete("all")
            self.cam_canvas.create_text(
                Cam.PREVIEW_W // 2, Cam.PREVIEW_H // 2,
                text="No camera found.\nPlug in your Logitech and retry,\nor drop photos onto the wells.",
                fill=DIM, font=(self.SANS, 11), justify="center", tags="msg")
            self.cap_front_btn.configure(state="disabled")
            self.cap_back_btn.configure(state="disabled")
            self.set_cam_hint("No camera — use drop/click on the wells", DIM)
            return
        self.cam_pick.configure(values=self.camera_labels)
        want = self.conf.get("cameraIndex", 0)
        label = "Camera %d" % want
        if label not in self.camera_labels:
            label = self.camera_labels[0]
        self.cam_pick.set(label)
        self.open_selected_camera()
        if self.auto_var.get():
            self._sync_auto_hint()

    def open_selected_camera(self):
        if Cam is None or not Cam.AVAILABLE:
            return
        self.release_camera(keep_tick=False)
        try:
            idx = int(self.cam_pick.get().split()[-1])
        except Exception:
            idx = 0
        self.cap = Cam.open_camera(idx)
        if self.cap is None:
            self.cam_canvas.delete("all")
            self.cam_canvas.create_text(
                Cam.PREVIEW_W // 2, Cam.PREVIEW_H // 2,
                text="Couldn’t open %s" % self.cam_pick.get(),
                fill=ALARM, font=(self.SANS, 11), tags="msg")
            self.set_cam_hint("Couldn’t open camera", ALARM)
            return
        self.conf["cameraIndex"] = idx
        self._cam_fail = 0
        self.cap_front_btn.configure(state="normal")
        self.cap_back_btn.configure(state="normal")
        self.tick_camera()

    def switch_camera(self):
        self.open_selected_camera()

    def release_camera(self, keep_tick=False):
        if not keep_tick and self._cam_tick is not None:
            try:
                self.after_cancel(self._cam_tick)
            except Exception:
                pass
            self._cam_tick = None
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def tick_camera(self):
        if self.cap is None or Cam is None:
            return
        ok, frame = self.cap.read()
        if not ok or frame is None:
            self._cam_fail += 1
            if self._cam_fail >= 12:
                self._cam_fail = 0
                self.set_cam_hint("Reconnecting camera…", ALARM)
                self.say("Reconnecting camera…", ALARM)
                try:
                    idx = int(self.cam_pick.get().split()[-1])
                except Exception:
                    idx = 0
                self.release_camera(keep_tick=True)
                self.cap = Cam.open_camera(idx)
            self._cam_tick = self.after(66, self.tick_camera)
            return
        self._cam_fail = 0
        self._cam_frame = frame

        detected = None
        progress = 0.0
        if not self.busy:
            detected = Cam.detect_coin(frame)
            stable = False
            if detected:
                if not self._cam_prev_detect or Cam.detection_stable(
                        self._cam_prev_detect, detected):
                    stable = True
                self._cam_prev_detect = detected
            progress = self._auto_capture_tick(detected if stable else None)
        else:
            self._hold_started = None
            self._hold_progress = 0.0

        self._cam_detected = detected
        overlay = Cam.draw_overlays(frame, detected, progress=progress)
        photo, _ = Cam.preview_photoimage(overlay)
        self._cam_photo = photo
        self.cam_canvas.delete("all")
        self.cam_canvas.create_image(0, 0, anchor="nw", image=photo)
        self.hold_bar["value"] = int(progress * 100)
        self._cam_tick = self.after(66, self.tick_camera)

    def _auto_capture_tick(self, detected):
        """Advance auto-capture state machine. Returns 0..1 hold progress."""
        now = time.monotonic()
        if not self.auto_var.get() or self.auto_phase == "done":
            self._hold_started = None
            return 0.0

        if self.auto_phase == "need_clear":
            if detected is None:
                if self._clear_started is None:
                    self._clear_started = now
                elif now - self._clear_started >= self.CLEAR_SECS:
                    self.auto_phase = "wait_back"
                    self._clear_started = None
                    self._hold_started = None
                    self._cam_prev_detect = None
                    self._sync_auto_hint()
            else:
                self._clear_started = None
            return 0.0

        if self.auto_phase not in ("wait_front", "wait_back"):
            return 0.0

        if detected is None:
            if self._hold_started is not None:
                if now - getattr(self, "_last_detect_at", 0) > self.DROP_GRACE:
                    self._hold_started = None
                    self._cam_prev_detect = None
            return 0.0

        self._last_detect_at = now
        if self._hold_started is None:
            self._hold_started = now
        elapsed = now - self._hold_started
        progress = min(1.0, elapsed / self.HOLD_SECS)
        if elapsed >= self.HOLD_SECS:
            side = "obverse" if self.auto_phase == "wait_front" else "reverse"
            self._hold_started = None
            self.capture_side(side, from_auto=True)
            if side == "obverse":
                self.auto_phase = "need_clear"
                self._clear_started = None
                self._cam_prev_detect = None
                self._sync_auto_hint()
                self._flash_preview()
            else:
                self.auto_phase = "done"
                self._sync_auto_hint()
                self._flash_preview()
            return 0.0
        return progress

    def _flash_preview(self):
        self.cam_canvas.configure(highlightbackground=BRASS)
        self.after(350, lambda: self.cam_canvas.configure(highlightbackground=LINE))

    def capture_side(self, side, from_auto=False):
        if Cam is None or self._cam_frame is None:
            return self.say("Camera isn’t ready. Drop a photo onto the well instead.", ALARM)
        crop = Cam.crop_roi(self._cam_frame, self._cam_detected)
        im = Cam.frame_to_pil(crop)
        self.load_from_pil(side, im, label="auto" if from_auto else "camera")
        if not from_auto:
            # Manual capture advances phase sensibly
            if side == "obverse" and self.auto_phase == "wait_front":
                self.auto_phase = "need_clear"
                self._sync_auto_hint()
            elif side == "reverse" and self.auto_phase in ("wait_back", "need_clear", "wait_front"):
                self.auto_phase = "done"
                self._sync_auto_hint()

    def wells(self):
        row = tk.Frame(self.work, bg=BOARD)
        row.pack(pady=(8, 0))
        self.well_row = row
        self.canv = {}
        for side, label in (("obverse", "FRONT"), ("reverse", "BACK")):
            col = tk.Frame(row, bg=BOARD)
            col.pack(side="left", padx=18)
            c = tk.Canvas(col, width=WELL, height=WELL, bg=BOARD,
                          highlightthickness=0, cursor="hand2")
            c.pack()
            c.bind("<Button-1>", lambda e, s=side: self.pick(s))
            self.canv[side] = c
            self.blank(side)
            tk.Label(col, text=label, bg=BOARD, fg=BRASS,
                     font=(self.MONO, 8)).pack(pady=(6, 0))

    def blank(self, side):
        c = self.canv[side]
        c.delete("all")
        c.create_oval(4, 4, WELL - 4, WELL - 4, outline=LINE, width=2, dash=(6, 5),
                      fill=DARK)
        c.create_text(WELL // 2, WELL // 2, text="+", fill=DIM,
                      font=(self.MONO, 22))

    def fields_panel(self):
        p = self.seen
        tk.Label(p, text="Coin details", bg=DARK, fg=CREAM,
                 font=(self.SERIF, 15)).pack(anchor="w")
        self.saw = tk.Text(p, height=3, bg=DARK, fg=DIM, wrap="word", bd=0,
                           font=(self.SANS, 10), highlightthickness=0,
                           padx=0, pady=6)
        self.saw.pack(fill="x")
        self.saw.tag_config("k", foreground=CREAM, font=(self.SANS, 10, "bold"))
        self.saw.insert("1.0", "Load both sides, then Read. The model’s notes will show here.")
        self.saw.configure(state="disabled")
        tk.Label(p, text="Step 3 — Fix country/type if needed, fix DATE, then PRICE IT "
                         "(or PRICE & NEXT). Foreign / old silver: use AUTHENTICITY CHECK "
                         "with a scale — photos cannot prove a coin is genuine.",
                 bg=DARK, fg=OXIDE, font=(self.SANS, 9), justify="left",
                 wraplength=520).pack(anchor="w")
        self.sort_hint = tk.Label(
            p, text="", bg=DARK, fg=BRASS, font=(self.MONO, 9),
            justify="left", wraplength=520, anchor="w")
        self.sort_hint.pack(anchor="w", pady=(4, 0))

        self._country_btns = {}
        country_wrap = tk.Frame(p, bg=DARK)
        country_wrap.pack(fill="x", pady=(8, 0))
        tk.Label(country_wrap, text="COUNTRY", bg=DARK, fg=OXIDE,
                 font=(self.MONO, 8)).pack(anchor="w")
        for row_chips in (COUNTRY_CHIPS_ROW1, COUNTRY_CHIPS_ROW2):
            country_row = tk.Frame(country_wrap, bg=DARK)
            country_row.pack(fill="x", pady=(4, 0))
            for label, country in row_chips:
                btn = ttk.Button(
                    country_row, text=label, style="Ghost.TButton", width=5,
                    command=lambda c=country: self.apply_country_chip(c),
                )
                btn.pack(side="left", padx=(0, 4))
                self._country_btns[country] = btn

        self.type_chips_row = tk.Frame(p, bg=DARK)
        self.type_chips_row.pack(fill="x", pady=(6, 0))
        self._type_chip_btns = []  # (btn, denom, series)
        self._type_chips_country = None
        self.rebuild_type_chips("United States")

        self.auth_frame = tk.Frame(p, bg=DARK)
        self._auth_bundle = None
        # packed when refresh_auth_panel has content

        grid = tk.Frame(p, bg=DARK)
        grid.pack(fill="x", pady=(10, 0))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self._details_grid = grid
        self.f = {}
        self.year_label = None

        cat = tk.Frame(grid, bg=DARK)
        cat.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        tk.Label(cat, text="CATALOG NAME", bg=DARK, fg=BRASS,
                 font=(self.MONO, 8)).pack(anchor="w")
        cat_row = tk.Frame(cat, bg=DARK)
        cat_row.pack(fill="x")
        e = ttk.Entry(cat_row, font=(self.SANS, 11))
        e.pack(side="left", fill="x", expand=True)
        self.hint(e, "CA-0007 · U · year type — baggie ID")
        self.f["catalog"] = e
        ttk.Button(cat_row, text="UPDATE", style="Ghost.TButton",
                   command=self.update_catalog_name, width=10).pack(
            side="left", padx=(8, 0))

        spec = [("year", "DATE", "year on coin", 1, 0),
                ("mint", "MINT MARK", "P, D, S, or none", 1, 1),
                ("country", "COUNTRY", "country", 2, 0),
                ("denom", "DENOMINATION", "e.g. five cents", 2, 1),
                ("series", "SERIES OR TYPE", "e.g. Jefferson Nickel", 3, 0),
                ("grade", "GRADE ESTIMATE", "e.g. circulated", 3, 1),
                ("url", "EXTRA SOURCE URL (OPTIONAL)", "paste a price-guide page", 4, 0)]
        live_keys = {"year", "mint", "denom", "series", "country"}
        for key, label, hint, r, col in spec:
            cell = tk.Frame(grid, bg=DARK)
            span = 2 if key == "url" else 1
            cell.grid(row=r, column=col, columnspan=span, sticky="ew",
                      padx=(0, 10 if col == 0 else 0), pady=(0, 8))
            lab = tk.Label(cell, text=label, bg=DARK, fg=OXIDE,
                           font=(self.MONO, 8))
            lab.pack(anchor="w")
            if key == "year":
                self.year_label = lab
            e = ttk.Entry(cell, font=(self.SANS, 11))
            e.pack(fill="x")
            self.hint(e, hint)
            self.f[key] = e
            if key in live_keys:
                e.bind("<FocusOut>", lambda ev: self._live_catalog_tick(), add="+")
                if key in ("country", "series", "denom", "year"):
                    e.bind("<FocusOut>", lambda ev: self.refresh_auth_panel(), add="+")
                    e.bind("<FocusOut>", lambda ev: self.update_sort_hint(), add="+")
                if key == "country":
                    e.bind("<FocusOut>", lambda ev: self.sync_identity_chips(), add="+")
                if key in ("series", "denom"):
                    e.bind("<FocusOut>", lambda ev: self.sync_identity_chips(), add="+")

        disp_row = tk.Frame(p, bg=DARK)
        disp_row.pack(fill="x", pady=(4, 0))
        tk.Label(disp_row, text="STATUS", bg=DARK, fg=OXIDE,
                 font=(self.MONO, 8)).pack(side="left", padx=(0, 8))
        self._disposition_btns = {}
        for name in DISPOSITIONS:
            btn = ttk.Button(
                disp_row, text=name, style="Ghost.TButton", width=6,
                command=lambda n=name: self.set_disposition(n),
            )
            btn.pack(side="left", padx=(0, 4))
            self._disposition_btns[name] = btn
        self.sync_disposition_chips()

        price_row = tk.Frame(p, bg=DARK)
        price_row.pack(fill="x", pady=(6, 0))
        self.price_btn = ttk.Button(price_row, text="PRICE IT", style="Go.TButton",
                                    command=self.price_coin)
        self.price_btn.pack(side="left", fill="x", expand=True)
        self.price_next_btn = ttk.Button(
            price_row, text="PRICE & NEXT", style="Go.TButton",
            command=lambda: self.price_coin(then_next=True))
        self.price_next_btn.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.face_skip_btn = ttk.Button(
            price_row, text="FACE SKIP", style="Ghost.TButton",
            command=self.face_skip_coin)
        self.face_skip_btn.pack(side="left", padx=(8, 0))

        look_row = tk.Frame(p, bg=DARK)
        look_row.pack(fill="x", pady=(6, 0))
        ttk.Button(
            look_row, text="FIND SIMILAR", style="Ghost.TButton",
            command=self.find_similar_coin,
        ).pack(side="left")
        tk.Label(
            look_row,
            text="No date? FIND SIMILAR guesses from the photo and lists "
                 "lookalike sold/asking prices here (no browser).",
            bg=DARK, fg=DIM, font=(self.SANS, 9), wraplength=420, justify="left",
        ).pack(side="left", padx=(10, 0))

        self.sync_identity_chips()
        self.update_sort_hint()

    def rebuild_type_chips(self, country):
        """Rebuild TYPE chip row for the active country."""
        if not hasattr(self, "type_chips_row"):
            return
        for w in self.type_chips_row.winfo_children():
            w.destroy()
        self._type_chip_btns = []
        key = country if country in TYPE_CHIPS else "United States"
        if country == "Other":
            key = "Other"
        self._type_chips_country = key
        label = "TYPE" if key != "United States" else "US TYPE"
        tk.Label(self.type_chips_row, text=label, bg=DARK, fg=OXIDE,
                 font=(self.MONO, 8)).pack(side="left", padx=(0, 8))
        chips = TYPE_CHIPS.get(key) or ()
        if not chips:
            tk.Label(self.type_chips_row,
                     text="edit denomination below",
                     bg=DARK, fg=DIM, font=(self.SANS, 9)).pack(side="left")
            return
        for chip_label, denom, series in chips:
            btn = ttk.Button(
                self.type_chips_row, text=chip_label, style="Ghost.TButton",
                width=6,
                command=lambda d=denom, s=series, c=key: self.apply_type_chip(d, s, c),
            )
            btn.pack(side="left", padx=(0, 4))
            self._type_chip_btns.append((btn, denom, series))

    def sync_identity_chips(self):
        """Highlight country/type chips from the current COUNTRY / SERIES fields."""
        if not hasattr(self, "_country_btns"):
            return
        country = normalize_country_name(self.val("country"))
        if not country and self.val("country"):
            country = "Other"
        # Rebuild type row when country changes
        want_types = country if country in TYPE_CHIPS else (
            "United States" if not country else "Other")
        if want_types != getattr(self, "_type_chips_country", None):
            self.rebuild_type_chips(want_types if country else "United States")
            if not country:
                # No country yet — show US types but none selected
                pass

        for name, btn in self._country_btns.items():
            on = bool(country) and name == country
            btn.configure(style="ChipOn.TButton" if on else "Ghost.TButton")

        series = (self.val("series") or "").strip().lower()
        denom = (self.val("denom") or "").strip().lower()
        for btn, d, s in getattr(self, "_type_chip_btns", []):
            on = (series and series == s.lower()) or (
                denom and denom == d.lower()) or (
                series and s.lower() in series) or (
                denom and d.lower() in denom)
            btn.configure(style="ChipOn.TButton" if on else "Ghost.TButton")

    def _type_belongs_to_country(self, country, denom=None, series=None):
        """True if denom/series matches a TYPE chip for that country."""
        chips = TYPE_CHIPS.get(country) or ()
        d = (denom if denom is not None else self.val("denom") or "").strip().lower()
        s = (series if series is not None else self.val("series") or "").strip().lower()
        if not d and not s:
            return True
        for _lab, chip_d, chip_s in chips:
            if d and d == (chip_d or "").lower():
                return True
            if s and s == (chip_s or "").lower():
                return True
            if s and (chip_s or "").lower() in s:
                return True
        return False

    def apply_country_chip(self, country):
        """Set COUNTRY from region chips; drop a type that belongs to another country."""
        prev = self.val("country")
        self.put("country", country)
        # Switching country must not leave "UK Penny" / "Lincoln Cent" in the catalog
        if country != prev and not self._type_belongs_to_country(country):
            self.put("series", "")
            self.put("denom", "")
        self.rebuild_type_chips(country if country in TYPE_CHIPS else "Other")
        self.sync_identity_chips()
        self.refresh_catalog_name(quiet=True)
        self.refresh_auth_panel()
        self.update_sort_hint()
        self.say("Country set to %s — tap a type chip or edit denom, fix DATE, then price."
                 % country)

    def apply_type_chip(self, denom, series, country=None):
        """One-tap denomination/series fix from the type chips."""
        if country:
            self.put("country", country)
        elif not self.val("country"):
            self.put("country", "United States")
        self.put("denom", denom)
        self.put("series", series)
        self.sync_identity_chips()
        self.refresh_catalog_name(quiet=True)
        self.refresh_auth_panel()
        self.set_workflow_step(3)
        self.after(40, lambda: self.reveal_details(focus_year=True))
        self.say("Set to %s — fix DATE if needed, then PRICE IT or PRICE & NEXT."
                 % series)
        self.update_sort_hint()

    def set_disposition(self, name, quiet=False, update_tray=True):
        name = normalize_disposition(name)
        self._disposition = name
        self.sync_disposition_chips()
        # If a tray entry is selected, update it in place
        if update_tray and self._tray_selected_id is not None:
            for c in self.tray:
                if c.get("id") == self._tray_selected_id:
                    c["disposition"] = name
                    self.save_tray()
                    self.draw_tray()
                    break
        if not quiet:
            self.say("Status: %s" % name)

    def sync_disposition_chips(self):
        cur = normalize_disposition(getattr(self, "_disposition", "Keep"))
        for name, btn in (getattr(self, "_disposition_btns", None) or {}).items():
            btn.configure(style="ChipOn.TButton" if name == cur else "Ghost.TButton")

    def current_likely_ag(self):
        tags = unusual_catalog_tags(
            country=self.val("country"),
            year=self.val("year"),
            series=self.val("series"),
            denom=self.val("denom"),
            grade=self.val("grade"),
            melt="",
            specs=(getattr(self, "_auth_bundle", None) or {}).get("specs"),
        )
        if "Ag" in tags:
            return True
        if hasattr(E, "lookup_type_entry"):
            try:
                entry = E.lookup_type_entry(
                    self.val("country"), self.val("series"), self.val("denom"))
                if entry and "silver" in (
                        (entry.get("specs") or {}).get("metal") or "").lower():
                    return True
            except Exception:
                pass
        return False

    def update_sort_hint(self):
        if not hasattr(self, "sort_hint"):
            return
        has_id = bool(
            self.val("country") or self.val("year")
            or self.val("series") or self.val("denom"))
        if not has_id:
            self.sort_hint.configure(text="", fg=DIM)
            self._current_likely_ag = False
            return
        ag = self.current_likely_ag()
        self._current_likely_ag = ag
        if ag:
            self.sort_hint.configure(
                text="Likely silver (Ag) — prioritize for the Keep / Check pile",
                fg=BRASS)
        else:
            self.sort_hint.configure(
                text="Base metal — usually low priority unless scarce date / error",
                fg=DIM)

    def toggle_tray_filter(self, key):
        if key == "all":
            self._tray_filter_country = None
            self._tray_filter_flags = set()
        elif key in ("SU", "CA", "US", "UK", "FR", "DE", "XX"):
            if self._tray_filter_country == key:
                self._tray_filter_country = None
            else:
                self._tray_filter_country = key
        elif key in ("Ag", "RG") or key in DISPOSITIONS:
            flags = self._tray_filter_flags
            if key in flags:
                flags.discard(key)
            else:
                # Disposition filters are exclusive with each other
                if key in DISPOSITIONS:
                    for d in DISPOSITIONS:
                        flags.discard(d)
                flags.add(key)
        self.sync_filter_chips()
        self.draw_tray()

    def sync_filter_chips(self):
        active_all = (
            not self._tray_filter_country and not self._tray_filter_flags)
        for key, btn in (getattr(self, "_filter_chip_btns", None) or {}).items():
            on = False
            if key == "all":
                on = active_all
            elif key in ("SU", "CA", "US", "UK", "FR", "DE", "XX"):
                on = self._tray_filter_country == key
            else:
                on = key in self._tray_filter_flags
            btn.configure(style="ChipOn.TButton" if on else "Ghost.TButton")

    def tray_chip_matches(self, c):
        if self._tray_filter_country:
            code, _n = parse_catalog_id(c.get("catalogName") or "")
            if not code:
                code = country_code(c.get("country") or "")
            if code != self._tray_filter_country:
                return False
        flags = self._tray_filter_flags
        if "Ag" in flags and not entry_is_ag(c):
            return False
        if "RG" in flags and entry_tier(c) not in ("R", "G"):
            return False
        for d in DISPOSITIONS:
            if d in flags:
                want = normalize_disposition(
                    c.get("disposition"), face_value=bool(c.get("faceValue")))
                if want != d:
                    return False
        return True

    def find_tray_duplicate(self, year, country, denom, mint, series):
        """Return an existing tray entry that looks like the same coin."""
        y = (year or "").strip()
        co = normalize_country_name(country) or (country or "").strip().lower()
        den = (denom or series or "").strip().lower()
        mi = (mint or "").strip().lower()
        if mi in ("none", "unclear", "n/a", "p"):
            mi = ""
        if not (y and co and den):
            return None
        for c in self.tray:
            cy = (c.get("year") or "").strip()
            cco = normalize_country_name(c.get("country")) or (
                c.get("country") or "").strip().lower()
            cden = (c.get("denomination") or c.get("series") or "").strip().lower()
            cmi = (c.get("mintMark") or "").strip().lower()
            if cmi in ("none", "unclear", "n/a", "p"):
                cmi = ""
            if cy == y and cco == co and cden == den and cmi == mi:
                return c
        return None

    def refresh_auth_panel(self):
        """Show AUTHENTICITY CHECK when foreign / old / silver specs apply."""
        if not hasattr(self, "auth_frame"):
            return
        for w in self.auth_frame.winfo_children():
            w.destroy()
        a = {"year": self.val("year"), "country": self.val("country"),
             "denomination": self.val("denom"), "series": self.val("series"),
             "observed": self.sides}
        hit = E.propose_identity(self.sides) if self.sides else None
        auth = E.authenticity_for_attribution(a, catalog_hit=hit)
        self._auth_bundle = auth
        if not auth:
            self.auth_frame.pack_forget()
            self.update_sort_hint()
            return
        if not self.auth_frame.winfo_ismapped():
            self.auth_frame.pack(fill="x", pady=(10, 0), before=self._details_grid)
        box = tk.Frame(self.auth_frame, bg=DARK, highlightbackground=BRASSD,
                       highlightthickness=1, padx=12, pady=10)
        box.pack(fill="x")
        tk.Label(box, text="AUTHENTICITY CHECK", bg=DARK, fg=BRASS,
                 font=(self.MONO, 8)).pack(anchor="w")
        specs = auth.get("specs") or {}
        if specs:
            bits = []
            if specs.get("diameter_mm"):
                bits.append("~%s mm" % specs["diameter_mm"])
            if specs.get("weight_g"):
                bits.append("~%s g" % specs["weight_g"])
            if specs.get("metal"):
                bits.append(str(specs["metal"]))
            if bits:
                tk.Label(box, text="Expected: " + " · ".join(bits),
                         bg=DARK, fg=CREAM, font=(self.SANS, 10),
                         anchor="w").pack(anchor="w", pady=(4, 0))
        tk.Label(box, text=auth.get("authNote") or "",
                 bg=DARK, fg=OXIDE, font=(self.SANS, 9),
                 wraplength=500, justify="left", anchor="w").pack(anchor="w", pady=(4, 0))
        for x in (auth.get("authChecks") or [])[:6]:
            tk.Label(box, text="• " + str(x), bg=DARK, fg=CREAM, anchor="w",
                     font=(self.SANS, 9), wraplength=500,
                     justify="left").pack(anchor="w", pady=(2, 0))
        self.update_sort_hint()

    def _live_catalog_tick(self):
        if self._suppress_live_catalog or self.busy:
            return
        if not (self.val("year") or self.val("series") or self.val("denom")
                or self.val("country")):
            return
        self.refresh_catalog_name(quiet=True)

    def hint(self, entry, text):
        """Placeholder text. Tracked with a flag, not by comparing strings — a
        coin really dated 1943 must not read as an empty 'DATE' field."""
        entry._hint = text
        entry._empty = True
        entry.insert(0, text)
        entry.configure(foreground=DIM)

        def clear(e):
            if entry._empty:
                entry.delete(0, "end")
                entry.configure(foreground=CREAM)
                entry._empty = False

        def restore(e):
            if not entry.get().strip():
                entry._empty = True
                entry.delete(0, "end")
                entry.insert(0, text)
                entry.configure(foreground=DIM)
        entry.bind("<FocusIn>", clear)
        entry.bind("<FocusOut>", restore)

    def val(self, key):
        e = self.f[key]
        return "" if e._empty else e.get().strip()

    def put(self, key, value):
        e = self.f[key]
        e.delete(0, "end")
        if value:
            e.insert(0, value)
            e.configure(foreground=CREAM)
            e._empty = False
        else:
            e.insert(0, e._hint)
            e.configure(foreground=DIM)
            e._empty = True

    # ---------------------------------------------------------------- images
    def pick(self, side):
        path = filedialog.askopenfilename(
            title="Photo of the %s" % side,
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"),
                       ("All files", "*.*")])
        if path:
            self.load_from_path(side, path)

    def load_from_path(self, side, path):
        path = os.path.normpath(path.strip().strip("{}"))
        try:
            im = Image.open(path)
            im.load()
        except Exception as e:
            return messagebox.showerror(
                "Can't open that",
                "%s\n\nIf it's a HEIC file from an iPhone, export it as JPEG first."
                % e)
        self.load_from_pil(side, im.convert("RGB"), label=os.path.basename(path))

    def load_from_pil(self, side, im, label="photo"):
        im = im.convert("RGB")
        self.images[side] = encode_for_model(im)
        self.show_well(side, im)
        self.read_btn.configure(state="normal")
        self.ensure_catalog_id()
        if all(self.images.values()):
            msg = "Both sides ready — hit Read the coin."
            self.set_workflow_step(2)
        elif side == "obverse":
            msg = "Front captured — flip for the back."
            self.set_workflow_step(1)
        else:
            msg = "Back captured — capture the front if needed."
            self.set_workflow_step(1)
        self.say("Loaded %s (%s). %s" % (side, label, msg))

    def current_country_code(self):
        return country_code(self.val("country"))

    def ensure_catalog_id(self):
        """Seed CATALOG NAME with the next CC-NNNN if the field is still empty."""
        if self.val("catalog"):
            return
        self.put("catalog", self.next_catalog_id())

    def next_catalog_id(self, code=None):
        code = (code or self.current_country_code() or "XX").upper()
        if code in ("OTHER", "CT"):
            code = "XX"
        nums = []
        for c in self.tray:
            cc, n = parse_catalog_id(c.get("catalogName") or "")
            if cc == code and n is not None:
                nums.append(n)
        return "%s-%04d" % (code, (max(nums) if nums else 0) + 1)

    def suggest_catalog(self):
        """After a read, fill catalog from current fields (keeps CC-NNNN when possible)."""
        self.refresh_catalog_name(quiet=True)

    def update_catalog_name(self):
        """UPDATE button: rebuild catalog from DATE / mint / series / denom."""
        self.reconcile_identity_fields()
        self.refresh_catalog_name(quiet=False)

    def refresh_catalog_name(self, quiet=False, tier=None, melt="", specs=None):
        """Build CC-NNNN · T · detail [Ag/comm/proof]. Converts legacy CT- ids."""
        code = self.current_country_code()
        cur = self.val("catalog")
        cc, num = parse_catalog_id(cur)
        # Keep per-country sequence when code still matches; else allocate
        if num is not None and cc and cc not in ("CT",) and cc == code:
            prefix = "%s-%04d" % (code, num)
        else:
            prefix = self.next_catalog_id(code)

        if tier is None:
            # Prefer last priced card if fields match; else provisional C
            tier = "C"
            try:
                # If catalog already embeds a tier letter, keep it until Price it
                m = re.search(r"·\s*([CURG])\s*·", cur or "", re.I)
                if m:
                    tier = m.group(1).upper()
            except Exception:
                pass
        else:
            tier = str(tier).upper() if str(tier).upper() in ("C", "U", "R", "G") else "C"

        mint = self.val("mint")
        if mint.lower() in ("none", "unclear", "n/a"):
            mint = ""
        bits = [self.val("year"), mint, self.val("series") or self.val("denom")]
        detail = " ".join(b for b in bits if b)
        tags = unusual_catalog_tags(
            country=self.val("country"),
            year=self.val("year"),
            series=self.val("series"),
            denom=self.val("denom"),
            grade=self.val("grade"),
            melt=melt or "",
            specs=specs,
        )
        # Also consult type catalog metal when no melt yet
        if "Ag" not in tags and hasattr(E, "lookup_type_entry"):
            try:
                entry = E.lookup_type_entry(
                    self.val("country"), self.val("series"), self.val("denom"))
                if entry and "silver" in (
                        (entry.get("specs") or {}).get("metal") or "").lower():
                    tags.insert(0, "Ag")
            except Exception:
                pass
        if tags:
            detail = ("%s %s" % (detail, " ".join(tags))).strip()

        mid = " · %s" % tier
        name = "%s%s" % (prefix, mid)
        if detail:
            name = "%s · %s" % (name, detail)
        self.put("catalog", name)
        if not quiet:
            self.say("Catalog name updated to %s." % name)

    def reconcile_identity_fields(self, prefer_guess=False):
        """Fill gaps from soft-ID. Never clobber a manual country/type correction.

        prefer_guess=True only from got_read (vision just ran). Chip taps and
        PRICE IT keep the user's COUNTRY / SERIES when they disagree with the guess.
        """
        denom = (self.val("denom") or "").lower()
        series = (self.val("series") or "").lower()
        user_country = normalize_country_name(self.val("country")) or (
            self.val("country") or "").strip()

        guess = E.propose_identity(self.sides) if self.sides else {}
        guess_country = normalize_country_name(guess.get("country") or "") or (
            guess.get("country") or "").strip()

        if guess.get("series"):
            if prefer_guess or not user_country:
                self.put("series", guess["series"])
                if guess.get("denomination"):
                    self.put("denom", guess["denomination"])
                if guess.get("country"):
                    self.put("country", guess["country"])
                return
            # User already picked a country — only refine when it matches the guess
            if guess_country and user_country and guess_country != user_country:
                return
            if not self.val("series"):
                self.put("series", guess["series"])
            if not self.val("denom") and guess.get("denomination"):
                self.put("denom", guess["denomination"])
            return

        # No vision sides — tidy obvious form contradictions only
        if ("dime" in series or "roosevelt" in series) and (
                "quarter" in denom or "five" in denom or "cent" in denom):
            self.put("denom", "One dime")
            self.put("series", "Roosevelt Dime")
            return
        if ("quarter" in series or "washington" in series) and (
                "five" in denom or "dime" in denom):
            self.put("denom", "Quarter dollar")
            self.put("series", "Washington Quarter")
            return
        if ("lincoln" in series or "penny" in series) and (
                "five" in denom or "nickel" in series):
            self.put("denom", "One cent")
            if "nickel" in series:
                self.put("series", "Lincoln Cent")
            return
        if ("jefferson" in series or "nickel" in series) and "quarter" in denom:
            self.put("denom", "Five cents")
            self.put("series", "Jefferson Nickel")

    def enable_dnd(self):
        """Register file drops on the window itself.

        Wells live inside a scrollable Canvas window; tkdnd often never
        delivers <<Drop>> to those nested widgets on Windows. Hit-test the
        pointer against each well so dropping on a circle still picks a side.
        """
        if not _DND:
            return
        # Root + outer canvas catch drops anywhere in the workspace
        for w in (self, self.work_canvas, self.work, self.well_row):
            self._register_drop(w)
        for side, canv in self.canv.items():
            self._register_drop(canv)
            self._register_drop(canv.master)

    def _register_drop(self, widget):
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._dnd_drop)
            widget.dnd_bind("<<DragEnter>>", lambda e: "copy")
            widget.dnd_bind("<<Drag>>", lambda e: "copy")
        except tk.TclError:
            pass

    def side_at(self, x_root, y_root):
        if x_root is None or y_root is None:
            return None
        for side, canv in self.canv.items():
            try:
                x, y = canv.winfo_rootx(), canv.winfo_rooty()
                w, h = canv.winfo_width(), canv.winfo_height()
                if x <= x_root < x + w and y <= y_root < y + h:
                    return side
            except tk.TclError:
                pass
        return None

    def _dnd_drop(self, event):
        side = self.side_at(getattr(event, "x_root", None),
                            getattr(event, "y_root", None))
        self.on_drop(event, side)
        return event.action if hasattr(event, "action") else None

    def drop_paths(self, data):
        raw = ()
        if data:
            try:
                raw = self.tk.splitlist(data)
            except tk.TclError:
                raw = (data,)
        out = []
        for p in raw:
            p = os.path.normpath(str(p).strip().strip("{}").strip('"'))
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext in IMAGE_EXTS:
                out.append(p)
        return out

    def on_drop(self, event, side=None):
        paths = self.drop_paths(getattr(event, "data", ""))
        if not paths:
            return self.say("Drop a JPG, PNG, WebP, BMP, or TIFF photo.", ALARM)
        if len(paths) >= 2:
            self.load_from_path("obverse", paths[0])
            self.load_from_path("reverse", paths[1])
            return
        target = side
        if target is None:
            target = "obverse" if not self.images["obverse"] else "reverse"
        self.load_from_path(target, paths[0])

    def circle(self, im, size):
        w, h = im.size
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
        im = im.resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
        im.putalpha(mask.resize((size, size), Image.LANCZOS))
        return im

    def show_well(self, side, im):
        c = self.canv[side]
        c.delete("all")
        photo = ImageTk.PhotoImage(self.circle(im, WELL - 8))
        self.thumbs[side] = photo
        c.create_image(WELL // 2, WELL // 2, image=photo)
        c.create_oval(4, 4, WELL - 4, WELL - 4, outline=BRASS, width=2)

    # ------------------------------------------------------------- threading
    def work_thread(self, fn, kind):
        self.set_busy(True)

        def run():
            try:
                self.q.put((kind, fn()))
            except E.OllamaDown as e:
                self.q.put(("error", e))
            except Exception as e:
                self.q.put(("error", "%s: %s" % (type(e).__name__, e)))
        threading.Thread(target=run, daemon=True).start()

    def pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "say":
                    self.say(payload)
                    continue
                if kind == "cameras":
                    self._finish_camera_list(payload)
                    continue
                if kind == "models_ready":
                    if payload:
                        self.model.configure(values=payload)
                        if not self.has_model() or self.model.get() not in payload:
                            self.model.set(payload[0])
                        self.ollama_ok = True
                        self.retry_btn.pack_forget()
                    continue
                if kind == "prefer_model":
                    if payload and payload in (self.model.cget("values") or ()):
                        self.model.set(payload)
                        self.conf["model"] = payload
                    elif payload:
                        vals = list(self.model.cget("values") or ())
                        if payload not in vals:
                            vals = [payload] + vals
                            self.model.configure(values=vals)
                        self.model.set(payload)
                        self.conf["model"] = payload
                    continue
                self.set_busy(False)
                if kind == "error":
                    self.say(self.friendly_error(payload), ALARM)
                elif kind == "ollama_boot":
                    self._finish_ollama_boot(payload)
                    continue
                elif kind == "read":
                    self.got_read(payload)
                elif kind == "price":
                    self.got_price(payload)
        except queue.Empty:
            pass
        self.after(120, self.pump)

    def say(self, text, color=DIM):
        self.status.configure(text=text, fg=color)

    def tell(self, text):
        self.q.put(("say", text))

    # ------------------------------------------------------------- step one
    def resolve_job_models(self, preferred=None):
        """Installed vision tags + best ID / fast pricing picks."""
        found = E.vision_models()
        pref = preferred if preferred is not None else self.model.get()
        id_model = E.pick_vision_model(found, preferred=pref, purpose="id")
        text_model = E.pick_pricing_model(found, id_model)
        ordered = E.order_vision_models(found, preferred=pref, purpose="id")
        return found, id_model, text_model, ordered

    def read_coin(self):
        if self.busy:
            return self.say("Still working.", ALARM)
        if not any(self.images.values()):
            return self.say("Add a photo first.", ALARM)
        model = self.model.get()
        imgs = dict(self.images)

        def job():
            ok, owned, detail = E.ensure_ollama()
            if owned:
                self.ollama_owned = True
            if not ok:
                raise E.OllamaDown(detail or "Ollama failed to start")
            found, id_model, _text, ordered = self.resolve_job_models(model)
            if not found:
                raise E.OllamaDown(
                    "Ollama is up but no models are installed. Run: ollama pull gemma3:4b"
                    "  (or qwen3-vl:8b for stronger date reading)")
            if not ordered:
                ordered = [id_model] if id_model else found[:]
            self.q.put(("models_ready", found))
            if id_model and id_model != model:
                self.tell("using %s for identification…" % id_model)
            out = {}
            chosen = ordered[0]
            for side in ("obverse", "reverse"):
                if not imgs.get(side):
                    continue
                last_err = None
                for m in ordered:
                    try:
                        self.tell("reading the %s with %s…" % (side, m))
                        out[side] = E.read_side(m, side, imgs[side])
                        chosen = m
                        # Stick with the model that worked for the other side
                        ordered = [m] + [x for x in ordered if x != m]
                        break
                    except E.OllamaDown as e:
                        last_err = e
                        self.tell("%s failed (%s) — trying another model…" % (m, e))
                        continue
                else:
                    raise last_err or E.OllamaDown("No model could read the %s" % side)
            self.q.put(("models_ready", found))  # ensure UI has list
            # Prefer the working model in the combobox
            self.q.put(("prefer_model", chosen))
            return out
        self.set_workflow_step(2)
        self.say("Making sure Ollama is up, then reading…")
        self.work_thread(job, "read")

    def got_read(self, sides):
        self.ollama_ok = True
        self.retry_btn.pack_forget()
        self.sides = sides
        o, v = sides.get("obverse", {}), sides.get("reverse", {})
        self.saw.configure(state="normal")
        self.saw.delete("1.0", "end")
        if o.get("legends"):
            self.saw.insert("end", "Front: ", "k")
            self.saw.insert("end", "%s. %s\n" % (o.get("legends"), o.get("design", "")))
        if v.get("legends"):
            self.saw.insert("end", "Back: ", "k")
            self.saw.insert("end", "%s. %s\n" % (v.get("legends"), v.get("design", "")))
        self.saw.insert("end", "Metal: ", "k")
        self.saw.insert("end", "%s   " % (o.get("metal") or v.get("metal") or "?"))
        self.saw.insert("end", "Wear: ", "k")
        self.saw.insert("end", o.get("wear") or v.get("wear") or "?")
        dmg = o.get("damage") or v.get("damage")
        if dmg and dmg.lower() != "none":
            self.saw.insert("end", "   Damage: ", "k")
            self.saw.insert("end", dmg)
        self.saw.configure(state="disabled")

        def clean(*vals):
            for x in vals:
                if x and str(x).lower() not in ("unclear", "none", "n/a"):
                    return str(x)
            return ""
        self._suppress_live_catalog = True
        try:
            self.put("year", clean(o.get("date"), v.get("date")))
            self.put("mint", clean(o.get("mintMark"), v.get("mintMark")))
            denom = clean(v.get("denomination"), o.get("denomination"))
            self.put("denom", denom)
            self.put("grade", clean(o.get("wear"), v.get("wear")))

            # Soft ID from legends/design (Lincoln / Jefferson / etc.)
            guess = E.propose_identity(sides)
            if guess.get("country"):
                self.put("country", guess["country"])
            if guess.get("denomination"):
                self.put("denom", guess["denomination"])
            if guess.get("series"):
                self.put("series", guess["series"])

            self.reconcile_identity_fields(prefer_guess=True)
            self.suggest_catalog()
            self.sync_identity_chips()
            self.refresh_auth_panel()
            self.update_sort_hint()
            # Default disposition: Check if silver-looking, else Keep
            if self._current_likely_ag:
                self.set_disposition("Check", quiet=True, update_tray=False)
            else:
                self.set_disposition("Keep", quiet=True, update_tray=False)
        finally:
            self._suppress_live_catalog = False
        self.set_workflow_step(3)
        self.after(40, self.reveal_details)
        hint = ""
        if self._current_likely_ag:
            hint = " Likely silver — prioritize."
        self.say("Country/type chips follow the Read — tap to correct. "
                 "Fix DATE, then PRICE IT.%s Foreign/old: check AUTHENTICITY."
                 % hint)

    # ------------------------------------------------------------- step two
    def find_similar_coin(self):
        """Design guess + marketplace lookalikes, shown in the SIMILAR COINS panel."""
        if self.busy:
            return self.say("Still working.", ALARM)
        has_photo = bool(self.images.get("obverse") or self.images.get("reverse"))
        has_fields = bool(
            self.val("year") or self.val("denom") or self.val("series")
            or self.val("country"))
        if not has_photo and not has_fields:
            return self.say(
                "Capture a photo (or set country/type), then FIND SIMILAR.", ALARM)
        self._price_then_next = False
        self.reconcile_identity_fields()
        self.refresh_catalog_name(quiet=True)
        model = self.model.get()
        year = E.normalize_year(self.val("year"))
        if year != self.val("year"):
            self.put("year", year)
        attribution = {"year": year, "mintMark": self.val("mint"),
                       "country": self.val("country"), "denomination": self.val("denom"),
                       "series": self.val("series"), "grade": self.val("grade"),
                       "observed": self.sides}
        imgs = [b for b in (self.images.get("obverse"), self.images.get("reverse")) if b]

        def job():
            ok, owned, detail = E.ensure_ollama()
            if owned:
                self.ollama_owned = True
            if not ok:
                raise E.OllamaDown(detail or "Ollama failed to start")
            found, id_model, _text, _ord = self.resolve_job_models(model)
            use = id_model or (found[0] if found else model)
            if use and use != model and E.needs_look_assist(attribution):
                self.tell("using %s for design match…" % use)
            return E.find_similar(use, attribution, images_b64=imgs, say=self.tell)

        self.set_workflow_step(4)
        self.say("Finding similar coins…")
        self.work_thread(job, "price")

    def face_skip_coin(self, then_next=False):
        """Bag as common / face without web search or Ollama pricing."""
        if self.busy:
            return self.say("Still working.", ALARM)
        has_fields = bool(
            self.val("year") or self.val("denom") or self.val("series")
            or (self.val("country") and self.val("denom")))
        if not has_fields and not (
                self.images.get("obverse") or self.images.get("reverse")):
            return self.say(
                "Set country/type (or Read first), then FACE SKIP.", ALARM)
        self.reconcile_identity_fields()
        self.refresh_catalog_name(quiet=True)
        year = E.normalize_year(self.val("year"))
        if year != self.val("year"):
            self.put("year", year)
        attribution = {
            "year": year, "mintMark": self.val("mint"),
            "country": self.val("country"), "denomination": self.val("denom"),
            "series": self.val("series"), "grade": self.val("grade"),
            "observed": self.sides,
        }
        auth = getattr(self, "_auth_bundle", None) or E.authenticity_for_attribution(
            attribution)
        self._auth_bundle = auth
        self._price_then_next = bool(then_next)
        self.set_disposition("Face", quiet=True, update_tray=False)
        res = E.face_skip_estimate(attribution, auth=auth)
        self.say("Face skip — bagging as common without web search…")
        self.got_price(res)

    def price_coin(self, then_next=False):
        if self.busy:
            return self.say("Still working.", ALARM)
        has_photo = bool(self.images.get("obverse") or self.images.get("reverse"))
        has_fields = bool(
            self.val("year") or self.val("denom") or self.val("series")
            or (self.val("country") and self.val("denom")))
        if not has_fields and not has_photo:
            return self.say(
                "Run READ THE COIN first, or capture a photo for a design guess. "
                "Grey hint text doesn’t count.", ALARM)
        self._price_then_next = bool(then_next)
        self.reconcile_identity_fields()
        self.refresh_catalog_name(quiet=True)
        model = self.model.get()
        year = E.normalize_year(self.val("year"))
        if year != self.val("year"):
            # Clear N/A / unclear placeholders so catalog names stay clean
            self.put("year", year)
        attribution = {"year": year, "mintMark": self.val("mint"),
                       "country": self.val("country"), "denomination": self.val("denom"),
                       "series": self.val("series"), "grade": self.val("grade"),
                       "observed": self.sides}
        urls = [self.val("url")] if self.val("url") else []
        imgs = [b for b in (self.images.get("obverse"), self.images.get("reverse")) if b]

        def job():
            # Commons at face value don't need Ollama or web search
            # (skip when undated/worn — look-assist may still refine identity)
            if (not any((u or "").startswith("http") for u in urls)
                    and not E.needs_look_assist(attribution)):
                face = E.common_circulated_fallback(attribution)
                if face:
                    self.tell("common coin — listing at face value")
                    return face
            ok, owned, detail = E.ensure_ollama()
            if owned:
                self.ollama_owned = True
            if not ok:
                raise E.OllamaDown(detail or "Ollama failed to start")
            found, id_model, text_model, _ord = self.resolve_job_models(model)
            use = id_model or (found[0] if found else model)
            text = text_model or use
            if use and use != model and E.needs_look_assist(attribution):
                self.tell("ID model %s…" % use)
            if text and text != use:
                self.tell("pricing text via %s…" % text)
            return E.price(use, attribution, urls, say=self.tell,
                           images_b64=imgs, text_model=text)
        self.set_workflow_step(4)
        msg = "Pricing…"
        if E.needs_look_assist(attribution) and has_photo:
            msg = "Pricing from design…"
        self.say(msg + (" then next coin." if then_next else ""))
        self.work_thread(job, "price")

    def got_price(self, res):
        if res.get("error"):
            self._price_then_next = False
            self.set_workflow_step(3)
            return self.say(res["error"], ALARM)
        # Design look-assist may have filled country/type when date was blank
        filled = res.get("lookFilled") or {}
        for ui_key, src_key in (("country", "country"), ("denom", "denomination"),
                                ("series", "series")):
            if filled.get(src_key) and not self.val(ui_key):
                self.put(ui_key, filled[src_key])
        if filled:
            self.sync_identity_chips()
        entry = dict(res)
        entry["id"] = int(__import__("time").time() * 1000)
        entry["year"] = self.val("year")
        entry["mintMark"] = self.val("mint")
        entry["country"] = self.val("country")
        entry["denomination"] = self.val("denom")
        entry["series"] = self.val("series")
        entry["grade"] = self.val("grade") or entry.get("grade")
        entry["observed"] = self.sides
        dup = self.find_tray_duplicate(
            entry["year"], entry["country"], entry["denomination"],
            entry["mintMark"], entry["series"])
        if dup:
            label = dup.get("catalogName") or dup.get("identification") or "a tray coin"
            if not messagebox.askyesno(
                    "Already in tray",
                    "Already have %s — add anyway?" % label):
                self._price_then_next = False
                self.set_workflow_step(3)
                return self.say("Skipped duplicate of %s." % label, ALARM)
        auth = getattr(self, "_auth_bundle", None) or E.authenticity_for_attribution({
            "year": entry["year"], "country": entry["country"],
            "denomination": entry["denomination"], "series": entry["series"],
            "observed": self.sides,
        })
        if auth:
            entry.setdefault("authChecks", auth.get("authChecks"))
            entry.setdefault("specs", auth.get("specs"))
            if auth.get("authChecks") and not entry.get("checks"):
                entry["checks"] = list(auth["authChecks"])[:8]
        entry["thumbs"] = [
            make_tray_thumb(b)
            for b in (self.images["obverse"], self.images["reverse"]) if b
        ]
        entry["tier"] = entry_tier(entry)
        # Country-first baggie ID with rarity letter + unusual tags
        self.refresh_catalog_name(
            quiet=True,
            tier=entry["tier"],
            melt=entry.get("melt") or "",
            specs=entry.get("specs"),
        )
        entry["catalogName"] = (self.val("catalog")
                                or entry.get("identification")
                                or self.next_catalog_id())
        entry["likelyAg"] = bool(
            self._current_likely_ag or entry_is_ag(entry)
            or "Ag" in (entry.get("catalogName") or ""))
        if entry.get("faceValue"):
            self.set_disposition("Face", quiet=True, update_tray=False)
        elif entry["likelyAg"] and self._disposition == "Keep":
            self.set_disposition("Check", quiet=True, update_tray=False)
        entry["disposition"] = normalize_disposition(
            self._disposition, face_value=bool(entry.get("faceValue")))
        self._tray_selected_id = entry["id"]
        self.render(entry)
        self.tray.insert(0, entry)
        self.save_tray()
        self.draw_tray()
        self.set_workflow_step(4)
        name = entry["catalogName"]
        tier = entry["tier"]
        then_next = self._price_then_next
        self._price_then_next = False
        if then_next:
            self.next_coin()
            self.say("Added %s [%s · %s]. Ready for the next coin."
                     % (name, tier, entry["disposition"]))
        else:
            self.say("Done. Added to the tray as %s [%s · %s]."
                     % (name, tier, entry["disposition"]))

    def _render_in_hand(self, c):
        """Compact magnet / weight / diameter checklist on the result card."""
        specs = c.get("specs") or {}
        checks = list(c.get("authChecks") or [])
        country = (c.get("country") or "").strip()
        foreign = bool(country) and country.lower() not in (
            "united states", "usa", "us", "u.s.", "u.s.a.", "america", "")
        show = bool(specs) or bool(checks) or entry_is_ag(c) or foreign
        if not show:
            return
        # Prefer a fresh authenticity bundle if the entry is thin
        if not specs or not checks:
            auth = E.authenticity_for_attribution({
                "year": c.get("year"), "country": c.get("country"),
                "denomination": c.get("denomination"), "series": c.get("series"),
                "observed": c.get("observed"),
            })
            if auth:
                specs = specs or (auth.get("specs") or {})
                if not checks:
                    checks = list(auth.get("authChecks") or [])
        if not (specs or checks):
            return
        tk.Label(self.card, text="IN HAND", bg=DARK, fg=OXIDE,
                 font=(self.MONO, 8)).pack(anchor="w", pady=(14, 4))
        bits = []
        if specs.get("diameter_mm"):
            bits.append("~%s mm" % specs["diameter_mm"])
        if specs.get("weight_g"):
            bits.append("~%s g" % specs["weight_g"])
        if specs.get("metal"):
            bits.append(str(specs["metal"]))
        if specs.get("edge"):
            bits.append("%s edge" % specs["edge"])
        if bits:
            tk.Label(
                self.card, text="Expected: " + " · ".join(bits),
                bg=DARK, fg=CREAM, font=(self.SANS, 10),
                wraplength=560, justify="left", anchor="w",
            ).pack(anchor="w")
        bullets = []
        for x in checks:
            s = str(x).strip()
            if not s:
                continue
            # Prefer magnet / weight / edge lines; skip long duplicates of note
            low = s.lower()
            if any(k in low for k in (
                    "magnet", "weigh", "diameter", "edge", "silver check",
                    "expected metal", "expected edge", "cast seam")):
                bullets.append(s)
        if specs.get("note"):
            note = str(specs["note"]).strip()
            if note and note not in bullets:
                bullets.append(note)
        if not bullets:
            bullets = [
                "Magnet test: silver/bronze usually not magnetic.",
                "Weigh and measure vs expected specs above.",
            ]
        for s in bullets[:6]:
            tk.Label(
                self.card, text="• " + s, bg=DARK, fg=DIM,
                font=(self.SANS, 9), wraplength=560, justify="left", anchor="w",
            ).pack(anchor="w", pady=(2, 0))

    def render(self, c):
        for w in self.card.winfo_children():
            w.destroy()
        self.card.pack(fill="x", pady=(18, 20))
        self.card.configure(padx=16, pady=14)

        cat = c.get("catalogName") or ""
        tier = entry_tier(c)
        disp = normalize_disposition(c.get("disposition"), face_value=bool(c.get("faceValue")))
        if cat:
            head = "%s  %s" % (tier, cat)
            if entry_is_ag(c):
                head += "  · Ag"
            tk.Label(self.card, text=head, bg=DARK, fg=BRASS,
                     font=(self.MONO, 10), wraplength=560,
                     justify="left", anchor="w").pack(anchor="w")
        tk.Label(self.card, text=c.get("identification", "Not identified"), bg=DARK,
                 fg=CREAM, font=(self.SERIF, 18), wraplength=560,
                 justify="left").pack(anchor="w", pady=(4 if cat else 0, 0))
        tk.Label(
            self.card,
            text="STATUS %s — change with Face / Keep / Sell / Check above" % disp.upper(),
            bg=DARK, fg=OXIDE, font=(self.MONO, 9),
        ).pack(anchor="w", pady=(4, 0))

        tk.Frame(self.card, bg=LINE, height=1).pack(fill="x", pady=(12, 0))
        face = bool(c.get("faceValue"))
        price_txt = money_span(c.get("valueLow"), c.get("valueHigh"), face=face)
        tk.Label(self.card, text="%s · %s" % (tier, price_txt),
                 bg=DARK, fg=DIM if face or tier == "C" else BRASS,
                 font=(self.MONO, 22, "bold")).pack(pady=(8, 0))
        tier_note = E.TIER_NAMES.get(tier, tier)
        if face:
            skip = " · FACE SKIP" if c.get("faceSkip") else ""
            tk.Label(self.card, text="%s · FACE VALUE · %s%s" % (tier_note, disp, skip),
                     bg=DARK, fg=OXIDE, font=(self.MONO, 9)).pack(pady=(0, 8))
        else:
            nsrc = len(c.get("sources") or [])
            if c.get("typicalEstimate"):
                src = "typical sold range (ballpark)"
            elif c.get("soldEvidence"):
                src = "includes sold / realized prices"
            elif c.get("marketSold"):
                src = "sold / asking (eBay & dealers)"
            elif nsrc:
                src = "from %d priced sources" % nsrc
            else:
                src = "researched value"
            tk.Label(self.card, text="%s · %s" % (tier_note, src),
                     bg=DARK, fg=DIM, font=(self.SANS, 9)).pack(pady=(0, 4))
            if c.get("marketSummary"):
                tk.Label(
                    self.card, text=c.get("marketSummary"), bg=DARK, fg=BRASS,
                    font=(self.MONO, 10), wraplength=560, justify="left",
                ).pack(pady=(0, 4))
            if c.get("evidenceNote"):
                tk.Label(
                    self.card, text=c.get("evidenceNote"), bg=DARK,
                    fg=ALARM if c.get("thinEvidence") else DIM,
                    font=(self.SANS, 9), wraplength=560, justify="left",
                    anchor="w",
                ).pack(anchor="w", pady=(0, 8))
            elif not face:
                tk.Frame(self.card, height=4, bg=DARK).pack()
        tk.Frame(self.card, bg=LINE, height=1).pack(fill="x")

        for k, v in (("GRADE", c.get("grade")), ("MELT", c.get("melt"))):
            if not v:
                continue
            r = tk.Frame(self.card, bg=DARK)
            r.pack(fill="x", pady=3)
            tk.Label(r, text=k, bg=DARK, fg=OXIDE, font=(self.MONO, 8)).pack(side="left")
            tk.Label(r, text=v, bg=DARK, fg=CREAM,
                     font=(self.SANS, 10)).pack(side="right")
        if c.get("conditionNote"):
            tk.Label(
                self.card, text=c.get("conditionNote"), bg=DARK, fg=DIM,
                font=(self.SANS, 9), wraplength=560, justify="left", anchor="w",
            ).pack(anchor="w", pady=(4, 0))

        self._render_in_hand(c)

        # Coinoscope-like similar marketplace matches
        similar = c.get("similarListings") or []
        look = c.get("lookAssist") or {}
        if similar or look:
            tk.Label(self.card, text="SIMILAR COINS", bg=DARK, fg=OXIDE,
                     font=(self.MONO, 8)).pack(anchor="w", pady=(14, 4))
            if look:
                bits = []
                if look.get("confidence"):
                    bits.append(look["confidence"])
                guess = " · ".join(
                    x for x in (
                        look.get("countryGuess"),
                        look.get("seriesGuess") or look.get("denominationGuess"),
                        look.get("era"),
                    ) if x)
                note = (look.get("notes") or "").strip()
                line = guess or note
                if bits and line:
                    line = "%s — %s" % (bits[0], line)
                elif bits:
                    line = bits[0]
                if line:
                    tk.Label(
                        self.card,
                        text="Design guess: " + line,
                        bg=DARK, fg=DIM, font=(self.SANS, 9),
                        wraplength=560, justify="left", anchor="w",
                    ).pack(anchor="w", pady=(0, 4))
            for x in similar[:8]:
                r = tk.Frame(self.card, bg=DARK)
                r.pack(fill="x", pady=2)
                price = x.get("price") or "—"
                kind = (x.get("kind") or "").strip().lower()
                kind_tag = ""
                if kind in ("sold", "asking", "catalog"):
                    kind_tag = kind
                right = tk.Frame(r, bg=DARK)
                right.pack(side="right")
                tk.Label(right, text=price, bg=DARK, fg=BRASS,
                         font=(self.MONO, 10)).pack(side="right")
                if kind_tag:
                    tk.Label(right, text=kind_tag + "  ", bg=DARK, fg=DIM,
                             font=(self.MONO, 8)).pack(side="right")
                title = x.get("title") or "listing"
                where = x.get("where") or "source"
                if x.get("url"):
                    link = tk.Label(
                        r, text=title, bg=DARK, fg=CREAM, anchor="w",
                        font=(self.SANS, 10), wraplength=360,
                        justify="left", cursor="hand2",
                    )
                    link.pack(side="left", fill="x", expand=True)
                    link.bind("<Button-1>", lambda e, u=x["url"]: webbrowser.open(u))
                    site = tk.Label(r, text=" " + where, bg=DARK, fg=DIM,
                                    font=(self.SANS, 9), cursor="hand2")
                    site.pack(side="left")
                    site.bind("<Button-1>", lambda e, u=x["url"]: webbrowser.open(u))
                else:
                    tk.Label(r, text=title, bg=DARK, fg=CREAM, anchor="w",
                             font=(self.SANS, 10), wraplength=380,
                             justify="left").pack(side="left")

        # Full citation list for the dollar range (price + condition per source)
        citations = list(c.get("citations") or [])
        if not citations:
            seen_u = set()
            for x in (c.get("comparables") or []):
                u = x.get("url") or ""
                key = u.split("?")[0].lower() if u else (x.get("what") or "")
                if key in seen_u:
                    continue
                seen_u.add(key)
                citations.append({
                    "title": x.get("what") or "source",
                    "url": u,
                    "where": x.get("where") or "web",
                    "price": x.get("price") or "",
                    "condition": x.get("condition") or "",
                    "kind": x.get("kind") or "",
                })
            for u in c.get("sources") or []:
                key = (u or "").split("?")[0].lower()
                if not key or key in seen_u:
                    continue
                seen_u.add(key)
                citations.append({
                    "title": u, "url": u, "where": "", "price": "",
                    "condition": "", "kind": "",
                })
        # Dedupe against similar strip only for display noise — still show all citations
        if citations:
            tk.Label(self.card, text="SOURCES", bg=DARK, fg=OXIDE,
                     font=(self.MONO, 8)).pack(anchor="w", pady=(14, 4))
            tk.Label(
                self.card,
                text="Price and condition from each source used in the estimate.",
                bg=DARK, fg=DIM, font=(self.SANS, 9),
                wraplength=560, justify="left", anchor="w",
            ).pack(anchor="w", pady=(0, 4))
            for x in citations[:20]:
                r = tk.Frame(self.card, bg=DARK)
                r.pack(fill="x", pady=2)
                right = tk.Frame(r, bg=DARK)
                right.pack(side="right")
                price = x.get("price") or "—"
                tk.Label(right, text=price, bg=DARK, fg=BRASS,
                         font=(self.MONO, 10)).pack(side="right")
                tags = []
                if x.get("condition"):
                    tags.append(x["condition"])
                kind = (x.get("kind") or "").strip().lower()
                if kind in ("sold", "asking", "guide", "catalog", "listing"):
                    tags.append(kind)
                if tags:
                    tk.Label(right, text=" · ".join(tags) + "  ", bg=DARK, fg=DIM,
                             font=(self.MONO, 8)).pack(side="right")
                title = x.get("title") or "source"
                where = x.get("where") or "source"
                if x.get("url"):
                    link = tk.Label(
                        r, text=title, bg=DARK, fg=CREAM, anchor="w",
                        font=(self.SANS, 10), wraplength=320,
                        justify="left", cursor="hand2",
                    )
                    link.pack(side="left", fill="x", expand=True)
                    link.bind("<Button-1>", lambda e, u=x["url"]: webbrowser.open(u))
                    site = tk.Label(r, text=" " + where, bg=DARK, fg=DIM,
                                    font=(self.SANS, 9), cursor="hand2")
                    site.pack(side="left")
                    site.bind("<Button-1>", lambda e, u=x["url"]: webbrowser.open(u))
                else:
                    tk.Label(r, text=title, bg=DARK, fg=CREAM, anchor="w",
                             font=(self.SANS, 10), wraplength=380,
                             justify="left").pack(side="left")

        auth_checks = c.get("authChecks") or []
        checks = auth_checks or c.get("checks") or []
        specs = c.get("specs") or {}
        if checks or specs:
            box = tk.Frame(self.card, bg=DARK, highlightbackground=BRASSD,
                           highlightthickness=1, padx=12, pady=10)
            box.pack(fill="x", pady=(14, 0))
            title = ("AUTHENTICITY CHECK" if (auth_checks or specs)
                     else "CHECK IN HAND")
            tk.Label(box, text=title, bg=DARK, fg=BRASS,
                     font=(self.MONO, 8)).pack(anchor="w")
            if specs:
                bits = []
                if specs.get("diameter_mm"):
                    bits.append("~%s mm" % specs["diameter_mm"])
                if specs.get("weight_g"):
                    bits.append("~%s g" % specs["weight_g"])
                if specs.get("metal"):
                    bits.append(str(specs["metal"]))
                if bits:
                    tk.Label(box, text="Expected: " + " · ".join(bits),
                             bg=DARK, fg=CREAM, font=(self.SANS, 10),
                             anchor="w").pack(anchor="w", pady=(4, 0))
            for x in checks:
                tk.Label(box, text="• " + str(x), bg=DARK, fg=CREAM, anchor="w",
                         font=(self.SANS, 10), wraplength=520,
                         justify="left").pack(anchor="w", pady=(4, 0))

        if c.get("notes"):
            tk.Label(self.card, text=c["notes"], bg=DARK, fg=DIM, anchor="w",
                     font=(self.SANS, 10), wraplength=560,
                     justify="left").pack(anchor="w", pady=(12, 0))

        lab_row = tk.Frame(self.card, bg=DARK)
        lab_row.pack(fill="x", pady=(14, 0))
        ttk.Button(lab_row, text="COPY BAGGIE LABEL", style="Ghost.TButton",
                   command=lambda cc=c: self.copy_baggie_label(cc)).pack(side="left")
        ttk.Button(lab_row, text="PRINT ALL LABELS", style="Ghost.TButton",
                   command=self.print_baggie_labels).pack(side="left", padx=(8, 0))
        pcgs = c.get("pcgsGuideUrl") or ""
        if not pcgs and (c.get("country") or "").lower() in (
                "", "united states", "usa", "us"):
            pcgs = E.pcgs_price_guide_url({
                "country": c.get("country") or "United States",
                "series": c.get("series"),
                "denomination": c.get("denomination"),
                "year": c.get("year"),
            }) or E.PCGS_US_PRICES
        if pcgs:
            ttk.Button(
                lab_row, text="OPEN PCGS GUIDE", style="Ghost.TButton",
                command=lambda u=pcgs: webbrowser.open(u),
            ).pack(side="left", padx=(8, 0))

        self.after(60, lambda: self.work_canvas.yview_moveto(1.0))

    # ----------------------------------------------------------------- tray
    def on_tray_filter(self):
        e = self.tray_find
        self._tray_query = "" if getattr(e, "_empty", False) else e.get().strip()
        self.draw_tray()

    def filtered_tray(self):
        items = [c for c in self.tray
                 if tray_matches(c, self._tray_query) and self.tray_chip_matches(c)]
        mode = self.tray_sort.get() if hasattr(self, "tray_sort") else "Country"
        if mode == "Value":
            def key(c):
                try:
                    return -((float(c.get("valueLow") or 0) + float(c.get("valueHigh") or 0)) / 2)
                except (TypeError, ValueError):
                    return 0
            items = sorted(items, key=key)
        elif mode == "Tier":
            order = {"G": 0, "R": 1, "U": 2, "C": 3}
            items = sorted(items, key=lambda c: (order.get(entry_tier(c), 9),
                                                 -(c.get("id") or 0)))
        elif mode == "Country":
            items = sorted(items, key=catalog_sort_key)
        # Newest: keep tray order (newest first)
        return items

    def restore_coin(self, c):
        """Load a past tray entry into the workspace for review / re-price."""
        self._tray_selected_id = c.get("id")
        self.sides = c.get("observed")
        self.put("catalog", c.get("catalogName") or "")
        self.put("year", c.get("year") or "")
        self.put("mint", c.get("mintMark") or "")
        self.put("country", c.get("country") or "")
        self.put("denom", c.get("denomination") or "")
        self.put("series", c.get("series") or "")
        self.put("grade", c.get("grade") or "")
        self.set_disposition(
            normalize_disposition(
                c.get("disposition"), face_value=bool(c.get("faceValue"))),
            quiet=True, update_tray=False)
        self.update_sort_hint()
        # Infer year/series from identification when older entries lack fields
        ident = c.get("identification") or ""
        if not self.val("year"):
            m = re.search(r"(19|20)\d{2}", ident) or re.search(
                r"(19|20)\d{2}", c.get("catalogName") or "")
            if m:
                self.put("year", m.group(0))
        if not self.val("series") and ident:
            # drop leading year/mint for a rough series guess
            rest = re.sub(r"^(19|20)\d{2}\s*", "", ident)
            rest = re.sub(r"^[A-Z]\s+", "", rest).strip()
            if rest:
                self.put("series", rest)

        self.images = {"obverse": None, "reverse": None}
        for side in ("obverse", "reverse"):
            self.blank(side)
        thumbs = c.get("thumbs") or []
        for i, side in enumerate(("obverse", "reverse")):
            if i >= len(thumbs) or not thumbs[i]:
                continue
            try:
                im = Image.open(io.BytesIO(base64.b64decode(thumbs[i]))).convert("RGB")
                self.show_well(side, im)
                # Keep a usable image for a re-Read if they want
                self.images[side] = encode_for_model(im)
            except Exception:
                pass
        if any(self.images.values()):
            self.read_btn.configure(state="normal")
        self.set_workflow_step(3)
        self.render(c)
        self.draw_tray()
        self.after(40, self.reveal_details)
        self.sync_identity_chips()
        self.refresh_auth_panel()
        name = c.get("catalogName") or c.get("identification") or "coin"
        self.say("Loaded %s from the tray — edit fields or PRICE IT again." % name)

    def _tray_title(self, c):
        cat = (c.get("catalogName") or "").strip()
        code, num = parse_catalog_id(cat)
        if code and num is not None:
            return "%s-%04d" % (code, num)
        ident = (c.get("identification") or "").strip()
        return (ident[:18] + "…") if len(ident) > 18 else (ident or "Coin")

    def _tray_subtitle(self, c):
        bits = [
            E.normalize_year(c.get("year") or ""),
            (c.get("country") or "").strip(),
            (c.get("denomination") or c.get("series") or "").strip(),
        ]
        line = " / ".join(b for b in bits if b)
        if not line:
            line = (c.get("identification") or c.get("catalogName") or "")[:36]
        return line[:38] + ("…" if len(line) > 38 else "")

    def draw_tray(self):
        for w in self.tray_box.winfo_children():
            w.destroy()
        shown = self.filtered_tray()
        lo = sum(float(c.get("valueLow") or 0) for c in self.tray)
        hi = sum(float(c.get("valueHigh") or 0) for c in self.tray)
        filtering = bool(self._tray_query or self._tray_filter_country
                         or self._tray_filter_flags)
        if filtering and len(shown) != len(self.tray):
            self.total.configure(
                text="%d/%d  %s–%s" % (len(shown), len(self.tray), money(lo), money(hi))
                if self.tray else "")
        else:
            self.total.configure(text="%d  %s–%s" % (len(self.tray), money(lo), money(hi))
                                 if self.tray else "")
        self._icons = []
        if not self.tray:
            empty = tk.Label(self.tray_box,
                             text="Priced coins land here.\nExport when you’re done.",
                             bg=BOARD, fg=DIM, font=(self.SANS, 10),
                             justify="left", anchor="w", pady=20)
            empty.pack(fill="x")
            return
        if not shown:
            empty = tk.Label(self.tray_box,
                             text="No matches for current filters.",
                             bg=BOARD, fg=DIM, font=(self.SANS, 10),
                             justify="left", anchor="w", pady=20)
            empty.pack(fill="x")
            return
        for c in shown:
            selected = c.get("id") == self._tray_selected_id
            bg = DARK if selected else BOARD
            # Outer pad so rows breathe; selected sits in a quiet block
            shell = tk.Frame(self.tray_box, bg=BOARD, pady=3)
            shell.pack(fill="x")
            row = tk.Frame(shell, bg=bg, padx=8, pady=8)
            row.pack(fill="x")

            # One thumb — two made the list noisy
            thumbs = c.get("thumbs") or []
            if thumbs:
                try:
                    im = Image.open(io.BytesIO(base64.b64decode(thumbs[0]))).convert("RGB")
                    ph = ImageTk.PhotoImage(self.circle(im, 40))
                    self._icons.append(ph)
                    thumb = tk.Label(row, image=ph, bg=bg)
                    thumb.pack(side="left", padx=(0, 10))
                except Exception:
                    thumb = None
            else:
                thumb = None

            face = bool(c.get("faceValue"))
            tier = entry_tier(c)
            price_txt = money_span(c.get("valueLow"), c.get("valueHigh"), face=face)
            if price_txt in ("—", "—–—", "—-—"):
                price_txt = "—"
            price_fg = BRASS if (not face and tier in ("U", "R", "G")) else CREAM
            if face or tier == "C":
                price_fg = OXIDE
            disp = normalize_disposition(c.get("disposition"), face_value=face)
            disp_code = DISPOSITION_CODES.get(disp, "?")
            ag = entry_is_ag(c)

            right = tk.Frame(row, bg=bg)
            right.pack(side="right", padx=(6, 0))
            x = tk.Label(right, text="×", bg=bg, fg=DIM, cursor="hand2",
                         font=(self.SANS, 12), padx=2)
            x.pack(anchor="e")
            tier_lab = tk.Label(right, text=tier, bg=bg, fg=price_fg,
                                font=(self.MONO, 11, "bold"))
            tier_lab.pack(anchor="e", pady=(2, 0))
            badge = disp_code + (" · Ag" if ag else "")
            badge_lab = tk.Label(right, text=badge, bg=bg,
                                 fg=BRASS if ag else DIM,
                                 font=(self.MONO, 8))
            badge_lab.pack(anchor="e")
            price_lab = tk.Label(right, text=price_txt, bg=bg, fg=price_fg,
                                 font=(self.MONO, 10))
            price_lab.pack(anchor="e")

            txt = tk.Frame(row, bg=bg)
            txt.pack(side="left", fill="both", expand=True)
            title = self._tray_title(c)
            n = tk.Label(txt, text=title, bg=bg, fg=CREAM, anchor="w",
                         font=(self.MONO, 10, "bold"))
            n.pack(fill="x")
            sub = self._tray_subtitle(c)
            g = tk.Label(txt, text=sub, bg=bg, fg=DIM, anchor="w",
                         font=(self.SANS, 9), wraplength=150, justify="left")
            g.pack(fill="x", pady=(2, 0))

            clickables = [n, g, txt, row, shell, tier_lab, price_lab, right,
                          badge_lab]
            if thumb is not None:
                clickables.append(thumb)
            for w in clickables:
                w.bind("<Button-1>", lambda e, cc=c: self.restore_coin(cc))
            x.bind("<Button-1>", lambda e, i=c.get("id"): self.drop(i))

    def drop(self, cid):
        self.tray = [c for c in self.tray if c.get("id") != cid]
        if self._tray_selected_id == cid:
            self._tray_selected_id = None
        self.save_tray()
        self.draw_tray()

    def export_csv(self):
        if not self.tray:
            return self.say("Nothing in the tray yet.", ALARM)
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            initialfile="coin-tray.csv")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["tier", "disposition", "catalog", "coin", "grade", "low", "high",
                        "avg", "median", "soldCount", "askingCount", "likelyAg",
                        "notes", "conditionNote", "evidenceNote",
                        "sources", "citations"])
            for c in self.tray:
                cite_bits = []
                for x in c.get("citations") or []:
                    cite_bits.append(" | ".join(
                        p for p in (
                            x.get("title") or "",
                            x.get("price") or "",
                            x.get("condition") or "",
                            x.get("kind") or "",
                            x.get("url") or "",
                        ) if p
                    ))
                if not cite_bits:
                    cite_bits = c.get("sources") or []
                w.writerow([entry_tier(c),
                            normalize_disposition(
                                c.get("disposition"),
                                face_value=bool(c.get("faceValue"))),
                            c.get("catalogName"),
                            c.get("identification"), c.get("grade"),
                            c.get("valueLow"), c.get("valueHigh"),
                            c.get("valueAvg"), c.get("valueMedian"),
                            c.get("soldCount"), c.get("askingCount"),
                            "Ag" if entry_is_ag(c) else "",
                            c.get("notes"),
                            c.get("conditionNote") or "",
                            c.get("evidenceNote") or "",
                            " ".join(c.get("sources") or []),
                            " || ".join(cite_bits)])
        self.say("Wrote %s" % os.path.basename(path))

    def export_json(self):
        if not self.tray:
            return self.say("Nothing in the tray yet.", ALARM)
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            initialfile="coin-tray.json")
        if not path:
            return
        with open(path, "w") as f:
            json.dump([{k: v for k, v in c.items() if k != "thumbs"}
                       for c in self.tray], f, indent=1)
        self.say("Wrote %s" % os.path.basename(path))

    def copy_baggie_label(self, c):
        tier, lines = baggie_label_lines(c)
        text = "\n".join(["%s  %s" % (tier, lines[0])] + lines[1:] if lines
                         else [tier])
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except Exception as e:
            return self.say("Couldn't copy: %s" % e, ALARM)
        self.say("Baggie label copied — paste onto a sticker or slip.")

    def print_baggie_labels(self):
        items = self.filtered_tray() if self.tray else []
        if not items:
            return self.say(
                "Nothing to print — clear filters or add coins to the tray.", ALARM)
        cards = []
        for c in items:
            tier, lines = baggie_label_lines(c)
            body = "<br>".join(html.escape(x) for x in lines)
            face = " face" if c.get("faceValue") or tier == "C" else ""
            cards.append(
                '<div class="label%s"><span class="tier">%s</span><div class="body">%s</div></div>'
                % (face, html.escape(tier), body))
        doc = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Coin Tray baggie labels</title>
<style>
  @page { margin: 0.4in; }
  body { font-family: Georgia, serif; margin: 0; color: #1a1a1a; }
  h1 { font-size: 14px; font-weight: normal; color: #666; margin: 0 0 12px; }
  .sheet { display: flex; flex-wrap: wrap; gap: 8px; }
  .label {
    position: relative; width: 2.1in; min-height: 1.15in; box-sizing: border-box;
    border: 1px solid #333; padding: 8px 10px 8px 36px; font-size: 11px;
    line-height: 1.35; page-break-inside: avoid;
  }
  .label .tier {
    position: absolute; left: 6px; top: 6px; font-family: Consolas, monospace;
    font-size: 22px; font-weight: bold; line-height: 1;
  }
  .label.face { border-style: dashed; color: #444; }
  .label.face .tier { color: #666; }
  @media print {
    h1 { display: none; }
    .label { border-color: #000; }
  }
</style></head><body>
<h1>Coin Tray — C/U/R/G tier · Face/Keep/Sell/Check status — filtered view — File → Print</h1>
<div class="sheet">
%s
</div>
<script>window.onload = function () { window.print(); }</script>
</body></html>""" % "\n".join(cards)
        path = os.path.join(tempfile.gettempdir(), "coin_tray_baggie_labels.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(doc)
        webbrowser.open("file:///" + path.replace("\\", "/"))
        self.say("Opened %d label(s) from current filter — print, cut, slip into each baggie."
                 % len(items))

    # ------------------------------------------------------------- settings
    def boot_ollama(self):
        """Start Ollama if needed, then refresh the model list."""
        self.say("Starting Ollama…")
        self.retry_btn.pack_forget()
        self.bar.pack(side="right", padx=14)
        self.bar.start(12)

        def job():
            try:
                self.q.put(("ollama_boot", E.ensure_ollama()))
            except Exception as e:
                self.q.put(("ollama_boot", (False, False, str(e))))
        threading.Thread(target=job, daemon=True).start()

    def _finish_ollama_boot(self, payload):
        self.bar.stop()
        self.bar.pack_forget()
        ok, owned, detail = payload
        self.ollama_owned = owned
        if not ok:
            self.model.configure(values=[])
            self.model.set("")
            return self.set_ollama_status(False, detail)
        self.load_models()

    def load_models(self):
        found = E.vision_models()
        if not found:
            self.model.configure(values=[])
            self.model.set("")
            return self.set_ollama_status(
                False, "Ollama is up but no models are installed. Pull a vision model.")
        self.model.configure(values=found)
        want = self.conf.get("model")
        # Auto-pick best ID model; override fragile/weak saved prefs
        pick = E.pick_vision_model(found, preferred=want, purpose="id")
        self.model.set(pick if pick in found else found[0])
        self.conf["model"] = self.model.get()
        tip = " Place a coin under the camera and Capture." if (
            Cam and Cam.AVAILABLE) else (" Drop photos onto the wells." if _DND else "")
        fast = E.pick_pricing_model(found, self.model.get())
        extra = ""
        if fast and fast != self.model.get():
            extra = " ID=%s, price-text=%s." % (self.model.get(), fast)
        else:
            extra = " ID model: %s." % self.model.get()
        self.set_ollama_status(
            True,
            "Ready. %d model%s available.%s%s" % (
                len(found), "" if len(found) == 1 else "s", tip, extra))
        # Warm the ID model in the background so the first Read isn't cold
        warm = self.model.get()
        if warm:
            def warm_job():
                try:
                    if E.warmup_model(warm):
                        self.q.put(("say", "Model warm (%s) — ready to read." % warm))
                except Exception:
                    pass
            threading.Thread(target=warm_job, daemon=True).start()

    def on_close(self):
        self.release_camera()
        self.save_conf()
        # Leave Ollama running — killing it on exit made the next launch flaky.
        E.stop_owned_ollama()
        self.destroy()

    def settings(self):
        d = tk.Toplevel(self)
        d.title("Settings")
        d.configure(bg=BOARD, padx=20, pady=18)
        d.transient(self)
        tk.Label(d, text="Price sources", bg=BOARD, fg=CREAM,
                 font=(self.SERIF, 15)).pack(anchor="w")
        tk.Label(d, text="One domain per line. Search results from anywhere else are\n"
                         "discarded, so a random blog can't drive a valuation.",
                 bg=BOARD, fg=DIM, font=(self.SANS, 9), justify="left").pack(anchor="w",
                                                                            pady=(2, 8))
        box = tk.Text(d, width=42, height=12, bg=DARK, fg=CREAM, bd=0,
                      insertbackground=BRASS, font=(self.MONO, 10), padx=10, pady=8,
                      highlightthickness=1, highlightbackground=LINE)
        box.pack()
        box.insert("1.0", "\n".join(E.SOURCES))
        row = tk.Frame(d, bg=BOARD)
        row.pack(fill="x", pady=(12, 0))

        def save():
            E.SOURCES = [l.strip().replace("https://", "").replace("www.", "").strip("/")
                         for l in box.get("1.0", "end").splitlines() if l.strip()]
            self.save_conf()
            d.destroy()
            self.say("Sources updated (%d)." % len(E.SOURCES))
        ttk.Button(row, text="REFRESH MODELS", style="Ghost.TButton",
                   command=self.boot_ollama).pack(side="left")
        ttk.Button(row, text="SAVE", style="Go.TButton",
                   command=save).pack(side="right")

    def help(self):
        messagebox.showinfo("Setup", (
            "1. Install Ollama from ollama.com\n\n"
            "2. In a terminal:\n"
            "       ollama pull gemma3:4b\n\n"
            "   gemma3:4b works well for coin photos. qwen3-vl:8b is stronger\n"
            "   on tiny dates/mint marks if you have the VRAM.\n"
            "   Avoid llama3.2-vision unless your Ollama build supports mllama.\n\n"
            "3. Coin Tray starts Ollama when needed and leaves it running\n"
            "   after you quit (more reliable on Windows).\n\n"
            "Workflow:\n"
            "  1 Capture — Auto (2s) or Capture front/back (or drop photos).\n"
            "  2 Read — hit READ THE COIN.\n"
            "  3 Fix DATE & fields — tap country/type chips if wrong;\n"
            "     edit DATE; catalog name updates from the fields.\n"
            "     STATUS (Face/Keep/Sell/Check) is your bagging decision.\n"
            "     Silver hint appears after Read when the date/type looks Ag.\n"
            "     FACE SKIP (Ctrl+F) bags commons with no web search.\n"
            "     Then PRICE IT or PRICE & NEXT for live comps.\n"
            "  4 Price — commons are C at face value; U / R / G for higher mid values.\n"
            "     Duplicate year/country/type asks before adding again.\n"
            "     Use COPY BAGGIE LABEL or PRINT LABELS (uses current tray filters).\n\n"
            "Lighting for dates: soft even light from two sides (shade the bulbs).\n"
            "Avoid one harsh overhead lamp or flash — glare washes out the year.\n"
            "Matte dark background; keep the coin flat in the green circle.\n"
            "After Read, DATE is focused so you can type the correct year.\n\n"
            "Tiers: C common (<$1 / face) · U uncommon ($1–$25) · R rare ($25–$250)\n"
            "· G gem ($250+). Letters print large on baggie labels.\n\n"
            "CATALOG NAME is the baggie ID: CA-0007 · U · 1964 25c Ag\n"
            "  country code · rarity · year/type · unusual tags (Ag/comm/proof).\n"
            "Legacy CT-0001 names convert when you hit UPDATE or Price it.\n"
            "Tray filters: SU / CA / US / Ag / R+G / Keep / Sell / Check / Face.\n"
            "Type in Find to filter; click a row to reload it.\n"
            "Sort by Country, Newest, Value, or Tier.\n\n"
            "Your tray is saved to coin_tray_data.json (atomic write). Each save\n"
            "also copies the previous file into coin_tray_backups/ (last 20 kept).\n"
            "File → Restore tray backup… if you need an older copy."))


if __name__ == "__main__":
    app = CoinTray()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
