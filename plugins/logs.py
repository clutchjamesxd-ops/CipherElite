# =============================================================================
#  CipherElite Userbot Plugin - Logs
#
#  Plugin Name:    logs
#  Version:        1.0.0
#  Author:         CipherElite Dev (@rishabhops)
#  Repository:     https://github.com/rishabhops/CipherElite
#
#  LICENSE:        MIT
#
#  WHAT THIS DOES:
#   The rest of CipherElite (bot.py, cipher_ai.py, ai_setup.py, etc.) logs
#   almost everything via plain print() — startup messages, plugin load
#   status, errors. This plugin transparently "tees" stdout/stderr into a
#   rotating log file on disk, THEN gives you Telegram commands to read,
#   search, live-watch, download, or clear that log — without needing SSH
#   access to the VPS for routine debugging.
# =============================================================================

VERSION = "1.0.0"
CATEGORY = "developer"

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime
from telethon import events
from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler

PROJECT_ROOT = Path(__file__).parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "cipherelite.log"

MAX_LOG_BYTES = 5 * 1024 * 1024   # rotate at 5MB
BACKUP_COUNT = 3                   # keep this many rotated backups
ERROR_MARKERS = ("error", "exception", "traceback", "failed", "❌", "⚠️")

# chat_id -> {"task": asyncio.Task, "msg": Message, "buffer": str, "pos": int, "ticks": int}
WATCHERS = {}


# =============================================================================
#  stdout/stderr capture
# =============================================================================
def _rotate_if_needed():
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            for i in range(BACKUP_COUNT - 1, 0, -1):
                src = LOG_DIR / f"cipherelite.log.{i}"
                dst = LOG_DIR / f"cipherelite.log.{i + 1}"
                if src.exists():
                    src.replace(dst)
            LOG_FILE.replace(LOG_DIR / "cipherelite.log.1")
    except Exception:
        pass  # logging must never crash the bot


class _TeeStream:
    """Writes everything to the original stream AND to the log file, so
    every existing print() call across the whole codebase gets captured
    for free — no changes needed anywhere else."""

    def __init__(self, original):
        self._original = original
        self._fh = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
        self._writes = 0

    def write(self, data):
        try:
            self._original.write(data)
        except Exception:
            pass
        if not data:
            return
        try:
            self._writes += 1
            if self._writes % 50 == 0:
                self._fh.close()
                _rotate_if_needed()
                self._fh = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            self._fh.write(data)
            self._fh.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass
        try:
            self._fh.flush()
        except Exception:
            pass

    def isatty(self):
        return False


_installed = False


def _install_tee():
    global _installed
    if _installed:
        return
    sys.stdout = _TeeStream(sys.stdout)
    sys.stderr = _TeeStream(sys.stderr)
    _installed = True
    print(f"📝 Log capture active → {LOG_FILE}")


# =============================================================================
#  Helpers
# =============================================================================
def _read_all_lines():
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        return [l.rstrip("\n") for l in f.readlines()]


def _tail(n):
    return _read_all_lines()[-n:]


def _tail_errors(n, scan_window=3000):
    lines = _read_all_lines()[-scan_window:]
    matches = [l for l in lines if any(m in l.lower() or m in l for m in ERROR_MARKERS)]
    return matches[-n:]


async def _reply_lines(event, lines, empty_msg, header):
    if not lines:
        await event.reply(empty_msg)
        return
    body = "\n".join(lines)
    text = f"{header}\n\n<code>{_esc(body)}</code>"
    if len(text) <= 4000:
        await event.reply(text, parse_mode="html")
    else:
        # Too long for a message — send as a file instead
        tmp_path = LOG_DIR / f"logs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        tmp_path.write_text(body, encoding="utf-8")
        await event.reply(f"{header}\n\n📎 Output too long for a message — sent as a file.", file=str(tmp_path))
        try:
            tmp_path.unlink()
        except Exception:
            pass


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# =============================================================================
def init(client):
    """Initialize the Logs plugin"""
    _install_tee()

    commands = [
        ".logs [n]        — Show the last n log lines (default 50)",
        ".logserr [n]     — Show the last n error/warning lines (default 30)",
        ".logsfile        — Send the raw log file as a downloadable document",
        ".logswatch on    — Live-tail new log lines into this chat (auto-stops after 30 min)",
        ".logswatch off   — Stop live-tailing",
        ".clearlogs       — Archive and clear the current log file",
    ]
    add_handler("logs", commands, "Logs — capture, view, search, live-watch & export bot logs")

    # ---------------------------------------------------------------
    @CipherElite.on(events.NewMessage(pattern=r"\.logs(?:\s+(\d+))?$"))
    @rishabh()
    async def logs_handler(event):
        n = int(event.pattern_match.group(1)) if event.pattern_match.group(1) else 50
        n = max(1, min(n, 500))
        lines = _tail(n)
        await _reply_lines(event, lines, "📭 **Log file is empty.**", f"📜 **Last {len(lines)} log lines**")

    # ---------------------------------------------------------------
    @CipherElite.on(events.NewMessage(pattern=r"\.logserr(?:\s+(\d+))?$"))
    @rishabh()
    async def logserr_handler(event):
        n = int(event.pattern_match.group(1)) if event.pattern_match.group(1) else 30
        n = max(1, min(n, 300))
        lines = _tail_errors(n)
        await _reply_lines(event, lines, "✅ **No errors found** in the recent log window.", f"🚨 **Last {len(lines)} error/warning lines**")

    # ---------------------------------------------------------------
    @CipherElite.on(events.NewMessage(pattern=r"\.logsfile$"))
    @rishabh()
    async def logsfile_handler(event):
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
            await event.reply("📭 **Log file is empty.**")
            return
        size_kb = LOG_FILE.stat().st_size / 1024
        await event.reply(f"📎 **Full log file** (`{size_kb:.1f} KB`)", file=str(LOG_FILE))

    # ---------------------------------------------------------------
    @CipherElite.on(events.NewMessage(pattern=r"\.clearlogs$"))
    @rishabh()
    async def clearlogs_handler(event):
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
                archive = LOG_DIR / f"cipherelite.log.cleared_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                LOG_FILE.replace(archive)
            open(LOG_FILE, "a", encoding="utf-8").close()
            # Re-point the live Tee handles at the fresh file
            if isinstance(sys.stdout, _TeeStream):
                sys.stdout._fh.close()
                sys.stdout._fh = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            if isinstance(sys.stderr, _TeeStream):
                sys.stderr._fh.close()
                sys.stderr._fh = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            await event.reply("🧹 **Logs cleared!** Previous log archived in `/logs`.")
        except Exception as e:
            await event.reply(f"❌ **Error clearing logs:** {str(e)[:150]}")

    # ---------------------------------------------------------------
    @CipherElite.on(events.NewMessage(pattern=r"\.logswatch(?:\s+(on|off))?$"))
    @rishabh()
    async def logswatch_handler(event):
        chat_id = event.chat_id
        arg = (event.pattern_match.group(1) or "").lower()

        if arg == "off":
            watcher = WATCHERS.pop(chat_id, None)
            if not watcher:
                await event.reply("📭 **Not currently watching** logs in this chat.")
                return
            watcher["task"].cancel()
            try:
                await watcher["msg"].edit("⏹ **Stopped watching logs.**")
            except Exception:
                pass
            return

        if chat_id in WATCHERS:
            await event.reply("👀 **Already watching** logs in this chat. Use `.logswatch off` to stop.")
            return

        start_pos = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
        watch_msg = await event.reply("👀 **Watching logs live...** (auto-stops in 30 min)\n\n<code>waiting for new output...</code>", parse_mode="html")

        async def _watch_loop():
            state = {"pos": start_pos, "buffer": ""}
            try:
                for _tick in range(360):  # 360 * 5s = 30 minutes safety cap
                    await asyncio.sleep(5)
                    if not LOG_FILE.exists():
                        continue
                    size = LOG_FILE.stat().st_size
                    if size < state["pos"]:
                        state["pos"] = 0  # file was rotated/cleared
                    if size > state["pos"]:
                        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(state["pos"])
                            new_data = f.read()
                        state["pos"] = size
                        state["buffer"] += new_data
                        state["buffer"] = state["buffer"][-3500:]  # keep it under Telegram's limit
                        try:
                            await watch_msg.edit(
                                f"👀 **Watching logs live...**\n\n<code>{_esc(state['buffer'])}</code>",
                                parse_mode="html",
                            )
                        except Exception:
                            pass
                try:
                    await watch_msg.edit("⏹ **Log watch auto-stopped** after 30 minutes. Run `.logswatch on` to resume.")
                except Exception:
                    pass
            except asyncio.CancelledError:
                pass
            finally:
                WATCHERS.pop(chat_id, None)

        task = asyncio.create_task(_watch_loop())
        WATCHERS[chat_id] = {"task": task, "msg": watch_msg}

    print(f"✅ Logs Plugin v{VERSION} initialized (capturing stdout/stderr → {LOG_FILE})")
    return True
