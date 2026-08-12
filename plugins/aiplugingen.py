# =============================================================================
#  CipherElite Userbot Plugin
#
#  Plugin Name:    aiplugingen
#  Version:        1.2.0
#  Author:         CipherElite Dev
#  Target path:    plugins/aiplugingen.py
#
#  What it does:
#  Adds ".newplugin <describe the plugin>" (sudo/owner only).
#  Sends the request to your AI API(s) and, if one provider/model fails or
#  errors out, automatically moves to the next one in the list until it gets
#  a working response. Generated code is saved as a .py file and uploaded to
#  the SAME chat/group the command was used in, together with a summary of
#  the commands it adds and any extra pip requirements it needs.
#
#  v1.1.0 changes:
#  - The system prompt now reads plugins/README.md straight off disk on
#    every run instead of a baked-in copy, so editing the README instantly
#    updates what the AI is told (see the new "Telethon Import Rules"
#    section added there).
#  - NEW: real import validation. After generating code we actually try to
#    import every `telethon.*` symbol the AI used against the Telethon
#    version really installed on this server. If something doesn't exist
#    (e.g. the AI hallucinates `from telethon.tl.types import ReportReason`,
#    which is not a real class), we send the exact ImportError straight
#    back to the AI and ask for a corrected file - up to 2 auto-fix rounds -
#    before ever showing you the file.
#
#  v1.2.0 changes:
#  - NEW: live progress bar. The status message now shows a
#    ▰▰▰▰▰▰▱▱▱▱▱▱▱▱ NN% bar that fills up as it tries providers/models,
#    validates imports, and runs auto-fix rounds, instead of just a plain
#    "Trying..." line.
#
#  IMPORTANT: this only GENERATES and UPLOADS the file for you to review.
#  It does NOT auto-install it into plugins/ or restart the bot - always
#  read AI-generated code before dropping it into a running userbot, since
#  it runs with full account access.
# =============================================================================

import ast
import importlib
import json
import re
import time
from pathlib import Path

import aiohttp
from telethon import events

from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler
from config.config import Config

VERSION = "1.2.0"
CATEGORY = "developer"

OUTPUT_DIR = Path(__file__).parent.parent / "DB" / "generated_plugins"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_FIX_ATTEMPTS = 2  # how many times we let the AI self-correct bad imports

# ---------------------------------------------------------------------------
# Provider configuration - add/remove/reorder entries here to change the
# failover order. Every entry is tried in order until one gives a usable
# response.
# ---------------------------------------------------------------------------
PROVIDERS = [
    {
        "name": "codexapi",
        "type": "get_query",
        "base_url": getattr(Config, "CODEXAPI_URL", "https://chatbot.codexapi.workers.dev"),
        # Full model list from /v1/models on this API, ordered strongest/most
        # code-capable first so the best model is tried before falling back.
        "models": [
            "gpt-5.2",
            "gpt-5.1",
            "gpt-5",
            "o1-preview",
            "o3-mini",
            "chatgpt-4o-latest",
            "anthropic/claude-sonnet-4",
            "deepseek-ai/deepseek-v3.2",
            "deepseek-ai/deepseek-v3.1-terminus",
            "deepseek-ai/deepseek-R1-0528",
            "qwen/qwen3-coder-480b-a35b-instruct",
            "qwen/qwen3.5-397b-a17b",
            "qwen/qwen3-235b-a22b",
            "qwen/qwq-32b",
            "moonshotai/kimi-k2.5",
            "moonshotai/kimi-k2-thinking",
            "moonshotai/kimi-k2-instruct-0905",
            "x-ai/grok-4",
            "google/gemini-2.5-pro-preview-05-06",
            "minimaxai/minimax-m2",
            "mercury-coder",
            "mistralai/mistral-large-3-675b-instruct-2512",
            "accounts/fireworks/models/glm-4p7",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "meta/llama-4-maverick-17b-128e-instruct",
            "meta/llama-4-scout-17b-16e-instruct",
            "meta/llama-3.1-405b-instruct",
            "google/gemma-3-27b-it",
            "nvidia/nemotron-3-nano-30b-a3b",
            "mistralai/magistral-small-2506",
            "mistralai/mistral-small-3.1-24b-instruct-2503",
            "mistralai/ministral-14b-instruct-2512",
            "meta-llama-3.3-70b-instruct",
            "meta-llama-3.1-8b-instruct",
            "meta-llama/Llama-3.1-8B-Instruct",
            "Olmo-3.1-32B-Instruct",
        ],
    },
    {
        "name": "copilotapi",
        "type": "openai_chat",
        "base_url": getattr(Config, "COPILOT_API_URL", "https://copilot-api-delta.vercel.app"),
        "models": ["copilot"],
    },
]

TOTAL_MODELS = sum(len(p["models"]) for p in PROVIDERS)


def _bar(percent) -> str:
    """Render a simple text progress bar, e.g. '▰▰▰▰▰▰▱▱▱▱▱▱▱▱  45%'."""
    percent = max(0, min(100, int(percent)))
    width = 14
    filled = round(width * percent / 100)
    return "▰" * filled + "▱" * (width - filled) + f"  {percent}%"


SYSTEM_PROMPT = r"""You are the plugin-writer for the CipherElite Telegram userbot (Telethon-based).
Follow the project's own plugin development guide EXACTLY - it is reproduced
below in full. Use it to decide normal vs inline plugin, imports, init(),
register_commands(), decorators, formatting and everything else.

===================== plugins/README.md (verbatim) =====================

# 🎭 CipherElite Plugin Development Guide

CipherElite has **two kinds of plugins**:

1. **Normal plugins** — userbot replies directly in the chat (`.reverse`, `.upper`, most commands).
2. **Inline plugins** — userbot sends the message *through the assistant bot* using Telegram inline mode, then hides the "via @bot" tag so it looks native (`.alive`, `.ping`).

Both live in `plugins/*.py` and are auto-loaded on startup — you never register a plugin manually anywhere else.

---

## 1️⃣ Normal Plugin (direct reply)

### Basic Structure

```python
from telethon import events
from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler

def init(client_instance):
    commands = [
        ".command <param> - Description of command"
    ]
    description = "🎭 Plugin Name - Brief description"
    add_handler("plugin_name", commands, description)

async def register_commands():
    @CipherElite.on(events.NewMessage(pattern=r"\.command\s+(.+)"))
    @rishabh()
    async def command_handler(event):
        try:
            param = event.pattern_match.group(1).strip()
            await event.reply("✅ **Success!**")
        except Exception as e:
            await event.reply(f"❌ **Error:** {str(e)}")
```

### Required Components

**Imports**
```python
from telethon import events
from utils.utils import CipherElite       # the userbot client
from utils.decorators import rishabh      # access control
from plugins.bot import add_handler       # registers plugin in .help menu
```

**`init()`** — runs once at startup, registers the plugin so it shows up in `.help`:
```python
def init(client_instance):
    commands = [
        ".cmd <param> - Description"   # full syntax with parameters
    ]
    description = "🎭 Plugin - What it does"
    add_handler("short_name", commands, description)   # keep the name short
```

**`register_commands()`** — must be `async`, this is where you attach the actual `@CipherElite.on(...)` handlers:
```python
async def register_commands():
    @CipherElite.on(events.NewMessage(pattern=r"\.cmd\s+(.+)"))
    @rishabh()
    async def handler(event):
        try:
            await event.reply("🎭 **Cipher Elite Result**\n\n✅ Success")
        except Exception as e:
            await event.reply(f"❌ **Error:** {str(e)}")
```

> The loader (`startup/startup.py`) does exactly this for every file in `plugins/`:
> `module.init(client)` → `await module.register_commands()`. If either is missing/throws, only that plugin fails to load — the rest of the bot keeps running.

**`CATEGORY`** — optional module-level string that groups the plugin in the `.help` category view (`plugins/bot.py` reads it, defaults to `"utilities"` if you don't set one):
```python
CATEGORY = "utilities"   # set once at the top of the file, alongside VERSION
```

Use one of these existing categories (don't invent new ones unless nothing below fits):

| Category | For plugins that... | Example files |
|---|---|---|
| `admin` | Manage groups/members — bans, warns, flood control, broadcasts, greetings | `admin.py`, `antiflood.py`, `autokick.py`, `broadcast.py`, `warn.py` |
| `animations` | Play a text/emoji animation sequence | `animation.py`, `emoji_greetings.py`, `fun_animations.py`, `Shayri.py` |
| `developer` | Dev/ops tooling for the bot itself — installing, updating, sending on behalf of the bot | `install.py`, `send.py`, `updater.py` |
| `fun` | Games, memes, jokes, text effects, stickers, cat/troll stuff | `games.py`, `memestext.py`, `figlet.py`, `quotes.py`, `trolls.py` |
| `media` | Image/video/sticker generation or editing | `carbon.py`, `giftools.py`, `imagetools.py`, `stickertools.py`, `videotools.py` |
| `utilities` | Everything else — info lookups, account tools, general commands | `alive.py`, `afk.py`, `chats.py`, `infos.py`, `stats.py`, `tools.py` |

---

## 2️⃣ Inline Plugin (userbot + assistant bot combo)

Used when you want the message to look like it came straight from the userbot with rich media/buttons, but you're actually letting the **assistant bot** build it via Telegram's inline mode. This is the `.alive` / `.ping` pattern.

### How the flow works

```
.alive typed  ──▶  userbot builds text/media
                    │
                    ▼
        stores it in a global INLINE_DATA dict
                    │
                    ▼
   userbot calls event.client.inline_query(BOT_USERNAME, "alive")
                    │
                    ▼
     assistant bot's @bot.on(events.InlineQuery) handler
     reads INLINE_DATA and returns a photo/article result
                    │
                    ▼
   userbot does results[0].click(chat_id, hide_via=True)
     → sends it as if typed directly (no "via @bot" tag)
                    │
                    ▼
        original ".alive" trigger message is deleted
```

If the assistant bot is offline / not configured, the handler **must** fall back to a plain `event.reply(...)` so the command still works.

### Template

```python
from telethon import events, Button
from plugins.bot import add_handler, bot          # bot = the assistant TelegramClient
from utils.utils import CipherElite
from utils.decorators import rishabh
from config.config import Config

VERSION = "1.0.0"
CATEGORY = "utilities"

# Bridge: userbot writes here, assistant bot reads from here
INLINE_DATA = {
    "mycmd_text": "Hello from Cipher Elite",
    "mycmd_media": None,
}

BUTTONS = [[Button.url("💬 Support", "https://t.me/cipherelite_support")]]


def init(client_instance):
    commands = [".mycmd - Inline example command"]
    add_handler("mycmd", commands, "🎭 My inline command")


async def register_commands():
    # ---- USERBOT SIDE: trigger ----
    @CipherElite.on(events.NewMessage(pattern=r"\.mycmd"))
    @rishabh()
    async def mycmd(event):
        text = f"Hello {event.sender.first_name}, this is inline!"

        global INLINE_DATA
        INLINE_DATA["mycmd_text"] = text
        INLINE_DATA["mycmd_media"] = None   # or a file/URL for a photo result

        try:
            results = await event.client.inline_query(Config.TG_BOT_USERNAME, "mycmd")
            await results[0].click(
                event.chat_id,
                reply_to=event.reply_to_msg_id,
                hide_via=True
            )
            await event.delete()
        except Exception:
            # Fallback: bot unavailable, just reply normally
            await event.reply(text, file=INLINE_DATA["mycmd_media"], parse_mode='html')

    # ---- BOT SIDE: builds the inline result ----
    if bot:
        @bot.on(events.InlineQuery(pattern=r"^mycmd$"))
        async def inline_mycmd(event):
            builder = event.builder
            text = INLINE_DATA["mycmd_text"]
            media = INLINE_DATA["mycmd_media"]

            if media:
                result = builder.photo(media, text=text, parse_mode='html', buttons=BUTTONS)
            else:
                result = builder.article("My Command", text=text, parse_mode='html', buttons=BUTTONS)

            await event.answer([result], cache_time=1)
```

### Key rules for inline plugins
- `INLINE_DATA` keys must be **unique per plugin** — don't reuse `"alive_text"` etc. from other plugins.
- The bot-side `InlineQuery` pattern (e.g. `r"^mycmd$"`) must exactly match the string passed to `event.client.inline_query(Config.TG_BOT_USERNAME, "mycmd")`.
- Always wrap the inline trigger in `try/except` with a plain-text fallback — the assistant bot may be down, unset, or not @-mentionable yet.
- Only build the `@bot.on(...)` handler `if bot:` — `bot` can be `None` if `plugins/bot.py` failed to init the assistant client.
- `.help` itself doesn't follow this per-plugin pattern — it's handled centrally by the catch-all `@bot.on(events.InlineQuery)` in `plugins/bot.py`, so you don't need to touch that for a normal inline plugin.

---

## 🔐 Access Control Decorators

Pick the right one from `utils/decorators.py`:

| Decorator | Who can use it | Behavior on deny |
|---|---|---|
| `@rishabh()` | Owner + sudo users only | Silently ignores the command (no reply) |
| `@rishabh_help()` | Owner + sudo users only | Replies/alerts with an access-denied message — use for callback/inline handlers |
| `@authorized_users_only()` | Owner/sudo, **or** group admins, **or** anyone in a private chat | Sends a "🎭 Access Denied" message |

```python
@CipherElite.on(events.NewMessage(pattern=r"\.ban"))
@authorized_users_only()   # group admins can use this one too
async def ban_handler(event):
    ...
```

> Admin checks are cached for 5 minutes per (chat, user) to keep group commands fast — you don't need to do anything extra for this, it's automatic in the decorator.

---

## Pattern Examples

```python
# Basic command
pattern=r"\.command"

# Required parameter
pattern=r"\.command\s+(.+)"

# Optional parameter
pattern=r"\.command\s*(.*)"

# Multiple parameters
pattern=r"\.command\s+(\w+)\s*(.*)"

# Exact match only (recommended to avoid clashing with similarly-named commands)
pattern=r"^\.command$"
```

⚠️ **Avoid command collisions**: two plugins registering the exact same pattern will *both* fire and double-reply. Run `python3 scan_conflicts2.py` from the project root before committing a new plugin to check for clashes with existing commands.

---

## Message Formatting

```python
# Success message
await event.reply("🎭 **Cipher Elite Success**\n\n"
                 "✅ **Result:** Your result here\n"
                 "🤖 **Powered by Cipher Elite**")

# Error message
await event.reply(f"🎭 **Cipher Elite Error**\n\n"
                 f"❌ **Error:** {str(e)}\n"
                 f"💡 **Try again with correct parameters**")

# Status updates
status = await event.reply("🔄 **Processing...**")
await status.edit("✅ **Complete!**")
```

---

## Complete Example — Normal Plugin

```python
from telethon import events
from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler

def init(client_instance):
    commands = [
        ".reverse <text> - Reverse text with Cipher Elite",
        ".upper <text> - Convert text to uppercase"
    ]
    description = "🎭 Text Tools - Basic text manipulation"
    add_handler("texttools", commands, description)

async def register_commands():
    @CipherElite.on(events.NewMessage(pattern=r"\.reverse\s+(.+)"))
    @rishabh()
    async def reverse_text(event):
        try:
            text = event.pattern_match.group(1).strip()
            result = text[::-1]

            await event.reply("🎭 **Cipher Elite Text Reverser**\n\n"
                            f"📝 **Original:** `{text}`\n"
                            f"🔄 **Reversed:** `{result}`\n"
                            f"✅ **Success!**")
        except Exception as e:
            await event.reply(f"❌ **Error:** {str(e)}")

    @CipherElite.on(events.NewMessage(pattern=r"\.upper\s+(.+)"))
    @rishabh()
    async def upper_text(event):
        try:
            text = event.pattern_match.group(1).strip()
            result = text.upper()

            await event.reply(f"🎭 **Uppercase Result**\n\n`{result}`")
        except Exception as e:
            await event.reply(f"❌ **Error:** {str(e)}")
```

## Complete Example — Inline Plugin

See the full `.mycmd` template in section 2️⃣ above — it's copy-paste ready. For a real reference implementation, read `plugins/alive.py` (`.alive`/`.ping`) end to end.

---

## Quick Checklist

### ✅ Must Have (all plugins)
- [ ] `init()` function with a `commands` list and `add_handler(...)` call
- [ ] `register_commands()` async function
- [ ] Correct decorator (`@rishabh()`, `@rishabh_help()`, or `@authorized_users_only()`)
- [ ] Try/except error handling around any logic that can fail
- [ ] Command syntax documented with `<required>` / `[optional]` parameters

### ✅ Extra for Inline Plugins
- [ ] Unique `INLINE_DATA` keys (not shared with other plugins)
- [ ] Bot-side `@bot.on(events.InlineQuery(pattern=...))` guarded with `if bot:`
- [ ] Fallback `event.reply(...)` if `inline_query()`/`.click()` throws
- [ ] `hide_via=True` on `.click()` so it doesn't show "via @yourbot"

### ✅ Best Practices
- [ ] Short plugin name for the `.help` menu button
- [ ] Cipher Elite branding in messages
- [ ] Clear parameter descriptions
- [ ] Input validation
- [ ] Ran `scan_conflicts2.py` to check for duplicate command patterns

---

## Quick Start

1. **Create file:** `plugins/myplugin.py`
2. **Pick a type:** normal (direct reply) or inline (via assistant bot)
3. **Copy the matching template above**
4. **Replace:** plugin name, commands, logic
5. **Check for conflicts:** `python3 scan_conflicts2.py`
6. **Test:** restart the bot, use `.help myplugin`
7. **Deploy:** commands work automatically — no manual registration needed anywhere else

Your plugin will appear in the `.help` menu and support direct access via `.help myplugin`!

===================== end of plugins/README.md =====================

Output rules for THIS request:
- Output ONE self-contained file for plugins/<name>.py and NOTHING else -
  no explanations before or after, no markdown prose, just the python file
  (you may wrap it in a single ```python code block).
- Follow every checklist item in the "Quick Checklist" section above.
- Always set a module-level `CATEGORY = "..."` right under `VERSION`, choosing
  the single best match from this exact set (see the "Plugin Category"
  table above for what each one means and example files):
  admin, animations, developer, fun, media, utilities.
  Do NOT invent a new category name - if nothing fits well, use "utilities".

Telethon import safety (Telethon==1.37.0 is what is actually installed):
- Never import a `telethon.tl.types` / `telethon.tl.functions` / `telethon.errors`
  symbol unless you are 100% certain it exists in Telethon 1.37.0.
- Prefer high-level `client`/`CipherElite` methods over raw MTProto requests
  whenever possible - they are stable across versions:
  client.send_message, client.edit_message, client.delete_messages,
  client.get_entity, client.kick_participant, client.edit_permissions,
  client.download_media, client.send_file.
- If a raw request is genuinely required, only use imports from this
  confirmed-working list (pulled from working plugins already in this
  codebase):
  from telethon import events, Button, functions, types, utils, errors, version, TelegramClient
  from telethon.errors import FloodWaitError, ChatAdminRequiredError, RPCError, UserAdminInvalidError, ChatWriteForbiddenError, UserBannedInChannelError, UserNotParticipantError, BotInlineDisabledError
  from telethon.errors.rpcerrorlist import MessageNotModifiedError, YouBlockedUserError
  from telethon.tl import functions, types
  from telethon.tl.functions.channels import CreateChannelRequest, DeleteChannelRequest, EditPhotoRequest, EditTitleRequest, EditBannedRequest, GetAdminedPublicChannelsRequest, GetFullChannelRequest, GetParticipantsRequest, GetParticipantRequest, InviteToChannelRequest
  from telethon.tl.functions.messages import ExportChatInviteRequest, CreateChatRequest, GetFullChatRequest, AddChatUserRequest, GetHistoryRequest
  from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest, GetUserPhotosRequest
  from telethon.tl.functions.users import GetFullUserRequest
  from telethon.tl.types import ChannelParticipantsAdmins, PeerUser, ChatBannedRights, DocumentAttributeSticker, InputStickerSetShortName, InputMediaDice, InputPhoto, MessageMediaPhoto, MessageMediaDocument, MessageEntityMention, MessageEntityMentionName, User, Chat, Channel
  from telethon.utils import get_display_name, get_peer_id, pack_bot_file_id, get_input_location
- If a feature (like reporting a user/message) needs a symbol NOT on that
  list, do not guess the name - write the plugin using only high-level
  methods, or add a short "# TODO:" comment saying the raw API name needs
  to be verified by a human instead of importing something unconfirmed.
- Never write `from telethon.tl.types import ReportReason` - it does not
  exist. Telegram's report API instead takes one of
  InputReportReasonSpam, InputReportReasonViolence, InputReportReasonPornography,
  InputReportReasonChildAbuse, InputReportReasonOther, InputReportReasonCopyright,
  InputReportReasonGeoIrrelevant, InputReportReasonFake, InputReportReasonIllegalDrugs,
  InputReportReasonPersonalDetails (each is its own class under telethon.tl.types).

- Never write code that mass-messages, mass-adds/invites, spams, floods,
  scrapes credentials, or otherwise abuses Telegram/third parties - refuse
  that part of a request and generate a safe stub with a comment explaining
  why, instead.

At the very end of the file, add this metadata block as plain comments
(fill it in truthfully based on the code you wrote):

# ============ METADATA ============
# PLUGIN_NAME: <short_snake_case_name>
# COMMANDS:
# .cmd1 <args> - what it does
# .cmd2 - what it does
# REQUIREMENTS:
# pip_package_one
# pip_package_two
# (write "none" if only stdlib/telethon is needed)
# ===================================
"""

def _extract_code(raw: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else raw.strip()


def _extract_metadata(code: str):
    name_match = re.search(r"#\s*PLUGIN_NAME:\s*(\S+)", code)
    plugin_name = name_match.group(1).strip() if name_match else None

    cmds_match = re.search(r"#\s*COMMANDS:\s*\n((?:#.*\n?)*?)#\s*REQUIREMENTS:", code)
    commands_block = ""
    if cmds_match:
        commands_block = "\n".join(
            line.lstrip("#").strip() for line in cmds_match.group(1).splitlines() if line.strip("# ").strip()
        )

    reqs_match = re.search(r"#\s*REQUIREMENTS:\s*\n((?:#.*\n?)*?)#\s*=+", code)
    requirements = []
    if reqs_match:
        for line in reqs_match.group(1).splitlines():
            item = line.lstrip("#").strip()
            if item and item.lower() != "none":
                requirements.append(item)

    if not plugin_name:
        fn_match = re.search(r'add_handler\(\s*"([a-zA-Z0-9_]+)"', code)
        plugin_name = fn_match.group(1) if fn_match else f"aiplugin_{int(time.time())}"

    cat_match = re.search(r'^CATEGORY\s*=\s*["\']([a-zA-Z_]+)["\']', code, re.MULTILINE)
    category = cat_match.group(1) if cat_match else "utilities"

    return plugin_name, commands_block, requirements, category


def _validate_telethon_imports(code: str):
    """Actually try every `telethon.*` import the generated code uses against
    the REAL Telethon package installed on this server, and report anything
    that doesn't really exist - this is exactly what catches things like
    `from telethon.tl.types import ReportReason` before you ever see the file."""
    problems = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"SyntaxError while parsing generated file: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("telethon"):
            try:
                mod = importlib.import_module(node.module)
            except Exception as e:
                problems.append(f"cannot import module '{node.module}': {e}")
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if not hasattr(mod, alias.name):
                    problems.append(f"cannot import name '{alias.name}' from '{node.module}'")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("telethon"):
                    try:
                        importlib.import_module(alias.name)
                    except Exception as e:
                        problems.append(f"cannot import module '{alias.name}': {e}")

    return problems


async def _call_get_query(session, base_url, model, full_prompt):
    url = f"{base_url.rstrip('/')}/"
    params = {"prompt": full_prompt, "model": model}
    async with session.get(url, params=params, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        text = await resp.text()
        try:
            data = json.loads(text)
        except Exception:
            return text
        for key in ("response", "text", "content", "message", "output", "result", "answer"):
            if isinstance(data, dict) and data.get(key):
                val = data[key]
                return val if isinstance(val, str) else json.dumps(val)
        return json.dumps(data)


async def _call_openai_chat(session, base_url, model, system_prompt, user_prompt):
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    async with session.post(url, json=payload, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return data["choices"][0]["message"]["content"]


async def _generate(system_prompt: str, user_prompt: str, status_cb=None, band=(5, 55)):
    """Try every provider/model in order, return (raw_text, provider_name, model).
    `band` is the (start_percent, end_percent) slice of the overall progress
    bar this call is allowed to move through, so multiple _generate() calls
    (initial attempt + auto-fix rounds) can share one continuous bar."""
    combined_prompt = f"{system_prompt}\n\nUser request: {user_prompt}"
    band_start, band_end = band
    tried = 0

    async with aiohttp.ClientSession() as session:
        for provider in PROVIDERS:
            for model in provider["models"]:
                tried += 1
                pct = band_start + (band_end - band_start) * (tried / TOTAL_MODELS)
                if status_cb:
                    await status_cb(f"🔄 Trying `{provider['name']}` → `{model}` ...", pct)
                try:
                    if provider["type"] == "get_query":
                        raw = await _call_get_query(session, provider["base_url"], model, combined_prompt)
                    elif provider["type"] == "openai_chat":
                        raw = await _call_openai_chat(
                            session, provider["base_url"], model, system_prompt, user_prompt
                        )
                    else:
                        continue

                    if raw and raw.strip():
                        return raw, provider["name"], model
                except Exception:
                    continue  # this provider/model failed, fall through to next

    return None, None, None


async def _generate_with_validation(user_prompt: str, status_cb=None):
    """Full pipeline: generate -> validate real telethon imports -> if broken,
    send the exact error back to the AI and ask for a fix, up to
    MAX_FIX_ATTEMPTS times. Returns (code, provider_name, model, problems).

    The whole pipeline is mapped onto one continuous 0-100% progress bar:
      0-3%    prepping the request
      3-55%   first generation attempt (across all providers/models)
      55-62%  extracting + validating imports
      62-90%  auto-fix rounds (split evenly across MAX_FIX_ATTEMPTS)
      90-100% wrapping up (handled by the caller once the file is uploaded)
    """
    system_prompt = SYSTEM_PROMPT

    if status_cb:
        await status_cb("🧠 Preparing request...", 3)

    raw, provider_name, model = await _generate(system_prompt, user_prompt, status_cb, band=(5, 55))
    if not raw:
        return None, None, None, []

    if status_cb:
        await status_cb("📦 Extracting generated code...", 57)
    code = _extract_code(raw)

    if status_cb:
        await status_cb("🔍 Validating telethon imports...", 60)
    problems = _validate_telethon_imports(code)

    fix_span = (90 - 62) / max(MAX_FIX_ATTEMPTS, 1)
    attempt = 0
    while problems and attempt < MAX_FIX_ATTEMPTS:
        band_start = 62 + fix_span * attempt
        band_end = 62 + fix_span * (attempt + 1)
        attempt += 1
        if status_cb:
            await status_cb(
                f"🛠️ Found {len(problems)} bad import(s) — asking the AI to fix "
                f"(attempt {attempt}/{MAX_FIX_ATTEMPTS})...",
                band_start,
            )

        fix_prompt = (
            f"{user_prompt}\n\n"
            "Your previous version of this file has REAL import errors when actually "
            "tested against the Telethon library installed on the server:\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\nHere is your previous code:\n```python\n"
            + code
            + "\n```\n\nReturn the FULL corrected file (same output rules as before), "
            "fixing ONLY these import problems - use real Telethon symbols that "
            "actually exist, or switch to a high-level client method instead."
        )

        raw, provider_name, model = await _generate(
            system_prompt, fix_prompt, status_cb, band=(band_start, band_end)
        )
        if not raw:
            break
        code = _extract_code(raw)
        if status_cb:
            await status_cb("🔍 Re-validating imports...", band_end)
        problems = _validate_telethon_imports(code)

    return code, provider_name, model, problems


def init(client_instance):
    commands = [
        ".newplugin <describe the plugin> - AI-generate a plugin .py file and upload it here",
    ]
    description = (
        "🤖 **AI Plugin Generator**\n"
        "🧠 Describe a plugin in plain text, get a ready .py file back\n"
        "🔁 Automatically fails over across multiple AI APIs/models\n"
        "✅ Self-checks & auto-fixes broken telethon imports before delivery\n"
        "📊 Live progress bar while it works\n"
        "📦 Also lists commands & pip requirements in the caption"
    )
    add_handler("aiplugingen", commands, description)


async def register_commands():
    @CipherElite.on(events.NewMessage(pattern=r"\.newplugin\s+(.+)", outgoing=True))
    @rishabh()
    async def new_plugin(event):
        user_prompt = event.pattern_match.group(1).strip()
        status = await event.reply(
            f"🎭 **Cipher Elite AI Plugin Generator**\n\n{_bar(0)}\n🔄 Starting..."
        )

        last_pct = {"value": 0}

        async def status_cb(msg, percent=None):
            try:
                pct = int(percent) if percent is not None else last_pct["value"]
                last_pct["value"] = pct
                await status.edit(f"🎭 **Cipher Elite AI Plugin Generator**\n\n{_bar(pct)}\n{msg}")
            except Exception:
                pass

        try:
            code, provider_name, model, problems = await _generate_with_validation(user_prompt, status_cb)
        except Exception as e:
            await status.edit(f"❌ **Unexpected error:** `{e}`")
            return

        if not code:
            await status.edit(
                f"{_bar(0)}\n\n"
                "❌ **Failed.** All configured AI providers/models were unreachable or errored out.\n"
                "Check your API URLs/keys or try again later."
            )
            return

        await status_cb("📝 Reading commands, category & requirements...", 93)
        plugin_name, commands_block, requirements, category = _extract_metadata(code)

        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", plugin_name)[:40] or f"aiplugin_{int(time.time())}"
        file_path = OUTPUT_DIR / f"{safe_name}.py"
        file_path.write_text(code, encoding="utf-8")

        await status_cb("📤 Uploading file...", 97)

        caption_lines = [
            "🎭 **Cipher Elite - AI Generated Plugin**",
            "",
            f"🧠 **Generated by:** `{provider_name}` / `{model}`",
            f"📄 **Suggested filename:** `plugins/{safe_name}.py`",
            f"🏷️ **Category:** `{category}`",
            "",
            "⚙️ **Commands:**",
            f"`{commands_block}`" if commands_block else "_(see file - none declared)_",
            "",
            "📦 **Extra pip requirements:**",
            ("`pip install " + " ".join(requirements) + "`") if requirements else "_None - stdlib/telethon only_",
            "",
        ]

        if problems:
            caption_lines += [
                f"⚠️ **{len(problems)} import issue(s) could NOT be auto-fixed after "
                f"{MAX_FIX_ATTEMPTS} attempts - fix these manually before using:**",
            ]
            caption_lines += [f"• `{p}`" for p in problems]
            caption_lines.append("")
        else:
            caption_lines.append("✅ **All `telethon.*` imports verified against the installed library.**")

        caption_lines.append("⚠️ **Still review the code before adding it to `plugins/` and restarting the bot.**")
        caption = "\n".join(caption_lines)

        try:
            await event.client.send_file(
                event.chat_id,
                str(file_path),
                caption=caption,
                reply_to=event.reply_to_msg_id,
                parse_mode="md",
            )
            await status.delete()
        except Exception as e:
            await status.edit(f"❌ **Generated but failed to upload:** `{e}`\nSaved locally at `{file_path}`")
