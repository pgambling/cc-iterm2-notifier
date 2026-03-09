#!/usr/bin/env bash
set -euo pipefail

# cc-iterm2-notifier install script
# Symlinks the daemon into iTerm2 AutoLaunch and creates config directory.
# Safe to run multiple times (idempotent).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SRC="$SCRIPT_DIR/cc_iterm2_notifier.py"
AUTOLAUNCH_DIR="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
SYMLINK_DEST="$AUTOLAUNCH_DIR/cc_iterm2_notifier.py"
CONFIG_DIR="$HOME/.config/cc-iterm2-notifier"
CONFLICT_PLUGIN="claude-code-iterm2-tab-status"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No color

info()  { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1" >&2; }
die()   { error "$1"; exit 1; }

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

uninstall() {
    echo "cc-iterm2-notifier: uninstalling..."

    if [[ -L "$SYMLINK_DEST" || -f "$SYMLINK_DEST" ]]; then
        rm -f "$SYMLINK_DEST"
        info "Removed $SYMLINK_DEST"
    else
        warn "Symlink not found at $SYMLINK_DEST (already removed?)"
    fi

    if [[ -d "$CONFIG_DIR" ]]; then
        read -rp "Remove config directory $CONFIG_DIR? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            rm -rf "$CONFIG_DIR"
            info "Removed $CONFIG_DIR"
        else
            info "Config directory kept at $CONFIG_DIR"
        fi
    fi

    echo ""
    info "Uninstall complete. Restart iTerm2 to stop the daemon."
    exit 0
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
fi

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

echo "cc-iterm2-notifier: running pre-flight checks..."
echo ""

# 1. macOS check
if [[ "$(uname)" != "Darwin" ]]; then
    die "This plugin requires macOS."
fi
info "macOS detected"

# 2. iTerm2 installed
if [[ ! -d "/Applications/iTerm.app" ]]; then
    die "iTerm2 not found. Install from https://iterm2.com"
fi
info "iTerm2 found"

# 3. Python API enabled (Scripts directory exists)
SCRIPTS_DIR="$HOME/Library/Application Support/iTerm2/Scripts"
if [[ ! -d "$SCRIPTS_DIR" ]]; then
    die "iTerm2 Python API not enabled. Go to iTerm2 → Settings → General → Magic → Enable Python API"
fi
info "iTerm2 Python API enabled"

# 4. No conflict with claude-code-iterm2-tab-status
CONFLICT_FOUND=false
# Check AutoLaunch directory for the conflicting plugin
if [[ -d "$AUTOLAUNCH_DIR" ]]; then
    for f in "$AUTOLAUNCH_DIR"/*; do
        if [[ "$(basename "$f")" == *"$CONFLICT_PLUGIN"* ]]; then
            CONFLICT_FOUND=true
            break
        fi
    done
fi
# Also check if it's a registered Claude Code plugin
CLAUDE_PLUGINS_DIR="$HOME/.claude/plugins"
if [[ -d "$CLAUDE_PLUGINS_DIR" ]]; then
    for d in "$CLAUDE_PLUGINS_DIR"/*/; do
        if [[ -f "$d/.claude-plugin/plugin.json" ]]; then
            name=$(python3 -c "import json; print(json.load(open('$d/.claude-plugin/plugin.json')).get('name',''))" 2>/dev/null || true)
            if [[ "$name" == "$CONFLICT_PLUGIN" ]]; then
                CONFLICT_FOUND=true
                break
            fi
        fi
    done
fi
if $CONFLICT_FOUND; then
    die "$CONFLICT_PLUGIN detected. Disable it first to avoid duplicate tab indicators."
fi
info "No conflicting plugins"

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

echo ""
echo "cc-iterm2-notifier: installing..."
echo ""

# 1. Create AutoLaunch directory if needed
mkdir -p "$AUTOLAUNCH_DIR"

# 2. Symlink daemon script
ln -sf "$DAEMON_SRC" "$SYMLINK_DEST"
info "Symlinked daemon → $SYMLINK_DEST"

# 3. Create config directory
mkdir -p "$CONFIG_DIR"
info "Config directory ready at $CONFIG_DIR"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
info "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Restart iTerm2 (or run Scripts → cc_iterm2_notifier.py manually)"
echo "  2. The daemon will start automatically on future iTerm2 launches"
echo "  3. Optional: customize settings in $CONFIG_DIR/config.json"
echo ""
echo "To uninstall: $0 --uninstall"
