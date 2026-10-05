"""Gacha: banners, single/10 pulls, edit-based animation with a Skip button."""
from __future__ import annotations

import asyncio
import logging

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from config import RARITY_ORDER
from services.economy_service import EconomyError
from utils.announce import announce_achievements, level_up_text, send_art
from utils.formatting import SEP, clean, header, mention, num, rarity_line
from utils.permissions import cb_gate, soft_gate
from utils.tg import callback, command, delete_quietly, edit, reply

log = logging.getLogger(__name__)
BANNER_EMOJI = {"anime_dreams": "🌸", "battle": "⚔️", "classic": "💮"}
EPIC_IDX = RARITY_ORDER.index("EPIC")


def register(app, ctx):
    gacha = ctx.gacha

    async def current_banner(user_id: int) -> str:
        row = await ctx.db.fetchone("SELECT value FROM user_settings WHERE user_id=? AND key='banner'", (user_id,))
        if row and await gacha.banner(row["value"]):
            return row["value"]
        return "classic"

    async def menu_text(user_id: int, banner_id: str):
        bal = await ctx.economy.balance(user_id)
        banners = await gacha.banners()
        lines = [header("🎴 **GACHA**"), ""]
        for b in banners:
            mark = "▶️ " if b["id"] == banner_id else ""
            lines.append(f"{mark}{BANNER_EMOJI.get(b['id'], '🎴')} **{clean(b['name'], 40)}**\n      {clean(b['description'], 60)} • {b['cost_gems']} 💎")
        lines += ["", f"💎 You have **{num(bal['gems'])}** gems", f"{SEP}", "__Pick a banner, then pull.__"]
        kb = [[B(f"{BANNER_EMOJI.get(b['id'], '🎴')} {b['name'].split()[0].title()}",
                 callback_data=f"gacha:banner:{b['id']}:{user_id}") for b in banners]]
        kb.append([B("🎴 Pull ×1", callback_data=f"gacha:pull:{banner_id}:1:{user_id}"),
                   B("🎴 Pull ×10", callback_data=f"gacha:pull:{banner_id}:10:{user_id}")])
        return "\n".join(lines), M(kb)

    @command(app, "gacha")
    async def gacha_cmd(_, message):
        if not await soft_gate(ctx, message, "gacha"):
            return
        uid = message.from_user.id
        text, kb = await menu_text(uid, await current_banner(uid))
        banner = await gacha.banner(await current_banner(uid))
        art = await ctx.images.send_card(ctx.bot, message.chat.id, f"anime gacha banner, {banner['name']}, "
                                         f"colorful abstract background, no characters", text, kb) if banner else None
        if art is None:
            await reply(message, text, reply_markup=kb)

    @callback(app, r"^gacha:banner:[a-z0-9_]{1,24}:\d{1,15}$")
    async def banner_cb(_, cb):
        _, _, bid, owner = cb.data.split(":")
        if int(owner) != cb.from_user.id:
            return await cb.answer("Open your own menu with /gacha.", show_alert=True)
        if not await gacha.banner(bid):
            return await cb.answer("That banner isn't available.", show_alert=True)
        await ctx.db.execute("INSERT INTO user_settings (user_id,key,value) VALUES (?,'banner',?) "
                             "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value", (cb.from_user.id, bid))
        text, kb = await menu_text(cb.from_user.id, bid)
        await cb.answer()
        try:
            await cb.message.edit_text(text, reply_markup=kb)
        except Exception:
            try:
                await cb.message.edit_caption(text, reply_markup=kb)
            except Exception:
                pass

    # ---- the pull ------------------------------------------------------------
    async def do_pull(chat_id: int, user, banner_id: str, count: int, reply_to: int | None, answer=None):
        uid = user.id
        wait = await ctx.db.check_cooldown(uid, "gacha", 3)
        if wait:
            msg = f"⏳ Easy there — wait {int(wait) + 1}s."
            return await answer(msg) if answer else await ctx.bot.send_message(chat_id, msg)
        try:
            out = await gacha.pull(uid, banner_id, count)
        except EconomyError as exc:
            await ctx.db.execute("DELETE FROM cooldowns WHERE user_id=? AND key='gacha'", (uid,))
            msg = f"❌ {clean(exc, 200)}"
            return await answer(msg) if answer else await ctx.bot.send_message(chat_id, msg)
        best = out["best"]["waifu"]
        frames = ["✨ **The portal opens...**"]
        if RARITY_ORDER.index(best.rarity) >= EPIC_IDX:
            frames.append("🌈 **RAINBOW AURA!**")
        frames.append(f"🌸 **{best.rarity}!**")
        kb = M([[B("⏩ Skip", callback_data=f"gacha:skip:{uid}")]])
        anim = await ctx.bot.send_message(chat_id, "🎴 **SUMMONING...**", reply_markup=kb)
        key = (chat_id, anim.id)
        for frame in frames:
            await asyncio.sleep(0.9)
            if key in ctx.gacha_skip:
                break
            await edit(anim, frame, reply_markup=kb)
        ctx.gacha_skip.discard(key)
        await delete_quietly(anim)

        who = mention(user)
        banner = out["banner"]
        if count == 1:
            w = best
            text = (f"🎴 **GACHA RESULT**\n\nYou pulled:\n\n🌸 **{clean(w.name, 80)}**\n📺 {clean(w.anime, 80)}\n"
                    f"⭐ **{rarity_line(w.rarity)}**\n\n💰 Value: **{num(w.value)}**\n{SEP}\n"
                    f"{BANNER_EMOJI.get(banner['id'], '🎴')} {clean(banner['name'], 40)} • -{out['cost']} 💎")
        else:
            ordered = sorted(out["results"], key=lambda r: -RARITY_ORDER.index(r["waifu"].rarity))
            lines = [f"{r['waifu'].rarity:<9} {clean(r['waifu'].name, 28)}" for r in ordered]
            text = (f"🎴 **GACHA RESULT ×10**\n\n" + "\n".join(f"`{ln}`" for ln in lines) +
                    f"\n\n🌟 Best: **{clean(best.name, 60)}** — {clean(best.anime, 50)}\n{SEP}\n"
                    f"{clean(banner['name'], 40)} • -{out['cost']} 💎")
        text += f"\n\n__Pity: {out['pity']}/{ctx.config.pity_threshold}__"
        await send_art(ctx, chat_id, best.image_url, text, reply_to=reply_to)
        if out["level_up"]:
            await ctx.bot.send_message(chat_id, level_up_text(who, out["level_up"]))
        await ctx.progress.add_progress(uid, "pull", count)
        await announce_achievements(ctx, chat_id, uid, who)
        log.info("Gacha pull x%d by %s", count, uid)

    async def pull_cmd(message, count: int):
        if not await soft_gate(ctx, message, "gacha"):
            return
        uid = message.from_user.id
        await do_pull(message.chat.id, message.from_user, await current_banner(uid), count, message.id,
                      answer=lambda t: reply(message, t))

    @command(app, ["pull", "summon", "roll"])
    async def pull1_cmd(_, message):
        await pull_cmd(message, 1)

    @command(app, ["10pull", "multi"])
    async def pull10_cmd(_, message):
        await pull_cmd(message, 10)

    @callback(app, r"^gacha:pull:[a-z0-9_]{1,24}:(1|10):\d{1,15}$")
    async def pull_cb(_, cb):
        _, _, bid, count, owner = cb.data.split(":")
        if int(owner) != cb.from_user.id:
            return await cb.answer("These are someone else's buttons — use /gacha.", show_alert=True)
        if not await cb_gate(ctx, cb, "gacha"):
            return
        await cb.answer()
        await do_pull(cb.message.chat.id, cb.from_user, bid, int(count), None)

    @callback(app, r"^gacha:skip:\d{1,15}$")
    async def skip_cb(_, cb):
        if int(cb.data.split(":")[2]) != cb.from_user.id:
            return await cb.answer("Only the summoner can skip.", show_alert=True)
        ctx.gacha_skip.add((cb.message.chat.id, cb.message.id))
        await cb.answer("⏩ Skipping…")
