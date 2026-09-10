"""Background rotation: applies the next cursor set every N seconds.

Signals: SIGUSR1 = switch now, SIGHUP = rescan/rebuild, SIGTERM/SIGINT = quit.
"""
from __future__ import annotations

import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import config, sets as sets_mod, theme

_wake = False
_reload = False
_quit = False


def log(msg: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- pid handling

def running_pid() -> Optional[int]:
    try:
        pid = int(config.PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read()
    except OSError:
        return None
    if b"cursor-manager" not in cmd:
        return None          # pid was reused by some unrelated process
    return pid


def is_running() -> bool:
    return running_pid() is not None


def send_signal(sig) -> bool:
    pid = running_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def stop() -> bool:
    ok = send_signal(signal.SIGTERM)
    for _ in range(30):
        if not is_running():
            break
        time.sleep(0.1)
    return ok


def skip() -> bool:
    return send_signal(signal.SIGUSR1)


def reload() -> bool:
    return send_signal(signal.SIGHUP)


# --------------------------------------------------------------------------- building (out of process)

LAUNCHER = config.PROJECT_DIR / "cursor-manager"
SCAN_EVERY = 30.0          # seconds between full folder scans when nothing obviously changed


def _folder_stamp(cfg: dict) -> str:
    """Cheap change detector: directory mtimes + file count of the cursor folders."""
    parts = []
    for d in (Path(cfg["images_dir"]).expanduser(), config.BUNDLED_DIR):
        try:
            st = d.stat()
            parts.append(f"{d}:{int(st.st_mtime)}:{len(os.listdir(d))}")
        except OSError:
            parts.append(f"{d}:missing")
    return "|".join(parts)


def _build_child(*args: str) -> subprocess.Popen:
    """Run theme building in a separate process so its image memory is freed afterwards."""
    return subprocess.Popen([sys.executable, str(LAUNCHER), "build", "--quiet", *args],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------- main loop

def _on_signal(signum, _frame):
    global _wake, _reload, _quit
    if signum == signal.SIGUSR1:
        _wake = True
    elif signum == signal.SIGHUP:
        _reload = True
        _wake = True
    else:
        _quit = True
        _wake = True


def _pick_next(order: List[sets_mod.CursorSet], current_id: Optional[str], shuffle: bool):
    if not order:
        return None
    if shuffle:
        choices = [s for s in order if s.id != current_id] or order
        return random.choice(choices)
    ids = [s.id for s in order]
    if current_id in ids:
        return order[(ids.index(current_id) + 1) % len(order)]
    return order[0]


def run(apply_immediately: bool = True) -> int:
    global _wake, _reload
    if is_running():
        log("daemon already running")
        return 1
    config.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.PID_FILE.write_text(str(os.getpid()))
    for s in (signal.SIGUSR1, signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(s, _on_signal)
    log(f"daemon started (pid {os.getpid()})")
    config.save_state(daemon_pid=os.getpid())

    known_sig = None
    all_sets: List[sets_mod.CursorSet] = []
    order: List[sets_mod.CursorSet] = []
    next_change = 0.0
    first = apply_immediately
    failures = 0
    applied_interval = None
    last_scan = 0.0
    last_stamp = None
    build_proc: Optional[subprocess.Popen] = None
    try:
        while not _quit:
            cfg = config.load_config()
            now = time.time()
            stamp = _folder_stamp(cfg)
            if _reload or stamp != last_stamp or now - last_scan >= SCAN_EVERY:
                all_sets = sets_mod.scan(cfg["images_dir"])
                last_scan, last_stamp = now, stamp
            sig = "|".join(f"{s.id}:{s.signature}" for s in all_sets) + f"|{cfg['cursor_size']}|{cfg.get('hotspots')}"
            if sig != known_sig or _reload:
                _reload = False
                known_sig = sig
                # Refresh the active set first so a size/hotspot change shows up right away.
                st = config.load_state()
                cur = next((s for s in all_sets if s.id == st.get("current_set")), None)
                if (cur is not None and theme.current_theme() == cur.theme_name
                        and not theme.is_built(cur, cfg)
                        and time.time() - float(st.get("last_change") or 0) > 3):
                    try:
                        _build_child("--set", cur.id).wait(timeout=120)
                        log(f"refreshed '{cur.name}' ({theme.apply_set(cur, cfg)})")
                    except Exception as exc:  # noqa: BLE001
                        log(f"refresh failed for {cur.name}: {exc}")
                # Everything else builds in the background in a child process.
                if build_proc is not None and build_proc.poll() is None:
                    build_proc.terminate()
                build_proc = _build_child("--pending")
                log(f"{len(all_sets)} cursor set(s) available; building themes in the background")
            if build_proc is not None and build_proc.poll() is not None:
                log("themes up to date" if build_proc.returncode == 0 else f"background build exited {build_proc.returncode}")
                build_proc = None
            order = sets_mod.enabled_sets(all_sets, cfg)

            if first:
                first = False
                cur_id = config.load_state().get("current_set")
                cur = next((s for s in order if s.id == cur_id), None)
                if cur is not None and theme.current_theme() == cur.theme_name:
                    # Logged back in with the last cursor still active: keep it, just start the timer.
                    next_change = time.time() + cfg["interval_seconds"]
                    config.save_state(next_change=next_change, interval_seconds=cfg["interval_seconds"])
                    log(f"resuming with '{cur.name}'")
                else:
                    _wake = True

            now = time.time()
            if _wake or now >= next_change:
                _wake = False
                st = config.load_state()
                nxt = _pick_next(order, st.get("current_set"), cfg.get("shuffle", False))
                delay = cfg["interval_seconds"]
                if nxt is not None:
                    try:
                        msg = theme.apply_set(nxt, cfg)
                        log(f"applied '{nxt.name}' ({msg})")
                        failures = 0
                    except Exception as exc:  # noqa: BLE001
                        failures += 1
                        # Right after login the desktop may not be ready yet: retry soon.
                        delay = 5 if failures <= 24 else delay
                        log(f"apply failed for {nxt.name}: {exc} (retry in {delay}s)")
                else:
                    delay = 5 if failures <= 24 else delay
                    failures += 1
                    log(f"no enabled cursor sets in {cfg['images_dir']}; checking again in {delay}s")
                next_change = time.time() + delay
                config.save_state(next_change=next_change, interval_seconds=cfg["interval_seconds"])
            # The interval can change in the app while we wait: re-time the next switch.
            if applied_interval is not None and cfg["interval_seconds"] != applied_interval:
                last = float(config.load_state().get("last_change") or time.time())
                next_change = last + cfg["interval_seconds"]
                config.save_state(next_change=next_change, interval_seconds=cfg["interval_seconds"])
            applied_interval = cfg["interval_seconds"]
            remaining = next_change - time.time()
            time.sleep(min(1.0, max(0.05, remaining)) if remaining > 0 else 0.05)
    finally:
        log("daemon stopped")
        try:
            config.PID_FILE.unlink()
        except OSError:
            pass
        config.save_state(daemon_pid=None, next_change=None)
    return 0
