"""/profile and /stats with a small page switcher (profile:pg:<page>:<owner>)."""
from __future__ import annotations

import logging

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from handlers.inline import article, provider, rid
from utils.announce import send_art
from utils.formatting import SEP, bar, clean, header, mention, num
from utils.permissions import soft_gate
from utils.targets import resolve_target
from utils.tg import callback, command, edit, reply
from services.economy_service import xp_for_level

log = logging.getLogger(__name__)


async def build_profile(ctx, uid: int, name: str, page: str = "main") -> tuple:
    s = await ctx.progress.stats(uid)
    fav = await ctx.waifus.favorite_waifu(uid)
    base, nxt = xp_for_level(s["level"]), xp_for_level(s["level"] + 1)
    if page == "stats":
        text = "\n".join([
            header("📊 **STATS**"), "", f"👤 **{name}**", "",
            f"🎵 Songs played: **{num(s['songs'])}**", f"🌸 Waifus collected: **{num(s['waifus'])}**",
            f"🎴 Gacha pulls: **{num(s['pulls'])}**", f"💰 Coins earned: **{num(s['coins_earned'])}**",
            f"💎 Gems earned: **{num(s['gems_earned'])}**", f"🏆 Achievements: **{s['achievements']}**",
            f"🔥 Daily streak: **{s['streak']}**", f"💬 Messages: **{num(s['messages'])}**"])
    else:
        text = "\n".join([
            header("👤 **PROFILE**"), "", f"**{name}**", "",
            f"🎚 Level: **{s['level']}**", f"✨ XP: **{num(s['xp'])}** {bar(s['xp'] - base, nxt - base, 8)}",
            f"💰 Coins: **{num(s['coins'])}**", f"💎 Gems: **{num(s['gems'])}**",
            f"🌸 Waifus: **{num(s['waifus'])}**", f"🎵 Songs played: **{num(s['songs'])}**",
            f"🏆 Achievements: **{s['achievements']}**",
            f"💖 Favorite: **{clean(fav.name, 50) if fav else 'not set'}**", SEP])
    kb = M([[B(("• " if page == "main" else "") + "👤 Profile", callback_data=f"profile:pg:main:{uid}"),
             B(("• " if page == "stats" else "") + "📊 Stats", callback_data=f"profile:pg:stats:{uid}")]])
    return text, kb, fav


def register(app, ctx):
    async def show(message, page: str):
        if not await soft_gate(ctx, message):
            return
        uid, name = message.from_user.id, mention(message.from_user)
        target, tname, _ = await resolve_target(ctx, message)
        if target:
            uid, name = target, tname
        text, kb, fav = await build_profile(ctx, uid, name, page)
        if fav and page == "main":
            sent = await send_art(ctx, message.chat.id, fav.image_url, text, kb, reply_to=message.id)
            if sent is not None:
                return
        await reply(message, text, reply_markup=kb)

    @command(app, "profile")
    async def profile_cmd(_, message):
        await show(message, "main")

    @command(app, "stats")
    async def stats_cmd(_, message):
        await show(message, "stats")

    @callback(app, r"^profile:pg:(main|stats):\d{1,15}$")
    async def profile_cb(_, cb):
        _, _, page, uid = cb.data.split(":")
        user = await ctx.db.fetchone("SELECT username, first_name FROM users WHERE user_id=?", (int(uid),))
        if not user:
            return await cb.answer("Profile not found.", show_alert=True)
        name = ("@" + clean(user["username"], 40)) if user["username"] else clean(user["first_name"] or "User", 40)
        text, kb, _ = await build_profile(ctx, int(uid), name, page)
        await cb.answer()
        try:
            await cb.message.edit_text(text, reply_markup=kb)
        except Exception:
            try:
                await cb.message.edit_caption(text, reply_markup=kb)
            except Exception:
                pass


@provider
async def profile_provider(ctx, query: str, user=None):
    if user is None or query.strip().lower() != "profile":
        return []
    await ctx.db.ensure_user(user.id, user.username, user.first_name)
    text, _, _ = await build_profile(ctx, user.id, mention(user), "main")
    return [article(rid("p", user.id), "👤 My profile", "Share your level, coins and collection", text)]
