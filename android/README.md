# Coin Tray — Android companion

Open **this `android/` folder** in Android Studio, not the Python repo root.

## First run

1. On the PC: `python coin_tray.py` (from the repo root). Copy Phone URL + Token.
2. Android Studio → Open → `CoinCollector/android` (or your clone’s `android/`).
3. USB-debug an Android phone → Run.

If Studio says Gradle is incompatible with JDK 25: **File → Settings → Build,
Execution, Deployment → Build Tools → Gradle → Gradle JDK** and pick **17 or 21**,
or Sync again after this repo’s Gradle 9.1 / AGP 9.0 bump.

4. App Settings: paste `http://YOUR_LAN_IP:8722` and the token → Save & ping PC.
   Expect “PC reachable. Ollama model: …”.

5. One junk-coin loop before real sorting: Capture Front/Back (torch if indoor,
   tap preview to focus, 1×/2× as needed). Wells should look **tight**. Read,
   fix DATE + STATUS, Price it — range + IN HAND. Then Price & Next.

Cleartext HTTP on the LAN is allowed (`network_security_config.xml`). Do not
expose port 8722 to the internet. Auth uses the `X-Coin-Tray-Token` header only.
