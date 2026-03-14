#!/usr/bin/env python3
"""Build script to package Steam Backlog Organizer as a standalone .exe"""

import sys
import PyInstaller.__main__

if "--cli" in sys.argv:
    # Build CLI version (console app)
    PyInstaller.__main__.run(
        [
            "organizer.py",
            "--onefile",
            "--name=SteamBacklogOrganizer-CLI",
            "--console",
            "--clean",
            "--hidden-import=anthropic",
            "--hidden-import=rich",
        ]
    )
else:
    # Build GUI version (windowed app, no console)
    PyInstaller.__main__.run(
        [
            "gui.py",
            "--onefile",
            "--name=SteamBacklogOrganizer",
            "--windowed",
            "--clean",
            "--hidden-import=anthropic",
            "--hidden-import=rich",
            "--hidden-import=customtkinter",
            "--collect-data=customtkinter",
        ]
    )
