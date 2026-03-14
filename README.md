# Steam Backlog Organizer

Automatically categorizes your Steam game library into four collections:

- **Completed** - Games you've finished the main story/campaign
- **In Progress / Backlog** - Completable games you haven't finished yet
- **Endless** - Games with no real ending (multiplayer, sandbox, strategy, etc.)
- **Not a Game** - Demos, tools, utilities, soundtracks, etc.

Results are written directly to your Steam library as collections (synced across machines) and optionally saved as a JSON file.

## How It Works

Classification happens in two layers:

1. **Rule-based (free, no API key)** — Uses Steam tags, genres, playtime, and achievement data to classify the obvious cases. Handles ~70-80% of games automatically.
2. **AI-powered (optional, requires Anthropic API key)** — For ambiguous games that rules can't confidently classify, Claude AI makes the call using game knowledge, achievement names, and playtime context.

You get useful results out of the box. AI makes them better.

## Requirements

- Python 3.12+
- A [Steam Web API key](https://steamcommunity.com/dev/apikey) (free)
- Your Steam profile's game details set to **Public**
- *(Optional)* An [Anthropic API key](https://console.anthropic.com/settings/keys) for AI classification

## Setup

```powershell
cd steam-library-organizer
python -m venv venv

# Windows PowerShell:
.\venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

pip install requests rich
# Optional, for AI classification:
pip install anthropic
```

## Usage

```powershell
# First run — prompts for Steam API key and Steam ID
python organizer.py

# Update your saved configuration
python organizer.py --setup

# Manually override specific game categories
python organizer.py --override
```

## Features

- **Saved classifications** — Results persist between runs. Only new games get classified, so you never lose corrections.
- **Manual overrides** — Use `--override` to fix any game the rules or AI got wrong. Overrides always take priority.
- **Cloud sync** — Collections sync across machines via Steam Cloud, not just stored locally.
- **Caching** — Library and achievement data cached locally to avoid redundant Steam API calls.

## Building a Standalone Executable

```powershell
pip install pyinstaller
python build.py
```

Creates `dist/SteamLibraryOrganizer.exe`.

## Important Notes

- **Steam must be closed** when writing collections
- **API keys are stored locally** in `.config/settings.json` — never commit this file

## Development Log

### v0.1 — Initial release
- AI-only classification using Claude API (all 569 games sent every run)
- Wrote collections to Steam's local cloud storage file

### v0.2 — Iteration
- Achievement data and existing user collections as AI context improved accuracy
- Batch processing with progress save/resume handled interruptions
- Collections sync fix (updates Steam's sync metadata files)
- Added NOT_A_GAME category, manual overrides, saved classifications

### v1.0 — Hybrid rewrite (current)
- **Rule-based classification engine** — Uses Steam Store API (game type, genres, categories), achievement patterns, and playtime to classify ~70-80% of games for free
- **AI is now optional** — `anthropic` package and API key no longer required. Rules handle most games; AI only classifies the ambiguous remainder
- **Steam Store API integration** — Fetches game type/genres/categories, cached in `.cache/store_details.json`
- **Removed `--reclassify` flag** and batch progress save/resume (no longer needed since AI only handles small batches)
- **Code rewrite** — `main()` broken into helper functions, clean top-to-bottom flow
