# Coin Tray — operator guide

Day-to-day use of the desktop app and phone companion. For architecture and portfolio overview, see [ARCHITECTURE.md](ARCHITECTURE.md) and the root [README](../README.md).

## Setup

**1. Install [Ollama](https://ollama.com)**, then pull a vision model:

```
ollama pull qwen3-vl:8b        # best for identification (tiny dates / foreign legends)
ollama pull gemma3:4b          # fast default; also used for pricing text when both installed
ollama pull moondream          # last resort on weak hardware, ~2 GB
```

Coin Tray auto-picks the best **identification** model (prefers `qwen*-vl`, then
`gemma3`, avoids fragile `llama3.2-vision` / weak `moondream` when better tags exist).
Pricing JSON can use a smaller model so you are not waiting on a heavy VL twice.
The ID model is warmed in the background after Ollama starts.

**2. Install Python packages:**

```
pip install -r requirements.txt
```

(`pillow` for images; `opencv-python-headless` for the live view;
`tkinterdnd2` for drag-and-drop onto the photo wells.)

**3. Run the desktop app:**

```
python coin_desktop.py
```

Leave Ollama installed; Coin Tray will start it when needed. It leaves Ollama
running after you quit (more reliable on Windows). Click **Retry** in the status
bar if the model list is empty.

Optional: copy `coin_tray_config.example.json` to `coin_tray_config.json` to seed
model/sources (never commit a file that contains `phoneToken`).

### Webcam (recommended)

Plug in a USB webcam. Aim it straight down at a mat or tray.
Even, indirect light — no harsh overhead glare.

1. Place a coin inside the green circle on the live view.
2. With **Auto** on (default), hold steady ~2 seconds to capture the front;
   lift and flip; hold ~2 seconds again for the back. Or use **Capture front/back**.
3. **Read the coin**, correct the fields, then **Price it**.
4. **Next coin** clears the wells and re-arms auto-capture.

A second thicker green ring appears when the app spots the coin automatically; Capture
then crops to that. If detection misses, it still crops to the fixed center guide.
You can still click or drop image files onto the wells.

Pick the camera from the dropdown if the OS lists more than one. The choice is saved.

### Stream Deck (Windows)

1. In the Stream Deck app, add a **System → Open** action to a key.
2. Point it at `start_coin_tray.vbs` in this folder (relative path from the repo root).
3. Optionally set a coin/tray icon on the key.

The script starts the desktop app with no console window.

### If Tkinter is missing

- **macOS** — the system Python ships an ancient Tk. Use the python.org installer,
  or `brew install python-tk`.
- **Linux** — `sudo apt install python3-tk`
- **Windows** — included with the standard Python installer.

## Files

| file | what it is |
|---|---|
| `coin_desktop.py` | the desktop app |
| `coin_camera.py` | webcam live view, green guides, capture crop |
| `coin_engine.py` | shared logic: Ollama calls, search, price extraction |
| `start_coin_tray.vbs` | Windows launcher for Stream Deck (no console) |
| `start_phone_server.bat` | LAN API for the Android companion (`coin_tray.py`) |
| `requirements.txt` | Pillow + OpenCV + tkinterdnd2 |
| `coin_tray.py` + `index.html` | browser UI **and** phone LAN API (same engine) |
| `android/` | Kotlin / Compose CameraX companion |
| `coin_tray_data.json` | your tray (local only — gitignored) |
| `coin_tray_backups/` | rotating copies of the tray (last 20 saves) |

### Phone companion

The phone uses its camera. Identification and live pricing still run on the PC
(Ollama + allowlisted sites). Same Wi‑Fi required for v1. Settings has an
**On-device** toggle stub for a later phone model.

**On the PC**

1. Start Ollama as usual (or let the desktop app start it).
2. Run `start_phone_server.bat` or `python coin_tray.py`.
3. Note the printed **Phone URL** (`http://YOUR_LAN_IP:8722`) and **Token**.
4. If the firewall asks, allow Python on **private** networks.
5. Do **not** expose port 8722 to the internet.

**On the Android phone**

1. Enable USB debugging (Settings → Developer options).
2. Open the `android/` folder in Android Studio (File → Open). Let Gradle sync.
   Prefer **JDK 17 or 21** for Gradle (JDK 25 often breaks sync).
3. Plug in the phone, pick it as the run device, press Run.
4. In the app: **Settings** → paste the PC URL and token → **Save & ping PC**.
5. **Camera** → coin in the circle → Capture front/back → **Read** → **Edit** to
   fix DATE / STATUS → **Price it**, **Face skip**, or **Price & Next**.
   **Tray** can Pull/Push the PC list.

Keep the PC on and `coin_tray.py` running while you work. The desktop Tk app can
stay closed; both share `coin_tray_data.json`.

## How it works

**1. Capture.** Live view from your webcam (or drop/click photos into the wells).
Each side goes to your local model separately — small models do better on one image
than two. It reports only what it sees: legends, date, mint mark, design, metal
colour, wear, damage. It's told not to guess at an identification.

**2. Correct.** You get that transcription in editable fields. This is the important
step. An 8B model misreads worn dates constantly, and you're holding the coin.
Give each coin a **catalog name** (baggie ID) so you can find it later in the tray
and in exports. Format: `CA-0007 · U · 1964 25c Ag` —
**country code · rarity (C/U/R/G) · year/type · unusual tags** (`Ag` / `comm` / `proof`).
Numbers count up per country (`CA-0001…`, `SU-0001…`). Legacy `CT-0001` names convert
when you hit **UPDATE** or **Price it**. Tray sort defaults to **Country**.

After **Read**, a silver/base sort hint appears when the date and type look like
silver-era issues. Set **STATUS** (`Face` / `Keep` / `Sell` / `Check`) for how you
want to bag the coin — independent of the market tier. Tray filter chips
(`SU`, `CA`, `US`, `Ag`, `R+G`, and status) narrow the list; **Print baggie labels**
uses the filtered view. Pricing warns if the same year/country/type is already in
the tray.

**3. Price.** Your corrected details become search queries. Results are filtered to
an allowlist of sites, each page is fetched, and only passages mentioning money are
kept. Your local model reads those and reports a range with clickable sources.
It's told not to invent figures the sources don't support.

If the date (or most lettering) is worn away, **Price it** still runs: the vision
model guesses the type from the design, searches without a year, and falls back to
a typical circulated ballpark when listings are thin. **Find similar** does the
lookalike search in-app (design guess + marketplace listings in the SIMILAR COINS
panel) — no browser windows or temp photo files.

Click any coin in the tray to pull its result back up. Export to CSV or JSON from
the sidebar or the File menu — exports include the catalog name.

**FACE SKIP** bags an obvious common at face / nominal value with no web search
(Ctrl+F). Use **PRICE IT** when you want live comps. Result cards show an **IN HAND**
checklist (weight, diameter, magnet, edge) for foreign / silverish / catalogued types.

### Tray backups

The tray is written atomically to `coin_tray_data.json`. Each save also copies the
previous file into `coin_tray_backups/` (last 20 kept). If the main file is missing
or corrupt, Coin Tray loads the newest backup automatically. Use
**File ▸ Restore tray backup…** or **Open backups folder** to recover manually.

## Sources

Valuations only use pages from an allowlist (edit under **File ▸ Settings**).
Random blogs and one-off dealer pages outside that list are discarded.

**Default allowlist (trusted / commonly used):**

| Host | Role |
|---|---|
| pcgs.com, ngccoin.com | Major grading services + price guides |
| usacoinbook.com, coinvaluechecker.com, coinstudy.com | Retail / guide estimates |
| greatcollections.com, ha.com, stacksbowers.com, coinarchives.com | Auction houses / realized prices |
| numista.com | Catalog + collector reference (world coins) |
| ma-shops.com, catawiki.com, delcampe.net | World / European dealer marketplaces |
| ebay.com / .co.uk / .de, etsy.com | Sold & asking marketplace comps |

There is no single official “true” price for most coins. Industry practice is to
cross-check **guides** (PCGS/NGC), **auction realized** (Heritage, Stack’s, etc.),
and **recent sold listings** (eBay sold, dealer sales) rather than one asking price.

**What Coin Tray shows after Price it**

- **Range** — interquartile band from priced comps (resists one ridiculous ask)
- **Average / median** — across those comps
- **Sold vs asking** — counts of sold/realized vs asking listings it found
- **Thin evidence** warning — fewer than 2 prices, or only one site, or asking-only
- **SOURCES** — every page used, with price, condition/grade when stated, and kind

If search is thin on an odd world coin, paste a price-guide URL into the extra
source field and it’ll read that page directly.

## Photos

Prefer the live webcam: coin inside the green circle, even indirect light, no flash.
Glare and shadow are what wreck the reading. HEIC files from an iPhone still need
exporting as JPEG first if you load files instead of capturing.

## Worth knowing

Photos can't show surface, luster, or edge, and those decide a lot of a coin's value.
Never clean a coin. If something comes back worth real money, get it graded before
you sell.

## Security checklist

- Same private Wi‑Fi only; firewall allow Python on private networks
- Never port-forward `:8722`
- Token via `X-Coin-Tray-Token` header (apps already do this)
- Do not commit `coin_tray_data.json`, `coin_tray_config.json`, `samples/`, or logs
