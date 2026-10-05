"""Telegram helpers: safe sending/editing, error guard, handler registration."""
from __future__ import annotations

import functools
import logging

from pyrogram import ContinuePropagation, StopPropagation, filters
from pyrogram.errors import MessageNotModified, RPCError
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

log = logging.getLogger(__name__)
ALLOWED_PREFIXES = "!.,;$#%+-~"           # characters a group may choose with /setprefix
COMMAND_PREFIXES = ["/"] + list(ALLOWED_PREFIXES)
GROUP_TYPES = {"GROUP", "SUPERGROUP"}
prefix_lookup = None  # async fn(chat_id) -> str, installed by handlers.admin
GENERIC_ERROR = "⚠️ Something went wrong. Please try again in a moment."


def guard(fn):
    """Never let a handler exception escape or leak a traceback to users."""
    @functools.wraps(fn)
    async def wrapper(client, update):
        try:
            if prefix_lookup and getattr(update, "command", None) and getattr(update, "text", None) \
                    and update.chat and update.chat.type.name in GROUP_TYPES and update.text[0] != "/":
                if update.text[0] != await prefix_lookup(update.chat.id):
                    return  # not this group's prefix
            await fn(client, update)
        except (StopPropagation, ContinuePropagation):
            raise
        except MessageNotModified:
            pass
        except Exception:
            log.exception("Handler %s failed", fn.__name__)
            try:
                if hasattr(update, "data"):
                    await update.answer(GENERIC_ERROR, show_alert=True)
                else:
                    await update.reply_text(GENERIC_ERROR, quote=True)
            except Exception:
                pass
    return wrapper


def command(app, names, flt=None, group: int = 0):
    f = filters.command(names, prefixes=COMMAND_PREFIXES)
    if flt is not None:
        f = f & flt
    def deco(fn):
        app.add_handler(MessageHandler(guard(fn), f), group)
        return fn
    return deco


def callback(app, pattern: str, group: int = 0):
    def deco(fn):
        app.add_handler(CallbackQueryHandler(guard(fn), filters.regex(pattern)), group)
        return fn
    return deco


async def reply(message, text: str, **kw):
    try:
        return await message.reply_text(text, quote=kw.pop("quote", True), **kw)
    except RPCError as exc:
        log.warning("reply failed (%s); retrying as plain text", type(exc).__name__)
        try:
            return await message.reply_text(text, quote=False, parse_mode=None,
                                            reply_markup=kw.get("reply_markup"))
        except RPCError:
            log.exception("reply failed twice")
            return None


async def edit(message, text: str, **kw):
    try:
        return await message.edit_text(text, **kw)
    except MessageNotModified:
        return message
    except RPCError as exc:
        log.warning("edit failed: %s", type(exc).__name__)
        return None


async def delete_quietly(message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


def arg_text(message) -> str:
    return " ".join(message.command[1:]).strip() if getattr(message, "command", None) else ""


async def edit_any(message, text: str, **kw):
    """Edit a text message or a photo caption, whichever the message is."""
    try:
        return await message.edit_text(text, **kw)
    except MessageNotModified:
        return message
    except RPCError:
        try:
            return await message.edit_caption(text, **kw)
        except MessageNotModified:
            return message
        except RPCError as exc:
            log.warning("edit_any failed: %s", type(exc).__name__)
            return None
