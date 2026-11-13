#!/usr/bin/env bash
#
# wifiant installer
#
#   ./install.sh              install for the current user (recommended)
#   ./install.sh --system     install system-wide via /usr/local (needs sudo)
#   ./install.sh --dev        editable install + test dependencies
#   ./install.sh --no-venv    skip the venv, pip install into the current env
#   ./install.sh --uninstall  remove what this script created
#
# The user install never touches system Python: it builds a private venv under
# ${XDG_DATA_HOME:-~/.local/share}/wifiant/venv and drops a small launcher in
# ~/.local/bin. Nothing is written outside your home directory.

set -euo pipefail

REPO="${WIFIANT_REPO:-https://github.com/4akashtiwari/Wifi-ant.git}"
REF="${WIFIANT_REF:-main}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX_DIR="$DATA_HOME/wifiant"
VENV="$PREFIX_DIR/venv"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/wifiant"
LOCAL_SRC="$PREFIX_DIR/src"

MODE="user"
NO_VENV=0
DEV=0
UNINSTALL=0
WITH_SYSTEMD=0

# ---------------------------------------------------------------- output ---
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
    c_reset=$'\033[0m'; c_dim=$'\033[2m'; c_bold=$'\033[1m'
    c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_cyan=$'\033[36m'
else
    c_reset=''; c_dim=''; c_bold=''; c_green=''; c_yellow=''; c_red=''; c_cyan=''
fi

say()  { printf '%s\n' "$*"; }
step() { printf '  %s▸%s %s\n' "$c_cyan" "$c_reset" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$c_green" "$c_reset" "$*"; }
warn() { printf '  %s!%s %s\n' "$c_yellow" "$c_reset" "$*"; }
die()  { printf '  %s✗%s %s\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }

usage() {
    sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

# ------------------------------------------------------------------ args ---
for arg in "$@"; do
    case "$arg" in
        --system)    MODE="system" ;;
        --user)      MODE="user" ;;
        --no-venv)   NO_VENV=1 ;;
        --dev)       DEV=1 ;;
        --systemd)   WITH_SYSTEMD=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)   usage ;;
        *)           die "unknown option: $arg (try --help)" ;;
    esac
done

# -------------------------------------------------------------- uninstall ---
if [ "$UNINSTALL" -eq 1 ]; then
    say "${c_bold}removing wifiant${c_reset}"
    [ -f "$LAUNCHER" ] && rm -f "$LAUNCHER" && ok "removed $LAUNCHER"
    if [ -d "$PREFIX_DIR" ]; then
        warn "your recorded runs and revisions live in $DATA_HOME/wifiant/designs"
        warn "and $DATA_HOME/wifiant/watch - they are NOT deleted"
        rm -rf "$VENV" "$LOCAL_SRC"
        rmdir "$PREFIX_DIR" 2>/dev/null || true
        ok "removed venv and source checkout"
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files 2>/dev/null | grep -q wifiant; then
        warn "a wifiant systemd unit still exists - remove with:"
        warn "  systemctl --user disable --now wifiant-watch && rm ~/.config/systemd/user/wifiant-watch.service"
    fi
    say ""
    say "done. Lab data left in place."
    exit 0
fi

# ---------------------------------------------------------------- python ---
say "${c_bold}wifiant installer${c_reset}  ${c_dim}($MODE install)${c_reset}"
say ""

step "looking for python 3.10+"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PY="$(command -v "$cand")"
            break
        fi
    fi
done
[ -n "$PY" ] || die "need python 3.10 or newer - install it and re-run"
ok "$("$PY" --version 2>&1) at $PY"

step "checking network tools"
missing=()
command -v iw >/dev/null 2>&1   || missing+=("iw")
command -v ping >/dev/null 2>&1 || missing+=("ping")
if [ ${#missing[@]} -gt 0 ]; then
    warn "missing: ${missing[*]} (wifiant still runs, but signal reads get worse)"
    case "$(uname -s)" in
        Linux)
            if [ -f /etc/debian_version ]; then
                say "    ${c_dim}sudo apt install ${missing[*]}${c_reset}"
            elif [ -f /etc/arch-release ]; then
                say "    ${c_dim}sudo pacman -S ${missing[*]/ping/iputils}${c_reset}"
            elif [ -f /etc/fedora-release ]; then
                say "    ${c_dim}sudo dnf install ${missing[*]/iw/iw}${c_reset}"
            fi
            ;;
    esac
else
    ok "iw and ping present"
fi

# ---------------------------------------------------------------- source ---
SRC=""
if [ -f "./pyproject.toml" ] && grep -q 'name = "wifiant"' ./pyproject.toml 2>/dev/null; then
    SRC="$(pwd)"
    step "installing from the current checkout"
    ok "$SRC"
else
    command -v git >/dev/null 2>&1 || die "git is required to fetch the source"
    step "fetching source"
    if [ -d "$LOCAL_SRC/.git" ]; then
        git -C "$LOCAL_SRC" fetch --quiet origin "$REF"
        git -C "$LOCAL_SRC" checkout --quiet "$REF"
        git -C "$LOCAL_SRC" reset --quiet --hard "origin/$REF"
        ok "updated $LOCAL_SRC"
    else
        mkdir -p "$(dirname "$LOCAL_SRC")"
        git clone --quiet --depth 1 --branch "$REF" "$REPO" "$LOCAL_SRC"
        ok "cloned to $LOCAL_SRC"
    fi
    SRC="$LOCAL_SRC"
fi

# ------------------------------------------------------------- installing ---
if [ "$MODE" = "system" ]; then
    step "installing system-wide"
    if [ "$NO_VENV" -eq 0 ]; then
        : # system installs go straight to /usr/local, no venv
    fi
    SUDO=""
    [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    $SUDO "$PY" -m pip install --quiet --upgrade --break-system-packages "$SRC" \
        || $SUDO "$PY" -m pip install --quiet --upgrade "$SRC"
    ok "installed to $($PY -c 'import site; print(site.getsitepackages()[0])')"
    say ""
    say "run it with: ${c_bold}wifiant${c_reset}"
    exit 0
fi

if [ "$NO_VENV" -eq 1 ]; then
    step "installing into the current python environment"
    "$PY" -m pip install --quiet --upgrade "$SRC"
    ok "installed"
else
    step "creating a private environment"
    mkdir -p "$PREFIX_DIR"
    if [ ! -x "$VENV/bin/python" ]; then
        "$PY" -m venv "$VENV" || die "venv creation failed - is python3-venv installed?"
        ok "venv at $VENV"
    else
        ok "reusing venv at $VENV"
    fi

    step "installing wifiant"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    if [ "$DEV" -eq 1 ]; then
        "$VENV/bin/python" -m pip install --quiet -e "$SRC[dev]"
        ok "editable install with dev extras"
    else
        "$VENV/bin/python" -m pip install --quiet --upgrade "$SRC"
        ok "installed into the venv"
    fi

    step "adding launcher"
    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# generated by wifiant install.sh
exec "$VENV/bin/python" -m wifiant.cli "\$@"
EOF
    chmod +x "$LAUNCHER"
    ok "$LAUNCHER"
fi

# ------------------------------------------------------------- verifying ---
step "verifying"
if [ -x "$LAUNCHER" ]; then
    VERSION="$("$LAUNCHER" --version 2>/dev/null || true)"
else
    VERSION="$(wifiant --version 2>/dev/null || true)"
fi
[ -n "$VERSION" ] || die "install finished but the command does not run"
ok "$VERSION"

# -------------------------------------------------------------- post-setup ---
say ""
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH - add it, e.g.:"
       say "    ${c_dim}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc${c_reset}" ;;
esac

if [ "$WITH_SYSTEMD" -eq 1 ]; then
    if ! command -v systemctl >/dev/null 2>&1; then
        warn "--systemd given but systemctl is not available"
    else
        step "installing a daily watch service (user unit)"
        UNIT_DIR="$HOME/.config/systemd/user"
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT_DIR/wifiant-watch.service" <<EOF
[Unit]
Description=wifiant link logger

[Service]
Type=simple
ExecStart=$LAUNCHER watch --interval 60
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now wifiant-watch.service 2>/dev/null \
            && ok "wifiant-watch is running (logs to journalctl --user -u wifiant-watch)" \
            || warn "unit written but could not start - check: systemctl --user status wifiant-watch"
        say "  ${c_dim}loginctl enable-linger $USER  # keep it running while logged out${c_reset}"
    fi
fi

say ""
say "${c_bold}ready.${c_reset}"
say ""
say "  ${c_bold}wifiant${c_reset}            live view of your link"
say "  ${c_bold}wifiant --demo${c_reset}     same, against a fake radio"
say "  ${c_bold}wifiant stats${c_reset}      one-shot dump"
say "  ${c_bold}wifiant watch${c_reset}      log for hours, then wifiant analyze"
say ""
say "${c_dim}recorded runs live in $DATA_HOME/wifiant/designs${c_reset}"
