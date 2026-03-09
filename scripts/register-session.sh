#!/usr/bin/env bash
# Registers the current Claude Code session's TTY so the iTerm2 daemon
# can map hook events to the correct terminal tab.
# Runs as a SessionStart command hook — receives JSON on stdin.

{
    [[ "$(uname)" == "Darwin" ]] || exit 0

    SESSIONS_DIR="$HOME/.config/cc-iterm2-notifier/sessions"
    mkdir -p "$SESSIONS_DIR"

    # Read session_id from stdin JSON
    INPUT=$(cat)
    SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
    [[ -n "$SESSION_ID" ]] || exit 0

    # Get the TTY of the current terminal
    TTY=$(tty 2>/dev/null) || true
    [[ -n "$TTY" && "$TTY" != "not a tty" ]] || exit 0

    # Write mapping file
    python3 -c "
import json, time
data = {'tty': '$TTY', 'timestamp': time.time()}
with open('$SESSIONS_DIR/$SESSION_ID.json', 'w') as f:
    json.dump(data, f)
" 2>/dev/null
} 2>/dev/null

exit 0
