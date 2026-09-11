#!/usr/bin/env bash
# Cursor Manager installer - Linux only.
# Creates a private Python virtualenv (.venv), installs the dependencies into it,
# and adds the app to your application menu. Safe to run again at any time.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
PROJECT_DIR="$PWD"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn()  { printf '\033[33m%s\033[0m\n' "$*"; }
fail()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
ask()   { local a; read -r -p "$1 [y/N] " a </dev/tty || a=""; [[ "${a,,}" == y* ]]; }

[[ "$(uname -s)" == "Linux" ]] || fail "Cursor Manager is Linux only."

# ---------------------------------------------------------------- package manager
PM=""; PY_PKGS=""
if   command -v dnf    >/dev/null; then PM="sudo dnf install -y";         PY_PKGS="python3 python3-pip"
elif command -v apt-get >/dev/null; then PM="sudo apt-get install -y";    PY_PKGS="python3 python3-venv python3-pip"
elif command -v pacman >/dev/null; then PM="sudo pacman -S --noconfirm";   PY_PKGS="python python-pip"
elif command -v zypper >/dev/null; then PM="sudo zypper install -y";      PY_PKGS="python3 python3-pip"
elif command -v apk    >/dev/null; then PM="sudo apk add";                PY_PKGS="python3 py3-pip"
fi

install_system_python() {
    if [[ -z "$PM" ]]; then
        fail "Python 3 was not found and I do not recognise your package manager.
Install Python 3.9+ (with the venv module) using your distribution's tools, then run this script again."
    fi
    warn "Python 3 (or its venv module) is missing."
    echo "I can install it with:  $PM $PY_PKGS"
    if ask "Install it now?"; then
        $PM $PY_PKGS
    else
        fail "Install Python 3 first, then run ./install.sh again."
    fi
}

# ---------------------------------------------------------------- python
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$(command -v "$c")"; break
    fi
done
if [[ -z "$PY" ]]; then
    install_system_python
    PY="$(command -v python3)" || fail "python3 still not found after installation."
fi
if ! "$PY" -m venv --help >/dev/null 2>&1; then
    install_system_python           # Debian/Ubuntu ship venv separately (python3-venv)
fi
bold "Using $("$PY" --version) at $PY"

# ---------------------------------------------------------------- virtualenv
# --system-site-packages lets the venv reuse your distro's PySide6/Pillow (and its KDE
# theme integration) when they are installed; pip only downloads what is missing.
if [[ ! -x .venv/bin/python ]]; then
    bold "Creating virtualenv in $PROJECT_DIR/.venv"
    "$PY" -m venv --system-site-packages .venv
fi
bold "Installing Python dependencies (PySide6, Pillow) - the first run can take a few minutes"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# ---------------------------------------------------------------- desktop integration
chmod +x cursor-manager
./cursor-manager check || warn "Some checks failed - the app may still run, see the notes above."
./cursor-manager install >/dev/null
bold "Added 'Cursor Manager' to your application menu."

if [[ -n "${XDG_CURRENT_DESKTOP:-}" && "${XDG_CURRENT_DESKTOP}" != *KDE* ]]; then
    warn "Note: live cursor switching is built for KDE Plasma. On ${XDG_CURRENT_DESKTOP} only the gsettings path is used."
fi

if [[ ! -e "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/cursor-manager.desktop" ]]; then
    if ask "Start the cursor rotation automatically when you log in?"; then
        ./cursor-manager autostart on
    fi
fi

echo
bold "Done!  Launch 'Cursor Manager' from your application menu, or run:  $PROJECT_DIR/cursor-manager"
echo "On first start it asks which folder your cursor packs live in."
