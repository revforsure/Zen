"""Shared message helpers used by several handler modules."""
from __future__ import annotations

import logging

from utils.formatting import clean

log = logging.getLogger(__name__)


async def send_art(ctx, chat_id: int, url: str, caption: str, markup=None, reply_to: int | None = None):
    """Send character artwork with caption; falls back to plain text if the image can't be sent."""
    try:
        msg = await ctx.images.send_photo(ctx.bot, chat_id, url, caption, markup, reply_to)
        if msg is not None:
            return msg
    except Exception as exc:  # noqa: BLE001
        log.info("Art send failed (%s); using text", type(exc).__name__)
    try:
        kw = {"reply_to_message_id": reply_to} if reply_to else {}
        return await ctx.bot.send_message(chat_id, caption, reply_markup=markup, **kw)
    except Exception as exc:  # noqa: BLE001
        log.warning("Text fallback failed: %s", type(exc).__name__)
        return None


async def announce_achievements(ctx, chat_id: int, user_id: int, who: str) -> None:
    try:
        new = await ctx.progress.check_achievements(user_id)
    except Exception:
        log.exception("Achievement check failed")
        return
    if new:
        body = "\n".join(f"• {clean(n, 80)}" for n in new)
        try:
            await ctx.bot.send_message(chat_id, f"🏆 **ACHIEVEMENT UNLOCKED** — {clean(who, 40)}\n\n{body}")
        except Exception:
            pass


def level_up_text(who: str, level: int) -> str:
    return f"🎉 **LEVEL UP!** {clean(who, 40)} reached **level {level}** (+{100} 💰)"
