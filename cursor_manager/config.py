"""Paths, configuration and runtime state."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

APP_ID = "cursor-manager"
APP_NAME = "Cursor Manager"
AUTHOR = "Newton667"
PROJECT_URL = "https://github.com/Newton667/SimpleAnimatedCursorManager-inux-"
PROJECT_DIR = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_DIR / ".venv"
DEFAULT_IMAGES_DIR = PROJECT_DIR / "Cursors_Imgs"
BUNDLED_DIR = PROJECT_DIR / "assets" / "bundled"      # optional local "built-in" packs (not in git)
# Places to get animated cursors, shown as links in the app: (name, url, blurb)
CURSOR_SOURCES = [
    ("Maplequan", "https://ko-fi.com/maplequan",
     "Hololive animated cursor packs."),
    ("Noiire", "https://ko-fi.com/noiire/shop",
     "More animated cursor packs."),
    ("EbiEbiBeam", "https://ko-fi.com/I3I6H9UHF/shop",
     "Animated cursor packs."),
]

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_ID
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/{os.getuid()}"))
ICONS_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "icons"
LEGACY_ICONS_DIR = Path.home() / ".icons"
AUTOSTART_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / f"{APP_ID}.desktop"
APPS_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"

CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = STATE_DIR / "state.json"
PID_FILE = RUNTIME_DIR / f"{APP_ID}.pid"
LOG_FILE = STATE_DIR / "daemon.log"
THEME_PREFIX = "cm-"

DEFAULT_CONFIG = {
    "images_dir": str(DEFAULT_IMAGES_DIR),
    "interval_seconds": 600,
    "shuffle": False,
    "cursor_size": 32,          # pixel size drawn at Plasma's default nominal size 24
    "disabled_sets": [],        # set ids excluded from rotation
    "hotspots": {},             # set id -> [x, y] override for plain images
    "setup_done": False,        # the app asked the user to pick a cursors folder
}


def _read_json(path: Path, default):
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return dict(default)


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    stored = _read_json(CONFIG_FILE, {})
    if "images_dir" in stored and "setup_done" not in stored:
        stored["setup_done"] = True      # a folder was chosen before this flag existed
    cfg.update(stored)
    cfg["interval_seconds"] = max(5, int(cfg.get("interval_seconds", 600)))
    cfg["cursor_size"] = max(8, min(256, int(cfg.get("cursor_size", 32))))
    return cfg


def save_config(cfg: dict):
    _write_json(CONFIG_FILE, cfg)


def load_state() -> dict:
    return _read_json(STATE_FILE, {})


def save_state(**updates):
    st = load_state()
    st.update(updates)
    st["updated_at"] = time.time()
    _write_json(STATE_FILE, st)
    return st
