# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Steam Backlog Organizer — a Python CLI tool that categorizes a user's Steam game library into four collections (Completed, In Progress/Backlog, Endless, Not a Game) using a hybrid approach: rule-based classification for obvious cases, optional AI (Claude) for ambiguous ones. Writes collections directly to Steam's local cloud storage with proper sync metadata so they appear on all machines.

## Commands

```bash
# Setup & run
python -m venv venv
source venv/bin/activate        # or .\venv\Scripts\Activate.ps1 on Windows
pip install requests rich
pip install anthropic           # optional, for AI classification

# Run
python organizer.py
python organizer.py --setup     # reconfigure API keys / Steam ID
python organizer.py --override  # manually fix game categories

# Build standalone exe
pip install pyinstaller
python build.py                 # outputs to dist/SteamLibraryOrganizer.exe
```

No test suite exists.

## Architecture

**organizer.py** is the single-file application with these sections:

- **Config management** — API keys and Steam ID in `.config/settings.json`. Priority: env vars > saved config > interactive prompt.
- **Steam API integration** — Fetches owned games, playtime, and per-game achievements. Rate-limited at 0.5s between calls.
- **Steam collections I/O** — Reads/writes Steam's `cloud-storage-namespace-1.json` in userdata. Also updates `cloud-storage-namespace-1.modified.json` and `cloud-storage-namespaces.json` so Steam syncs changes to the cloud. Requires Steam to be closed.
- **Caching** — Library data cached 24h in `.cache/library.json`. Store API details cached permanently in `.cache/store_details.json`.
- **Saved classifications** — Final results persisted in `.cache/classifications_final.json`. Only new games get classified on subsequent runs.
- **Manual overrides** — User corrections stored in `.config/overrides.json`. Always take priority over rules and AI.
- **Steam Store API** — `fetch_store_details()` gets game type/genres/categories from `store.steampowered.com/api/appdetails`. Rate-limited at 0.3s between calls.
- **Rule-based classification** — `classify_by_rules()` applies 9 priority rules: store type, name patterns, story achievements, high achievement %, multiplayer-only, MMO, genre-based, unplayed SP. `classify_all_games()` orchestrates: overrides → saved → rules → AI → fallback.
- **AI classification (optional)** — Sends ambiguous games to Claude for classification. Only runs if Anthropic API key is provided.
- **Output** — Rich tables in terminal, writes Steam collections, optional JSON report.

**build.py** — PyInstaller wrapper for building a one-file Windows executable.

## Key Design Decisions

- Hybrid classification: rules first (free), AI optional (costs money)
- Classifications are saved permanently — never re-run AI on already-classified games
- Manual overrides always win over both rules and AI
- Four categories: COMPLETED, IN_PROGRESS, ENDLESS, NOT_A_GAME
- Steam cloud sync requires updating three files, not just the namespace JSON
- Single-file architecture (no package/module structure)

## Categories

- **COMPLETED** — User finished the main story/campaign
- **IN_PROGRESS** — Game has a clear ending but user hasn't reached it (includes backlog)
- **ENDLESS** — No real completion state (multiplayer, sandbox, strategy, roguelikes)
- **NOT_A_GAME** — Demos, teasers, tools, utilities, soundtracks, dedicated servers
