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
            "--icon=icon.ico",
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
            "--icon=icon.ico",
            "--add-data=icon.ico;.",
            "--add-data=icon_16.png;.",
            "--add-data=icon_32.png;.",
            "--add-data=icon_48.png;.",
            "--add-data=icon_64.png;.",
            "--add-data=icon_128.png;.",
            "--add-data=icon_256.png;.",
            "--hidden-import=rich",
            "--hidden-import=requests",
            "--hidden-import=customtkinter",
            "--collect-data=customtkinter",
        ]
    )
