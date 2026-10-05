"""Makes sure the assistant (user) account is a member of the group before joining its voice chat."""
from __future__ import annotations

import logging

from services.player import VoiceError

log = logging.getLogger(__name__)


def make_assistant_check(ctx):
    joined: set[int] = set()

    async def ensure(chat_id: int) -> None:
        from pyrogram.errors import UserAlreadyParticipant, UserNotParticipant
        if chat_id in joined:
            return
        try:
            await ctx.assistant.get_chat_member(chat_id, "me")
            joined.add(chat_id)
            return
        except UserNotParticipant:
            pass
        except Exception as exc:  # noqa: BLE001
            log.info("Assistant membership check inconclusive: %s", type(exc).__name__)
        try:
            chat = await ctx.bot.get_chat(chat_id)
            link = f"https://t.me/{chat.username}" if getattr(chat, "username", None) \
                else await ctx.bot.export_chat_invite_link(chat_id)
            await ctx.assistant.join_chat(link)
        except UserAlreadyParticipant:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("Assistant could not join chat %s: %s", chat_id, type(exc).__name__)
            raise VoiceError(
                "I couldn't add my assistant account to this group. Make me an admin with the "
                "'Invite users' permission, or add the assistant account manually.", fatal=True) from exc
        joined.add(chat_id)
        log.info("Assistant joined chat %s", chat_id)

    return ensure
