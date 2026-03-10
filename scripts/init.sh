#!/usr/bin/env bash
# cc-iterm2-notifier session init — runs on every Claude Code SessionStart.
# Silently ensures the iTerm2 AutoLaunch symlink is in place.
# Exits 0 even on failure so it never blocks Claude Code.

{
    # Only works on macOS
    [[ "$(uname)" == "Darwin" ]] || exit 0

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    DAEMON_SRC="$SCRIPT_DIR/cc_iterm2_notifier.py"
    AUTOLAUNCH_DIR="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
    SYMLINK_DEST="$AUTOLAUNCH_DIR/cc_iterm2_notifier.py"
    CONFIG_DIR="$HOME/.config/cc-iterm2-notifier"

    # iTerm2 not installed — nothing to do
    [[ -d "/Applications/iTerm.app" ]] || exit 0

    # Python API not enabled — nothing to do
    [[ -d "$HOME/Library/Application Support/iTerm2/Scripts" ]] || exit 0

    # Already set up and pointing to the right place — fast exit
    if [[ -L "$SYMLINK_DEST" ]]; then
        target="$(readlink "$SYMLINK_DEST")"
        [[ "$target" == "$DAEMON_SRC" ]] && exit 0
    fi

    # Daemon script must exist — don't create a dangling symlink
    [[ -f "$DAEMON_SRC" ]] || exit 0

    # Set up: create dirs, place symlink
    mkdir -p "$AUTOLAUNCH_DIR"
    mkdir -p "$CONFIG_DIR"
    ln -sf "$DAEMON_SRC" "$SYMLINK_DEST"

    # Install Python dependencies into the iTerm2 runtime if needed.
    # The iTerm2 Python env lives under ~/Library/Application Support/iTerm2/iterm2env
    # or ~/.config/iterm2/AppSupport/iterm2env — find whichever pip3 exists.
    ITERM2_ENV_DIRS=(
        "$HOME/.config/iterm2/AppSupport/iterm2env"
        "$HOME/Library/Application Support/iTerm2/iterm2env"
    )
    PIP3=""
    for env_dir in "${ITERM2_ENV_DIRS[@]}"; do
        # Find the most recent Python version's pip3
        if [[ -d "$env_dir/versions" ]]; then
            pip_candidate="$(find "$env_dir/versions" -name pip3 -path "*/bin/pip3" 2>/dev/null | sort -V | tail -1)"
            if [[ -n "$pip_candidate" && -x "$pip_candidate" ]]; then
                PIP3="$pip_candidate"
                break
            fi
        fi
    done

    # If we found pip3, ensure aiohttp is installed
    if [[ -n "$PIP3" ]]; then
        "$PIP3" show aiohttp >/dev/null 2>&1 || "$PIP3" install --quiet aiohttp
    fi
} 2>/dev/null

exit 0
