# =============================================================================
#  CipherElite Userbot Plugin
#
#  Plugin Name:    errornotify
#  Version:        1.0.0
#  Author:         CipherElite Dev
#  Target path:    plugins/errornotify.py
#
#  What it does:
#  Catches ANY unhandled error in the userbot (command crashes, background
#  task crashes, event-loop crashes) and reports them to Config.LOG_CHAT_ID
#  so the host of the bot always knows when something breaks.
#  Nothing needs to be changed anywhere else - just drop this file in
#  plugins/ and restart the bot.
# =============================================================================

import asyncio
import logging
import sys
import time
import traceback

from telethon import events

from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler
from config.config import Config

VERSION = "1.0.0"
CATEGORY = "utilities"

# ---------------------------------------------------------------------------
# Simple de-dupe / rate-limit so one crash loop doesn't spam the log group
# with hundreds of identical messages in a few seconds.
# ---------------------------------------------------------------------------
_LAST_SENT = {}
_COOLDOWN = 30  # seconds between two identical error reports
_SENDING_GUARD = False  # prevents an error inside the notifier from looping


def _short_id(text: str) -> str:
    return str(abs(hash(text)) % (10 ** 8))


async def _notify(source: str, exc: BaseException):
    """Send a formatted crash report to LOG_CHAT_ID. Never raises."""
    global _SENDING_GUARD

    log_chat_id = getattr(Config, "LOG_CHAT_ID", 0)
    if not log_chat_id:
        return  # not configured, nothing to do

    if _SENDING_GUARD:
        return  # avoid infinite loop if sending itself fails

    tb_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    key = f"{source}:{type(exc).__name__}:{tb_text[-300:]}"
    dedupe_id = _short_id(key)

    now = time.time()
    last = _LAST_SENT.get(dedupe_id, 0)
    if now - last < _COOLDOWN:
        return
    _LAST_SENT[dedupe_id] = now

    if len(tb_text) > 3500:
        tb_text = tb_text[:1700] + "\n... (truncated) ...\n" + tb_text[-1700:]

    text = (
        "🎭 **Cipher Elite - Userbot Crash Report**\n\n"
        f"⚠️ **Source:** `{source}`\n"
        f"❗ **Error:** `{type(exc).__name__}: {exc}`\n\n"
        f"```\n{tb_text}\n```"
    )

    try:
        _SENDING_GUARD = True
        if CipherElite is not None and CipherElite.is_connected():
            await CipherElite.send_message(log_chat_id, text, parse_mode="md")
    except Exception:
        # Swallow - we genuinely cannot let the notifier itself crash the loop.
        pass
    finally:
        _SENDING_GUARD = False


def _schedule_notify(source: str, exc: BaseException):
    """Sync-safe wrapper: schedule the async notifier from any context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_notify(source, exc))
        else:
            loop.run_until_complete(_notify(source, exc))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1) Catch exceptions raised inside Telethon event handlers.
#    Telethon logs these through the "telethon" logger instead of crashing,
#    so we attach a logging.Handler that watches for ERROR/CRITICAL records
#    and forwards them.
# ---------------------------------------------------------------------------
class _TelegramLogForwarder(logging.Handler):
    def emit(self, record):
        if record.levelno < logging.ERROR:
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = record.msg

        exc = record.exc_info[1] if record.exc_info else RuntimeError(msg)
        _schedule_notify(f"handler:{record.name}", exc)


# ---------------------------------------------------------------------------
# 2) Catch exceptions from asyncio background tasks (e.g. loops/pollers
#    started with asyncio.create_task that nobody awaits).
# ---------------------------------------------------------------------------
def _install_asyncio_hook():
    loop = asyncio.get_event_loop()
    default_handler = loop.get_exception_handler()

    def handler(loop, context):
        exc = context.get("exception")
        if exc is not None:
            _schedule_notify("asyncio", exc)
        else:
            _schedule_notify("asyncio", RuntimeError(context.get("message", "unknown asyncio error")))
        if default_handler is not None:
            default_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


# ---------------------------------------------------------------------------
# 3) Catch anything that would otherwise kill the whole process
#    (top-level uncaught exceptions, outside asyncio).
# ---------------------------------------------------------------------------
def _install_excepthook():
    default_hook = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        _schedule_notify("process", exc_value)
        default_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def init(client_instance):
    commands = [
        ".testerror - Trigger a dummy error to verify crash reporting works",
    ]
    description = (
        "🚨 **Error Notifier**\n"
        "📥 Reports every userbot crash/error to your LOG_CHAT_ID\n"
        "🛡️ Covers command errors, background task errors and top-level crashes"
    )
    add_handler("errornotify", commands, description)


async def register_commands():
    logging.getLogger().addHandler(_TelegramLogForwarder())
    _install_asyncio_hook()
    _install_excepthook()

    @CipherElite.on(events.NewMessage(pattern=r"^\.testerror$"))
    @rishabh()
    async def test_error(event):
        await event.reply("🧪 Triggering a test error now, check your log group...")
        raise RuntimeError("This is a test error triggered via .testerror")
