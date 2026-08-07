# =============================================================================
#  CipherElite Userbot Plugin
#
#  Plugin Name:    voice_chat
#  Description:    Start, end, and invite users to group voice chats.
#  Created:        08/08/2026
# =============================================================================

VERSION = "1.0.0"
CATEGORY = "utilities"

from telethon import events
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.phone import (
    CreateGroupCallRequest,
    DiscardGroupCallRequest,
    GetGroupCallRequest,
    InviteToGroupCallRequest,
)
from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler


def init(client_instance):
    commands = [
        ".startvc - Start a voice chat in the current group",
        ".endvc - End the voice chat in the current group",
        ".vcinvite - Invite all non-bot members to the voice chat",
    ]
    description = "🎤 CipherElite Voice Chat – Control group voice chats"
    add_handler("voice_chat", commands, description)


async def get_vc_call(event):
    """Get the current voice chat call object."""
    chat_full = await event.client(GetFullChannelRequest(event.chat_id))
    if not chat_full.full_chat.call:
        return None
    return await event.client(GetGroupCallRequest(chat_full.full_chat.call))


def chunk_list(lst, chunk_size):
    """Yield successive chunk_size-sized chunks from lst."""
    for i in range(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


async def register_commands():

    @CipherElite.on(events.NewMessage(pattern=r"\.startvc$"))
    @rishabh()
    async def start_vc_cmd(event):
        try:
            await event.client(CreateGroupCallRequest(event.chat_id))
            await event.reply("🔊 **Voice Chat Started Successfully**")
            await event.delete()
        except Exception as e:
            await event.reply(f"❌ **Error:** `{str(e)}`")

    @CipherElite.on(events.NewMessage(pattern=r"\.endvc$"))
    @rishabh()
    async def end_vc_cmd(event):
        try:
            vc_call = await get_vc_call(event)
            if not vc_call:
                await event.reply("❌ No active voice chat found.")
                return
            await event.client(DiscardGroupCallRequest(vc_call))
            await event.reply("📍 **Voice Chat Ended Successfully**")
            await event.delete()
        except Exception as e:
            await event.reply(f"❌ **Error:** `{str(e)}`")

    @CipherElite.on(events.NewMessage(pattern=r"\.vcinvite$"))
    @rishabh()
    async def invite_vc_cmd(event):
        try:
            # Get the current voice chat call
            vc_call = await get_vc_call(event)
            if not vc_call:
                await event.reply("❌ No active voice chat found. Start one with `.startvc` first.")
                return

            status = await event.reply("🧐 Inviting users to voice chat...")

            # Collect all non-bot user IDs
            users = []
            async for user in event.client.iter_participants(event.chat_id):
                if not user.bot:
                    users.append(user.id)

            if not users:
                await status.edit("❌ No non-bot users found in this group.")
                return

            # Invite in chunks of 6 (Telegram limit)
            invited_count = 0
            for chunk in chunk_list(users, 6):
                try:
                    await event.client(
                        InviteToGroupCallRequest(call=vc_call, users=chunk)
                    )
                    invited_count += len(chunk)
                except Exception:
                    # Continue with next chunk if one fails
                    pass

            await status.edit(f"🚀 **Invited** `{invited_count}` **users to the voice chat.**")
            await event.delete()
        except Exception as e:
            await event.reply(f"❌ **Error:** `{str(e)}`")
