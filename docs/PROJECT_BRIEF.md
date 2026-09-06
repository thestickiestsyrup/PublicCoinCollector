# Coin Tray — Project Brief

**Local-first coin identification & pricing**  
Desktop + Android companion · privacy-preserving numismatic workflow

## Problem

Sorting a mixed box of coins needs a quick identity and a rough market range without uploading every photo to a cloud AI service or paying per-token vision APIs.

## Solution

Coin Tray runs **Ollama vision models on your PC**, lets you correct fields while holding the coin, stamps structured **catalog baggie IDs**, then prices from an **allowlist** of guides and marketplaces with citations. An optional **Android CameraX** app captures on the phone and talks to the PC over home Wi‑Fi.

## Architecture (one line)

`Webcam / Phone camera → PC engine (Ollama VL) → Edit + Catalog ID → Allowlisted pricing → Tray (JSON / CSV)`

Android is a thin client: `http://LAN:8722` + `X-Coin-Tray-Token`. Ollama never leaves the PC.

## Resume bullets

- Built a **local-first** coin sorting system with on-machine vision ID (no cloud AI billing).
- Implemented **allowlisted** live pricing (guides, auctions, marketplaces) with IQR ranges, sold vs asking, and source citations.
- Designed **catalog baggie IDs** (`CA-0007 · U · 1964 25c Ag`) plus disposition workflow (Keep / Sell / Check / Face).
- Shipped a **Kotlin / Jetpack Compose / CameraX** Android companion over a LAN HTTP API.
- Hardened the companion path for home use: header-only token auth, no internet exposure, collection data kept out of git.

## Stack

| Area | Tech |
|------|------|
| Desktop | Python, Tk, OpenCV |
| Engine | `coin_engine.py`, Ollama VL + text |
| Phone API | stdlib `ThreadingHTTPServer` |
| Android | Kotlin, Compose, CameraX |

## Requirements

- Ollama + a vision model (e.g. `qwen3-vl:8b` or `gemma3:4b`)
- Python 3 + `pip install -r requirements.txt`
- Optional: Android Studio (JDK 17/21), phone on same Wi‑Fi

## Status

Personal / portfolio project. Estimates are comps, not appraisals. Not a substitute for professional grading.

## Links

- Repository: https://github.com/thestickiestsyrup/PublicCoinCollector  
- Docs: `README.md`, `docs/ARCHITECTURE.md`, `docs/OPERATOR.md`  
- License: MIT
