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
} 2>/dev/null

exit 0
