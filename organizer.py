#!/usr/bin/env python3
"""
Steam Library Organizer
Categorizes your Steam library using AI into:
  - Completed: Games you've finished
  - In Progress / Backlog: Games with endings you haven't reached yet
  - Endless / No Completion: Games with no real "ending" (multiplayer, sandbox, etc.)

Reads your existing Steam collections and writes new ones directly to Steam.
"""

import json
import os
import re
import secrets
import string
import subprocess
import sys
import time
from pathlib import Path

import anthropic
import requests
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

CONFIG_DIR = Path(__file__).parent / ".config"
CONFIG_FILE = CONFIG_DIR / "settings.json"
CACHE_DIR = Path(__file__).parent / ".cache"
LIBRARY_CACHE = CACHE_DIR / "library.json"
PROGRESS_CACHE = CACHE_DIR / "classification_progress.json"
CLASSIFICATIONS_FILE = CACHE_DIR / "classifications_final.json"
OVERRIDES_FILE = CONFIG_DIR / "overrides.json"


# ── Saved configuration ───────────────────────────────────────────────────────


def load_saved_config() -> dict:
    """Load saved API keys and Steam ID from local config file."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict):
    """Save API keys and Steam ID to local config file."""
    CONFIG_DIR.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    console.print(f"[dim]Settings saved to {CONFIG_FILE}[/dim]")


def run_setup(force: bool = False):
    """
    Interactive setup to configure/update API keys and Steam ID.
    If force=True, prompts for all fields even if already saved.
    """
    saved = load_saved_config()
    updated = False

    console.print(
        Panel(
            "[bold]Configuration Setup[/bold]\n"
            "Enter your API keys and Steam ID. These will be saved locally\n"
            "so you only need to do this once. Press Enter to keep existing values.",
            title="Setup",
            border_style="blue",
        )
    )
    console.print()

    # Steam API Key
    current_steam_key = saved.get("steam_api_key", "")
    if current_steam_key and not force:
        masked = current_steam_key[:4] + "..." + current_steam_key[-4:]
        console.print(f"[dim]Steam API Key: {masked} (saved)[/dim]")
    else:
        console.print(
            "[bold]Steam API Key[/bold]\n"
            "  Get one here: [link=https://steamcommunity.com/dev/apikey]"
            "https://steamcommunity.com/dev/apikey[/link]\n"
            "  (Log into Steam, enter any domain name, and click Register.)\n"
        )
        hint = f" (Enter to keep current)" if current_steam_key else ""
        new_key = Prompt.ask(f"  Steam API Key{hint}", default="").strip()
        if new_key:
            saved["steam_api_key"] = new_key
            updated = True

    # Anthropic API Key
    current_anthropic_key = saved.get("anthropic_api_key", "")
    if current_anthropic_key and not force:
        masked = current_anthropic_key[:7] + "..." + current_anthropic_key[-4:]
        console.print(f"[dim]Anthropic API Key: {masked} (saved)[/dim]")
    else:
        console.print()
        console.print(
            "[bold]Anthropic API Key[/bold]\n"
            "  Get one here: [link=https://console.anthropic.com/settings/keys]"
            "https://console.anthropic.com/settings/keys[/link]\n"
        )
        hint = f" (Enter to keep current)" if current_anthropic_key else ""
        new_key = Prompt.ask(f"  Anthropic API Key{hint}", default="").strip()
        if new_key:
            saved["anthropic_api_key"] = new_key
            updated = True

    # Steam ID
    current_steam_id = saved.get("steam_id", "")
    if current_steam_id and not force:
        console.print(f"[dim]Steam ID: {current_steam_id} (saved)[/dim]")
    else:
        console.print()
        console.print(
            "[bold]Steam ID[/bold]\n"
            "  Enter your 64-bit Steam ID or custom profile URL name.\n"
            "  - 64-bit ID looks like: 76561198012345678\n"
            "  - Custom URL name: if your profile is steamcommunity.com/id/[bold]myname[/bold], enter [bold]myname[/bold]\n"
            "  - Find your ID at: [link=https://steamid.io]https://steamid.io[/link]\n"
        )
        hint = f" (Enter to keep current)" if current_steam_id else ""
        new_id = Prompt.ask(f"  Steam ID or profile name{hint}", default="").strip()
        if new_id:
            saved["steam_id_input"] = new_id
            # Clear resolved ID so it gets re-resolved
            saved.pop("steam_id", None)
            updated = True

    if updated:
        save_config(saved)
        console.print("[green]Configuration updated![/green]")
    else:
        console.print("[dim]No changes made.[/dim]")

    return saved

# ── Steam API helpers ──────────────────────────────────────────────────────────

STEAM_API_BASE = "https://api.steampowered.com"


def resolve_vanity_url(api_key: str, vanity_name: str) -> str | None:
    """Resolve a Steam vanity URL name to a 64-bit Steam ID."""
    resp = requests.get(
        f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/",
        params={"key": api_key, "vanityurl": vanity_name},
    )
    data = resp.json().get("response", {})
    if data.get("success") == 1:
        return data["steamid"]
    return None


def get_owned_games(api_key: str, steam_id: str) -> list[dict]:
    """Fetch all owned games with playtime and app info."""
    resp = requests.get(
        f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
        params={
            "key": api_key,
            "steamid": steam_id,
            "include_appinfo": True,
            "include_played_free_games": True,
        },
    )
    data = resp.json().get("response", {})
    return data.get("games", [])


def get_player_achievements(api_key: str, steam_id: str, app_id: int) -> dict | None:
    """Fetch achievement data for a specific game. Returns None if unavailable."""
    try:
        resp = requests.get(
            f"{STEAM_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/",
            params={"key": api_key, "steamid": steam_id, "appid": app_id},
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("playerstats", {})
        if not data.get("success"):
            return None
        achievements = data.get("achievements", [])
        if not achievements:
            return None
        total = len(achievements)
        achieved = sum(1 for a in achievements if a.get("achieved"))
        return {
            "total": total,
            "achieved": achieved,
            "percentage": round(achieved / total * 100, 1) if total > 0 else 0,
            "names_achieved": [
                a["apiname"] for a in achievements if a.get("achieved")
            ][:20],  # cap to avoid huge payloads
        }
    except Exception:
        return None


# ── Steam Collections (local file read/write) ─────────────────────────────────

STEAM_BASE = Path("C:/Program Files (x86)/Steam")


def is_steam_running() -> bool:
    """Check if Steam is currently running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe"],
            capture_output=True,
            text=True,
        )
        return "steam.exe" in result.stdout.lower()
    except Exception:
        return False


def wait_for_steam_closed():
    """Block until the user closes Steam, checking periodically."""
    while is_steam_running():
        console.print(
            "\n[red bold]Steam is currently running.[/red bold]\n"
            "  Please close Steam completely before writing collections.\n"
            "  (Steam tray icon → Exit Steam, or Task Manager → End Task)\n"
        )
        if not Confirm.ask("Check again?", default=True):
            console.print("[yellow]Skipping collection write.[/yellow]")
            return False
    return True


def find_steam_userdata() -> Path | None:
    """Find the Steam userdata directory. Returns the first user's path."""
    userdata = STEAM_BASE / "userdata"
    if not userdata.exists():
        return None
    users = [d for d in userdata.iterdir() if d.is_dir()]
    if len(users) == 1:
        return users[0]
    if len(users) > 1:
        console.print("[bold]Multiple Steam accounts found:[/bold]")
        for i, u in enumerate(users):
            console.print(f"  {i + 1}. {u.name}")
        choice = Prompt.ask(
            "  Which account?", choices=[str(i + 1) for i in range(len(users))]
        )
        return users[int(choice) - 1]
    return None


def load_steam_collections(userdata_path: Path) -> tuple[list, Path]:
    """Load the cloud storage JSON that contains Steam collections."""
    cloud_storage = (
        userdata_path / "config" / "cloudstorage" / "cloud-storage-namespace-1.json"
    )
    if not cloud_storage.exists():
        return [], cloud_storage

    data = json.loads(cloud_storage.read_text(encoding="utf-8"))
    return data, cloud_storage


def get_existing_collections(cloud_data: list) -> dict:
    """Extract user collections from cloud storage data."""
    collections = {}
    for entry in cloud_data:
        key = entry[0]
        meta = entry[1]
        if key.startswith("user-collections.") and not meta.get("is_deleted"):
            value = json.loads(meta.get("value", "{}"))
            if value.get("name"):
                collections[value["name"]] = {
                    "key": key,
                    "id": value.get("id"),
                    "added": value.get("added", []),
                    "removed": value.get("removed", []),
                }
    return collections


def generate_collection_id() -> str:
    """Generate a random collection ID in Steam's format."""
    chars = string.ascii_letters + string.digits + "+/"
    random_part = "".join(secrets.choice(chars) for _ in range(12))
    return f"uc-{random_part}"


def get_next_version(cloud_data: list) -> int:
    """Find the highest version number in the cloud data and return next."""
    max_version = 0
    for entry in cloud_data:
        v = int(entry[1].get("version", "0"))
        if v > max_version:
            max_version = v
    return max_version + 1


def write_collections_to_steam(
    cloud_data: list,
    cloud_path: Path,
    categories: dict[str, list[dict]],
    collection_names: dict[str, str],
):
    """
    Write classification results as Steam collections.

    categories: {"COMPLETED": [...], "IN_PROGRESS": [...], "ENDLESS": [...]}
    collection_names: {"COMPLETED": "AI: Completed", ...}
    """
    existing = get_existing_collections(cloud_data)
    version = get_next_version(cloud_data)
    timestamp = int(time.time())
    modified_keys = []

    for cat_key, display_name in collection_names.items():
        app_ids = [g["appid"] for g in categories.get(cat_key, []) if g.get("appid")]

        if display_name in existing:
            # Update existing collection
            coll = existing[display_name]
            new_value = json.dumps(
                {
                    "id": coll["id"],
                    "name": display_name,
                    "added": app_ids,
                    "removed": [],
                }
            )
            # Find and update the entry in cloud_data
            for entry in cloud_data:
                if entry[0] == coll["key"]:
                    entry[1]["value"] = new_value
                    entry[1]["timestamp"] = timestamp
                    entry[1]["version"] = str(version)
                    modified_keys.append(coll["key"])
                    break
        else:
            # Create new collection
            coll_id = generate_collection_id()
            coll_key = f"user-collections.{coll_id}"
            new_value = json.dumps(
                {
                    "id": coll_id,
                    "name": display_name,
                    "added": app_ids,
                    "removed": [],
                }
            )
            cloud_data.append(
                [
                    coll_key,
                    {
                        "key": coll_key,
                        "timestamp": timestamp,
                        "value": new_value,
                        "version": str(version),
                    },
                ]
            )
            modified_keys.append(coll_key)

        version += 1

    # Write the collection data
    cloud_path.write_text(json.dumps(cloud_data), encoding="utf-8")

    # Tell Steam these keys were modified locally so it syncs them to the cloud
    modified_path = cloud_path.with_name("cloud-storage-namespace-1.modified.json")
    existing_modified = []
    if modified_path.exists():
        try:
            existing_modified = json.loads(modified_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            existing_modified = []
    all_modified = list(set(existing_modified + modified_keys))
    modified_path.write_text(json.dumps(all_modified), encoding="utf-8")

    # Update the namespace version so Steam knows local state is newer
    namespaces_path = cloud_path.with_name("cloud-storage-namespaces.json")
    if namespaces_path.exists():
        try:
            namespaces = json.loads(namespaces_path.read_text(encoding="utf-8"))
            for ns in namespaces:
                if ns[0] == 1:
                    ns[1] = str(version)
                    break
            namespaces_path.write_text(json.dumps(namespaces), encoding="utf-8")
        except (json.JSONDecodeError, ValueError, IndexError):
            pass


# ── Caching ────────────────────────────────────────────────────────────────────


def save_library_cache(steam_id: str, games_data: list[dict]):
    """Cache the library data so we don't re-fetch from Steam on reruns."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache = {
        "steam_id": steam_id,
        "timestamp": time.time(),
        "games": games_data,
    }
    LIBRARY_CACHE.write_text(json.dumps(cache, indent=2))


def load_library_cache(steam_id: str) -> tuple[list[dict], float] | None:
    """Load cached library if it exists and matches the Steam ID."""
    if not LIBRARY_CACHE.exists():
        return None
    try:
        cache = json.loads(LIBRARY_CACHE.read_text())
        if cache.get("steam_id") != steam_id:
            return None
        age_hours = (time.time() - cache.get("timestamp", 0)) / 3600
        return cache.get("games"), age_hours
    except (json.JSONDecodeError, KeyError):
        return None


def save_classification_progress(
    steam_id: str, classified: list[dict], batch_index: int
):
    """Save classification progress so we can resume if interrupted."""
    CACHE_DIR.mkdir(exist_ok=True)
    progress = {
        "steam_id": steam_id,
        "timestamp": time.time(),
        "batch_index": batch_index,
        "classified": classified,
    }
    PROGRESS_CACHE.write_text(json.dumps(progress, indent=2))


def load_classification_progress(steam_id: str) -> tuple[list[dict], int] | None:
    """Load saved classification progress if available."""
    if not PROGRESS_CACHE.exists():
        return None
    try:
        progress = json.loads(PROGRESS_CACHE.read_text())
        if progress.get("steam_id") != steam_id:
            return None
        return progress.get("classified", []), progress.get("batch_index", 0)
    except (json.JSONDecodeError, KeyError):
        return None


def clear_classification_progress():
    """Remove progress cache after successful completion."""
    if PROGRESS_CACHE.exists():
        PROGRESS_CACHE.unlink()


# ── Saved classifications & manual overrides ──────────────────────────────────


def load_saved_classifications() -> dict:
    """Load previously saved final classifications. Returns {appid: game_dict}."""
    if not CLASSIFICATIONS_FILE.exists():
        return {}
    try:
        data = json.loads(CLASSIFICATIONS_FILE.read_text(encoding="utf-8"))
        return {g["appid"]: g for g in data if g.get("appid")}
    except (json.JSONDecodeError, KeyError):
        return {}


def save_final_classifications(all_classified: list):
    """Save final classifications so future runs reuse them."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CLASSIFICATIONS_FILE.write_text(
        json.dumps(all_classified, indent=2), encoding="utf-8"
    )


def load_overrides() -> dict:
    """Load manual overrides. Returns {appid: category_string}."""
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return {}


def save_overrides(overrides: dict):
    """Save manual overrides to config."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OVERRIDES_FILE.write_text(json.dumps(overrides, indent=2), encoding="utf-8")


def run_override_menu(games_data: list, saved: dict):
    """Interactive menu to manually override game categories."""
    overrides = load_overrides()
    name_lookup = {g["appid"]: g.get("name", f"App {g['appid']}") for g in games_data}
    category_labels = {"1": "COMPLETED", "2": "IN_PROGRESS", "3": "ENDLESS", "4": "NOT_A_GAME"}

    while True:
        console.print(
            "\n[bold]Manual Override[/bold]\n"
            "  Type a game name to search, or 'done' to finish."
        )
        if overrides:
            console.print(f"  [dim]({len(overrides)} override(s) currently set)[/dim]")

        query = Prompt.ask("\n  Search").strip()
        if query.lower() == "done":
            break

        # Search for matching games
        matches = [
            g for g in games_data
            if query.lower() in g.get("name", "").lower()
        ]
        if not matches:
            console.print(f"  [red]No games found matching '{query}'.[/red]")
            continue

        # Show matches
        for i, g in enumerate(matches[:15]):
            appid = g["appid"]
            current = overrides.get(str(appid)) or saved.get(appid, {}).get("category", "?")
            console.print(f"  {i + 1}. {g.get('name', '?')} [dim](currently: {current})[/dim]")

        if len(matches) > 15:
            console.print(f"  [dim]...and {len(matches) - 15} more. Try a more specific search.[/dim]")

        choice = Prompt.ask("  Select game #, or 'back'").strip()
        if choice.lower() == "back":
            continue
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= min(len(matches), 15):
                raise ValueError
        except ValueError:
            console.print("  [red]Invalid selection.[/red]")
            continue

        game = matches[idx]
        console.print(
            f"\n  [bold]{game.get('name')}[/bold]\n"
            "  Set category:\n"
            "    1. COMPLETED\n"
            "    2. IN_PROGRESS\n"
            "    3. ENDLESS\n"
            "    4. NOT_A_GAME\n"
            "    5. Remove override"
        )
        cat_choice = Prompt.ask("  Choice", choices=["1", "2", "3", "4", "5"])
        if cat_choice == "5":
            overrides.pop(str(game["appid"]), None)
            console.print(f"  [yellow]Override removed for {game.get('name')}.[/yellow]")
        else:
            overrides[str(game["appid"])] = category_labels[cat_choice]
            console.print(
                f"  [green]{game.get('name')} → {category_labels[cat_choice]}[/green]"
            )

    save_overrides(overrides)
    return overrides


# ── AI Classification ──────────────────────────────────────────────────────────

CLASSIFICATION_PROMPT = """\
You are a gaming expert. Classify each Steam game into one of four categories:

1. **COMPLETED** - The user has finished the main storyline or primary content.
   "Completed" means beating the main story/campaign — NOT 100% achievements.
   Many players complete a game with 20-50% of achievements.

2. **IN_PROGRESS** - The game HAS a clear ending/completion point, but the user
   probably hasn't reached it yet. This includes the user's backlog (owned but unplayed
   completable games).

3. **ENDLESS** - The game has no real "completion" state. Examples: multiplayer-only
   games, competitive games, sandbox games, simulation games, strategy games like
   Age of Empires or Civilization, endless roguelikes.

4. **NOT_A_GAME** - Not a real game. Examples: demos, teasers, playable teasers,
   tech demos, tools, utilities, software (Wallpaper Engine, RPG Maker), VR home
   apps, soundtracks, video players, benchmarks, dedicated servers, modding tools.

For each game, respond with ONLY a JSON object (no markdown, no explanation):
{
  "games": [
    {
      "appid": 12345,
      "name": "Game Name",
      "category": "COMPLETED" | "IN_PROGRESS" | "ENDLESS",
      "confidence": "HIGH" | "MEDIUM" | "LOW",
      "reason": "Brief explanation"
    }
  ]
}

PRIORITY RULES (follow in this order):
1. If a game has a "user_collection" field, that is the USER'S OWN categorization and
   takes highest priority. If the collection name suggests completion (e.g. "Completed",
   "Done", "Finished", "Beat"), classify as COMPLETED regardless of achievement %.
   Other collection names are informational hints.
2. Use achievements to HELP determine completion, but do NOT require high achievement %
   to mark a game as completed. Look for story-related achievement names (e.g.
   "beat_final_boss", "credits", "the_end", "chapter_5_complete") as completion signals.
   A player who earned 15% of achievements but has story-completion achievements is COMPLETED.
3. Use playtime relative to the game's known typical completion time as a signal.
4. Use genre/game knowledge for games without achievement data.

Other edge cases:
- A game with 0 minutes played that IS completable → IN_PROGRESS (backlog)
- A game with 0 minutes played that is NOT completable → ENDLESS
- Roguelikes with story elements (Hades) → completable
- Multiplayer-focused games with short campaigns (COD) → use playtime to judge
- Free-to-play games the user may have tried briefly → still classify normally
- Demos, teasers, playable teasers → NOT_A_GAME (even if they have achievements)
- Tools/utilities/software that happen to be on Steam → NOT_A_GAME
"""


def classify_games_batch(
    client: anthropic.Anthropic, games_batch: list[dict]
) -> list[dict]:
    """Send a batch of games to Claude for classification."""
    games_text = json.dumps(games_batch, indent=2)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"{CLASSIFICATION_PROMPT}\n\nHere are the games to classify:\n{games_text}",
            }
        ],
    )

    response_text = message.content[0].text

    # Extract JSON from response (handle markdown code blocks)
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    try:
        result = json.loads(response_text)
        return result.get("games", result) if isinstance(result, dict) else result
    except json.JSONDecodeError:
        console.print("[red]Warning: Could not parse AI response for a batch.[/red]")
        return []


# ── Setup & config loading ─────────────────────────────────────────────────────


def get_config() -> dict:
    """
    Load configuration from saved file, env vars, or prompt user.
    Priority: env vars > saved config > interactive prompt.
    First run triggers full setup automatically.
    """
    saved = load_saved_config()

    # Resolve config values: env var > saved > None
    api_key = os.environ.get("STEAM_API_KEY") or saved.get("steam_api_key")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or saved.get("anthropic_api_key")
    steam_id = saved.get("steam_id")
    steam_id_input = saved.get("steam_id_input")

    # If any required value is missing, run first-time setup
    if not api_key or not anthropic_key or (not steam_id and not steam_id_input):
        console.print(
            Panel(
                "[bold]Steam Library Organizer[/bold]\n"
                "Categorize your Steam games using AI into:\n"
                "  Completed | In Progress / Backlog | Endless\n\n"
                "Results are written directly to your Steam library as collections.\n\n"
                "[yellow]First time? Let's get you set up. "
                "You only need to do this once.[/yellow]",
                title="Welcome",
                border_style="blue",
            )
        )
        console.print()
        saved = run_setup(force=True)
        api_key = saved.get("steam_api_key")
        anthropic_key = saved.get("anthropic_api_key")
        steam_id = saved.get("steam_id")
        steam_id_input = saved.get("steam_id_input")

    if not api_key:
        console.print("[red]No Steam API key configured. Run with --setup to configure.[/red]")
        sys.exit(1)
    if not anthropic_key:
        console.print("[red]No Anthropic API key configured. Run with --setup to configure.[/red]")
        sys.exit(1)

    # Resolve vanity URL if needed
    if not steam_id and steam_id_input:
        if steam_id_input.isdigit():
            steam_id = steam_id_input
        else:
            console.print(f"[dim]Resolving vanity URL '{steam_id_input}'...[/dim]")
            resolved = resolve_vanity_url(api_key, steam_id_input)
            if resolved:
                steam_id = resolved
                console.print(f"[green]Resolved to Steam ID: {steam_id}[/green]")
                # Save resolved ID so we don't need to resolve again
                saved["steam_id"] = steam_id
                save_config(saved)
            else:
                console.print(
                    f"[red]Could not resolve '{steam_id_input}' to a Steam ID. "
                    "Run with --setup to reconfigure.[/red]"
                )
                sys.exit(1)

    if not steam_id:
        console.print("[red]No Steam ID configured. Run with --setup to configure.[/red]")
        sys.exit(1)

    return {
        "steam_api_key": api_key.strip(),
        "anthropic_api_key": anthropic_key.strip(),
        "steam_id": steam_id.strip(),
    }


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    # Handle flags
    if "--setup" in sys.argv:
        run_setup(force=True)
        return
    force_reclassify = "--reclassify" in sys.argv
    do_override = "--override" in sys.argv

    console.print(
        Panel(
            "[bold]Steam Library Organizer[/bold]\n"
            "Categorize your Steam games using AI into:\n"
            "  Completed | In Progress / Backlog | Endless",
            title="Welcome",
            border_style="blue",
        )
    )
    console.print()

    config = get_config()
    console.print()

    # ── Step 0: Read existing Steam collections ───────────────────────────
    userdata_path = find_steam_userdata()
    cloud_data = []
    cloud_path = None
    user_collection_hints = {}  # {appid: "collection_name"} for AI context

    if userdata_path:
        cloud_data, cloud_path = load_steam_collections(userdata_path)
        existing_collections = get_existing_collections(cloud_data)

        if existing_collections:
            # Show user collections (skip AI-generated ones)
            user_collections = {
                name: coll
                for name, coll in existing_collections.items()
                if not name.startswith("AI: ")
            }
            if user_collections:
                console.print("[dim]Found existing Steam collections:[/dim]")
                for name, coll in user_collections.items():
                    console.print(
                        f"  [dim]- {name} ({len(coll['added'])} games)[/dim]"
                    )

                # Build hints from ALL user collections for AI context
                for name, coll in user_collections.items():
                    for appid in coll.get("added", []):
                        user_collection_hints[appid] = name

                if user_collection_hints:
                    console.print(
                        f"\n[dim]Using your existing collections as hints "
                        f"for better classification.[/dim]"
                    )
    else:
        console.print(
            "[yellow]Could not find Steam userdata directory. "
            "Will output results to JSON only.[/yellow]"
        )

    console.print()

    # ── Step 1: Get library data (from cache or Steam API) ────────────────
    games_data = None
    cache_result = load_library_cache(config["steam_id"])

    if cache_result:
        cached_games, age_hours = cache_result
        console.print(
            f"[dim]Found cached library data ({len(cached_games)} games, "
            f"{age_hours:.1f} hours old).[/dim]"
        )
        if age_hours < 24:
            use_cache = Confirm.ask("Use cached library data?", default=True)
        else:
            use_cache = Confirm.ask(
                f"Cache is {age_hours:.0f} hours old. Use it anyway?", default=False
            )
        if use_cache:
            games_data = cached_games

    if games_data is None:
        # Fetch fresh from Steam
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching your Steam library...", total=None)
            games = get_owned_games(config["steam_api_key"], config["steam_id"])
            progress.update(task, description=f"Found {len(games)} games!")

        if not games:
            console.print(
                "[red]No games found. Check that your Steam profile/game details are set to public.[/red]\n"
                "  Go to: Steam > Profile > Edit Profile > Privacy Settings\n"
                "  Set 'Game details' to Public."
            )
            sys.exit(1)

        # Build base game data
        played_games = [g for g in games if g.get("playtime_forever", 0) > 0]
        console.print(
            f"\n[bold]Found {len(games)} games in your library "
            f"({len(played_games)} played).[/bold]\n"
        )

        # Fetch achievements for played games (much better accuracy)
        console.print(
            f"[dim]Fetching achievement data for {len(played_games)} played games "
            f"(skipping {len(games) - len(played_games)} unplayed)...[/dim]\n"
        )

        # Build a set of played appids for quick lookup
        played_appids = {g["appid"] for g in played_games}
        achievement_cache = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Fetching achievements...", total=len(played_games)
            )
            for i, game in enumerate(played_games):
                progress.update(
                    task,
                    description=f"Fetching achievements ({i+1}/{len(played_games)}): {game.get('name', 'Unknown')}",
                    completed=i,
                )
                achievements = get_player_achievements(
                    config["steam_api_key"],
                    config["steam_id"],
                    game["appid"],
                )
                if achievements:
                    achievement_cache[game["appid"]] = achievements
                time.sleep(0.5)  # rate limit — stay well under Steam's threshold

            progress.update(task, completed=len(played_games))

        console.print(
            f"[dim]Got achievement data for {len(achievement_cache)} games.[/dim]"
        )

        games_data = []
        for game in games:
            entry = {
                "appid": game["appid"],
                "name": game.get("name", f"App {game['appid']}"),
                "playtime_hours": round(
                    game.get("playtime_forever", 0) / 60, 1
                ),
            }
            if game["appid"] in achievement_cache:
                entry["achievements"] = achievement_cache[game["appid"]]
            games_data.append(entry)

        # Cache the library data
        save_library_cache(config["steam_id"], games_data)
        console.print("[dim]Library data cached for future runs.[/dim]")

    # Add user collection hints to game data for AI context
    for game in games_data:
        if game["appid"] in user_collection_hints:
            game["user_collection"] = user_collection_hints[game["appid"]]

    # ── Step 2: Handle manual overrides (if --override flag) ─────────────
    if do_override:
        saved = load_saved_classifications()
        overrides = run_override_menu(games_data, saved)
        # Apply overrides to saved classifications and rebuild categories
        for appid_str, category in overrides.items():
            appid = int(appid_str)
            if appid in saved:
                saved[appid]["category"] = category
            else:
                # Find game name from games_data
                name = next(
                    (g["name"] for g in games_data if g["appid"] == appid),
                    f"App {appid}",
                )
                saved[appid] = {
                    "appid": appid,
                    "name": name,
                    "category": category,
                    "confidence": "HIGH",
                    "reason": "Manual override",
                }
        all_classified = list(saved.values())
        save_final_classifications(all_classified)
    else:
        # ── Step 2: Classify with AI (reuse saved, only classify new) ──────
        saved = load_saved_classifications()
        overrides = load_overrides()
        all_known_appids = set(saved.keys())
        new_games = [g for g in games_data if g["appid"] not in all_known_appids]

        if saved and not force_reclassify:
            console.print(
                f"[dim]Found {len(saved)} previously classified games.[/dim]"
            )
            if new_games:
                console.print(
                    f"[bold]{len(new_games)} new game(s) to classify.[/bold]"
                )
            else:
                console.print(
                    "[green]All games already classified from previous run.[/green]"
                )

        if force_reclassify:
            games_to_classify = games_data
            console.print(
                f"\n[bold]Reclassifying all {len(games_to_classify)} games with AI...[/bold]\n"
            )
        else:
            games_to_classify = new_games

        if games_to_classify:
            if not force_reclassify and not new_games:
                pass  # Nothing to classify
            else:
                console.print(
                    f"\n[bold]Classifying {len(games_to_classify)} games with AI...[/bold]\n"
                )
                client = anthropic.Anthropic(api_key=config["anthropic_api_key"])

                BATCH_SIZE = 25
                newly_classified = []
                start_batch = 0

                # Check for interrupted progress
                saved_progress = load_classification_progress(config["steam_id"])
                if saved_progress:
                    saved_classified, saved_batch = saved_progress
                    console.print(
                        f"[dim]Found interrupted classification progress: "
                        f"{len(saved_classified)} games classified, stopped at batch {saved_batch + 1}.[/dim]"
                    )
                    if Confirm.ask("Resume from where it left off?", default=True):
                        newly_classified = saved_classified
                        start_batch = saved_batch + 1

                batches = [
                    games_to_classify[i : i + BATCH_SIZE]
                    for i in range(0, len(games_to_classify), BATCH_SIZE)
                ]

                if start_batch >= len(batches):
                    console.print(
                        "[green]Classification already complete from previous run![/green]"
                    )
                else:
                    remaining = len(batches) - start_batch
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console,
                    ) as progress:
                        task = progress.add_task(
                            f"Classifying batch {start_batch + 1}/{len(batches)}...",
                            total=remaining,
                        )

                        for i in range(start_batch, len(batches)):
                            progress.update(
                                task,
                                description=f"Classifying batch {i+1}/{len(batches)}...",
                                completed=i - start_batch,
                            )
                            classified = classify_games_batch(client, batches[i])
                            newly_classified.extend(classified)

                            save_classification_progress(
                                config["steam_id"], newly_classified, i
                            )

                            if i < len(batches) - 1:
                                time.sleep(1)

                        progress.update(task, completed=remaining)

                clear_classification_progress()

                # Merge new classifications into saved
                for g in newly_classified:
                    if g.get("appid"):
                        saved[g["appid"]] = g

        # Apply manual overrides on top
        for appid_str, category in overrides.items():
            appid = int(appid_str)
            if appid in saved:
                saved[appid]["category"] = category
                saved[appid]["reason"] = "Manual override"
                saved[appid]["confidence"] = "HIGH"

        all_classified = list(saved.values())
        save_final_classifications(all_classified)

    # ── Step 3: Display results ───────────────────────────────────────────
    categories = {"COMPLETED": [], "IN_PROGRESS": [], "ENDLESS": [], "NOT_A_GAME": []}
    for game in all_classified:
        cat = game.get("category", "ENDLESS")
        categories.setdefault(cat, []).append(game)

    # Sort each category by name
    for cat in categories:
        categories[cat].sort(key=lambda g: g.get("name", "").lower())

    # Build a lookup for playtime
    playtime_lookup = {g["appid"]: g["playtime_hours"] for g in games_data}

    console.print()

    category_styles = {
        "COMPLETED": ("green", "Completed"),
        "IN_PROGRESS": ("yellow", "In Progress / Backlog"),
        "ENDLESS": ("cyan", "Endless / No Completion"),
        "NOT_A_GAME": ("dim", "Not a Game"),
    }

    for cat_key, (color, label) in category_styles.items():
        games_list = categories.get(cat_key, [])
        table = Table(
            title=f"{label} ({len(games_list)} games)",
            border_style=color,
            show_lines=False,
        )
        table.add_column("Game", style="bold", min_width=30)
        table.add_column("Playtime", justify="right", min_width=10)
        table.add_column("Confidence", justify="center", min_width=10)
        table.add_column("Reason", max_width=50)

        for game in games_list:
            h = playtime_lookup.get(game.get("appid"), 0)
            playtime = f"{h}h" if h > 0 else "Never played"

            conf = game.get("confidence", "?")
            conf_style = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(
                conf, "white"
            )

            table.add_row(
                game.get("name", "Unknown"),
                playtime,
                f"[{conf_style}]{conf}[/{conf_style}]",
                game.get("reason", ""),
            )

        console.print(table)
        console.print()

    # Summary
    console.print(
        Panel(
            f"[green]Completed:[/green] {len(categories.get('COMPLETED', []))} games\n"
            f"[yellow]In Progress / Backlog:[/yellow] {len(categories.get('IN_PROGRESS', []))} games\n"
            f"[cyan]Endless:[/cyan] {len(categories.get('ENDLESS', []))} games\n"
            f"[dim]Not a Game:[/dim] {len(categories.get('NOT_A_GAME', []))} items\n"
            f"[dim]Total classified: {len(all_classified)} / {len(games_data)} games[/dim]",
            title="Summary",
            border_style="blue",
        )
    )

    # ── Step 4: Write to Steam collections ────────────────────────────────
    if cloud_data and cloud_path:
        console.print()
        console.print(
            "[bold]Ready to update your Steam library collections.[/bold]\n"
            "  This will create/update these collections in Steam:\n"
            "    [green]AI: Completed[/green]\n"
            "    [yellow]AI: In Progress[/yellow]\n"
            "    [cyan]AI: Endless[/cyan]\n"
            "    [dim]AI: Not a Game[/dim]\n"
            "\n"
            "  [dim]Your existing collections will NOT be touched — only 'AI:' prefixed ones are managed.\n"
            "  If these collections already exist from a previous run, they will be updated.\n"
            "  Steam must be closed before writing and restarted after for changes to appear.[/dim]"
        )

        if Confirm.ask("\nWrite collections to Steam?", default=True):
            if not wait_for_steam_closed():
                console.print(
                    "[dim]Collections were not written. "
                    "You can re-run the script to try again (cached data will be used).[/dim]"
                )
            else:
                collection_names = {
                    "COMPLETED": "AI: Completed",
                    "IN_PROGRESS": "AI: In Progress",
                    "ENDLESS": "AI: Endless",
                    "NOT_A_GAME": "AI: Not a Game",
                }
                write_collections_to_steam(
                    cloud_data, cloud_path, categories, collection_names
                )
                console.print(
                    "\n[green]Collections written![/green] "
                    "Start Steam to see them in your library sidebar."
                )

    # ── Step 5: Save JSON backup ──────────────────────────────────────────
    if Confirm.ask("\nSave results to a JSON file?", default=True):
        output = {
            "steam_id": config["steam_id"],
            "total_games": len(games_data),
            "categories": {
                cat_key: [
                    {
                        "appid": g.get("appid"),
                        "name": g.get("name"),
                        "confidence": g.get("confidence"),
                        "reason": g.get("reason"),
                    }
                    for g in cat_games
                ]
                for cat_key, cat_games in categories.items()
            },
        }
        # Save to user's Documents folder for easy access
        documents = Path.home() / "Documents"
        if not documents.exists():
            documents = Path.home()  # fallback if Documents doesn't exist
        output_path = documents / "steam_library_organized.json"
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        console.print(f"[green]Saved to {output_path}[/green]")


if __name__ == "__main__":
    main()
