# =============================================================================
#  CipherElite Userbot Plugin - AI Setup Manager v2.0
#
#  Plugin Name:    ai_setup
#  Version:        2.0.0
#  Author:         Rishabh Anand (@rishabhops)
#  Repository:     https://github.com/rishabhops/CipherElite
#
#  LICENSE:        MIT
#
#  CHANGELOG (v1.0.0 -> v2.0.0):
#   - Multi-key support with automatic rotation: add several FREE Gemini
#     keys and Cipher AI effectively gets a multiple of the free daily quota,
#     since each call picks whichever key isn't currently rate-limited
#   - Live key validation (a real ping to Google) before a key is saved, so
#     typos / dead keys get caught immediately instead of failing mid-chat
#   - Per-key rate-limit cooldown tracking (mark_rate_limited) so a key that
#     just got a 429 is skipped for a bit instead of being retried instantly
#   - .aikeys command — see every configured key, masked, with live status
#   - Fully backward compatible: get_api_key() / is_enabled() still work
#     exactly as before, so cipher_ai.py needs ZERO changes to benefit
#   - Beautiful step-by-step "how to get a free API key" tutorial, rendered
#     as Telegram's native EXPANDABLE BLOCKQUOTE ("quote" / "show more"
#     feature) so it stays out of the way until someone taps to expand it
# =============================================================================

import os
import json
import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime, timedelta
from telethon import events
from telethon.tl import types as tltypes
from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler

VERSION = "2.0.0"
CATEGORY = "utilities"

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "DB"
CONFIG_DIR.mkdir(exist_ok=True)
AI_CONFIG_FILE = CONFIG_DIR / "ai_config.json"

DEFAULT_COOLDOWN_SECONDS = 60  # how long a rate-limited key is skipped for


# =============================================================================
#  Tiny rich-text builder for Telegram formatting entities
#  (offsets/lengths must be counted in UTF-16 code units, not Python chars —
#  emoji outside the BMP take 2 units, so we can't just use len())
# =============================================================================
def _u16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


class RichText:
    def __init__(self):
        self.text = ""
        self.entities = []

    def _add(self, s, entity_cls=None, **kwargs):
        start = _u16len(self.text)
        self.text += s
        if entity_cls:
            self.entities.append(entity_cls(offset=start, length=_u16len(s), **kwargs))
        return self

    def plain(self, s):
        return self._add(s)

    def bold(self, s):
        return self._add(s, tltypes.MessageEntityBold)

    def italic(self, s):
        return self._add(s, tltypes.MessageEntityItalic)

    def code(self, s):
        return self._add(s, tltypes.MessageEntityCode)

    def quote(self, s, collapsed=False):
        """Telegram's native blockquote. collapsed=True renders it as an
        EXPANDABLE quote block with a 'Show more' toggle."""
        return self._add(s, tltypes.MessageEntityBlockquote, collapsed=collapsed)


# =============================================================================
#  Config manager
# =============================================================================
class AIConfigManager:
    """Centralized manager for Gemini API keys (supports rotation) and settings"""

    def __init__(self):
        self.config = {
            "keys": [],          # [{"key": str, "added": iso, "uses": int, "errors": int, "rate_limited_until": iso|None}]
            "active_index": 0,
            "ai_enabled": False,
            "last_updated": None,
        }
        self._load()

    # ---------------------------------------------------------------
    def _load(self):
        try:
            if AI_CONFIG_FILE.exists():
                with open(AI_CONFIG_FILE, "r") as f:
                    on_disk = json.load(f)

                # Migrate old single-key schema (v1.0.0) transparently
                if "gemini_api_key" in on_disk and "keys" not in on_disk:
                    migrated_keys = []
                    if on_disk.get("gemini_api_key"):
                        migrated_keys.append({
                            "key": on_disk["gemini_api_key"],
                            "added": on_disk.get("last_updated") or datetime.now().isoformat(),
                            "uses": 0, "errors": 0, "rate_limited_until": None,
                        })
                    on_disk = {
                        "keys": migrated_keys,
                        "active_index": 0,
                        "ai_enabled": bool(migrated_keys),
                        "last_updated": on_disk.get("last_updated"),
                    }

                self.config.update(on_disk)
        except Exception as e:
            print(f"⚠️ AI Config load error: {e}")

        if not self.config["keys"]:
            env_key = os.environ.get("GEMINI_API_KEY")
            if env_key:
                self.config["keys"].append({
                    "key": env_key, "added": datetime.now().isoformat(),
                    "uses": 0, "errors": 0, "rate_limited_until": None,
                })
                self.config["ai_enabled"] = True
                self._save()

    def _save(self):
        try:
            with open(AI_CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"⚠️ AI Config save error: {e}")

    # ---------------------------------------------------------------
    @staticmethod
    def _mask(key: str) -> str:
        if not key or len(key) < 12:
            return "`****`"
        return f"`{key[:8]}...{key[-4:]}`"

    def add_key(self, key: str) -> bool:
        key = key.strip()
        if any(k["key"] == key for k in self.config["keys"]):
            return False  # already present
        self.config["keys"].append({
            "key": key, "added": datetime.now().isoformat(),
            "uses": 0, "errors": 0, "rate_limited_until": None,
        })
        self.config["ai_enabled"] = True
        self.config["last_updated"] = datetime.now().isoformat()
        self._save()
        os.environ["GEMINI_API_KEY"] = key  # keep env var in sync for compatibility
        return True

    def set_api_key(self, key):
        """Backward-compatible: replaces ALL keys with a single one (or clears if None)."""
        self.config["keys"] = []
        if key:
            self.config["keys"].append({
                "key": key.strip(), "added": datetime.now().isoformat(),
                "uses": 0, "errors": 0, "rate_limited_until": None,
            })
        self.config["ai_enabled"] = bool(key)
        self.config["last_updated"] = datetime.now().isoformat()
        self._save()
        os.environ["GEMINI_API_KEY"] = key.strip() if key else ""

    def remove_key(self, index: int = None) -> bool:
        """index is 1-based (as shown to the user). None = remove all."""
        if index is None:
            self.config["keys"] = []
        else:
            idx = index - 1
            if idx < 0 or idx >= len(self.config["keys"]):
                return False
            self.config["keys"].pop(idx)
        self.config["ai_enabled"] = bool(self.config["keys"])
        self.config["last_updated"] = datetime.now().isoformat()
        self._save()
        return True

    def get_api_key(self):
        """
        Backward-compatible entry point (cipher_ai.py etc. call this).
        Rotates round-robin across configured keys, skipping any that are
        still in a rate-limit cooldown window, so a plugin doing nothing
        differently automatically benefits from multi-key rotation.
        """
        keys = self.config["keys"]
        if not keys:
            return None
        now = datetime.now()
        n = len(keys)
        for step in range(n):
            idx = (self.config["active_index"] + step) % n
            entry = keys[idx]
            cooldown = entry.get("rate_limited_until")
            if cooldown and datetime.fromisoformat(cooldown) > now:
                continue
            self.config["active_index"] = (idx + 1) % n
            entry["uses"] = entry.get("uses", 0) + 1
            self._save()
            return entry["key"]
        # everything is cooling down — return the one closest to recovering anyway
        best = min(keys, key=lambda e: e.get("rate_limited_until") or "")
        return best["key"]

    def mark_rate_limited(self, key: str, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS):
        """Optional hook other plugins can call after a 429 so this key is
        skipped for a bit. Safe to ignore if a plugin never calls it."""
        for entry in self.config["keys"]:
            if entry["key"] == key:
                entry["errors"] = entry.get("errors", 0) + 1
                entry["rate_limited_until"] = (datetime.now() + timedelta(seconds=cooldown_seconds)).isoformat()
                self._save()
                return True
        return False

    def list_keys(self):
        now = datetime.now()
        out = []
        for i, entry in enumerate(self.config["keys"], start=1):
            cooldown = entry.get("rate_limited_until")
            cooling = bool(cooldown and datetime.fromisoformat(cooldown) > now)
            out.append({
                "index": i,
                "masked": self._mask(entry["key"]),
                "uses": entry.get("uses", 0),
                "errors": entry.get("errors", 0),
                "status": "⏳ cooling down" if cooling else "✅ active",
            })
        return out

    def is_enabled(self):
        return bool(self.config.get("keys"))

    def key_count(self):
        return len(self.config.get("keys", []))


async def validate_gemini_key(key: str) -> bool:
    """Lightweight live check against Google's API before we save a key."""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
    except Exception:
        return False


# Global instance
ai_config = AIConfigManager()


# =============================================================================
#  Tutorial content — rendered as an expandable native Telegram blockquote
# =============================================================================
def build_tutorial():
    rt = RichText()
    rt.bold("🔑 Cipher AI — Google Gemini API Key Setup\n\n")
    rt.plain("Bina ek bhi rupaya kharch kiye, ")
    rt.bold("2 minute")
    rt.plain(" me apni free Gemini API key mil jayegi. Neeche tap karke pura step-by-step guide kholo 👇\n\n")

    tutorial_lines = (
        "📖 FREE GEMINI API KEY — STEP BY STEP\n\n"
        "1️⃣  Browser me kholo → aistudio.google.com\n\n"
        "2️⃣  Apne Google account se Sign in karo (koi bhi Gmail chalega)\n\n"
        "3️⃣  Left sidebar me \"Get API key\" pe click karo\n\n"
        "4️⃣  \"Create API key\" button dabao\n\n"
        "5️⃣  Ek naya project select karo (ya \"Create in new project\" choose karo)\n\n"
        "6️⃣  Key generate hote hi copy icon se copy kar lo — ye \"AIza...\" se start hogi\n\n"
        "7️⃣  Wapas Telegram me aao aur type karo:\n"
        "     .setai <apni_key_paste_karo>\n\n"
        "💡 PRO TIP: Ek se zyada FREE keys bana sakte ho (alag Google accounts se). "
        "Har extra key .addai se add karo — Cipher AI khud rotate karke use karega, "
        "matlab tumhara daily free limit practically multiply ho jata hai!\n\n"
        "⚠️ Apni key kabhi kisi ke saath share mat karo — ye tumhara private access hai.\n\n"
        "🔗 Direct link: https://aistudio.google.com/apikey"
    )
    rt.quote(tutorial_lines, collapsed=True)
    return rt


def build_status_message():
    rt = RichText()
    rt.bold("📊 AI Configuration Status\n\n")
    n = ai_config.key_count()
    if n == 0:
        rt.plain("🔑 Keys configured: ")
        rt.bold("0")
        rt.plain("\n🤖 Status: ")
        rt.bold("❌ Disabled\n\n")
        rt.plain("Run ")
        rt.code(".setai")
        rt.plain(" with no arguments for the free key setup guide.")
        return rt

    rt.plain("🔑 Keys configured: ")
    rt.bold(str(n))
    rt.plain(f"  ({'rotation active — higher effective free quota' if n > 1 else 'single key'})\n")
    rt.plain("🤖 Status: ")
    rt.bold("✅ Enabled")
    rt.plain("\n⚙️ Used by: PM Permit & Cipher AI\n\n")

    for entry in ai_config.list_keys():
        rt.plain(f"#{entry['index']} {entry['status']}  {entry['masked']}  ")
        rt.italic(f"uses: {entry['uses']}, errors: {entry['errors']}\n")

    rt.plain("\n")
    rt.code(".setai <key>")
    rt.plain(" replace all keys · ")
    rt.code(".addai <key>")
    rt.plain(" add rotation key · ")
    rt.code(".rmai [n]")
    rt.plain(" remove key · ")
    rt.code(".aikeys")
    rt.plain(" list keys")
    return rt


def init(client):
    """Initialize AI Setup plugin"""
    commands = [
        ".setai <key>      — Set/replace the Gemini API key (no arg = free-key tutorial)",
        ".addai <key>      — Add another key for rotation (multiplies free daily quota)",
        ".rmai [n]         — Remove key #n, or all keys if no number given",
        ".aikeys           — List all configured keys with live status",
        ".aistatus         — Show AI configuration status",
    ]
    add_handler("ai_setup", commands, "AI Configuration Manager v2.0 — multi-key rotation")

    async def _auto_delete(event, msg, delay=5):
        await asyncio.sleep(delay)
        try:
            await event.delete()
            await msg.delete()
        except Exception:
            pass

    @CipherElite.on(events.NewMessage(outgoing=True, pattern=r"\.setai(?:\s+(.+))?$"))
    @rishabh()
    async def _setai(event):
        raw = event.pattern_match.group(1)
        if not raw:
            rt = build_tutorial()
            await event.reply(rt.text, formatting_entities=rt.entities)
            return

        key = raw.strip()
        checking = await event.reply("🔍 **Validating key with Google...**")
        valid = await validate_gemini_key(key)
        if not valid:
            await checking.edit(
                "❌ **This key didn't validate.**\n\n"
                "Double-check you copied the whole key, or it may not be activated yet.\n"
                "Run `.setai` with no arguments for the step-by-step guide."
            )
            return

        ai_config.set_api_key(key)
        await checking.edit(
            "✅ **Gemini API Key validated & saved!**\n\n"
            "🤖 AI is now **ACTIVE** for all plugins.\n"
            "💬 Use `.ai <question>` in Cipher AI\n"
            "🛡️ PM Permit AI Gatekeeper is ready\n\n"
            "💡 Tip: add more free keys with `.addai <key>` to raise your effective daily quota."
        )
        await _auto_delete(event, checking)

    @CipherElite.on(events.NewMessage(outgoing=True, pattern=r"\.addai(?:\s+(.+))?$"))
    @rishabh()
    async def _addai(event):
        raw = event.pattern_match.group(1)
        if not raw:
            await event.reply("❌ **Usage:** `.addai <another_gemini_api_key>`\n\nGet a free one: https://aistudio.google.com/apikey")
            return

        key = raw.strip()
        checking = await event.reply("🔍 **Validating key with Google...**")
        valid = await validate_gemini_key(key)
        if not valid:
            await checking.edit("❌ **This key didn't validate.** Double-check it and try again.")
            return

        added = ai_config.add_key(key)
        if not added:
            await checking.edit("⚠️ **That key is already configured.**")
            return

        await checking.edit(
            f"✅ **Key added to rotation!**\n\n"
            f"🔁 Total keys now: `{ai_config.key_count()}`\n"
            f"Cipher AI will automatically spread requests across all valid keys, "
            f"skipping any that hit a rate limit until it cools down."
        )
        await _auto_delete(event, checking)

    @CipherElite.on(events.NewMessage(outgoing=True, pattern=r"\.rmai(?:\s+(\d+))?$"))
    @rishabh()
    async def _rmai(event):
        arg = event.pattern_match.group(1)
        index = int(arg) if arg else None
        ok = ai_config.remove_key(index)
        if not ok:
            msg = await event.reply(f"❌ No key numbered `{arg}` found. Run `.aikeys` to see valid numbers.")
            await _auto_delete(event, msg)
            return
        label = f"key #{index}" if index else "all keys"
        msg = await event.reply(f"🛑 **Removed {label}.**\n\nRemaining keys: `{ai_config.key_count()}`")
        await _auto_delete(event, msg)

    @CipherElite.on(events.NewMessage(outgoing=True, pattern=r"\.aikeys$"))
    @rishabh()
    async def _aikeys(event):
        entries = ai_config.list_keys()
        if not entries:
            await event.reply("📭 **No keys configured.** Run `.setai` with no arguments for the setup guide.")
            return
        rt = RichText()
        rt.bold(f"🔑 Configured Gemini Keys ({len(entries)})\n\n")
        for e in entries:
            rt.plain(f"#{e['index']} {e['status']}  {e['masked']}  ")
            rt.italic(f"(uses: {e['uses']}, errors: {e['errors']})\n")
        await event.reply(rt.text, formatting_entities=rt.entities)

    @CipherElite.on(events.NewMessage(outgoing=True, pattern=r"\.aistatus$"))
    @rishabh()
    async def _aistatus(event):
        rt = build_status_message()
        await event.reply(rt.text, formatting_entities=rt.entities)

    print(f"✅ AI Setup Plugin v{VERSION} initialized (multi-key rotation ready)")
    return ai_config
