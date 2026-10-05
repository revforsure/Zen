"""Polished /start screen."""
from __future__ import annotations

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from handlers.profile import build_profile
from utils.formatting import clean, header, mention
from utils.strings import t
from utils.tg import callback, command, edit_any, reply


def start_text(name: str) -> str:
    return f"{header(f'✦ **{clean(name, 30).upper()}**')}\n\n{t('start_tag')}\n\n{t('start_body')}"


def start_kb(bot_username: str) -> M:
    rows = [[B("🎵 Music", callback_data="help:cat:music"), B("🌸 Waifus", callback_data="help:cat:waifu")],
            [B("🎴 Gacha", callback_data="help:cat:gacha"), B("🎮 Games", callback_data="help:cat:fun")],
            [B("👤 Profile", callback_data="start:profile"), B("📚 Help", callback_data="help:home")]]
    if bot_username:
        rows.append([B("➕ Add me to a group", url=f"https://t.me/{bot_username}?startgroup=true")])
    return M(rows)


def register(app, ctx):
    @command(app, "start")
    async def start_cmd(_, message):
        if message.from_user:
            await ctx.db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        text, kb = start_text(ctx.config.bot_name), start_kb(ctx.bot_username)
        art = await ctx.images.send_card(ctx.bot, message.chat.id,
                                         f"{ctx.config.bot_name} anime music bot welcome banner, abstract, no characters, no text",
                                         text, kb)
        if art is None:
            await reply(message, text, reply_markup=kb)

    @callback(app, r"^start:profile$")
    async def start_profile_cb(_, cb):
        await ctx.db.ensure_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        text, kb, _ = await build_profile(ctx, cb.from_user.id, mention(cb.from_user), "main")
        await cb.answer()
        await edit_any(cb.message, text, reply_markup=kb)
