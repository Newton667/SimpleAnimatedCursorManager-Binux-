"""Run-at-login (XDG autostart) and application menu entries."""
from __future__ import annotations

import shlex
from pathlib import Path

from . import config

LAUNCHER = config.PROJECT_DIR / "cursor-manager"
DESKTOP_TEMPLATE = config.PROJECT_DIR / "cursor-manager.desktop.in"
LOCAL_DESKTOP_FILE = config.PROJECT_DIR / "cursor-manager.desktop"
ICON_FILE = config.PROJECT_DIR / "assets" / "cursor-manager.svg"


def _exec(args: str) -> str:
    # Go through the launcher: it picks the project's virtualenv automatically.
    return f"{shlex.quote(str(LAUNCHER))} {args}"


def autostart_enabled() -> bool:
    return config.AUTOSTART_FILE.exists()


def set_autostart(enabled: bool) -> None:
    if not enabled:
        try:
            config.AUTOSTART_FILE.unlink()
        except FileNotFoundError:
            pass
        return
    config.AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.AUTOSTART_FILE.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Cursor Manager (rotation)\n"
        "Comment=Rotates the mouse cursor theme on a timer\n"
        f"Exec={_exec('daemon')}\n"
        f"Icon={ICON_FILE}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-after=panel\n"
        "X-KDE-StartupNotify=false\n"
    )


def tray_autostart_enabled() -> bool:
    return config.TRAY_AUTOSTART_FILE.exists()


def set_tray_autostart(enabled: bool) -> None:
    """Start the app hidden in the system tray at login."""
    if not enabled:
        try:
            config.TRAY_AUTOSTART_FILE.unlink()
        except FileNotFoundError:
            pass
        return
    config.TRAY_AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TRAY_AUTOSTART_FILE.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Cursor Manager (tray)\n"
        "Comment=Cursor Manager icon in the system tray\n"
        f"Exec={_exec('gui --hidden')}\n"
        f"Icon={ICON_FILE}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-after=panel\n"
        "X-KDE-StartupNotify=false\n"
    )


def render_desktop_entry() -> str:
    """Fill the repo's .desktop template with this checkout's absolute path."""
    return DESKTOP_TEMPLATE.read_text().replace("@PROJECT_DIR@", str(config.PROJECT_DIR))


def install_menu_entry() -> Path:
    """Write the app-menu entry and a double-clickable copy next to the code."""
    text = render_desktop_entry()
    LOCAL_DESKTOP_FILE.write_text(text)
    LOCAL_DESKTOP_FILE.chmod(0o755)
    config.APPS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.APPS_DIR / f"{config.APP_ID}.desktop"
    path.write_text(text)
    path.chmod(0o755)
    return path


def remove_menu_entry() -> None:
    for p in (config.APPS_DIR / f"{config.APP_ID}.desktop", LOCAL_DESKTOP_FILE):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
