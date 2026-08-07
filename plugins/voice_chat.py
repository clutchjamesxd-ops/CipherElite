# =============================================================================
#  CipherElite Userbot Plugin
#
#  Plugin Name:    voice_chat
#  Description:    Start, end, invite all members, and join voice chats.
#  Created:        08/08/2026
# =============================================================================

VERSION = "1.0.0"
CATEGORY = "utilities"

from telethon import events
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.phone import (
    CreateGroupCallRequest,
    DiscardGroupCallRequest,
    InviteToGroupCallRequest,
    JoinGroupCallRequest,
)
from utils.utils import CipherElite
from utils.decorators import rishabh
from plugins.bot import add_handler


def init(client_instance):
    commands = [
        ".startvc - Start a voice chat",
        ".endvc - End the voice chat in the current group",
        ".vcinvite - Invite ALL members to the voice chat",
        ".joinvc - Make your userbot join the active voice chat",
    ]
    description = "🎤 CipherElite Voice Chat"
    add_handler("voice_chat", commands, description)


async def get_vc_call(event):
    """Return the current voice chat call object, or None if none exists."""
    try:
        chat_full = await event.client(GetFullChannelRequest(event.chat_id))
        return chat_full.full_chat.call
    except Exception:
        return None


def chunk_list(lst, chunk_size):
    for i in range(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


async def register_commands():

    @CipherElite.on(events.NewMessage(pattern=r"\.startvc$"))
    @rishabh()
    async def start_vc_cmd(event):
        try:
            if await get_vc_call(event):
                await event.reply("🔊 **Voice chat is already active.**")
                await event.delete()
                return

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
            vc_call = await get_vc_call(event)
            if not vc_call:
                await event.reply("❌ No active voice chat found. Start one with `.startvc` first.")
                return

            status = await event.reply("🧐 Inviting all members to voice chat...")

            # Get ALL participants, including bots
            users = []
            async for user in event.client.iter_participants(event.chat_id):
                users.append(user.id)

            if not users:
                await status.edit("❌ No members found in this group.")
                return

            invited_count = 0
            for chunk in chunk_list(users, 6):
                try:
                    await event.client(
                        InviteToGroupCallRequest(call=vc_call, users=chunk)
                    )
                    invited_count += len(chunk)
                except Exception:
                    pass

            await status.edit(f"🚀 **Invited** `{invited_count}` **members to the voice chat.**")
            await event.delete()
        except Exception as e:
            await event.reply(f"❌ **Error:** `{str(e)}`")

    @CipherElite.on(events.NewMessage(pattern=r"\.joinvc$"))
    @rishabh()
    async def join_vc_cmd(event):
        try:
            vc_call = await get_vc_call(event)
            if not vc_call:
                await event.reply("❌ No active voice chat found. Start one with `.startvc` first.")
                return

            await event.client(
                JoinGroupCallRequest(
                    call=vc_call,
                    muted=True,
                    video_stopped=True,
                    invite_hash=None,
                )
            )
            await event.reply("✅ **Joined the voice chat.**")
            await event.delete()
        except Exception as e:
            await event.reply(f"❌ **Error:** `{str(e)}`")
