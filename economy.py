"""Economy, leveling, quests, achievements, leaderboards and message-activity tracking."""
from __future__ import annotations

import asyncio
import logging

from pyrogram import filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from services.economy_service import (EconomyError, OnCooldown, fmt_wait, level_for_xp, xp_for_level)
from services.progress_service import ACHIEVEMENTS
from utils.announce import announce_achievements, level_up_text
from utils.formatting import SEP, bar, clean, header, mention, num
from utils.permissions import soft_gate
from utils.targets import resolve_target
from utils.tg import arg_text, callback, command, edit, guard, reply

log = logging.getLogger(__name__)
CATEGORIES = {"coins": "💰", "xp": "✨", "waifus": "🌸", "music": "🎵"}


def label(row: dict) -> str:
    return ("@" + clean(row["username"], 30)) if row.get("username") else clean(row.get("first_name") or "User", 30)


async def flusher(ctx) -> None:
    """Persist buffered message counts / quest progress every 15 seconds."""
    while True:
        await asyncio.sleep(15)
        try:
            await ctx.progress.flush_activity(ctx.activity)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Activity flush failed")


def register(app, ctx):
    eco, prog = ctx.economy, ctx.progress
    ctx.background.append(flusher)

    # ---- passive XP / activity ------------------------------------------------
    async def on_message(_, message):
        user = message.from_user
        if user is None or user.is_bot or message.chat.type.name not in {"GROUP", "SUPERGROUP"}:
            return
        if message.text and message.text.startswith(("/", "!")):
            return
        if message.chat.id not in ctx.known_chats:
            cfg = ctx.config
            await ctx.db.ensure_group(message.chat.id, message.chat.title, cfg.waifu_default_interval,
                                      cfg.waifu_default_enabled)
            ctx.known_chats.add(message.chat.id)
        grant = ctx.activity.note(message.chat.id, user.id)
        if grant:
            await ctx.db.ensure_user(user.id, user.username, user.first_name)
            old, new = await eco.add_xp(user.id, 5)
            if new > old:
                await ctx.bot.send_message(message.chat.id, level_up_text(mention(user), new))
                await announce_achievements(ctx, message.chat.id, user.id, mention(user))

    app.add_handler(MessageHandler(guard(on_message), filters.group & ~filters.service & ~filters.bot), group=1)

    async def on_song(_, user_id: int, chat_id: int):
        await prog.add_progress(user_id, "song")
        old, new = await eco.add_xp(user_id, 5)
        if new > old:
            await ctx.bot.send_message(chat_id, level_up_text("A music lover", new))
        await announce_achievements(ctx, chat_id, user_id, "Music lover")
    ctx.on("song_played", on_song)

    # ---- balance ---------------------------------------------------------------
    @command(app, ["balance", "bal"])
    async def balance_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        uid, name = message.from_user.id, mention(message.from_user)
        target, tname, _ = await resolve_target(ctx, message)
        if target:
            uid, name = target, tname
        b = await eco.balance(uid)
        await reply(message, f"{header('💰 **BALANCE**')}\n\n👤 **{name}**\n\n💰 Coins: **{num(b['coins'])}**\n"
                             f"💎 Gems: **{num(b['gems'])}**")

    def reward_text(r: dict) -> str:
        parts = [f"💰 +{num(r['coins'])} Coins"]
        if r.get("gems"):
            parts.insert(0, f"💎 +{num(r['gems'])} Gems")
        parts.append(f"✨ +{r['xp']} XP")
        return "\n".join(parts)

    async def after_reward(message, r: dict):
        if r.get("level_up"):
            await ctx.bot.send_message(message.chat.id, level_up_text(mention(message.from_user), r["level_up"]))
        await announce_achievements(ctx, message.chat.id, message.from_user.id, mention(message.from_user))

    @command(app, "daily")
    async def daily_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        try:
            r = await eco.daily(message.from_user.id)
        except OnCooldown as exc:
            return await reply(message, f"⏳ You already claimed today. Come back in **{fmt_wait(exc.seconds)}**.")
        head = f"🔥 **{r['streak']} DAY STREAK!**" + ("  🎉 **BONUS!**" if r["bonus"] else "")
        await reply(message, f"{head}\n\nReward:\n\n{reward_text(r)}\n\n__Claim every day to keep your streak.__")
        await after_reward(message, r)

    @command(app, "weekly")
    async def weekly_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        try:
            r = await eco.weekly(message.from_user.id)
        except OnCooldown as exc:
            return await reply(message, f"⏳ Weekly reward ready in **{fmt_wait(exc.seconds)}**.")
        await reply(message, f"🎁 **WEEKLY REWARD**\n\n{reward_text(r)}")
        await after_reward(message, r)

    JOBS = ["fixed a vending machine", "delivered ramen", "streamed for a night", "drew commissions",
            "tuned a guitar", "walked a very fast dog", "debugged a legacy bot", "cosplayed at a convention"]

    async def job(message, fn, verb: str):
        if not await soft_gate(ctx, message):
            return
        try:
            r = await fn(message.from_user.id)
        except OnCooldown as exc:
            return await reply(message, f"⏳ You're tired. Try again in **{fmt_wait(exc.seconds)}**.")
        import random
        await reply(message, f"💼 You {random.choice(JOBS)} and {verb} **{num(r['coins'])}** 💰  (+{r['xp']} ✨)")
        await after_reward(message, r)

    @command(app, "work")
    async def work_cmd(_, message):
        await job(message, eco.work, "earned")

    @command(app, "earn")
    async def earn_cmd(_, message):
        await job(message, eco.earn, "picked up")

    @command(app, ["pay", "givecoins"])
    async def pay_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        target, name, rest = await resolve_target(ctx, message)
        amount = rest.split()[-1] if rest.split() else ""
        amount = amount.replace(",", "")
        if not target or not amount.isdigit():
            return await reply(message, "Usage: `/pay @user <amount>` (or reply to someone)")
        wait = ctx.limiter.check("pay", message.from_user.id, 5, 30)
        if wait:
            return await reply(message, "⏳ Slow down with the payments.")
        try:
            await eco.pay(message.from_user.id, target, int(amount))
        except EconomyError as exc:
            return await reply(message, f"⚠️ {clean(exc, 200)}")
        await reply(message, f"💸 {mention(message.from_user)} paid **{name}** **{num(int(amount))}** 💰")

    # ---- leaderboards ---------------------------------------------------------------
    async def lb_view(category: str, page: int, owner_id: int):
        rows, pages, title = await eco.leaderboard(category, page)
        page = min(max(1, page), pages)
        lines = [header(f"🏆 **LEADERBOARD** — {title}"), ""]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        if not rows:
            lines.append("__Nobody here yet.__")
        for i, r in enumerate(rows, (page - 1) * 10 + 1):
            lines.append(f"{medals.get(i, f'`{i}.`')} {label(r)} — **{num(r['score'])}**")
        lines += ["", f"{SEP}\nPage {page}/{pages}"]
        cats = [B(("•" if c == category else "") + e, callback_data=f"lb:{c}:1:{owner_id}") for c, e in CATEGORIES.items()]
        nav = []
        if page > 1:
            nav.append(B("◀️", callback_data=f"lb:{category}:{page - 1}:{owner_id}"))
        if page < pages:
            nav.append(B("▶️", callback_data=f"lb:{category}:{page + 1}:{owner_id}"))
        return "\n".join(lines), M([cats] + ([nav] if nav else []))

    async def lb_cmd(message, category: str):
        if not await soft_gate(ctx, message):
            return
        text, kb = await lb_view(category, 1, message.from_user.id)
        await reply(message, text, reply_markup=kb)

    for names, cat in ((["leaderboard", "top"], "xp"), (["topcoins"], "coins"), (["topxp"], "xp"),
                       (["topwaifus"], "waifus"), (["topmusic"], "music")):
        def make(cat=cat):
            async def handler(_, message):
                await lb_cmd(message, cat)
            return handler
        command(app, names)(make())

    @callback(app, r"^lb:(coins|xp|waifus|music):\d{1,4}:\d{1,15}$")
    async def lb_cb(_, cb):
        _, cat, page, owner = cb.data.split(":")
        if int(owner) != cb.from_user.id:
            return await cb.answer("Open your own board with /leaderboard.", show_alert=True)
        text, kb = await lb_view(cat, int(page), cb.from_user.id)
        await cb.answer()
        await edit(cb.message, text, reply_markup=kb)

    # ---- level / xp / rank ----------------------------------------------------------------
    @command(app, ["level", "xp", "rank"])
    async def level_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        uid, name = message.from_user.id, mention(message.from_user)
        target, tname, _ = await resolve_target(ctx, message)
        if target:
            uid, name = target, tname
        s = await prog.stats(uid)
        lvl, xp = s["level"], s["xp"]
        base, nxt = xp_for_level(lvl), xp_for_level(lvl + 1)
        rank = await eco.xp_rank(uid)
        await reply(message, f"{header('✨ **LEVEL**')}\n\n👤 **{name}**\n\n🎚 Level: **{lvl}**\n"
                             f"✨ XP: **{num(xp)}** / {num(nxt)}\n{bar(xp - base, nxt - base)}\n\n🏅 Rank: **#{rank}**")

    # ---- quests ----------------------------------------------------------------------------
    async def quest_view(uid: int):
        qs = await prog.quests(uid)
        lines = [header("📜 **DAILY QUESTS**"), ""]
        kb = []
        for q in qs:
            done = q["progress"] >= q["target"]
            state = "✅ Claimed" if q["claimed"] else ("🎁 Ready!" if done else f"{q['progress']}/{q['target']}")
            lines.append(f"{q['label']}\n   {bar(q['progress'], q['target'], 8)} {state}\n"
                         f"   💰 {q['coins']}  💎 {q['gems']}  ✨ {q['xp']}")
            if done and not q["claimed"]:
                kb.append([B(f"🎁 Claim: {q['label'][2:]}", callback_data=f"quest:claim:{q['id']}:{uid}")])
        lines += ["", "__Quests reset every day (UTC).__"]
        return "\n".join(lines), M(kb) if kb else None

    @command(app, "quests")
    async def quests_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        text, kb = await quest_view(message.from_user.id)
        await reply(message, text, reply_markup=kb)

    @callback(app, r"^quest:claim:[a-z0-9]{3,12}:\d{1,15}$")
    async def quest_cb(_, cb):
        _, _, qid, owner = cb.data.split(":")
        if int(owner) != cb.from_user.id:
            return await cb.answer("These are someone else's quests.", show_alert=True)
        try:
            r = await prog.claim_quest(cb.from_user.id, qid)
        except EconomyError as exc:
            return await cb.answer(clean(exc, 120), show_alert=True)
        await cb.answer(f"+{r['coins']} 💰 +{r['gems']} 💎 +{r['xp']} ✨")
        text, kb = await quest_view(cb.from_user.id)
        await edit(cb.message, text, reply_markup=kb)
        await announce_achievements(ctx, cb.message.chat.id, cb.from_user.id, mention(cb.from_user))

    @command(app, "claim")
    async def claim_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        got = await prog.claim_all(message.from_user.id)
        if not got:
            return await reply(message, "📭 No finished quests to claim. Check `/quests`.")
        tot = {k: sum(g[k] for g in got) for k in ("coins", "gems", "xp")}
        await reply(message, f"🎁 **Claimed {len(got)} quest{'s' if len(got) != 1 else ''}!**\n\n"
                             f"💰 +{num(tot['coins'])}  💎 +{num(tot['gems'])}  ✨ +{tot['xp']}")
        await announce_achievements(ctx, message.chat.id, message.from_user.id, mention(message.from_user))

    # ---- achievements ------------------------------------------------------------------------
    @command(app, "achievements")
    async def achievements_cmd(_, message):
        if not await soft_gate(ctx, message):
            return
        have = await prog.unlocked(message.from_user.id)
        lines = [header("🏆 **ACHIEVEMENTS**"), "", f"Unlocked: **{len(have)}/{len(ACHIEVEMENTS)}**", ""]
        for aid, (name, desc, _, gems) in ACHIEVEMENTS.items():
            lines.append(f"{'✅' if aid in have else '🔒'} {name}\n      __{desc}__ • +{gems} 💎")
        await reply(message, "\n".join(lines))
