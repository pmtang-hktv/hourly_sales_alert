# CLAUDE.md — Operational notes for Claude Code sessions

## Process management

The app is managed by **launchd** on the Mac host (`~/Library/LaunchAgents/com.hktv.sales-alert.plist`, `KeepAlive=true`).

**Always restart via launchctl, never `nohup python3 main.py`:**
```bash
launchctl unload ~/Library/LaunchAgents/com.hktv.sales-alert.plist
launchctl load  ~/Library/LaunchAgents/com.hktv.sales-alert.plist
```

Using `nohup` directly creates a second, unmanaged instance. Because the Telegram bot uses long-polling (`getUpdates`), each extra instance receives and answers every message — causing duplicate "Looking it up..." replies and near-identical responses.

## Duplicate-instance incident (2025-06)

**Root cause:** Two `nohup python3 main.py` instances (PIDs 64108, 64182) were started manually during development and never killed. When the launchd-managed instance (PID 65040) launched later with new code including the `fcntl.flock` singleton lock, the lock only prevented *new* instances from starting — the pre-existing stale ones kept running.

**Fix applied:** `_kill_stale_siblings()` now runs at startup (before lock acquisition) and sends SIGTERM to any other process matching `hourly_sales_alert/main.py`. This handles lingering instances from before the lock era.

**Detection:** `ps aux | grep hourly_sales_alert/main.py | grep -v grep` — should show exactly one PID. If you see more than one, kill the extras and keep the launchd one (highest PID, started most recently, listed as `??` in the TTY column).

## Database

`data/sales.db` — SQLite, single writer (the scheduler process). Do not open with any tool that holds a write lock while the scheduler is running.

## Lock file

`data/.sales_alert.lock` — held by the running process via `fcntl.LOCK_EX`. If the process dies abnormally the lock is released automatically by the OS (no stale lock problem).
