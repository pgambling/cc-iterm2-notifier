# cc-iterm2-notifier

A Claude Code plugin that provides iTerm2 tab indicators and smart desktop notifications for Claude Code sessions.

## Features

- **Tab title prefixes** — see at a glance which state each Claude Code session is in (⚡ running, 💤 idle, 🔴 needs attention, ✅ completed)
- **Tab color indicators** — steady blue while running, flashing red when permission is needed, gentle green pulse when done
- **Badge text** — badges appear for attention and completed states
- **Desktop notifications** — native macOS notifications with configurable delay, only sent when the tab is unfocused
- **Focus-aware dismissal** — switching to a tab automatically clears indicators and cancels pending notifications

## Requirements

- macOS
- [iTerm2](https://iterm2.com) with Python API enabled (Settings → General → Magic → Enable Python API)
- Claude Code

## Installation

1. Clone or download this repository
2. Run the install script:

```bash
./scripts/install.sh
```

3. Restart iTerm2

The daemon starts automatically on every iTerm2 launch. No external dependencies are needed — everything runs inside iTerm2's bundled Python runtime.

## How It Works

The plugin installs HTTP hooks into Claude Code that POST events to a local server running inside iTerm2. The server tracks session states and updates tab indicators accordingly.

| Event | State | Tab Prefix | Tab Color | Notification |
|---|---|---|---|---|
| `UserPromptSubmit` | Running | ⚡ | Steady blue | No |
| `Notification(idle_prompt)` | Idle | 💤 | Original | No |
| `Notification(permission_prompt)` | Attention | 🔴 | Flashing red | Yes (after delay) |
| `Stop` | Completed | ✅ | Pulsing green | Yes (after delay) |

## Configuration

All settings have sensible defaults. Optionally create `~/.config/cc-iterm2-notifier/config.json`:

```json
{
  "port": 19222,
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

## Uninstall

```bash
./scripts/install.sh --uninstall
```

## License

MIT
