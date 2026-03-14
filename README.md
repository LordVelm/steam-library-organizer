# Steam Backlog Organizer

Automatically categorizes your Steam game library into four collections:

- **Completed** - Games you've finished the main story/campaign
- **In Progress / Backlog** - Completable games you haven't finished yet
- **Endless** - Games with no real ending (multiplayer, sandbox, strategy, etc.)
- **Not a Game** - Demos, tools, utilities, soundtracks, etc.

Results are written directly to your Steam library as collections (synced across machines) and optionally saved as a JSON file.

## How It Works

Classification uses **rule-based logic** that analyzes your Steam data:

- **Steam Store data** — Game type (demo, tool, DLC), genres, categories (single-player, multiplayer)
- **Achievement data** — Story-completion achievement names, achievement percentage
- **Playtime** — Hours played relative to game type
- **Your existing Steam collections** — Used as hints for classification

No external AI or paid APIs needed beyond the free Steam Web API key.

## Requirements

- Python 3.12+
- A [Steam Web API key](https://steamcommunity.com/dev/apikey) (free)
- Your Steam profile's game details set to **Public**

## Setup

```powershell
cd steam-backlog-organizer
python -m venv venv

# Windows PowerShell:
.\venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

pip install requests rich customtkinter
```

## Usage

### GUI

```powershell
python gui.py
```

Or download the standalone `.exe` from [Releases](https://github.com/LordVelm/steam-backlog-organizer/releases).

### CLI

```powershell
# First run — prompts for Steam API key and Steam ID
python organizer.py

# Update your saved configuration
python organizer.py --setup

# Manually override specific game categories
python organizer.py --override
```

## Features

- **No paid APIs** — Fully rule-based classification using Steam's own data. Free to run.
- **Saved classifications** — Results persist between runs. Only new games get classified.
- **Manual overrides** — Fix any game the rules got wrong. Overrides always take priority.
- **Cloud sync** — Collections sync across machines via Steam Cloud.
- **Caching** — Library and achievement data cached locally to avoid redundant API calls.
- **Error handling** — Clear messages for network issues, invalid API keys, and file errors.

## Building a Standalone Executable

```powershell
pip install pyinstaller
python build.py          # GUI exe → dist/SteamBacklogOrganizer.exe
python build.py --cli    # CLI exe → dist/SteamBacklogOrganizer-CLI.exe
```

## Important Notes

- **Steam must be closed** when writing collections
- **API keys are stored locally** in `%APPDATA%/SteamBacklogOrganizer/config/settings.json` — not embedded in the exe

## Development Log

### v0.1 — Initial release
- AI-only classification using Claude API (all 569 games sent every run)
- Wrote collections to Steam's local cloud storage file

### v0.2 — Iteration
- Achievement data and existing user collections as AI context improved accuracy
- Batch processing with progress save/resume handled interruptions
- Collections sync fix (updates Steam's sync metadata files)
- Added NOT_A_GAME category, manual overrides, saved classifications

### v1.0 — Hybrid rewrite
- **Rule-based classification engine** — Uses Steam Store API (game type, genres, categories), achievement patterns, and playtime to classify ~70-80% of games for free
- **AI became optional** — Rules handled most games; AI only classified the ambiguous remainder
- **Steam Store API integration** — Fetches game type/genres/categories with permanent caching

### v2.0 — Pure rules, no AI (current)
- **Removed AI/Anthropic dependency entirely** — No paid API keys needed
- **Expanded rules from 9 to 14** — Covers all cases including moderate achievement + playtime heuristics, significant SP playtime detection, and genre-based fallbacks
- **GUI** — CustomTkinter app with Simple and Detailed view modes
- **Error handling** — Timeouts on all API calls, clear error messages for network/auth/permission failures, graceful handling of corrupt cache files

## Feedback & Support

- **Bug reports & feature requests** — [Open an issue](https://github.com/LordVelm/steam-backlog-organizer/issues)
- **Support the project** — [Buy Me a Coffee](https://buymeacoffee.com/lordvelm)
