#!/usr/bin/env python3
"""
Steam Library Organizer
Categorizes your Steam library using a hybrid approach:
  - Rule-based classification (free) handles most games
  - Optional AI classification (requires Anthropic API key) for ambiguous ones

Results are written directly to your Steam library as collections:
  Completed | In Progress / Backlog | Endless | Not a Game
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

try:
    import anthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

import requests
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# ── Paths ────────────────────────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).parent / ".config"
CONFIG_FILE = CONFIG_DIR / "settings.json"
CACHE_DIR = Path(__file__).parent / ".cache"
LIBRARY_CACHE = CACHE_DIR / "library.json"
CLASSIFICATIONS_FILE = CACHE_DIR / "classifications_final.json"
STORE_CACHE = CACHE_DIR / "store_details.json"
OVERRIDES_FILE = CONFIG_DIR / "overrides.json"

# Old progress cache — no longer used
PROGRESS_CACHE = CACHE_DIR / "classification_progress.json"

# ── Constants ────────────────────────────────────────────────────────────────

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_API = "https://store.steampowered.com/api/appdetails"
STEAM_BASE = Path("C:/Program Files (x86)/Steam")

# Patterns that indicate something is not a real game
NOT_A_GAME_NAME_PATTERNS = re.compile(
    r"(?i)\b("
    r"dedicated server|soundtrack|ost\b|sdk\b|benchmark|"
    r"demo\b|teaser|playable teaser|tech demo|"
    r"modding tool|level editor|map editor|"
    r"wallpaper engine|rpg maker|game maker|"
    r"vr home|steamvr|"
    r"test server|public test"
    r")\b"
)

# Achievement names that suggest story completion
COMPLETION_ACHIEVEMENT_PATTERNS = re.compile(
    r"(?i)("
    r"final.?boss|last.?boss|beat.?the.?game|"
    r"the.?end|credits|end.?credits|"
    r"complete.?the.?game|finish.?the.?game|game.?complete|"
    r"chapter.?\d+.?complete|act.?\d+.?complete|"
    r"epilogue|finale|"
    r"platinum|true.?ending|good.?ending|bad.?ending|"
    r"beat.?campaign|campaign.?complete|story.?complete"
    r")"
)


# ── Saved configuration ─────────────────────────────────────────────────────


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

    # Anthropic API Key (optional)
    current_anthropic_key = saved.get("anthropic_api_key", "")
    if current_anthropic_key and not force:
        masked = current_anthropic_key[:7] + "..." + current_anthropic_key[-4:]
        console.print(f"[dim]Anthropic API Key: {masked} (saved)[/dim]")
    else:
        console.print()
        console.print(
            "[bold]Anthropic API Key (optional)[/bold]\n"
            "  For AI-powered classification of ambiguous games.\n"
            "  Get one here: [link=https://console.anthropic.com/settings/keys]"
            "https://console.anthropic.com/settings/keys[/link]\n"
            "  [dim]Leave blank to use rule-based classification only (free).[/dim]\n"
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


# ── Steam API helpers ────────────────────────────────────────────────────────


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


# ── Steam Store API ──────────────────────────────────────────────────────────


def load_store_cache() -> dict:
    """Load cached store details. Returns {appid_str: details_dict}."""
    if not STORE_CACHE.exists():
        return {}
    try:
        return json.loads(STORE_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_store_cache(cache: dict):
    """Save store details cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STORE_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def fetch_store_details(app_id: int) -> dict | None:
    """Fetch store page details for a single app from Steam's store API."""
    try:
        resp = requests.get(
            STEAM_STORE_API,
            params={"appids": app_id, "l": "english"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        app_data = data.get(str(app_id), {})
        if not app_data.get("success"):
            return None
        details = app_data.get("data", {})
        return {
            "type": details.get("type", ""),
            "genres": [g.get("description", "") for g in details.get("genres", [])],
            "categories": [
                c.get("description", "") for c in details.get("categories", [])
            ],
        }
    except Exception:
        return None


def fetch_store_details_batch(
    app_ids: list[int], store_cache: dict, progress_callback=None
) -> dict:
    """Fetch store details for multiple apps, using cache where available.

    progress_callback(event, data): optional callback for GUI progress updates.
      Events: "store_progress" with {current, total, name}
    """
    uncached = [aid for aid in app_ids if str(aid) not in store_cache]

    if uncached:
        if progress_callback:
            progress_callback("store_progress", {"current": 0, "total": len(uncached), "name": ""})
        else:
            console.print(
                f"[dim]Fetching store details for {len(uncached)} games "
                f"({len(app_ids) - len(uncached)} cached)...[/dim]"
            )

        if not progress_callback:
            progress_ctx = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            )
            progress_obj = progress_ctx.__enter__()
            task = progress_obj.add_task(
                "Fetching store details...", total=len(uncached)
            )

        for i, app_id in enumerate(uncached):
            if progress_callback:
                progress_callback("store_progress", {"current": i + 1, "total": len(uncached), "name": str(app_id)})
            elif progress_obj:
                progress_obj.update(
                    task,
                    description=f"Store details ({i + 1}/{len(uncached)})",
                    completed=i,
                )
            details = fetch_store_details(app_id)
            if details:
                store_cache[str(app_id)] = details
            else:
                # Cache a miss so we don't retry
                store_cache[str(app_id)] = {"type": "", "genres": [], "categories": []}
            time.sleep(0.3)  # rate limit for store API

        if not progress_callback:
            progress_obj.update(task, completed=len(uncached))
            progress_ctx.__exit__(None, None, None)

        save_store_cache(store_cache)

    return store_cache


# ── Steam Collections (local file read/write) ───────────────────────────────


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


def find_steam_userdata(account_index: int | None = None) -> Path | None:
    """Find the Steam userdata directory.

    account_index: if provided, pick that account (0-indexed) without prompting.
    """
    userdata = STEAM_BASE / "userdata"
    if not userdata.exists():
        return None
    users = [d for d in userdata.iterdir() if d.is_dir()]
    if len(users) == 1:
        return users[0]
    if len(users) > 1:
        if account_index is not None:
            if 0 <= account_index < len(users):
                return users[account_index]
            return users[0]
        console.print("[bold]Multiple Steam accounts found:[/bold]")
        for i, u in enumerate(users):
            console.print(f"  {i + 1}. {u.name}")
        choice = Prompt.ask(
            "  Which account?", choices=[str(i + 1) for i in range(len(users))]
        )
        return users[int(choice) - 1]
    return None


def get_steam_accounts() -> list[Path]:
    """Return list of Steam userdata account directories."""
    userdata = STEAM_BASE / "userdata"
    if not userdata.exists():
        return []
    return [d for d in userdata.iterdir() if d.is_dir()]


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


# ── Caching ──────────────────────────────────────────────────────────────────


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


# ── Saved classifications & manual overrides ─────────────────────────────────


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


# ── Classification engine ────────────────────────────────────────────────────


def classify_by_rules(game: dict, store_info: dict | None) -> tuple[str, str] | None:
    """
    Classify a single game using rule-based logic.
    Returns (category, reason) or None if the game is ambiguous.

    Priority order:
      1. Steam type = demo/tool/music/dlc → NOT_A_GAME
      2. Name patterns (dedicated server, soundtrack, SDK, etc.) → NOT_A_GAME
      3. Story-completion achievements earned → COMPLETED
      4. High achievement % (≥80%) → COMPLETED
      5. Multiplayer-only (has MP, no SP) → ENDLESS
      6. MMO genre → ENDLESS
      7. Unplayed single-player → IN_PROGRESS (backlog)
      8. Otherwise → None (ambiguous)
    """
    name = game.get("name", "")
    playtime = game.get("playtime_hours", 0)
    achievements = game.get("achievements")

    # Store info
    store_type = ""
    genres = []
    categories = []
    if store_info:
        store_type = store_info.get("type", "").lower()
        genres = [g.lower() for g in store_info.get("genres", [])]
        categories = [c.lower() for c in store_info.get("categories", [])]

    # Rule 1: Steam type indicates non-game
    if store_type in ("demo", "tool", "music", "dlc", "video", "hardware", "mod"):
        return ("NOT_A_GAME", f"Steam type: {store_type}")

    # Rule 2: Name patterns
    if NOT_A_GAME_NAME_PATTERNS.search(name):
        match = NOT_A_GAME_NAME_PATTERNS.search(name)
        return ("NOT_A_GAME", f"Name pattern: {match.group()}")

    # Rule 3: Story-completion achievements earned
    if achievements and achievements.get("names_achieved"):
        for ach_name in achievements["names_achieved"]:
            if COMPLETION_ACHIEVEMENT_PATTERNS.search(ach_name):
                return ("COMPLETED", f"Story achievement: {ach_name}")

    # Rule 4: High achievement percentage
    if achievements and achievements.get("percentage", 0) >= 80:
        pct = achievements["percentage"]
        return ("COMPLETED", f"Achievement completion: {pct}%")

    # Rule 5: Multiplayer-only
    has_mp = any("multi-player" in c or "multiplayer" in c for c in categories)
    has_sp = any("single-player" in c for c in categories)
    if has_mp and not has_sp:
        return ("ENDLESS", "Multiplayer-only (no single-player)")

    # Rule 6: MMO genre
    if any("mmo" in g for g in genres):
        return ("ENDLESS", "MMO genre")

    # Rule 7: Sandbox/strategy/simulation with no story indicators
    endless_genres = {"simulation", "strategy", "casual", "sports", "racing"}
    if genres and not has_sp:
        game_genres_set = set(genres)
        if game_genres_set & endless_genres and not achievements:
            matched = game_genres_set & endless_genres
            return ("ENDLESS", f"Genre: {', '.join(matched)}")

    # Rule 8: Unplayed with single-player → backlog
    if playtime == 0 and has_sp:
        return ("IN_PROGRESS", "Unplayed single-player game (backlog)")

    # Rule 9: Unplayed with no store info → default to IN_PROGRESS
    if playtime == 0 and not store_info:
        return ("IN_PROGRESS", "Unplayed game (backlog)")

    return None  # Ambiguous — needs AI or defaults to IN_PROGRESS


def classify_all_games(
    games_data: list[dict],
    saved: dict,
    overrides: dict,
    store_cache: dict,
    anthropic_key: str | None,
    progress_callback=None,
) -> list[dict]:
    """
    Classify all games using the hybrid approach:
      1. Manual overrides → always win
      2. Saved classifications → reuse as-is
      3. Rule-based classification → handles ~70-80%
      4. AI classification (optional) → handles ambiguous remainder
      5. Fallback → IN_PROGRESS for anything still unclassified
    """
    results = {}
    unclassified = []
    rule_count = 0
    saved_count = 0
    override_count = 0

    for game in games_data:
        appid = game["appid"]
        appid_str = str(appid)

        # Layer 1: Manual overrides always win
        if appid_str in overrides:
            results[appid] = {
                "appid": appid,
                "name": game.get("name", f"App {appid}"),
                "category": overrides[appid_str],
                "confidence": "HIGH",
                "reason": "Manual override",
            }
            override_count += 1
            continue

        # Layer 1.5: NOT_A_GAME name patterns override saved classifications
        # (a "dedicated server" or "soundtrack" is never a real game, even if
        # a previous AI run classified it as something else)
        game_name = game.get("name", "")
        if NOT_A_GAME_NAME_PATTERNS.search(game_name):
            results[appid] = {
                "appid": appid,
                "name": game_name or f"App {appid}",
                "category": "NOT_A_GAME",
                "confidence": "HIGH",
                "reason": "Rule: name matches not-a-game pattern",
            }
            rule_count += 1
            continue

        # Layer 2: Reuse saved classifications
        if appid in saved:
            results[appid] = saved[appid]
            saved_count += 1
            continue

        # Layer 3: Rule-based classification
        store_info = store_cache.get(appid_str)
        rule_result = classify_by_rules(game, store_info)
        if rule_result:
            category, reason = rule_result
            results[appid] = {
                "appid": appid,
                "name": game.get("name", f"App {appid}"),
                "category": category,
                "confidence": "HIGH",
                "reason": f"Rule: {reason}",
            }
            rule_count += 1
            continue

        # Not classified yet — collect for AI or fallback
        unclassified.append(game)

    status_msg = (
        f"Classification: {override_count} overrides, "
        f"{saved_count} saved, {rule_count} rule-based, "
        f"{len(unclassified)} remaining"
    )
    if progress_callback:
        progress_callback("classify_status", {"message": status_msg})
    else:
        console.print(f"[dim]{status_msg}[/dim]")

    # Layer 4: AI classification for ambiguous games
    if unclassified and anthropic_key and HAS_ANTHROPIC:
        if progress_callback:
            progress_callback("classify_status", {"message": f"Classifying {len(unclassified)} ambiguous games with AI..."})
        else:
            console.print(
                f"\n[bold]Classifying {len(unclassified)} ambiguous games with AI...[/bold]"
            )
        ai_results = classify_with_ai(anthropic_key, unclassified, progress_callback=progress_callback)
        for g in ai_results:
            if g.get("appid"):
                results[g["appid"]] = g

        # Check if any still unclassified after AI
        ai_classified_ids = {g["appid"] for g in ai_results if g.get("appid")}
        still_unclassified = [g for g in unclassified if g["appid"] not in ai_classified_ids]
    elif unclassified and not anthropic_key:
        msg = (f"{len(unclassified)} games could not be classified by rules. "
               f"Add an Anthropic API key for AI classification.")
        if progress_callback:
            progress_callback("classify_status", {"message": msg})
        else:
            console.print(f"[dim]{msg}[/dim]")
        still_unclassified = unclassified
    elif unclassified and not HAS_ANTHROPIC:
        msg = (f"{len(unclassified)} games could not be classified by rules. "
               f"Install anthropic (pip install anthropic) for AI classification.")
        if progress_callback:
            progress_callback("classify_status", {"message": msg})
        else:
            console.print(f"[dim]{msg}[/dim]")
        still_unclassified = unclassified
    else:
        still_unclassified = []

    # Layer 5: Fallback — anything still unclassified defaults to IN_PROGRESS
    for game in still_unclassified:
        appid = game["appid"]
        results[appid] = {
            "appid": appid,
            "name": game.get("name", f"App {appid}"),
            "category": "IN_PROGRESS",
            "confidence": "LOW",
            "reason": "No rule match, no AI — defaulted to In Progress",
        }

    return list(results.values())


# ── AI Classification ────────────────────────────────────────────────────────

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
    client, games_batch: list[dict]
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


def classify_with_ai(anthropic_key: str, games: list[dict], progress_callback=None) -> list[dict]:
    """Classify a list of games using the Anthropic API."""
    client = anthropic.Anthropic(api_key=anthropic_key)
    BATCH_SIZE = 25
    all_classified = []

    batches = [games[i : i + BATCH_SIZE] for i in range(0, len(games), BATCH_SIZE)]

    if progress_callback:
        for i, batch in enumerate(batches):
            progress_callback("ai_progress", {"batch": i + 1, "total": len(batches)})
            classified = classify_games_batch(client, batch)
            all_classified.extend(classified)
            if i < len(batches) - 1:
                time.sleep(1)
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"AI batch 1/{len(batches)}...", total=len(batches)
            )
            for i, batch in enumerate(batches):
                progress.update(
                    task,
                    description=f"AI batch {i + 1}/{len(batches)}...",
                    completed=i,
                )
                classified = classify_games_batch(client, batch)
                all_classified.extend(classified)
                if i < len(batches) - 1:
                    time.sleep(1)
            progress.update(task, completed=len(batches))

    return all_classified


# ── Setup & config loading ───────────────────────────────────────────────────


def get_config() -> dict:
    """
    Load configuration from saved file, env vars, or prompt user.
    Priority: env vars > saved config > interactive prompt.
    First run triggers full setup automatically.
    Anthropic API key is optional — if missing, only rule-based classification is used.
    """
    saved = load_saved_config()

    # Resolve config values: env var > saved > None
    api_key = os.environ.get("STEAM_API_KEY") or saved.get("steam_api_key")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or saved.get("anthropic_api_key")
    steam_id = saved.get("steam_id")
    steam_id_input = saved.get("steam_id_input")

    # If Steam key or ID is missing, run first-time setup
    if not api_key or (not steam_id and not steam_id_input):
        console.print(
            Panel(
                "[bold]Steam Library Organizer[/bold]\n"
                "Categorize your Steam games into:\n"
                "  Completed | In Progress / Backlog | Endless\n\n"
                "Uses rule-based classification (free) with optional AI assist.\n"
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

    # Anthropic key is optional — just note it
    if not anthropic_key:
        if not HAS_ANTHROPIC:
            console.print(
                "[dim]AI classification unavailable (anthropic not installed). "
                "Using rules only.[/dim]"
            )
        else:
            console.print(
                "[dim]No Anthropic API key configured. Using rule-based classification only. "
                "Run with --setup to add one for AI-powered classification of ambiguous games.[/dim]"
            )

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
        "anthropic_api_key": anthropic_key.strip() if anthropic_key else None,
        "steam_id": steam_id.strip(),
    }


def get_config_from_values(steam_api_key: str, steam_id_input: str, anthropic_api_key: str = "") -> dict:
    """Build a config dict from explicit values (for GUI use). Raises ValueError on errors."""
    if not steam_api_key:
        raise ValueError("Steam API key is required.")
    if not steam_id_input:
        raise ValueError("Steam ID is required.")

    steam_id = None
    if steam_id_input.isdigit():
        steam_id = steam_id_input
    else:
        resolved = resolve_vanity_url(steam_api_key, steam_id_input)
        if resolved:
            steam_id = resolved
        else:
            raise ValueError(f"Could not resolve '{steam_id_input}' to a Steam ID.")

    # Save for future use
    saved = load_saved_config()
    saved["steam_api_key"] = steam_api_key
    saved["steam_id_input"] = steam_id_input
    saved["steam_id"] = steam_id
    if anthropic_api_key:
        saved["anthropic_api_key"] = anthropic_api_key
    save_config(saved)

    return {
        "steam_api_key": steam_api_key.strip(),
        "anthropic_api_key": anthropic_api_key.strip() if anthropic_api_key else None,
        "steam_id": steam_id.strip(),
    }


# ── Display & output ────────────────────────────────────────────────────────


def display_results(categories: dict[str, list[dict]], playtime_lookup: dict):
    """Display classification results as Rich tables."""
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


def display_summary(categories: dict[str, list[dict]], total_games: int):
    """Display classification summary panel."""
    console.print(
        Panel(
            f"[green]Completed:[/green] {len(categories.get('COMPLETED', []))} games\n"
            f"[yellow]In Progress / Backlog:[/yellow] {len(categories.get('IN_PROGRESS', []))} games\n"
            f"[cyan]Endless:[/cyan] {len(categories.get('ENDLESS', []))} games\n"
            f"[dim]Not a Game:[/dim] {len(categories.get('NOT_A_GAME', []))} items\n"
            f"[dim]Total classified: {sum(len(v) for v in categories.values())} / {total_games} games[/dim]",
            title="Summary",
            border_style="blue",
        )
    )


def save_json_export(config: dict, games_data: list, categories: dict):
    """Save results to a JSON file if the user wants."""
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
        documents = Path.home() / "Documents"
        if not documents.exists():
            documents = Path.home()
        output_path = documents / "steam_library_organized.json"
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        console.print(f"[green]Saved to {output_path}[/green]")


def write_steam_collections(cloud_data: list, cloud_path: Path, categories: dict):
    """Prompt and write classification results to Steam collections."""
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


# ── Fetch library data ──────────────────────────────────────────────────────


def fetch_library_data(config: dict, use_cache: bool | None = None, progress_callback=None) -> list[dict]:
    """Fetch library data from cache or Steam API, including achievements.

    use_cache: True=use cache, False=force refresh, None=prompt user (CLI mode).
    progress_callback(event, data): optional GUI progress updates.
      Events: "library_status", "library_progress", "achievement_progress"
    """
    games_data = None
    cache_result = load_library_cache(config["steam_id"])

    if cache_result:
        cached_games, age_hours = cache_result
        if use_cache is True:
            games_data = cached_games
            if progress_callback:
                progress_callback("library_status", {"message": f"Using cached data ({len(cached_games)} games, {age_hours:.1f}h old)"})
        elif use_cache is False:
            pass  # skip cache, fetch fresh
        else:
            # CLI mode — prompt
            console.print(
                f"[dim]Found cached library data ({len(cached_games)} games, "
                f"{age_hours:.1f} hours old).[/dim]"
            )
            if age_hours < 24:
                should_use = Confirm.ask("Use cached library data?", default=True)
            else:
                should_use = Confirm.ask(
                    f"Cache is {age_hours:.0f} hours old. Use it anyway?", default=False
                )
            if should_use:
                games_data = cached_games

    if games_data is None:
        # Fetch fresh from Steam
        if progress_callback:
            progress_callback("library_status", {"message": "Fetching your Steam library..."})
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Fetching your Steam library...", total=None)
                games = get_owned_games(config["steam_api_key"], config["steam_id"])
                progress.update(task, description=f"Found {len(games)} games!")

        if progress_callback:
            games = get_owned_games(config["steam_api_key"], config["steam_id"])
            progress_callback("library_status", {"message": f"Found {len(games)} games"})

        if not games:
            msg = ("No games found. Check that your Steam profile/game details are set to public. "
                   "Go to: Steam > Profile > Edit Profile > Privacy Settings > Set 'Game details' to Public.")
            if progress_callback:
                progress_callback("error", {"message": msg})
                return []
            else:
                console.print(f"[red]{msg}[/red]")
                sys.exit(1)

        played_games = [g for g in games if g.get("playtime_forever", 0) > 0]
        if progress_callback:
            progress_callback("library_status", {"message": f"Fetching achievements for {len(played_games)} played games..."})
        else:
            console.print(
                f"\n[bold]Found {len(games)} games in your library "
                f"({len(played_games)} played).[/bold]\n"
            )
            console.print(
                f"[dim]Fetching achievement data for {len(played_games)} played games "
                f"(skipping {len(games) - len(played_games)} unplayed)...[/dim]\n"
            )

        achievement_cache = {}
        if progress_callback:
            for i, game in enumerate(played_games):
                progress_callback("achievement_progress", {
                    "current": i + 1, "total": len(played_games),
                    "name": game.get("name", "Unknown"),
                })
                achievements = get_player_achievements(
                    config["steam_api_key"], config["steam_id"], game["appid"],
                )
                if achievements:
                    achievement_cache[game["appid"]] = achievements
                time.sleep(0.5)
        else:
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
                    time.sleep(0.5)  # rate limit
                progress.update(task, completed=len(played_games))

        if not progress_callback:
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

        save_library_cache(config["steam_id"], games_data)
        if progress_callback:
            progress_callback("library_status", {"message": f"Library data cached ({len(games_data)} games)"})
        else:
            console.print("[dim]Library data cached for future runs.[/dim]")

    return games_data


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    # Handle flags
    if "--setup" in sys.argv:
        run_setup(force=True)
        return
    do_override = "--override" in sys.argv

    # Welcome banner
    console.print(
        Panel(
            "[bold]Steam Library Organizer[/bold]\n"
            "Categorize your Steam games into:\n"
            "  Completed | In Progress / Backlog | Endless\n\n"
            "[dim]Hybrid: rules (free) + optional AI for ambiguous games[/dim]",
            title="Welcome",
            border_style="blue",
        )
    )
    console.print()

    # Load config (Steam key required, Anthropic optional)
    config = get_config()
    console.print()

    # Read existing Steam collections
    userdata_path = find_steam_userdata()
    cloud_data = []
    cloud_path = None
    user_collection_hints = {}

    if userdata_path:
        cloud_data, cloud_path = load_steam_collections(userdata_path)
        existing_collections = get_existing_collections(cloud_data)

        if existing_collections:
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

    # Get library data (cache or Steam API + achievements)
    games_data = fetch_library_data(config)

    # Add user collection hints to game data for AI context
    for game in games_data:
        if game["appid"] in user_collection_hints:
            game["user_collection"] = user_collection_hints[game["appid"]]

    # Handle manual overrides (if --override flag)
    if do_override:
        saved = load_saved_classifications()
        overrides = run_override_menu(games_data, saved)
    else:
        overrides = load_overrides()

    # Fetch store details for games that need rule-based classification
    saved = load_saved_classifications()
    games_needing_classification = [
        g for g in games_data
        if g["appid"] not in saved and str(g["appid"]) not in overrides
    ]

    store_cache = load_store_cache()
    if games_needing_classification:
        app_ids_to_fetch = [g["appid"] for g in games_needing_classification]
        store_cache = fetch_store_details_batch(app_ids_to_fetch, store_cache)

    # Classify all games
    all_classified = classify_all_games(
        games_data, saved, overrides, store_cache, config.get("anthropic_api_key")
    )

    # Save classifications
    save_final_classifications(all_classified)

    # Clean up old progress cache if it exists
    if PROGRESS_CACHE.exists():
        PROGRESS_CACHE.unlink()

    # Build categories and display
    categories = {"COMPLETED": [], "IN_PROGRESS": [], "ENDLESS": [], "NOT_A_GAME": []}
    for game in all_classified:
        cat = game.get("category", "ENDLESS")
        categories.setdefault(cat, []).append(game)

    for cat in categories:
        categories[cat].sort(key=lambda g: g.get("name", "").lower())

    playtime_lookup = {g["appid"]: g["playtime_hours"] for g in games_data}

    console.print()
    display_results(categories, playtime_lookup)
    display_summary(categories, len(games_data))

    # Write to Steam collections
    if cloud_data and cloud_path:
        write_steam_collections(cloud_data, cloud_path, categories)

    # Optional JSON export
    save_json_export(config, games_data, categories)


if __name__ == "__main__":
    main()
