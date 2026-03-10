#!/usr/bin/env bash
# Registers the current Claude Code session's TTY so the iTerm2 daemon
# can map hook events to the correct terminal tab.
# Runs as a SessionStart command hook — receives JSON on stdin.

{
    [[ "$(uname)" == "Darwin" ]] || exit 0

    SESSIONS_DIR="$HOME/.config/cc-iterm2-notifier/sessions"
    mkdir -p -m 700 "$SESSIONS_DIR"

    # Read session_id from stdin JSON
    INPUT=$(cat)
    SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
    [[ -n "$SESSION_ID" ]] || exit 0

    # Get the controlling TTY from the process tree.
    # We can't use `tty` because stdin is piped JSON in hook context.
    # Instead, walk up the process tree via ps to find the controlling terminal.
    get_controlling_tty() {
        local pid=$$
        while [ "$pid" -gt 1 ]; do
            local tty_name
            tty_name=$(ps -o tty= -p "$pid" 2>/dev/null | tr -d ' ')
            if [ -n "$tty_name" ] && [ "$tty_name" != "??" ]; then
                echo "/dev/$tty_name"
                return 0
            fi
            pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        done
        return 1
    }

    TTY=$(get_controlling_tty) || exit 0
    [[ -n "$TTY" ]] || exit 0

    # Write mapping file safely using Python's json.dumps to avoid injection
    python3 -c "
import json, sys, time, os
session_id = sys.argv[1]
tty = sys.argv[2]
out_dir = sys.argv[3]
# Sanitize session_id for use as filename
safe_id = ''.join(c for c in session_id if c.isalnum() or c in '-_')
if not safe_id:
    sys.exit(0)
path = os.path.join(out_dir, safe_id + '.json')
data = {'tty': tty, 'timestamp': time.time()}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f:
    json.dump(data, f)
" "$SESSION_ID" "$TTY" "$SESSIONS_DIR" 2>/dev/null
} 2>/dev/null

exit 0
