#!/usr/bin/env python3
"""
cc-iterm2-notifier — iTerm2 AutoLaunch script that provides tab indicators
and smart desktop notifications for Claude Code sessions.

Runs as a daemon inside iTerm2's Python runtime. Receives hook events from
Claude Code via HTTP POST to localhost:PORT/hook.
"""

import asyncio
import errno
import json
import math
import pathlib
import time
import typing

import iterm2
from aiohttp import web

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

PORT = 19222  # Fixed — must match the port in hooks.json
DEFAULT_DELAY_SECONDS = 5
CONFIG_DIR = pathlib.Path.home() / ".config" / "cc-iterm2-notifier"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_DIR = CONFIG_DIR / "sessions"

DEFAULT_CONFIG: dict = {
    "notifications": {
        "delay_seconds": DEFAULT_DELAY_SECONDS,
        "attention": {
            "sound": "Ping",
            "title": "Claude Code: Permission Required",
            "message": "Claude needs approval to proceed.",
        },
        "completed": {
            "sound": "default",
            "title": "Claude Code: Task Completed",
            "message": "The requested task has been finished.",
        },
    },
    "tab_indicators": {
        "running": {"prefix": "⚡ ", "color": {"r": 59, "g": 130, "b": 246}},
        "idle": {"prefix": "💤 "},
        "attention": {
            "prefix": "🔴 ",
            "color": {"r": 239, "g": 68, "b": 68},
            "flash_interval": 0.6,
            "badge": "⚠️ Needs input",
        },
        "completed": {
            "prefix": "✅ ",
            "color": {"r": 34, "g": 197, "b": 94},
            "flash_interval": 1.2,
            "badge": "Review ready",
        },
    },
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> dict:
    """Load configuration from disk, falling back to defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                user_config = json.load(f)
            return _deep_merge(DEFAULT_CONFIG, user_config)
        except Exception as exc:
            print(f"cc-iterm2-notifier: failed to load config: {exc}")
    return _deep_merge(DEFAULT_CONFIG, {})


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------


def _color_from_dict(d: dict) -> iterm2.Color:
    return iterm2.Color(d.get("r", 0), d.get("g", 0), d.get("b", 0))


def _color_distance(c1: iterm2.Color, c2: iterm2.Color) -> float:
    """Simple Euclidean distance in RGB space."""
    return math.sqrt(
        (c1.red - c2.red) ** 2
        + (c1.green - c2.green) ** 2
        + (c1.blue - c2.blue) ** 2
    )


def auto_contrast(target: iterm2.Color, background: iterm2.Color) -> iterm2.Color:
    """If *target* is too close to *background*, pick a fallback."""
    if _color_distance(target, background) > 80:
        return target
    # Try blue, white, then inverted
    fallbacks = [
        iterm2.Color(59, 130, 246),
        iterm2.Color(255, 255, 255),
        iterm2.Color(255 - background.red, 255 - background.green, 255 - background.blue),
    ]
    for fb in fallbacks:
        if _color_distance(fb, background) > 80:
            return fb
    return fallbacks[-1]


# ---------------------------------------------------------------------------
# Tab Snapshot — captures original tab state for clean restore
# ---------------------------------------------------------------------------


class TabSnapshot:
    """Stores the original tab state so it can be restored later."""

    __slots__ = ("title", "tab_color", "badge")

    def __init__(self, title: str, tab_color: typing.Optional[iterm2.Color], badge: str):
        self.title = title
        self.tab_color = tab_color
        self.badge = badge

    @classmethod
    async def capture(cls, session: iterm2.Session) -> "TabSnapshot":
        profile = await session.async_get_profile()
        tab_color = None
        if await _profile_uses_tab_color(profile):
            tab_color = profile.tab_color
        title = session.name or ""
        badge = profile.badge_text or ""
        return cls(title=title, tab_color=tab_color, badge=badge)

    async def restore(self, session: iterm2.Session) -> None:
        profile = await session.async_get_profile()
        # Restore badge
        await profile.async_set_badge_text(self.badge)
        # Restore tab color
        if self.tab_color is not None:
            await profile.async_set_tab_color(self.tab_color)
            await profile.async_set_use_tab_color(True)
        else:
            await profile.async_set_use_tab_color(False)
        # Restore title — strip any prefix we may have added
        await session.async_set_name(self.title)


async def _profile_uses_tab_color(profile) -> bool:
    try:
        return profile.use_tab_color
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session State
# ---------------------------------------------------------------------------

# States
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_ATTENTION = "attention"
STATE_COMPLETED = "completed"


class SessionState:
    """Tracks state for a single Claude Code session (identified by session_id)."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = STATE_IDLE
        self.snapshot: typing.Optional[TabSnapshot] = None
        self.flash_task: typing.Optional[asyncio.Task] = None
        self.notification_task: typing.Optional[asyncio.Task] = None
        self.focused = False


# ---------------------------------------------------------------------------
# Notifier — native macOS notifications via pyobjc
# ---------------------------------------------------------------------------


class Notifier:
    """Sends native macOS desktop notifications using UNUserNotificationCenter."""

    def __init__(self):
        self._center = None
        self._auth_resolved = False  # True once the OS callback fires
        self._authorized = False
        self._init_attempted = False

    def _ensure_init(self):
        if self._init_attempted:
            return
        self._init_attempted = True
        try:
            import Foundation
            import UserNotifications

            self._UNUserNotificationCenter = UserNotifications.UNUserNotificationCenter
            self._UNMutableNotificationContent = UserNotifications.UNMutableNotificationContent
            self._UNNotificationRequest = UserNotifications.UNNotificationRequest
            self._UNTimeIntervalNotificationTrigger = UserNotifications.UNTimeIntervalNotificationTrigger
            self._UNNotificationSound = UserNotifications.UNNotificationSound
            self._NSUUID = Foundation.NSUUID

            self._center = self._UNUserNotificationCenter.currentNotificationCenter()
            self._request_authorization()
        except ImportError:
            print("cc-iterm2-notifier: pyobjc UserNotifications not available, "
                  "desktop notifications disabled")
        except Exception as exc:
            print(f"cc-iterm2-notifier: notification init failed: {exc}")

    def _request_authorization(self):
        if self._center is None:
            return
        try:
            # Request alert + sound permissions
            self._center.requestAuthorizationWithOptions_completionHandler_(
                0x04 | 0x02 | 0x01,  # badge | sound | alert
                lambda granted, error: (
                    setattr(self, "_authorized", granted),
                    setattr(self, "_auth_resolved", True),
                ),
            )
        except Exception as exc:
            print(f"cc-iterm2-notifier: authorization request failed: {exc}")

    def send(self, title: str, message: str, sound: str = "default",
             identifier: typing.Optional[str] = None):
        """Send a desktop notification."""
        self._ensure_init()
        if self._center is None:
            return
        # If authorization hasn't resolved yet, optimistically try sending.
        # If explicitly denied, skip.
        if self._auth_resolved and not self._authorized:
            return
        try:
            content = self._UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(message)

            if sound and sound != "none":
                if sound == "default":
                    content.setSound_(self._UNNotificationSound.defaultSound())
                else:
                    content.setSound_(
                        self._UNNotificationSound.soundNamed_(sound)
                    )

            req_id = identifier or self._NSUUID.UUID().UUIDString()
            trigger = self._UNTimeIntervalNotificationTrigger.triggerWithTimeInterval_repeats_(
                0.1, False
            )
            request = self._UNNotificationRequest.requestWithIdentifier_content_trigger_(
                req_id, content, trigger
            )
            self._center.addNotificationRequest_withCompletionHandler_(
                request, lambda error: None
            )
        except Exception as exc:
            print(f"cc-iterm2-notifier: failed to send notification: {exc}")

    def cancel(self, identifier: str):
        """Cancel a pending notification by identifier."""
        self._ensure_init()
        if self._center is None:
            return
        try:
            self._center.removePendingNotificationRequestsWithIdentifiers_([identifier])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main Controller
# ---------------------------------------------------------------------------


class Controller:
    """Orchestrates state management, iTerm2 tab updates, and notifications."""

    def __init__(self, app: iterm2.App, connection: iterm2.Connection):
        self.app = app
        self.connection = connection
        self.config = load_config()
        self.sessions: dict[str, SessionState] = {}
        self.notifier = Notifier()
        self._config_mtime: typing.Optional[float] = None
        self._focus_monitor_task: typing.Optional[asyncio.Task] = None
        self._last_cleanup = 0.0
        self._tty_cache: dict[str, str] = {}  # session_id → tty

    # -- Housekeeping -------------------------------------------------------

    def _cleanup_stale_sessions(self) -> None:
        """Remove tracked sessions whose mapping files are older than 24h
        or whose TTY no longer matches any open iTerm2 session."""
        stale_keys = []
        now = time.time()
        for key in list(self.sessions):
            mapping_file = SESSIONS_DIR / f"{key}.json"
            try:
                if mapping_file.exists():
                    with open(mapping_file, "r") as f:
                        data = json.load(f)
                    ts = data.get("timestamp", 0)
                    if now - ts > 86400:  # 24 hours
                        stale_keys.append(key)
                        mapping_file.unlink(missing_ok=True)
                else:
                    stale_keys.append(key)
            except Exception:
                stale_keys.append(key)
        for key in stale_keys:
            self.sessions.pop(key, None)
            self._tty_cache.pop(key, None)

    # -- Config hot-reload --------------------------------------------------

    def _maybe_reload_config(self):
        try:
            if CONFIG_FILE.exists():
                mtime = CONFIG_FILE.stat().st_mtime
                if self._config_mtime is None or mtime > self._config_mtime:
                    self.config = load_config()
                    self._config_mtime = mtime
        except Exception:
            pass
        # Periodic stale session cleanup (every 10 minutes)
        now = time.time()
        if now - self._last_cleanup > 600:
            self._last_cleanup = now
            self._cleanup_stale_sessions()

    # -- Session lookup -----------------------------------------------------

    def _get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(session_id)
        return self.sessions[session_id]

    def _resolve_tty(self, session_id: str) -> typing.Optional[str]:
        """Look up the TTY for a Claude Code session_id from mapping files.

        The register-session.sh hook writes these files on SessionStart.
        Results are cached in memory to avoid repeated file I/O.
        """
        if session_id in self._tty_cache:
            return self._tty_cache[session_id]
        mapping_file = SESSIONS_DIR / f"{session_id}.json"
        try:
            if mapping_file.exists():
                with open(mapping_file, "r") as f:
                    data = json.load(f)
                tty = data.get("tty")
                if tty:
                    self._tty_cache[session_id] = tty
                return tty
        except Exception:
            pass
        return None

    def _find_iterm_session_by_tty(self, tty: str) -> typing.Optional[iterm2.Session]:
        """Find an iTerm2 session by TTY path."""
        for window in self.app.windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    try:
                        if session.tty == tty:
                            return session
                    except Exception:
                        pass
        return None

    def _find_iterm_session(self, session_id: str) -> typing.Optional[iterm2.Session]:
        """Find the iTerm2 session for a Claude Code session_id.

        Uses the TTY mapping written by register-session.sh to bridge
        Claude Code's opaque session_id to an iTerm2 terminal session.
        """
        tty = self._resolve_tty(session_id)
        if tty:
            return self._find_iterm_session_by_tty(tty)
        return None

    # -- State transitions --------------------------------------------------

    async def handle_event(self, event: dict) -> None:
        """Process an incoming hook event from Claude Code."""
        self._maybe_reload_config()

        hook_event = event.get("hook_event_name", "")
        notification_type = event.get("notification_type", "")
        session_id = event.get("session_id", "")
        if not session_id:
            return

        new_state = self._map_event_to_state(hook_event, notification_type)
        if new_state is None:
            return

        ss = self._get_or_create(session_id)
        old_state = ss.state

        # Skip if already in this state
        if old_state == new_state:
            return

        # Find the iTerm2 session via TTY mapping
        iterm_session = self._find_iterm_session(session_id)

        # Cancel any running flash/notification tasks
        await self._cancel_tasks(ss)

        # Capture snapshot on first non-idle transition
        if iterm_session and ss.snapshot is None and new_state != STATE_IDLE:
            ss.snapshot = await TabSnapshot.capture(iterm_session)

        ss.state = new_state

        if iterm_session:
            await self._apply_state(ss, iterm_session)

    def _map_event_to_state(self, hook_event: str, notification_type: str) -> typing.Optional[str]:
        if hook_event == "UserPromptSubmit":
            return STATE_RUNNING
        elif hook_event == "Stop":
            return STATE_COMPLETED
        elif hook_event == "Notification":
            if notification_type == "idle_prompt":
                return STATE_IDLE
            elif notification_type == "permission_prompt":
                return STATE_ATTENTION
        return None

    async def _apply_state(self, ss: SessionState, session: iterm2.Session) -> None:
        """Apply visual indicators for the current state."""
        indicators = self.config.get("tab_indicators", {})
        state_config = indicators.get(ss.state, {})
        profile = await session.async_get_profile()

        # For idle/running: restore snapshot first, then apply minimal indicators
        if ss.state in (STATE_IDLE, STATE_RUNNING) and ss.snapshot:
            await ss.snapshot.restore(session)
            # Re-fetch profile after restore since it may have changed
            profile = await session.async_get_profile()
            if ss.state == STATE_IDLE:
                ss.snapshot = None

        # Set prefix on tab title
        prefix = state_config.get("prefix", "")
        base_title = ss.snapshot.title if ss.snapshot else (session.name or "")
        # Strip any existing prefix (look for known prefixes)
        for s in indicators.values():
            p = s.get("prefix", "")
            if p and base_title.startswith(p):
                base_title = base_title[len(p):]
                break
        await session.async_set_name(f"{prefix}{base_title}")

        # Set badge
        badge = state_config.get("badge", "")
        await profile.async_set_badge_text(badge)

        # Set tab color
        color_dict = state_config.get("color")
        flash_interval = state_config.get("flash_interval")

        if color_dict:
            target_color = _color_from_dict(color_dict)
            bg_color = ss.snapshot.tab_color if (ss.snapshot and ss.snapshot.tab_color) else iterm2.Color(0, 0, 0)
            target_color = auto_contrast(target_color, bg_color)

            if flash_interval:
                # Start flash/pulse task
                ss.flash_task = asyncio.ensure_future(
                    self._flash_loop(ss, session, target_color, bg_color, flash_interval)
                )
            else:
                # Steady color
                await profile.async_set_tab_color(target_color)
                await profile.async_set_use_tab_color(True)
        else:
            # Restore original color (idle state)
            if ss.snapshot and ss.snapshot.tab_color:
                await profile.async_set_tab_color(ss.snapshot.tab_color)
                await profile.async_set_use_tab_color(True)
            else:
                await profile.async_set_use_tab_color(False)

        # Schedule desktop notification for attention/completed
        if ss.state in (STATE_ATTENTION, STATE_COMPLETED):
            delay = self.config.get("notifications", {}).get("delay_seconds", DEFAULT_DELAY_SECONDS)
            ss.notification_task = asyncio.ensure_future(
                self._delayed_notification(ss, delay)
            )

    async def _flash_loop(
        self,
        ss: SessionState,
        session: iterm2.Session,
        color: iterm2.Color,
        original: iterm2.Color,
        interval: float,
    ) -> None:
        """Alternate tab color between *color* and *original*."""
        show_color = True
        profile = await session.async_get_profile()
        try:
            while True:
                if show_color:
                    await profile.async_set_tab_color(color)
                else:
                    if original:
                        await profile.async_set_tab_color(original)
                    else:
                        await profile.async_set_use_tab_color(False)
                        await asyncio.sleep(interval)
                        await profile.async_set_use_tab_color(True)
                        show_color = not show_color
                        continue
                await profile.async_set_use_tab_color(True)
                show_color = not show_color
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"cc-iterm2-notifier: flash loop error: {exc}")

    async def _delayed_notification(self, ss: SessionState, delay: float) -> None:
        """Wait *delay* seconds, then send a desktop notification if still relevant."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        if ss.state not in (STATE_ATTENTION, STATE_COMPLETED):
            return

        if ss.focused:
            return

        notif_config = self.config.get("notifications", {}).get(ss.state, {})
        title = notif_config.get("title", f"Claude Code: {ss.state}")
        message = notif_config.get("message", "")
        sound = notif_config.get("sound", "default")
        notif_id = f"cc-iterm2-{ss.session_id}-{ss.state}"

        self.notifier.send(title=title, message=message, sound=sound, identifier=notif_id)

    async def _cancel_tasks(self, ss: SessionState) -> None:
        """Cancel running flash and notification tasks."""
        if ss.flash_task and not ss.flash_task.done():
            ss.flash_task.cancel()
            try:
                await ss.flash_task
            except asyncio.CancelledError:
                pass
            ss.flash_task = None

        if ss.notification_task and not ss.notification_task.done():
            ss.notification_task.cancel()
            try:
                await ss.notification_task
            except asyncio.CancelledError:
                pass
            ss.notification_task = None

    async def handle_focus(self, iterm_session_id: str, focused: bool) -> None:
        """Handle focus change for an iTerm2 session.

        Maps the focused iTerm2 session back to a tracked Claude Code session
        by comparing TTYs.
        """
        # Get the TTY of the focused iTerm2 session
        focused_session = None
        for window in self.app.windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if session.session_id == iterm_session_id:
                        focused_session = session
                        break
        if focused_session is None:
            return
        try:
            focused_tty = focused_session.tty
        except Exception:
            return

        # Find which tracked Claude Code session maps to this TTY
        for cc_session_id, ss in self.sessions.items():
            tty = self._resolve_tty(cc_session_id)
            if tty == focused_tty:
                ss.focused = focused
                if focused and ss.state in (STATE_ATTENTION, STATE_COMPLETED):
                    await self._cancel_tasks(ss)
                    if ss.snapshot:
                        await ss.snapshot.restore(focused_session)
                        # Re-apply idle prefix
                        indicators = self.config.get("tab_indicators", {})
                        idle_prefix = indicators.get("idle", {}).get("prefix", "")
                        base_title = ss.snapshot.title
                        await focused_session.async_set_name(f"{idle_prefix}{base_title}")
                        ss.snapshot = None
                    ss.state = STATE_IDLE
                break

    # -- Focus monitor ------------------------------------------------------

    async def start_focus_monitor(self) -> None:
        """Monitor iTerm2 focus changes and dismiss alerts when tab is focused."""
        async with iterm2.FocusMonitor(self.connection) as monitor:
            while True:
                update = await monitor.async_get_next_update()
                if update.active_session_changed:
                    session_id = update.active_session_changed.session_id
                    await self.handle_focus(session_id, True)

    # -- HTTP Server --------------------------------------------------------

    async def start_server(self) -> bool:
        """Start the HTTP server. Returns True on success, False on failure."""
        app = web.Application()
        app.router.add_post("/hook", self._handle_hook_request)
        app.router.add_get("/health", self._handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", PORT)
        try:
            await site.start()
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                print(f"cc-iterm2-notifier: port {PORT} already in use — "
                      "another instance may be running. Exiting.")
                return False
            raise
        print(f"cc-iterm2-notifier: listening on http://127.0.0.1:{PORT}")
        return True

    async def _handle_hook_request(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        task = asyncio.ensure_future(self.handle_event(body))
        task.add_done_callback(self._log_task_exception)
        return web.json_response({"status": "ok"})

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            print(f"cc-iterm2-notifier: event handler error: {exc}")

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "sessions": len(self.sessions),
        })


# ---------------------------------------------------------------------------
# Entry point — iTerm2 AutoLaunch
# ---------------------------------------------------------------------------


async def main(connection: iterm2.Connection):
    app = await iterm2.async_get_app(connection)
    controller = Controller(app, connection)

    # Start HTTP server first — it must be ready before hooks arrive
    if not await controller.start_server():
        return

    # Focus monitor runs forever; restart it on failure so a transient
    # iTerm2 API error doesn't kill the entire daemon.
    while True:
        try:
            await controller.start_focus_monitor()
        except Exception as exc:
            print(f"cc-iterm2-notifier: focus monitor crashed, restarting: {exc}")
            await asyncio.sleep(2)


iterm2.run_forever(main)
