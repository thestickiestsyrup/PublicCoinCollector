# Publishing a clean public GitHub

Keep this private clone for real tray data. Publish a **new** public repo with a
**fresh history** (do not force-push or rewrite the existing private remote).

## Pre-push checklist

Run from the repo root:

```powershell
# Must ignore Android SDK path / username
git check-ignore -v android/app/local.properties

# Must NOT list tray, config, samples, logs, local.properties
git status

# Expect no hits in tracked source/docs
git grep -n "jakob" -- ":!docs/PUBLIC_RELEASE.md" 2>$null
git grep -n "192.168.1.10" 2>$null
git grep -n "/9j/4AAQ" 2>$null
```

Never commit: `coin_tray_data.json`, `coin_tray_config.json`, `coin_tray_backups/`,
`samples/`, `*.log`, `android/app/local.properties`, `**/.idea/`.

## Option A — orphan branch in this clone (then new remote)

```powershell
git checkout --orphan public-main
git add -A
git status   # review carefully
git commit -m "Initial public release: Coin Tray desktop + Android companion"
# Create empty public repo on GitHub (e.g. coin-tray), then:
git remote add public https://github.com/YOUR_USER/coin-tray.git
git push -u public public-main:main
git checkout master   # or your private branch — leave private history alone
```

## Option B — export clean folder

```powershell
.\scripts\export_public_tree.ps1 -Dest ..\coin-tray-public
cd ..\coin-tray-public
git init -b main
git add -A
git commit -m "Initial public release: Coin Tray desktop + Android companion"
# Create empty public repo, then:
git remote add origin https://github.com/YOUR_USER/coin-tray.git
git push -u origin main
```

## After publish

1. Put the public URL into `docs/PROJECT_BRIEF.md` and regenerate the PDF:
   `python docs/make_brief_pdf.py`
2. Attach `docs/Coin_Tray_Project_Brief.pdf` to applications.
3. Optionally add anonymized screenshots under `docs/screenshots/` (see that folder’s README).
4. Keep sorting in the **private** clone; never copy real tray JSON into the public tree.
