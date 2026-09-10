"""Discover cursor sets inside the images folder and map files to cursor roles."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config

CURSOR_EXTS = {".ani", ".cur"}
IMAGE_EXTS = {".png", ".gif", ".webp", ".apng", ".bmp", ".jpg", ".jpeg", ".ico"}
ALL_EXTS = CURSOR_EXTS | IMAGE_EXTS

# Canonical roles in the order Windows cursor schemes list them.
ROLES = [
    "default", "help", "progress", "wait", "crosshair", "text", "pencil",
    "not-allowed", "ns-resize", "ew-resize", "nwse-resize", "nesw-resize",
    "move", "alternate", "pointer", "person", "pin",
]

ROLE_LABELS = {
    "default": "Normal", "help": "Help", "progress": "Working", "wait": "Busy",
    "crosshair": "Precision", "text": "Text", "pencil": "Handwriting",
    "not-allowed": "Unavailable", "ns-resize": "Vertical", "ew-resize": "Horizontal",
    "nwse-resize": "Diagonal 1", "nesw-resize": "Diagonal 2", "move": "Move",
    "alternate": "Alternate", "pointer": "Link", "person": "Person", "pin": "Pin",
}

# Xcursor names each role provides. The first is the real file; the rest are symlinks.
ROLE_XCURSOR_NAMES: Dict[str, List[str]] = {
    "default": ["default", "left_ptr", "arrow", "top_left_arrow"],
    "help": ["help", "question_arrow", "left_ptr_help", "whats_this"],
    "progress": ["progress", "left_ptr_watch", "half-busy"],
    "wait": ["wait", "watch"],
    "crosshair": ["crosshair", "cross", "tcross", "cross_reverse", "diamond_cross"],
    "text": ["text", "xterm", "ibeam"],
    "pencil": ["pencil", "draft"],
    "not-allowed": ["not-allowed", "forbidden", "crossed_circle", "circle", "no-drop", "dnd-no-drop"],
    "ns-resize": ["ns-resize", "sb_v_double_arrow", "size_ver", "size-ver", "v_double_arrow", "double_arrow",
                  "n-resize", "s-resize", "top_side", "bottom_side", "row-resize", "split_v"],
    "ew-resize": ["ew-resize", "sb_h_double_arrow", "size_hor", "size-hor", "h_double_arrow",
                  "e-resize", "w-resize", "left_side", "right_side", "col-resize", "split_h"],
    "nwse-resize": ["nwse-resize", "size_fdiag", "size-fdiag", "bd_double_arrow",
                    "nw-resize", "se-resize", "top_left_corner", "bottom_right_corner"],
    "nesw-resize": ["nesw-resize", "size_bdiag", "size-bdiag", "fd_double_arrow",
                    "ne-resize", "sw-resize", "top_right_corner", "bottom_left_corner"],
    "move": ["move", "fleur", "size_all", "all-scroll", "grabbing", "closedhand", "dnd-move"],
    "alternate": ["right_ptr", "up_arrow", "center_ptr"],
    "pointer": ["pointer", "hand", "hand1", "hand2", "pointing_hand", "grab", "openhand"],
    "person": [], "pin": [],
}

# Keys used by Windows .inf cursor installers -> role
INF_KEYS = {
    "pointer": "default", "arrow": "default", "normal": "default",
    "help": "help", "work": "progress", "working": "progress", "appstarting": "progress",
    "busy": "wait", "wait": "wait", "cross": "crosshair", "precision": "crosshair",
    "text": "text", "ibeam": "text", "hand": "pencil", "pen": "pencil", "handwriting": "pencil", "nwpen": "pencil",
    "unavailiable": "not-allowed", "unavailable": "not-allowed", "no": "not-allowed",
    "vert": "ns-resize", "vertical": "ns-resize", "sizens": "ns-resize",
    "horz": "ew-resize", "horizontal": "ew-resize", "sizewe": "ew-resize",
    "dgn1": "nwse-resize", "diagonal1": "nwse-resize", "sizenwse": "nwse-resize",
    "dgn2": "nesw-resize", "diagonal2": "nesw-resize", "sizenesw": "nesw-resize",
    "move": "move", "sizeall": "move", "alternate": "alternate", "uparrow": "alternate",
    "link": "pointer", "person": "person", "pin": "pin",
}

# Filename keyword patterns -> role (checked in order, first match wins)
NAME_PATTERNS = [
    (r"diag(onal)?[_ -]?1|nwse|size[_ -]?nwse|bd_double|fdiag", "nwse-resize"),
    (r"diag(onal)?[_ -]?2|nesw|size[_ -]?nesw|fd_double|bdiag", "nesw-resize"),
    (r"vert|size[_ -]?ns|ns[_ -]?resize|v_double", "ns-resize"),
    (r"horz|horiz|size[_ -]?we|ew[_ -]?resize|h_double", "ew-resize"),
    (r"size[_ -]?all|\bmove\b|fleur", "move"),
    (r"unavail|not[_ -]?allowed|forbidden|\bno\b|nodrop", "not-allowed"),
    (r"handwrit|\bpen\b|pencil|nwpen", "pencil"),
    (r"app[_ -]?start|working|progress|background", "progress"),
    (r"\bbusy\b|\bwait\b|hourglass|loading", "wait"),
    (r"precision|cross(hair)?", "crosshair"),
    (r"\btext\b|ibeam|i[_ -]beam", "text"),
    (r"\bhelp\b|question|whats", "help"),
    (r"\blink\b|\bhand\b|pointer|pointing", "pointer"),
    (r"altern|up[_ -]?arrow|right_ptr", "alternate"),
    (r"person", "person"),
    (r"\bpin\b", "pin"),
    (r"normal|\barrow\b|default|left_ptr|select", "default"),
]


@dataclass
class CursorSet:
    id: str                         # stable id derived from the source path
    name: str                       # display name
    source: Path                    # file, folder or zip in the images folder
    kind: str                       # "pack" | "single"
    roles: Dict[str, Path] = field(default_factory=dict)   # role -> decoded file path
    signature: str = ""             # content signature (mtimes + sizes)
    bundled: bool = False           # shipped with the app (assets/bundled)

    @property
    def theme_name(self) -> str:
        return config.THEME_PREFIX + self.id

    @property
    def is_image(self) -> bool:
        """True when the set comes from plain images (no built-in hotspot)."""
        return self.kind == "single" and self.source.suffix.lower() in IMAGE_EXTS


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:40] or "set"


def _set_id(source: Path, bundled: bool = False) -> str:
    key = f"builtin:{source.name}" if bundled else str(source)
    h = hashlib.sha1(key.encode()).hexdigest()[:6]
    return f"{_slug(source.stem)}-{h}"


def _content_key(files: List[Path]) -> str:
    """Identify a pack by its file names and sizes, so the same zip in two places counts once."""
    h = hashlib.sha1()
    for f in sorted(files, key=lambda p: p.name.lower()):
        try:
            h.update(f"{f.name.lower()}:{f.stat().st_size}".encode())
        except OSError:
            continue
    return h.hexdigest()


def _display_name(source: Path) -> str:
    name = source.stem if source.is_file() else source.name
    name = re.sub(r"\.(zip|crdownload)$", "", name, flags=re.I)
    return name.strip() or source.name


def _is_zip(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False


EXTRACT_EXTS = ALL_EXTS | {".inf", ".zip"}
EXTRACT_VERSION = "v2"
MAX_EXTRACT_BYTES = 512 * 1024 * 1024     # refuse to unpack more than this from one zip
_active_extract_dirs: set = set()          # extraction folders used by the last scan()


def _safe_relpath(name: str) -> Optional[Path]:
    parts = [p for p in Path(name).parts if p not in ("..", "", "/")]
    return Path(*parts) if parts else None


def _extract_members(zf: zipfile.ZipFile, dest: Path) -> None:
    budget = MAX_EXTRACT_BYTES
    for member in zf.infolist():
        rel = _safe_relpath(member.filename)
        if member.is_dir() or rel is None or rel.suffix.lower() not in EXTRACT_EXTS:
            continue
        budget -= member.file_size
        if budget < 0:
            break
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as dst:
            dst.write(src.read())


def _extract_nested(folder: Path, depth: int = 0) -> None:
    """Unpack zips found inside an extracted zip (bundles of several cursor packs)."""
    if depth > 3:
        return
    inner = list(folder.rglob("*.zip"))
    for z in inner:
        target = z.with_suffix("")
        try:
            with zipfile.ZipFile(z) as zf:
                _extract_members(zf, target)
        except (zipfile.BadZipFile, OSError):
            pass
        try:
            z.unlink()
        except OSError:
            pass
    if inner:
        _extract_nested(folder, depth + 1)


def _extract_zip(path: Path) -> Optional[Path]:
    st = path.stat()
    key = hashlib.sha1(f"{path}:{st.st_size}:{int(st.st_mtime)}:{EXTRACT_VERSION}".encode()).hexdigest()[:10]
    dest = config.CACHE_DIR / "extract" / f"{_slug(path.stem)}-{key}"
    _active_extract_dirs.add(dest)
    if dest.is_dir():
        return dest
    try:
        with zipfile.ZipFile(path) as zf:
            dest.mkdir(parents=True, exist_ok=True)
            _extract_members(zf, dest)
        _extract_nested(dest)
        return dest
    except (zipfile.BadZipFile, OSError):
        return None


def prune_extract_cache() -> None:
    """Delete unpacked zips that no scan uses any more (removed or changed zips)."""
    base = config.CACHE_DIR / "extract"
    if not base.is_dir() or not _active_extract_dirs:
        return
    for d in base.iterdir():
        if d.is_dir() and d not in _active_extract_dirs:
            shutil.rmtree(d, ignore_errors=True)


def _cursor_files(folder: Path) -> List[Path]:
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in ALL_EXTS]
    return sorted(files, key=lambda p: (p.parent.as_posix(), p.name.lower()))


def _cursor_groups(folder: Path) -> List[Tuple[Optional[Path], List[Path]]]:
    """Split the files of a folder/zip into cursor packs.

    Returns [(group_dir, files)]. Real cursor files (.ani/.cur) win over plain
    images so preview pictures shipped with a pack are ignored. A bundle with
    several packs in sub-folders yields one group per folder.
    """
    files = _cursor_files(folder)
    real = [f for f in files if f.suffix.lower() in CURSOR_EXTS]
    if real:
        files = real
    if not files:
        return []
    by_dir: Dict[Path, List[Path]] = {}
    for f in files:
        by_dir.setdefault(f.parent, []).append(f)
    if len(by_dir) == 1:
        return [(None, files)]
    groups = [(d, fs) for d, fs in sorted(by_dir.items()) if len(fs) >= 2]
    return groups or [(None, files)]


def _group_label(folder: Path, group_dir: Path, all_dirs: List[Path]) -> str:
    rel = group_dir.relative_to(folder).parts
    if not rel:
        return group_dir.name
    firsts = [d.relative_to(folder).parts[:1] for d in all_dirs]
    if firsts.count(rel[:1]) > 1:
        return "/".join(rel)
    return rel[0]


def _parse_inf(folder: Path, files: List[Path]) -> Dict[str, Path]:
    roles: Dict[str, Path] = {}
    by_name = {f.name.lower(): f for f in files}
    for inf in folder.rglob("*.inf"):
        try:
            text = inf.read_text(errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"^\s*([A-Za-z0-9_]+)\s*=\s*\"?([^\"\r\n;]+?)\"?\s*$", text, re.M):
            key, value = m.group(1).lower(), m.group(2).strip()
            role = INF_KEYS.get(key)
            f = by_name.get(Path(value).name.lower())
            if role and f and role not in roles:
                roles[role] = f
    return roles


def _match_roles(folder: Path, files: List[Path]) -> Dict[str, Path]:
    roles = _parse_inf(folder, files)
    if len(roles) >= 2:
        return roles
    roles = {}
    unmatched: List[Path] = []
    for f in files:
        stem = f.stem.lower().replace("_", " ")
        for pattern, role in NAME_PATTERNS:
            if re.search(pattern, stem) and role not in roles:
                roles[role] = f
                break
        else:
            unmatched.append(f)
    # Numbered files (e.g. name_01.ani ... name_15.ani) follow the Windows scheme order.
    numbered = []
    for f in unmatched:
        m = re.search(r"(\d+)\D*$", f.stem)
        if m:
            numbered.append((int(m.group(1)), f))
    if numbered and len(roles) <= len(numbered):
        roles = {}
        for n, f in sorted(numbered):
            if 1 <= n <= len(ROLES) and ROLES[n - 1] not in roles:
                roles[ROLES[n - 1]] = f
        if "default" not in roles and numbered:
            roles["default"] = sorted(numbered)[0][1]
    if not roles and files:
        roles["default"] = files[0]
    return roles


def _signature(paths: List[Path]) -> str:
    h = hashlib.sha1()
    for p in paths:
        try:
            st = p.stat()
            h.update(f"{p}:{st.st_size}:{int(st.st_mtime)}".encode())
        except OSError:
            continue
    return h.hexdigest()[:12]


def _scan_dir(images_dir: Path, bundled: bool) -> List[CursorSet]:
    sets: List[CursorSet] = []
    if not images_dir.is_dir():
        return sets
    for entry in sorted(images_dir.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir() or _is_zip(entry):
            folder = entry if entry.is_dir() else _extract_zip(entry)
            if folder is None:
                continue
            groups = _cursor_groups(folder)
            if not groups:
                continue
            group_dirs = [d for d, _ in groups if d is not None]
            for gdir, files in groups:
                if gdir is None:
                    name, source = _display_name(entry), entry
                else:
                    label = _group_label(folder, gdir, group_dirs)
                    name, source = f"{_display_name(entry)} - {label}", entry / label
                if len(files) == 1:
                    sets.append(CursorSet(_set_id(source, bundled), name, entry, "single",
                                          {"default": files[0]}, _signature(files), bundled))
                    continue
                roles = _match_roles(gdir or folder, files)
                sets.append(CursorSet(_set_id(source, bundled), name, entry, "pack", roles,
                                      _signature(files), bundled))
        elif entry.is_file() and entry.suffix.lower() in ALL_EXTS:
            sets.append(CursorSet(_set_id(entry, bundled), _display_name(entry), entry, "single",
                                  {"default": entry}, _signature([entry]), bundled))
    return sets


def scan(images_dir: Optional[Path] = None) -> List[CursorSet]:
    """All cursor sets: the ones shipped with the app plus the user's folder.

    A pack that exists in both places is listed once (the built-in copy).
    """
    images_dir = Path(images_dir or config.load_config()["images_dir"]).expanduser()
    _active_extract_dirs.clear()
    result: List[CursorSet] = []
    seen = set()
    for cs in _scan_dir(config.BUNDLED_DIR, True) + _scan_dir(images_dir, False):
        key = _content_key(list(cs.roles.values()))
        if key in seen:
            continue
        seen.add(key)
        result.append(cs)
    result.sort(key=lambda s: (not s.bundled, s.name.lower()))
    return result


def enabled_sets(sets: List[CursorSet], cfg: dict) -> List[CursorSet]:
    disabled = set(cfg.get("disabled_sets", []))
    return [s for s in sets if s.id not in disabled]
