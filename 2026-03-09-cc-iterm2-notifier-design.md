# cc-iterm2-notifier Design

A Claude Code plugin that provides iTerm2 tab indicators and smart desktop notifications for Claude Code sessions.

## Architecture

```
┌─────────────────┐    HTTP POST     ┌──────────────────────────────────┐
│  Claude Code     │ ───────────────> │  cc-iterm2-notifier (Python)     │
│  (hook events)   │  localhost:PORT  │                                  │
└─────────────────┘                  │  ┌────────────────────────────┐  │
                                     │  │ HTTP Server (aiohttp)      │  │
                                     │  │ POST /hook -> parse event  │  │
                                     │  └──────────┬─────────────────┘  │
                                     │             │                    │
                                     │  ┌──────────v─────────────────┐  │
                                     │  │ State Manager              │  │
                                     │  │ tracks sessions:           │  │
                                     │  │  - running / idle /        │  │
                                     │  │    attention / completed   │  │
                                     │  │  - per-session snapshots   │  │
                                     │  │  - focus state             │  │
                                     │  └──────────┬─────────────────┘  │
                                     │             │                    │
                                     │  ┌──────────v─────────────────┐  │
                                     │  │ iTerm2 Python API          │  │
                                     │  │  - tab title prefixes      │  │
                                     │  │  - tab background color    │  │
                                     │  │  - tab color flash/pulse   │  │
                                     │  │  - badge text              │  │
                                     │  │  - focus monitor           │  │
                                     │  └──────────┬─────────────────┘  │
                                     │             │                    │
                                     │  ┌──────────v─────────────────┐  │
                                     │  │ Notifier (pyobjc)          │  │
                                     │  │  - native macOS desktop    │  │
                                     │  │    notifications via       │  │
                                     │  │    UNUserNotificationCenter│  │
                                     │  │  - shows iTerm2 icon       │  │
                                     │  │  - delayed, focus-aware    │  │
                                     │  └────────────────────────────┘  │
                                     └──────────────────────────────────┘
```

### Components

1. **HTTP Server** — listens on localhost, receives Claude Code hook POSTs, parses event type from JSON body (`hook_event_name` and `notification_type` fields).
2. **State Manager** — tracks per-session state (running/idle/attention/completed), stores iTerm2 tab snapshots (color, badge, title) for clean restore on state transition.
3. **iTerm2 API layer** — tab title prefixes, background colors, color flashing/pulsing, badges, focus monitoring via iTerm2's `FocusMonitor`.
4. **Notifier** — native macOS notifications via pyobjc (`UNUserNotificationCenter`), which is bundled in iTerm2's Python runtime. Notifications appear with the iTerm2 icon and group in Notification Center.

### Deployment

Single Python file installed as an iTerm2 AutoLaunch script (`~/Library/Application Support/iTerm2/Scripts/AutoLaunch/`). Starts automatically when iTerm2 launches.

## Event Flow & State Machine

### Hook event to state mapping

| Claude Code Event | Mapped State | Tab Prefix | Tab Color | Badge | Desktop Notification |
|---|---|---|---|---|---|
| `UserPromptSubmit` | Running | `⚡` | Steady blue | None | No |
| `Notification(idle_prompt)` | Idle | `💤` | None (original) | None | No |
| `Notification(permission_prompt)` | Attention | `🔴` | Flashing red | `⚠️ Needs input` | Yes (after delay) |
| `Stop` | Completed | `✅` | Gentle green pulse | `Review ready` | Yes (after delay) |

### State transitions

```
                  UserPromptSubmit
         ┌──────────────────────────────┐
         │                              │
         v                              │
     ┌────────┐  idle_prompt/Stop   ┌───┴───┐
     │Running │ ──────────────────> │ Idle  │
     └───┬────┘                     └───┬───┘
         │                              │
         │  permission_prompt           │  permission_prompt
         │         Stop                 │         Stop
         │                              │
         v                              v
     ┌─────────┐<───────────────────────┘
     │Attention │
     │Completed │
     └────┬─────┘
          │
          │  Focus tab -> restore snapshot + clear
          │  UserPromptSubmit -> transition to Running
          │
          └──> (any other state)
```

### Focus-based dismissal

When the iTerm2 FocusMonitor reports that an attention/completed-state tab receives focus, the adapter:

1. Stops the color flash/pulse
2. Restores the original tab color, badge, and title
3. Cancels any pending desktop notification

### Desktop notification delay

When entering Attention or Completed, a timer starts (default: 5 seconds). If the tab is still in that state and unfocused when the timer fires, a native macOS notification is sent with the configured sound. If the user focuses the tab before the timer fires, it is cancelled.

## Configuration

Single config file at `~/.config/cc-iterm2-notifier/config.json`, hot-reloaded on file change. All settings have sensible defaults — the config file is optional.

```json
{
  "notifications": {
    "delay_seconds": 5,
    "attention": {
      "sound": "Ping",
      "title": "Claude Code: Permission Required",
      "message": "Claude needs approval to proceed."
    },
    "completed": {
      "sound": "default",
      "title": "Claude Code: Task Completed",
      "message": "The requested task has been finished."
    }
  },
  "tab_indicators": {
    "running":   { "prefix": "⚡ ", "color": { "r": 59,  "g": 130, "b": 246 } },
    "idle":      { "prefix": "💤 " },
    "attention": { "prefix": "🔴 ", "color": { "r": 239, "g": 68,  "b": 68  }, "flash_interval": 0.6, "badge": "⚠️ Needs input" },
    "completed": { "prefix": "✅ ", "color": { "r": 34,  "g": 197, "b": 94  }, "flash_interval": 1.2, "badge": "Review ready" }
  }
}
```

### Behavior per state

- **Running** — steady blue tab background, prefix only, no notification.
- **Idle** — no color (restores original tab color), prefix only, no notification.
- **Attention** — flashes between red and original color at 0.6s intervals, badge set, notification after delay.
- **Completed** — gentle pulse between green and original color at 1.2s intervals, badge set, notification after delay.

### Auto-contrast

For flashing/pulsing states, if the configured color is too close to the tab's current background color, a fallback color is selected automatically (blue, then white, then inverted).

## Plugin Structure

```
cc-iterm2-notifier/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── hooks/
│   └── hooks.json               # Auto-installed hooks (SessionStart + HTTP)
├── scripts/
│   ├── cc_iterm2_notifier.py    # iTerm2 AutoLaunch script (daemon)
│   ├── init.sh                  # SessionStart hook: auto-symlinks daemon
│   ├── register-session.sh      # SessionStart hook: writes TTY mapping
│   └── install.sh               # Manual install/uninstall helper
└── README.md
```

### plugin.json

```json
{
  "name": "cc-iterm2-notifier",
  "description": "iTerm2 tab indicators and smart notifications for Claude Code",
  "version": "0.1.0"
}
```

### hooks/hooks.json

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/init.sh" },
          { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/register-session.sh" }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "idle_prompt|permission_prompt",
        "hooks": [{ "type": "http", "url": "http://localhost:19222/hook" }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "http", "url": "http://localhost:19222/hook" }]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [{ "type": "http", "url": "http://localhost:19222/hook" }]
      }
    ]
  }
}
```

### Graceful degradation

HTTP hooks treat connection failures as non-blocking errors. If the daemon is not running (iTerm2 closed, script crashed), Claude Code continues normally with no error popups or broken sessions.

## Installation Script

`install.sh` validates the full dependency chain before taking any action.

### Pre-flight checks (fail fast)

| Check | How | Failure message |
|---|---|---|
| macOS | `[[ "$(uname)" == "Darwin" ]]` | "This plugin requires macOS." |
| iTerm2 installed | `[[ -d "/Applications/iTerm.app" ]]` | "iTerm2 not found. Install from iterm2.com" |
| Python API enabled | Check for `~/Library/Application Support/iTerm2/Scripts/` | "iTerm2 Python API not enabled. Go to iTerm2 → Settings → General → Magic → Enable Python API" |
| No conflict | Check for claude-code-iterm2-tab-status plugin | "claude-code-iterm2-tab-status detected. Disable it first to avoid duplicate tab indicators." |

### Actions (only after all checks pass)

1. Symlink `cc_iterm2_notifier.py` into `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/`
2. Create default config directory `~/.config/cc-iterm2-notifier/`
3. Print next steps

### Properties

- **Idempotent** — safe to run multiple times. Overwrites existing symlink, skips directory creation if exists.
- **Uninstall** — `install.sh --uninstall` removes the symlink and optionally the config directory (with confirmation prompt).
- **Zero external dependencies** — no brew packages, no pip installs. pyobjc is bundled with iTerm2's Python runtime.

## Dependencies

None external. The daemon runs inside iTerm2's Python runtime which provides:

- `iterm2` — tab/session/window control, focus monitoring
- `pyobjc` — native macOS notifications via `UNUserNotificationCenter`
- `asyncio` — event loop, timers
- `aiohttp` — HTTP server (bundled with iTerm2's Python environment)

## Future Expansion (not in scope)

- Web UI dashboard showing session states (add routes to the existing HTTP server)
- Phone push notifications via Pushover/ntfy (add notification backend)
- Multi-device state sync (HTTP server is already queryable)
