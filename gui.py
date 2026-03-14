#!/usr/bin/env python3
"""
Steam Backlog Organizer — GUI
CustomTkinter interface with Simple and Detailed view modes.
"""

import json
import re
import sys
import threading
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
import webbrowser

import customtkinter as ctk

import organizer

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Help button hints ────────────────────────────────────────────────────────

HELP_STEAM_ID = (
    "Your 64-bit Steam ID or custom profile URL name.\n\n"
    "- 64-bit ID looks like: 76561198012345678\n"
    "- Custom URL: if your profile is\n"
    "  steamcommunity.com/id/myname, enter 'myname'\n"
    "- Find yours at: https://steamid.io"
)

HELP_API_KEY = (
    "Your Steam Web API key.\n\n"
    "Get one at: https://steamcommunity.com/dev/apikey\n"
    "(Log in, enter any domain name, click Register)"
)


class HelpButton:
    """A small '?' button that toggles an info popup on click.

    After creating, position with .btn.pack() or .btn.grid().
    """

    def __init__(self, parent, text: str):
        self.text = text
        self._popup = None
        self._bind_id = None
        self._configure_bind_id = None
        self._unbind_dismiss = lambda: None
        self.btn = ctk.CTkButton(
            parent, text="?", width=24, height=24,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#555555", hover_color="#666666",
            corner_radius=12,
            command=self._toggle,
        )

    def _toggle(self):
        if self._popup:
            self._popup.destroy()
            self._popup = None
            self._unbind_dismiss()
            return

        x = self.btn.winfo_rootx() + 30
        y = self.btn.winfo_rooty() - 5

        self._popup = tw = tk.Toplevel(self.btn)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)

        frame = tk.Frame(tw, background="#333333", borderwidth=1, relief="solid")
        frame.pack(fill="both", expand=True)

        textbox = tk.Text(
            frame, wrap="word", relief="flat", borderwidth=0,
            background="#333333", foreground="#e0e0e0",
            font=("Segoe UI", 9), padx=8, pady=6,
            cursor="arrow", selectbackground="#555555",
            highlightthickness=0,
        )

        # Insert text, making URLs clickable
        url_pattern = re.compile(r'(https?://\S+)')
        for line_idx, line in enumerate(self.text.split("\n")):
            if line_idx > 0:
                textbox.insert("end", "\n")
            parts = url_pattern.split(line)
            for part in parts:
                if url_pattern.match(part):
                    tag_name = f"link_{id(part)}_{line_idx}"
                    textbox.tag_configure(tag_name, foreground="#6ea8fe", underline=True)
                    textbox.tag_bind(tag_name, "<Enter>",
                                     lambda e: textbox.configure(cursor="hand2"))
                    textbox.tag_bind(tag_name, "<Leave>",
                                     lambda e: textbox.configure(cursor="arrow"))
                    url = part
                    textbox.tag_bind(tag_name, "<Button-1>",
                                     lambda e, u=url: webbrowser.open(u))
                    textbox.insert("end", part, tag_name)
                else:
                    textbox.insert("end", part)

        textbox.configure(state="normal")  # keep selectable

        lines = self.text.split("\n")
        width = min(max(len(line) for line in lines) + 2, 60)
        height = len(lines)
        textbox.configure(width=width, height=height)
        textbox.pack()

        # Close when clicking elsewhere or moving/resizing the window
        def _dismiss(event=None):
            if self._popup:
                try:
                    if event and event.type == tk.EventType.ButtonPress:
                        px = self._popup.winfo_rootx()
                        py = self._popup.winfo_rooty()
                        pw = self._popup.winfo_width()
                        ph = self._popup.winfo_height()
                        if px <= event.x_root <= px + pw and py <= event.y_root <= py + ph:
                            return
                    self._popup.destroy()
                    self._popup = None
                    self._unbind_dismiss()
                except tk.TclError:
                    pass

        def _unbind():
            root = self.btn.winfo_toplevel()
            try:
                if self._bind_id:
                    root.unbind("<Button-1>", self._bind_id)
                if self._configure_bind_id:
                    root.unbind("<Configure>", self._configure_bind_id)
            except tk.TclError:
                pass
            self._bind_id = None
            self._configure_bind_id = None

        self._unbind_dismiss = _unbind
        root = self.btn.winfo_toplevel()
        self._bind_id = root.bind("<Button-1>", _dismiss, add="+")
        self._configure_bind_id = root.bind("<Configure>", _dismiss, add="+")


class Tooltip:
    """Simple hover tooltip for short hints (e.g. button descriptions)."""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._tooltip_window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)

    def _schedule(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
        self._after_id = self.widget.after(400, self._show)

    def _show(self):
        self._after_id = None
        if self._tooltip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self._tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        label = tk.Label(tw, text=self.text, background="#333333", foreground="#e0e0e0",
                         font=("Segoe UI", 9), padx=8, pady=4, justify="left")
        label.pack()

    def _hide(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tooltip_window:
            self._tooltip_window.destroy()
            self._tooltip_window = None


# Category display config
CATEGORY_CONFIG = {
    "COMPLETED": {"label": "Completed", "color": "#2d8a4e"},
    "IN_PROGRESS": {"label": "In Progress", "color": "#3b8ed0"},
    "ENDLESS": {"label": "Endless", "color": "#8b5cf6"},
    "NOT_A_GAME": {"label": "Not a Game", "color": "#6b7280"},
}

COLLECTION_NAMES = {
    "COMPLETED": "SBO: Completed",
    "IN_PROGRESS": "SBO: In Progress",
    "ENDLESS": "SBO: Endless",
    "NOT_A_GAME": "SBO: Not a Game",
}


class SteamOrganizerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Steam Backlog Organizer")
        self.geometry("1000x720")
        self.minsize(800, 600)

        # Set window/taskbar icon
        if getattr(sys, "_MEIPASS", None):
            icon_dir = Path(sys._MEIPASS)
        else:
            icon_dir = Path(__file__).parent

        # Load all icon sizes
        self._icon_images = []
        for size in [256, 128, 64, 48, 32, 16]:
            icon_sized = icon_dir / f"icon_{size}.png"
            if icon_sized.exists():
                self._icon_images.append(tk.PhotoImage(file=str(icon_sized)))

        # Set icon now AND after CTk's 200ms delayed override
        self._iconbitmap_method_called = True  # prevent CTk from overriding
        self._apply_icon()
        self.after(300, self._apply_icon)

    def _apply_icon(self):
        if self._icon_images:
            self.iconphoto(True, *self._icon_images)

        # ── Shared state ──
        self.config = None
        self.games_data = []
        self.categories = {}
        self.playtime_lookup = {}
        self.cloud_data = []
        self.cloud_path = None
        self.overrides = {}
        self._running = False  # prevent double-clicks

        # Load saved config into fields
        self._saved = organizer.load_saved_config()

        # ── Top bar ──
        self.top_bar = ctk.CTkFrame(self, height=36, fg_color="transparent")
        self.top_bar.pack(fill="x", padx=10, pady=(8, 0))

        ctk.CTkLabel(self.top_bar, text="Steam Backlog Organizer",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        # View toggle
        self.view_var = ctk.StringVar(value="simple")
        toggle_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        toggle_frame.pack(side="right")
        ctk.CTkLabel(toggle_frame, text="Detailed", text_color="gray",
                     font=ctk.CTkFont(size=12)).pack(side="right", padx=(5, 0))
        self.view_switch = ctk.CTkSwitch(toggle_frame, text="", width=40,
                                         command=self._toggle_view,
                                         onvalue="detailed", offvalue="simple",
                                         variable=self.view_var)
        self.view_switch.pack(side="right")
        ctk.CTkLabel(toggle_frame, text="Simple", text_color="gray",
                     font=ctk.CTkFont(size=12)).pack(side="right", padx=(0, 5))

        # ── Views ──
        self.simple_view = SimpleView(self)
        self.detailed_view = DetailedView(self)
        self.simple_view.pack(fill="both", expand=True, padx=10, pady=10)

        # Load any existing results on startup
        self._load_existing_data()

    def _toggle_view(self):
        if self.view_var.get() == "detailed":
            self.simple_view.pack_forget()
            self.detailed_view.pack(fill="both", expand=True, padx=10, pady=10)
        else:
            self.detailed_view.pack_forget()
            self.simple_view.pack(fill="both", expand=True, padx=10, pady=10)
        self._refresh_views()

    def _load_existing_data(self):
        """Load cached classifications on startup so results show immediately."""
        saved = organizer.load_saved_classifications()
        if saved:
            all_classified = list(saved.values())
            self.categories = {"COMPLETED": [], "IN_PROGRESS": [], "ENDLESS": [], "NOT_A_GAME": []}
            for game in all_classified:
                cat = game.get("category", "ENDLESS")
                self.categories.setdefault(cat, []).append(game)
            for cat in self.categories:
                self.categories[cat].sort(key=lambda g: g.get("name", "").lower())

            # Load library cache for playtime
            steam_id = self._saved.get("steam_id", "")
            if steam_id:
                cache_result = organizer.load_library_cache(steam_id)
                if cache_result:
                    cached_games, _ = cache_result
                    self.games_data = cached_games
                    self.playtime_lookup = {g["appid"]: g.get("playtime_hours", 0) for g in cached_games}

            self.overrides = organizer.load_overrides()
            self._refresh_views()
            self._set_status(f"Loaded {len(all_classified)} cached classifications")

    def _refresh_views(self):
        """Update both views with current state."""
        self.simple_view.refresh(self.categories, self.playtime_lookup)
        self.detailed_view.refresh(self.categories, self.playtime_lookup)

    def _set_status(self, message: str):
        """Update status in both views."""
        self.simple_view.set_status(message)
        self.detailed_view.set_status(message)

    def _set_progress(self, value: float):
        """Update progress bar in both views (0.0 to 1.0)."""
        self.simple_view.set_progress(value)
        self.detailed_view.set_progress(value)

    def _set_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.simple_view.classify_btn.configure(state=state)
        self.simple_view.write_btn.configure(state=state)
        self.simple_view.refresh_btn.configure(state=state)
        self.detailed_view.classify_btn.configure(state=state)
        self.detailed_view.write_btn.configure(state=state)
        self.detailed_view.refresh_btn.configure(state=state)

    def _log(self, message: str):
        """Append to the log in detailed view."""
        self.detailed_view.log(message)

    # ── Get config from current field values ──
    def _get_field_values(self):
        """Get current config field values from whichever view is active."""
        # Simple view fields are canonical
        return self.simple_view.get_field_values()

    # ── Background operations ──

    def start_classify(self, force_refresh: bool = False):
        if self._running:
            return
        self._running = True
        self._force_refresh = force_refresh
        self._set_buttons_enabled(False)
        self._set_progress(0)
        thread = threading.Thread(target=self._classify_worker, daemon=True)
        thread.start()

    def start_refresh(self):
        """Re-fetch library from Steam API, then classify."""
        self.start_classify(force_refresh=True)

    def _progress_callback(self, event, data):
        """Called from background thread — schedules GUI update on main thread."""
        self.after(0, self._handle_progress, event, data)

    def _handle_progress(self, event, data):
        if event == "library_status":
            self._set_status(data["message"])
            self._log(data["message"])
        elif event == "achievement_progress":
            cur, total = data["current"], data["total"]
            self._set_progress(cur / total * 0.5)  # first 50% for achievements
            self._set_status(f"Achievements ({cur}/{total}): {data['name']}")
        elif event == "store_progress":
            cur, total = data["current"], data["total"]
            self._set_progress(0.5 + cur / total * 0.3)  # 50-80% for store
            self._set_status(f"Store details ({cur}/{total})")
        elif event == "classify_status":
            self._set_status(data["message"])
            self._log(data["message"])
        elif event == "error":
            messagebox.showerror("Error", data["message"])

    def _classify_worker(self):
        try:
            fields = self._get_field_values()

            # Build config
            self.after(0, self._set_status, "Validating configuration...")
            self.after(0, self._log, "Validating configuration...")
            try:
                self.config = organizer.get_config_from_values(
                    fields["steam_api_key"],
                    fields["steam_id"],
                )
            except ValueError as e:
                self.after(0, lambda: messagebox.showerror("Configuration Error", str(e)))
                return

            # Find Steam userdata
            userdata_path = organizer.find_steam_userdata(account_index=0)
            if userdata_path:
                self.cloud_data, self.cloud_path = organizer.load_steam_collections(userdata_path)
                existing = organizer.get_existing_collections(self.cloud_data)
                user_hints = {}
                for name, coll in existing.items():
                    if not name.startswith("SBO:"):
                        for appid in coll.get("added", []):
                            user_hints[appid] = name
            else:
                user_hints = {}

            # Use cache unless user clicked Refresh
            use_cache = not self._force_refresh
            self.after(0, self._log, "Refreshing library from Steam..." if self._force_refresh else "Loading library data...")
            self.games_data = organizer.fetch_library_data(
                self.config, use_cache=use_cache,
                progress_callback=self._progress_callback,
            )

            if not self.games_data:
                return

            # Add user collection hints
            for game in self.games_data:
                if game["appid"] in user_hints:
                    game["user_collection"] = user_hints[game["appid"]]

            self.playtime_lookup = {g["appid"]: g.get("playtime_hours", 0) for g in self.games_data}

            # Load overrides and saved
            self.overrides = organizer.load_overrides()
            saved = organizer.load_saved_classifications()

            # Fetch store details for unclassified games
            games_needing = [
                g for g in self.games_data
                if g["appid"] not in saved and str(g["appid"]) not in self.overrides
            ]
            store_cache = organizer.load_store_cache()
            if games_needing:
                self.after(0, self._log, f"Fetching store details for {len(games_needing)} games...")
                store_cache = organizer.fetch_store_details_batch(
                    [g["appid"] for g in games_needing], store_cache,
                    progress_callback=self._progress_callback,
                )

            # Classify
            self.after(0, self._log, "Classifying games...")
            all_classified = organizer.classify_all_games(
                self.games_data, saved, self.overrides, store_cache,
                progress_callback=self._progress_callback,
            )

            # Save
            organizer.save_final_classifications(all_classified)

            # Build categories
            categories = {"COMPLETED": [], "IN_PROGRESS": [], "ENDLESS": [], "NOT_A_GAME": []}
            for game in all_classified:
                cat = game.get("category", "ENDLESS")
                categories.setdefault(cat, []).append(game)
            for cat in categories:
                categories[cat].sort(key=lambda g: g.get("name", "").lower())
            self.categories = categories

            # Clean up old progress cache
            if organizer.PROGRESS_CACHE.exists():
                organizer.PROGRESS_CACHE.unlink()

            total = sum(len(v) for v in categories.values())
            summary = (
                f"Done! {total} games classified — "
                f"Completed: {len(categories['COMPLETED'])}, "
                f"In Progress: {len(categories['IN_PROGRESS'])}, "
                f"Endless: {len(categories['ENDLESS'])}, "
                f"Not a Game: {len(categories['NOT_A_GAME'])}"
            )
            self.after(0, self._set_progress, 1.0)
            self.after(0, self._set_status, summary)
            self.after(0, self._log, summary)
            self.after(0, self._refresh_views)

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
        finally:
            self.after(0, self._classify_done)

    def _classify_done(self):
        self._running = False
        self._set_buttons_enabled(True)

    def start_write_to_steam(self):
        if self._running:
            return
        if not self.categories or not any(self.categories.values()):
            messagebox.showwarning("No Data", "Run classification first before writing to Steam.")
            return
        if not self.cloud_data or not self.cloud_path:
            messagebox.showerror("Error", "Could not find Steam userdata directory.")
            return
        if organizer.is_steam_running():
            messagebox.showwarning(
                "Steam Running",
                "Steam must be closed before writing collections.\n\n"
                "Close Steam (tray icon → Exit Steam) and try again."
            )
            return

        try:
            organizer.write_collections_to_steam(
                self.cloud_data, self.cloud_path,
                self.categories, COLLECTION_NAMES,
            )
            self._set_status("Collections written! Start Steam to see them.")
            self._log("Collections written to Steam successfully.")
            messagebox.showinfo("Success", "Collections written!\nStart Steam to see them in your library sidebar.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to write collections:\n{e}")

    def open_override_dialog(self):
        OverrideDialog(self)


class SimpleView(ctk.CTkFrame):
    """Single-screen layout: settings bar → buttons → progress → results columns."""

    def __init__(self, parent: SteamOrganizerApp):
        super().__init__(parent, fg_color="transparent")
        self.app = parent

        # ── Settings bar ──
        settings_frame = ctk.CTkFrame(self)
        settings_frame.pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(settings_frame, text="Steam ID:").pack(side="left", padx=(10, 5), pady=8)
        self.steam_id_entry = ctk.CTkEntry(settings_frame, width=140,
                                           placeholder_text="76561198...")
        self.steam_id_entry.pack(side="left", padx=5)
        HelpButton(settings_frame, HELP_STEAM_ID).btn.pack(side="left", padx=(0, 5))

        ctk.CTkLabel(settings_frame, text="API Key:").pack(side="left", padx=(10, 5))
        self.api_key_entry = ctk.CTkEntry(settings_frame, width=160,
                                          placeholder_text="Steam API Key", show="•")
        self.api_key_entry.pack(side="left", padx=5)
        HelpButton(settings_frame, HELP_API_KEY).btn.pack(side="left", padx=(0, 5))

        # Pre-fill from saved config
        saved = parent._saved
        if saved.get("steam_id") or saved.get("steam_id_input"):
            self.steam_id_entry.insert(0, saved.get("steam_id") or saved.get("steam_id_input", ""))
        if saved.get("steam_api_key"):
            self.api_key_entry.insert(0, saved["steam_api_key"])

        # ── Action buttons + progress ──
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.pack(fill="x", pady=5)

        self.classify_btn = ctk.CTkButton(
            action_frame, text="▶  Classify Library", width=160, height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=parent.start_classify,
        )
        self.classify_btn.pack(side="left", padx=(0, 10))

        self.write_btn = ctk.CTkButton(
            action_frame, text="Write to Steam", width=140, height=36,
            fg_color="#2d8a4e", hover_color="#236b3c",
            command=parent.start_write_to_steam,
        )
        self.write_btn.pack(side="left", padx=5)

        ctk.CTkButton(
            action_frame, text="Overrides", width=100, height=36,
            fg_color="#6b5b3e", hover_color="#5a4c33",
            command=parent.open_override_dialog,
        ).pack(side="left", padx=5)

        self.refresh_btn = ctk.CTkButton(
            action_frame, text="↻  Refresh", width=100, height=36,
            fg_color="#4a4a4a", hover_color="#5a5a5a",
            command=parent.start_refresh,
        )
        self.refresh_btn.pack(side="left", padx=5)
        Tooltip(self.refresh_btn, "Re-fetch library data from Steam.\nUse this if you've played new games\nsince the last run.")

        # Progress + status
        status_frame = ctk.CTkFrame(self, fg_color="transparent")
        status_frame.pack(fill="x", pady=(0, 5))

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=300)
        self.progress_bar.pack(side="left", padx=(0, 10), pady=5)
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(status_frame, text="Ready",
                                         text_color="gray", font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", fill="x", expand=True, anchor="w")

        # ── Results: 4 columns ──
        self.results_frame = ctk.CTkFrame(self)
        self.results_frame.pack(fill="both", expand=True)
        self.results_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.results_frame.grid_rowconfigure(1, weight=1)

        self.col_headers = {}
        self.col_textboxes = {}
        for col, (cat_key, cfg) in enumerate(CATEGORY_CONFIG.items()):
            header = ctk.CTkLabel(self.results_frame, text=f"{cfg['label']} (0)",
                                  font=ctk.CTkFont(size=13, weight="bold"),
                                  text_color=cfg["color"])
            header.grid(row=0, column=col, padx=5, pady=(8, 3), sticky="w")
            self.col_headers[cat_key] = header

            textbox = ctk.CTkTextbox(self.results_frame, font=ctk.CTkFont(size=11),
                                     activate_scrollbars=True)
            textbox.grid(row=1, column=col, padx=5, pady=(0, 5), sticky="nsew")
            textbox.configure(state="disabled")
            self.col_textboxes[cat_key] = textbox

    def get_field_values(self) -> dict:
        return {
            "steam_id": self.steam_id_entry.get().strip(),
            "steam_api_key": self.api_key_entry.get().strip(),
        }

    def set_status(self, message: str):
        self.status_label.configure(text=message)

    def set_progress(self, value: float):
        self.progress_bar.set(value)

    def refresh(self, categories: dict, playtime_lookup: dict):
        for cat_key, cfg in CATEGORY_CONFIG.items():
            games = categories.get(cat_key, [])
            self.col_headers[cat_key].configure(text=f"{cfg['label']} ({len(games)})")

            tb = self.col_textboxes[cat_key]
            tb.configure(state="normal")
            tb.delete("1.0", "end")
            for g in games:
                h = playtime_lookup.get(g.get("appid"), 0)
                pt = f" ({h}h)" if h > 0 else ""
                tb.insert("end", f"  {g.get('name', '?')}{pt}\n")
            tb.configure(state="disabled")


class DetailedView(ctk.CTkFrame):
    """Tabbed layout: Setup | Classify | Results | Overrides."""

    def __init__(self, parent: SteamOrganizerApp):
        super().__init__(parent, fg_color="transparent")
        self.app = parent

        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True)

        self._build_setup_tab()
        self._build_classify_tab()
        self._build_results_tab()
        self._build_overrides_tab()

        self.tabview.set("  Classify  ")

    def _build_setup_tab(self):
        tab = self.tabview.add("  Setup  ")

        inner = ctk.CTkFrame(tab, fg_color="transparent")
        inner.pack(expand=True)

        ctk.CTkLabel(inner, text="Configuration",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 5))
        ctk.CTkLabel(inner, text="Enter your API keys and Steam ID. These are saved locally.",
                     text_color="gray").pack(pady=(0, 25))

        fields = ctk.CTkFrame(inner, fg_color="transparent")
        fields.pack()

        saved = self.app._saved

        ctk.CTkLabel(fields, text="Steam ID:", font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, padx=(0, 15), pady=10, sticky="e")
        self.setup_steam_id = ctk.CTkEntry(fields, width=350, placeholder_text="76561198... or vanity URL name")
        self.setup_steam_id.grid(row=0, column=1, pady=10)
        HelpButton(fields, HELP_STEAM_ID).btn.grid(row=0, column=2, padx=(5, 0), pady=10)
        if saved.get("steam_id") or saved.get("steam_id_input"):
            self.setup_steam_id.insert(0, saved.get("steam_id") or saved.get("steam_id_input", ""))

        ctk.CTkLabel(fields, text="Steam API Key:", font=ctk.CTkFont(size=13)).grid(
            row=1, column=0, padx=(0, 15), pady=10, sticky="e")
        self.setup_api_key = ctk.CTkEntry(fields, width=350, placeholder_text="From steamcommunity.com/dev/apikey", show="•")
        self.setup_api_key.grid(row=1, column=1, pady=10)
        HelpButton(fields, HELP_API_KEY).btn.grid(row=1, column=2, padx=(5, 0), pady=10)
        if saved.get("steam_api_key"):
            self.setup_api_key.insert(0, saved["steam_api_key"])

        ctk.CTkButton(inner, text="Save Settings", width=200, height=36,
                       command=self._save_settings).pack(pady=20)

        self.setup_status = ctk.CTkLabel(inner, text="", text_color="#2d8a4e")
        self.setup_status.pack()

    def _save_settings(self):
        """Save settings from the Setup tab and sync to Simple view."""
        steam_id = self.setup_steam_id.get().strip()
        api_key = self.setup_api_key.get().strip()

        if not api_key:
            messagebox.showwarning("Missing Field", "Steam API key is required.")
            return
        if not steam_id:
            messagebox.showwarning("Missing Field", "Steam ID is required.")
            return

        config = {"steam_api_key": api_key, "steam_id_input": steam_id}
        if steam_id.isdigit():
            config["steam_id"] = steam_id

        organizer.save_config(config)
        self.app._saved = config
        self.setup_status.configure(text="Settings saved!")

        # Sync to simple view
        sv = self.app.simple_view
        sv.steam_id_entry.delete(0, "end")
        sv.steam_id_entry.insert(0, steam_id)
        sv.api_key_entry.delete(0, "end")
        sv.api_key_entry.insert(0, api_key)

    def _build_classify_tab(self):
        tab = self.tabview.add("  Classify  ")

        self.classify_status_label = ctk.CTkLabel(
            tab, text="Ready to classify",
            font=ctk.CTkFont(size=14))
        self.classify_status_label.pack(pady=(25, 10))

        self.classify_progress = ctk.CTkProgressBar(tab, width=500)
        self.classify_progress.pack(pady=10)
        self.classify_progress.set(0)

        self.log_box = ctk.CTkTextbox(tab, width=600, height=220,
                                       font=ctk.CTkFont(size=12))
        self.log_box.pack(pady=15, fill="x", padx=20)
        self.log_box.configure(state="disabled")

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.pack(pady=15)

        self.classify_btn = ctk.CTkButton(
            btn_frame, text="▶  Classify Library", width=180, height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.app.start_classify,
        )
        self.classify_btn.pack(side="left", padx=10)

        self.write_btn = ctk.CTkButton(
            btn_frame, text="Write to Steam", width=160, height=40,
            fg_color="#2d8a4e", hover_color="#236b3c",
            command=self.app.start_write_to_steam,
        )
        self.write_btn.pack(side="left", padx=10)

        self.refresh_btn = ctk.CTkButton(
            btn_frame, text="↻  Refresh Library", width=160, height=40,
            fg_color="#4a4a4a", hover_color="#5a5a5a",
            command=self.app.start_refresh,
        )
        self.refresh_btn.pack(side="left", padx=10)
        Tooltip(self.refresh_btn, "Re-fetch library data from Steam.\nUse this if you've played new games\nsince the last run.")

    def _build_results_tab(self):
        tab = self.tabview.add("  Results  ")

        tab.grid_columnconfigure((0, 1, 2, 3), weight=1)
        tab.grid_rowconfigure(1, weight=1)

        self.result_headers = {}
        self.result_textboxes = {}
        for col, (cat_key, cfg) in enumerate(CATEGORY_CONFIG.items()):
            header = ctk.CTkLabel(tab, text=f"{cfg['label']} (0)",
                                  font=ctk.CTkFont(size=13, weight="bold"),
                                  text_color=cfg["color"])
            header.grid(row=0, column=col, padx=5, pady=(8, 3), sticky="w")
            self.result_headers[cat_key] = header

            textbox = ctk.CTkTextbox(tab, font=ctk.CTkFont(size=11))
            textbox.grid(row=1, column=col, padx=5, pady=(0, 5), sticky="nsew")
            textbox.configure(state="disabled")
            self.result_textboxes[cat_key] = textbox

    def _build_overrides_tab(self):
        tab = self.tabview.add("  Overrides  ")

        ctk.CTkLabel(tab, text="Manual Overrides",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(15, 5))
        ctk.CTkLabel(tab, text="Search for a game and set its category manually.",
                     text_color="gray").pack(pady=(0, 15))

        input_frame = ctk.CTkFrame(tab, fg_color="transparent")
        input_frame.pack(fill="x", padx=30)

        ctk.CTkLabel(input_frame, text="Game:").grid(row=0, column=0, padx=10, pady=8, sticky="e")
        self.override_search = ctk.CTkEntry(input_frame, width=300, placeholder_text="Search game name...")
        self.override_search.grid(row=0, column=1, pady=8)
        self.override_search.bind("<Return>", lambda e: self._search_override())

        ctk.CTkButton(input_frame, text="Search", width=80,
                       command=self._search_override).grid(row=0, column=2, padx=10, pady=8)

        ctk.CTkLabel(input_frame, text="Category:").grid(row=1, column=0, padx=10, pady=8, sticky="e")
        self.override_category = ctk.CTkComboBox(
            input_frame, width=300,
            values=["COMPLETED", "IN_PROGRESS", "ENDLESS", "NOT_A_GAME"])
        self.override_category.grid(row=1, column=1, pady=8)

        # Search results
        self.search_results_frame = ctk.CTkScrollableFrame(tab, height=150)
        self.search_results_frame.pack(fill="x", padx=30, pady=10)

        # Current overrides
        ctk.CTkLabel(tab, text="Current Overrides:",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(10, 5), anchor="w", padx=30)

        self.overrides_list_frame = ctk.CTkScrollableFrame(tab, height=150)
        self.overrides_list_frame.pack(fill="x", padx=30, pady=(0, 10))

        self._refresh_overrides_list()

    def _search_override(self):
        query = self.override_search.get().strip().lower()
        if not query:
            return

        # Clear previous results
        for w in self.search_results_frame.winfo_children():
            w.destroy()

        games = self.app.games_data
        if not games:
            ctk.CTkLabel(self.search_results_frame, text="No game data loaded. Run classification first.",
                         text_color="red").pack()
            return

        matches = [g for g in games if query in g.get("name", "").lower()][:15]
        if not matches:
            ctk.CTkLabel(self.search_results_frame, text="No games found.",
                         text_color="gray").pack()
            return

        for g in matches:
            row = ctk.CTkFrame(self.search_results_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)

            appid = g["appid"]
            name = g.get("name", "?")
            current = self.app.overrides.get(str(appid), "—")

            ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=12)).pack(side="left", padx=5)
            if current != "—":
                ctk.CTkLabel(row, text=f"[{current}]", text_color="#2d8a4e",
                             font=ctk.CTkFont(size=11)).pack(side="left", padx=5)

            ctk.CTkButton(
                row, text="Set", width=50, height=24,
                command=lambda aid=appid, n=name: self._apply_override(aid, n),
            ).pack(side="right", padx=5)

    def _apply_override(self, appid: int, name: str):
        category = self.override_category.get()
        self.app.overrides[str(appid)] = category
        organizer.save_overrides(self.app.overrides)
        self._refresh_overrides_list()
        self.app._set_status(f"Override: {name} → {category}")

    def _refresh_overrides_list(self):
        for w in self.overrides_list_frame.winfo_children():
            w.destroy()

        overrides = organizer.load_overrides()
        self.app.overrides = overrides

        if not overrides:
            ctk.CTkLabel(self.overrides_list_frame, text="No overrides set.",
                         text_color="gray").pack()
            return

        # Try to get names from game data or saved classifications
        saved = organizer.load_saved_classifications()
        for appid_str, category in overrides.items():
            row = ctk.CTkFrame(self.overrides_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)

            name = "Unknown"
            appid_int = int(appid_str)
            if appid_int in saved:
                name = saved[appid_int].get("name", name)
            else:
                for g in self.app.games_data:
                    if g["appid"] == appid_int:
                        name = g.get("name", name)
                        break

            ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=12)).pack(side="left", padx=5)
            ctk.CTkLabel(row, text=category, text_color="#2d8a4e",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=10)

            ctk.CTkButton(
                row, text="✕", width=30, height=24,
                fg_color="#8b3a3a", hover_color="#a04040",
                command=lambda a=appid_str: self._remove_override(a),
            ).pack(side="right", padx=5)

    def _remove_override(self, appid_str: str):
        self.app.overrides.pop(appid_str, None)
        organizer.save_overrides(self.app.overrides)
        self._refresh_overrides_list()

    def set_status(self, message: str):
        self.classify_status_label.configure(text=message)

    def set_progress(self, value: float):
        self.classify_progress.set(value)

    def log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"  {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def refresh(self, categories: dict, playtime_lookup: dict):
        # Results tab
        for cat_key, cfg in CATEGORY_CONFIG.items():
            games = categories.get(cat_key, [])
            self.result_headers[cat_key].configure(text=f"{cfg['label']} ({len(games)})")

            tb = self.result_textboxes[cat_key]
            tb.configure(state="normal")
            tb.delete("1.0", "end")
            for g in games:
                h = playtime_lookup.get(g.get("appid"), 0)
                pt = f" ({h}h)" if h > 0 else ""
                tb.insert("end", f"  {g.get('name', '?')}{pt}\n")
            tb.configure(state="disabled")

        # Overrides tab
        self._refresh_overrides_list()


class OverrideDialog(ctk.CTkToplevel):
    """Quick override dialog accessible from Simple view."""

    def __init__(self, parent: SteamOrganizerApp):
        super().__init__(parent)
        self.app = parent
        self.title("Manual Overrides")
        self.geometry("500x500")
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="Manual Overrides",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(15, 5))

        # Search
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.pack(fill="x", padx=20, pady=10)

        self.search_entry = ctk.CTkEntry(search_frame, width=300, placeholder_text="Search game name...")
        self.search_entry.pack(side="left", padx=(0, 10))
        self.search_entry.bind("<Return>", lambda e: self._search())

        self.category_box = ctk.CTkComboBox(
            search_frame, width=150,
            values=["COMPLETED", "IN_PROGRESS", "ENDLESS", "NOT_A_GAME"])
        self.category_box.pack(side="left")

        # Search results
        ctk.CTkLabel(self, text="Search Results:", font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=20, pady=(5, 0))
        self.results_frame = ctk.CTkScrollableFrame(self, height=120)
        self.results_frame.pack(fill="x", padx=20, pady=5)

        # Current overrides
        ctk.CTkLabel(self, text="Current Overrides:", font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=20, pady=(10, 0))
        self.overrides_frame = ctk.CTkScrollableFrame(self, height=150)
        self.overrides_frame.pack(fill="both", expand=True, padx=20, pady=(5, 15))

        self._refresh_overrides()

    def _search(self):
        query = self.search_entry.get().strip().lower()
        if not query:
            return

        for w in self.results_frame.winfo_children():
            w.destroy()

        games = self.app.games_data
        if not games:
            ctk.CTkLabel(self.results_frame, text="No game data. Run classification first.",
                         text_color="red").pack()
            return

        matches = [g for g in games if query in g.get("name", "").lower()][:10]
        if not matches:
            ctk.CTkLabel(self.results_frame, text="No matches.", text_color="gray").pack()
            return

        for g in matches:
            row = ctk.CTkFrame(self.results_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=g.get("name", "?"), font=ctk.CTkFont(size=12)).pack(side="left")
            ctk.CTkButton(
                row, text="Set", width=50, height=24,
                command=lambda aid=g["appid"], n=g.get("name", ""): self._set(aid, n),
            ).pack(side="right", padx=5)

    def _set(self, appid, name):
        cat = self.category_box.get()
        self.app.overrides[str(appid)] = cat
        organizer.save_overrides(self.app.overrides)
        self._refresh_overrides()
        self.app._set_status(f"Override: {name} → {cat}")

    def _refresh_overrides(self):
        for w in self.overrides_frame.winfo_children():
            w.destroy()

        overrides = organizer.load_overrides()
        self.app.overrides = overrides
        saved = organizer.load_saved_classifications()

        if not overrides:
            ctk.CTkLabel(self.overrides_frame, text="No overrides.", text_color="gray").pack()
            return

        for appid_str, cat in overrides.items():
            row = ctk.CTkFrame(self.overrides_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)

            name = "Unknown"
            appid_int = int(appid_str)
            if appid_int in saved:
                name = saved[appid_int].get("name", name)

            ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=12)).pack(side="left")
            ctk.CTkLabel(row, text=cat, text_color="#2d8a4e",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=10)
            ctk.CTkButton(
                row, text="✕", width=30, height=24,
                fg_color="#8b3a3a", hover_color="#a04040",
                command=lambda a=appid_str: self._remove(a),
            ).pack(side="right", padx=5)

    def _remove(self, appid_str):
        self.app.overrides.pop(appid_str, None)
        organizer.save_overrides(self.app.overrides)
        self._refresh_overrides()


if __name__ == "__main__":
    app = SteamOrganizerApp()
    app.mainloop()
