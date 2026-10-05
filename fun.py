"""Fun & social commands (60+). Content lives in utils/funcontent.py."""
from __future__ import annotations

import json
import logging
import random
import re
import secrets
import time

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from handlers.inline import article, provider, rid
from services.economy_service import EconomyError, add_currency
from utils import funcontent as fc
from utils.announce import send_art
from utils.formatting import SEP, clean, header, mention, num, rarity_line, waifu_card
from utils.helpers import TTLCache, safe_callback_parts
from utils.permissions import soft_gate
from utils.targets import resolve_target
from utils.tg import arg_text, callback, command, delete_quietly, edit, reply

log = logging.getLogger(__name__)
rng = random.Random()
RPS = {"r": ("🪨", "Rock"), "p": ("📄", "Paper"), "s": ("✂️", "Scissors")}
SOLO_OK = {"dance", "wave", "smile", "laugh", "blush", "cry", "pout", "shrug"}


def strip_desc(text: str | None, limit: int = 420) -> str:
    t = re.sub(r"<[^>]+>|~!.*?!~", " ", text or "", flags=re.S)
    return clean(t, limit) or "No description available."


def register(app, ctx):
    eco, prog = ctx.economy, ctx.progress
    gif_cache = TTLCache(ttl=3600, max_size=64)
    names_registered: list[str] = []

    def fun(names, **kw):
        lst = [names] if isinstance(names, str) else list(names)
        names_registered.extend(lst)
        return command(app, lst, **kw)

    async def throttle(message) -> bool:
        """Soft per-user limiter + registers the user. Returns True if allowed."""
        if message.from_user is None:
            return False
        if not await soft_gate(ctx, message):
            return False
        wait = ctx.limiter.check("fun", message.from_user.id, 8, 20)
        if wait:
            await reply(message, f"⏳ Slow down — try again in **{int(wait) + 1}s**.")
            return False
        return True

    async def fetch_gif(cat: str) -> str | None:
        urls = gif_cache.get(cat)
        if urls is None:
            urls = []
            try:
                r = await ctx.http.get(f"https://nekos.best/api/v2/{cat}", params={"amount": 10}, timeout=8)
                if r.status_code == 200:
                    urls = [x["url"] for x in r.json().get("results", []) if str(x.get("url", "")).startswith("https://")]
            except Exception as exc:  # noqa: BLE001
                log.info("GIF fetch failed: %s", type(exc).__name__)
            gif_cache.set(cat, urls, ttl=3600 if urls else 120)
        return rng.choice(urls) if urls else None

    # ---- social actions (hug, kiss, ...) --------------------------------------
    def make_action(name: str):
        emoji, text, solo, cat, needs = fc.ACTIONS[name]

        async def handler(_, message):
            if not await throttle(message):
                return
            me = mention(message.from_user)
            target, tname, _ = await resolve_target(ctx, message)
            if target and target != message.from_user.id:
                line = text.format(a=f"**{me}**", b=f"**{tname}**")
            elif needs and not target and not message.reply_to_message:
                line = solo.format(a=f"**{me}**")
            else:
                line = solo.format(a=f"**{me}**") if needs else text.format(a=f"**{me}**", b="")
            caption = f"{emoji} {line}"
            url = await fetch_gif(cat) if cat else None
            if url:
                try:
                    await ctx.bot.send_animation(message.chat.id, url, caption=caption, reply_to_message_id=message.id)
                    return
                except Exception as exc:  # noqa: BLE001
                    log.info("GIF send failed: %s", type(exc).__name__)
            await reply(message, caption)
        return handler

    for _name in fc.ACTIONS:
        fun(_name)(make_action(_name))

    # ---- romance ---------------------------------------------------------------
    async def pair(message):
        """Resolve (id, name) pairs: '/ship @a @b', reply + @b, or self + target."""
        parts = arg_text(message).split()
        names = [p for p in parts if p.startswith("@")]
        out = []
        for n in names[:2]:
            row = await ctx.db.fetchone("SELECT user_id, username FROM users WHERE lower(username)=lower(?)", (n[1:],))
            out.append((row["user_id"] if row else n.lower(), "@" + clean(n[1:], 30)))
        if message.reply_to_message and message.reply_to_message.from_user and len(out) < 2:
            u = message.reply_to_message.from_user
            out.append((u.id, mention(u)))
        me = (message.from_user.id, mention(message.from_user))
        if len(out) == 1:
            out = [me, out[0]]
        return out[:2]

    @fun("ship")
    async def ship_cmd(_, message):
        if not await throttle(message):
            return
        p = await pair(message)
        if len(p) < 2:
            return await reply(message, "Usage: `/ship @user1 @user2` (or reply to someone)")
        pct = fc.stable_percent(*sorted(str(x[0]) for x in p))
        await reply(message, f"💘 **SHIP**\n\n{p[0][1]} × {p[1][1]}\n__{fc.ship_name(p[0][1], p[1][1])}__\n\n"
                             f"{fc.love_bar(pct)}\n**{pct}%** compatible")

    @fun("shipname")
    async def shipname_cmd(_, message):
        if not await throttle(message):
            return
        parts = arg_text(message).split()
        if len(parts) < 2:
            return await reply(message, "Usage: `/shipname <name1> <name2>`")
        await reply(message, f"💞 Ship name: **{clean(fc.ship_name(parts[0], parts[1]), 40)}**")

    @fun(["love", "crush"])
    async def love_cmd(_, message):
        if not await throttle(message):
            return
        target, tname, _ = await resolve_target(ctx, message)
        if not target:
            return await reply(message, f"Usage: `/{message.command[0]} @user` (or reply to someone)")
        pct = fc.stable_percent("love", message.from_user.id, target, time.strftime("%Y-%m-%d"))
        word = "crush" if message.command[0] == "crush" else "love"
        await reply(message, f"💓 **{word.upper()} METER**\n\n{mention(message.from_user)} → {tname}\n"
                             f"{fc.love_bar(pct)}\n**{pct}%** today")

    @fun("marry")
    async def marry_cmd(_, message):
        if not await throttle(message):
            return
        target, tname, _ = await resolve_target(ctx, message)
        me = message.from_user.id
        if not target or target == me:
            return await reply(message, "Usage: `/marry @user` (or reply to them)")
        taken = await ctx.db.fetchone("SELECT 1 FROM marriages WHERE user_id IN (?,?)", (me, target))
        if taken:
            return await reply(message, "💍 One of you is already married. Use /divorce first.")
        token = secrets.token_hex(3)
        ctx.selections.set(f"marry:{token}", {"a": me, "b": target}, ttl=300)
        kb = M([[B("💍 Accept", callback_data=f"marry:acc:{token}"), B("💔 Decline", callback_data=f"marry:dec:{token}")]])
        await reply(message, f"💍 {mention(message.from_user)} proposes to **{tname}**!\n\n__{tname}, do you accept?__",
                    reply_markup=kb)

    @callback(app, r"^marry:(acc|dec):[0-9a-f]{6}$")
    async def marry_cb(_, cb):
        _, action, token = cb.data.split(":")
        prop = ctx.selections.get(f"marry:{token}")
        if not prop:
            return await cb.answer("This proposal expired.", show_alert=True)
        if cb.from_user.id != prop["b"]:
            return await cb.answer("Only the person being asked can answer.", show_alert=True)
        ctx.selections.pop(f"marry:{token}")
        if action == "dec":
            await cb.answer()
            return await edit(cb.message, "💔 The proposal was declined.")

        def op(c):
            if c.execute("SELECT 1 FROM marriages WHERE user_id IN (?,?)", (prop["a"], prop["b"])).fetchone():
                raise EconomyError("One of you is already married.")
            c.execute("INSERT INTO marriages (user_id, partner_id) VALUES (?,?)", (prop["a"], prop["b"]))
            c.execute("INSERT INTO marriages (user_id, partner_id) VALUES (?,?)", (prop["b"], prop["a"]))
        try:
            await ctx.db.transaction(op)
        except EconomyError as exc:
            return await cb.answer(clean(exc, 100), show_alert=True)
        await cb.answer("🎉")
        await edit(cb.message, "🎉 **Congratulations!** They're now married. 💒")

    @fun("divorce")
    async def divorce_cmd(_, message):
        if not await throttle(message):
            return
        me = message.from_user.id

        def op(c):
            row = c.execute("SELECT partner_id FROM marriages WHERE user_id=?", (me,)).fetchone()
            if not row:
                return False
            c.execute("DELETE FROM marriages WHERE user_id IN (?,?)", (me, row["partner_id"]))
            return True
        ok = await ctx.db.transaction(op)
        await reply(message, "💔 You're now divorced." if ok else "You're not married.")

    # ---- playful ----------------------------------------------------------------
    @fun("rate")
    async def rate_cmd(_, message):
        if not await throttle(message):
            return
        what = arg_text(message)
        if not what:
            return await reply(message, "Usage: `/rate <anything>`")
        r = fc.stable_rating(what)
        await reply(message, f"⭐ I rate **{clean(what, 60)}** a **{r}/10**.\n{'🌟' * r}{'▫️' * (10 - r)}")

    async def target_name(message):
        target, tname, _ = await resolve_target(ctx, message)
        return tname if target else mention(message.from_user)

    @fun("roast")
    async def roast_cmd(_, message):
        if not await throttle(message):
            return
        t = await target_name(message)
        await reply(message, "🔥 " + rng.choice(fc.ROASTS).format(t=f"**{t}**") + "\n__(all in good fun!)__")

    @fun("compliment")
    async def compliment_cmd(_, message):
        if not await throttle(message):
            return
        t = await target_name(message)
        await reply(message, "💐 " + rng.choice(fc.COMPLIMENTS).format(t=f"**{t}**"))

    @fun("kill")
    async def kill_cmd(_, message):
        if not await throttle(message):
            return
        target, tname, _ = await resolve_target(ctx, message)
        if not target:
            return await reply(message, "Usage: `/kill @user` — harmless cartoon fun only.")
        await reply(message, "🎭 " + rng.choice(fc.KILL_LINES).format(a=f"**{mention(message.from_user)}**", b=f"**{tname}**"))

    @fun("fortune")
    async def fortune_cmd(_, message):
        if not await throttle(message):
            return
        i = int(fc.stable_percent("fortune", message.from_user.id, time.strftime("%Y-%m-%d"))) % len(fc.FORTUNES)
        await reply(message, f"🔮 **FORTUNE**\n\n__{fc.FORTUNES[i]}__\n\n🍀 Lucky number: **{fc.stable_percent('lucky', message.from_user.id, time.strftime('%Y-%m-%d'))}**")

    @fun(["8ball", "eightball"])
    async def eightball_cmd(_, message):
        if not await throttle(message):
            return
        q = arg_text(message)
        if not q:
            return await reply(message, "Usage: `/8ball <question>`")
        await reply(message, f"🎱 **{clean(q, 100)}**\n\n__{rng.choice(fc.EIGHTBALL)}__")

    @fun("coinflip")
    async def coinflip_cmd(_, message):
        if not await throttle(message):
            return
        await reply(message, f"🪙 The coin lands on **{rng.choice(['Heads', 'Tails'])}**!")

    @fun("dice")
    async def dice_cmd(_, message):
        if not await throttle(message):
            return
        spec = fc.parse_dice(arg_text(message))
        if not spec:
            return await reply(message, "Usage: `/dice [NdM]` — e.g. `/dice 2d6` (max 10 dice, 1000 sides)")
        n, s = spec
        rolls = [rng.randint(1, s) for _ in range(n)]
        await reply(message, f"🎲 **{n}d{s}**: {', '.join(map(str, rolls))}" + (f"\nTotal: **{sum(rolls)}**" if n > 1 else ""))

    @fun("choose")
    async def choose_cmd(_, message):
        if not await throttle(message):
            return
        opts = fc.split_choices(arg_text(message))
        if len(opts) < 2:
            return await reply(message, "Usage: `/choose pizza | sushi | tacos`")
        await reply(message, f"🤔 I choose… **{clean(rng.choice(opts), 60)}**!")

    for _n, _lst, _title in (("truth", fc.TRUTHS, "🫣 **TRUTH**"), ("dare", fc.DARES, "😈 **DARE**"),
                             ("wouldyourather", fc.WYR, "🤷 **WOULD YOU RATHER**"), ("joke", fc.JOKES, "😄 **JOKE**"),
                             ("fact", fc.FACTS, "🧠 **FUN FACT**"), ("quote", fc.QUOTES, "💬 **QUOTE**")):
        def mk(lst=_lst, title=_title):
            async def h(_, message):
                if await throttle(message):
                    await reply(message, f"{title}\n\n{rng.choice(lst)}")
            return h
        fun(_n)(mk())

    @fun("meme")
    async def meme_cmd(_, message):
        if not await throttle(message):
            return
        try:
            r = await ctx.http.get("https://meme-api.com/gimme/wholesomememes", timeout=8)
            d = r.json()
            url = d.get("url", "")
            if r.status_code == 200 and url.startswith("https://") and not d.get("nsfw") and not d.get("spoiler"):
                await ctx.bot.send_photo(message.chat.id, url, caption=f"😂 {clean(d.get('title', 'Meme'), 150)}",
                                         reply_to_message_id=message.id)
                return
        except Exception as exc:  # noqa: BLE001
            log.info("Meme fetch failed: %s", type(exc).__name__)
        await reply(message, "😅 The meme service is asleep. Here's a joke instead:\n\n" + rng.choice(fc.JOKES))

    @fun("avatar")
    async def avatar_cmd(_, message):
        if not await throttle(message):
            return
        target, tname, _ = await resolve_target(ctx, message)
        uid, name = (target, tname) if target else (message.from_user.id, mention(message.from_user))
        try:
            async for photo in ctx.bot.get_chat_photos(uid, limit=1):
                await ctx.bot.send_photo(message.chat.id, photo.file_id, caption=f"🖼 **{name}**",
                                         reply_to_message_id=message.id)
                return
        except Exception as exc:  # noqa: BLE001
            log.info("Avatar fetch failed: %s", type(exc).__name__)
        await reply(message, "🖼 No visible profile photo for that user.")

    # ---- rock paper scissors -----------------------------------------------------
    @fun("rps")
    async def rps_cmd(_, message):
        if not await throttle(message):
            return
        uid = message.from_user.id
        kb = M([[B("🪨 Rock", callback_data=f"rps:r:{uid}")], [B("📄 Paper", callback_data=f"rps:p:{uid}")],
                [B("✂️ Scissors", callback_data=f"rps:s:{uid}")]])
        await reply(message, "✊ **ROCK PAPER SCISSORS**\n\nChoose your move:", reply_markup=kb)

    @callback(app, r"^rps:[rps]:\d{1,15}$")
    async def rps_cb(_, cb):
        _, pick, owner = cb.data.split(":")
        if int(owner) != cb.from_user.id:
            return await cb.answer("That's not your game — start one with /rps.", show_alert=True)
        wait = await ctx.db.check_cooldown(cb.from_user.id, "rps", 8)
        if wait:
            return await cb.answer(f"Wait {int(wait) + 1}s", show_alert=True)
        bot_pick = rng.choice("rps")
        res = fc.rps_result(pick, bot_pick)
        text = (f"✊ **ROCK PAPER SCISSORS**\n\nYou chose **{RPS[pick][1]}** {RPS[pick][0]}.\n"
                f"Bot chose **{RPS[bot_pick][1]}** {RPS[bot_pick][0]}.\n\n")
        if res == "win":
            await ctx.db.ensure_user(cb.from_user.id)
            await eco.grant(cb.from_user.id, coins=50, xp=3)
            text += "🎉 **YOU WIN!**\n\nReward:\n\n💰 +50 Coins"
        elif res == "tie":
            text += "🤝 **IT'S A TIE!**"
        else:
            text += "😅 **YOU LOSE!** Better luck next time."
        await cb.answer()
        await edit(cb.message, text)

    # ---- anime lookups (AniList, cached) -----------------------------------------
    @fun("anime")
    async def anime_cmd(_, message):
        if not await throttle(message):
            return
        q = arg_text(message)
        if not q:
            return await reply(message, "Usage: `/anime <title>`")
        m = await ctx.anime.search_anime(q)
        if not m:
            return await reply(message, "❌ I couldn't find that anime (or the service is unavailable).")
        await send_anime(message, m)

    async def send_anime(message, m: dict):
        t = m.get("title") or {}
        title = t.get("english") or t.get("romaji") or "Unknown"
        score = m.get("averageScore")
        lines = [f"📺 **{clean(title, 80)}**", ""]
        if score:
            lines.append(f"⭐ Score: **{score}/100**")
        if m.get("episodes"):
            lines.append(f"🎞 Episodes: **{m['episodes']}**")
        if m.get("seasonYear"):
            lines.append(f"📅 Year: **{m['seasonYear']}**")
        if m.get("genres"):
            lines.append("🏷 " + clean(", ".join(m["genres"][:4]), 80))
        lines += ["", f"__{strip_desc(m.get('description'))}__"]
        await send_art(ctx, message.chat.id, (m.get("coverImage") or {}).get("large", ""), "\n".join(lines), reply_to=message.id)

    @fun("randomanime")
    async def randomanime_cmd(_, message):
        if not await throttle(message):
            return
        m = await ctx.anime.random_anime(rng.randint(1, 200))
        if not m:
            return await reply(message, "❌ The anime service is unavailable right now.")
        await send_anime(message, m)

    @fun("character")
    async def character_cmd(_, message):
        if not await throttle(message):
            return
        q = arg_text(message)
        if not q:
            return await reply(message, "Usage: `/character <name>`")
        c = await ctx.anime.search_character(q)
        if not c:
            return await reply(message, "❌ I couldn't find that character.")
        shows = [((n.get("title") or {}).get("english") or (n.get("title") or {}).get("romaji")) for n in
                 (c.get("media") or {}).get("nodes", [])]
        lines = [f"🌸 **{clean(c['name']['full'], 80)}**", ""]
        if shows:
            lines.append("📺 " + clean(", ".join(s for s in shows if s), 120))
        if c.get("favourites"):
            lines.append(f"❤️ Favourites: **{num(c['favourites'])}**")
        lines += ["", f"__{strip_desc(c.get('description'), 350)}__"]
        await send_art(ctx, message.chat.id, (c.get("image") or {}).get("large", ""), "\n".join(lines), reply_to=message.id)

    @fun("randomwaifu")
    async def randomwaifu_cmd(_, message):
        if not await throttle(message):
            return
        w = await ctx.waifus.random_waifu()
        if not w:
            return await reply(message, "😴 The character pool is empty. An admin can run /syncwaifus.")
        await send_art(ctx, message.chat.id, w.image_url, "🎲 **RANDOM WAIFU**\n\n" + waifu_card(w), reply_to=message.id)

    @fun("waifuworld")
    async def waifuworld_cmd(_, message):
        if not await throttle(message):
            return
        pool = await ctx.db.fetchall("SELECT rarity, COUNT(*) AS n FROM waifus WHERE enabled=1 GROUP BY rarity")
        claimed = (await ctx.db.fetchone("SELECT COUNT(*) AS n FROM user_waifus"))["n"]
        owners = (await ctx.db.fetchone("SELECT COUNT(DISTINCT user_id) AS n FROM user_waifus"))["n"]
        top = await ctx.db.fetchall("SELECT w.anime, COUNT(*) AS n FROM user_waifus uw JOIN waifus w ON w.id=uw.waifu_id "
                                    "GROUP BY w.anime ORDER BY n DESC LIMIT 3")
        lines = [header("🌍 **WAIFU WORLD**"), "", f"🌸 Characters in the pool: **{num(sum(r['n'] for r in pool))}**"]
        lines += [f"   • {r['rarity']}: {r['n']}" for r in sorted(pool, key=lambda r: -r["n"])[:8]]
        lines += ["", f"💞 Claimed so far: **{num(claimed)}** by **{num(owners)}** collectors"]
        if top:
            lines += ["", "📺 Most collected:"] + [f"   {i}. {clean(r['anime'], 40)} ({r['n']})" for i, r in enumerate(top, 1)]
        await reply(message, "\n".join(lines))

    # ---- combat -------------------------------------------------------------------
    @fun("battle")
    async def battle_cmd(_, message):
        if not await throttle(message):
            return
        uid = message.from_user.id
        wait = await ctx.db.check_cooldown(uid, "battle", 120)
        if wait:
            return await reply(message, f"⏳ You're recovering. Battle again in **{int(wait) + 1}s**.")
        lvl = (await prog.stats(uid))["level"]
        name, mlvl = rng.choice([m for m in fc.MONSTERS if m[1] <= lvl + 6] or fc.MONSTERS[:1])
        win = rng.random() < lvl / (lvl + mlvl)
        if win:
            coins = rng.randint(50, 150) + mlvl * 5
            await eco.grant(uid, coins=coins, xp=10)
            await reply(message, f"⚔️ **BATTLE**\n\nYou fought a **{name}** (Lv {mlvl}) and **won!**\n\n💰 +{coins}  ✨ +10")
        else:
            await eco.grant(uid, xp=3)
            await reply(message, f"⚔️ **BATTLE**\n\nThe **{name}** (Lv {mlvl}) was too strong. You retreat safely.\n\n✨ +3")

    @fun("fight")
    async def fight_cmd(_, message):
        if not await throttle(message):
            return
        target, tname, _ = await resolve_target(ctx, message)
        if not target or target == message.from_user.id:
            return await reply(message, "Usage: `/fight @user` — a friendly spar with no stakes.")
        a, b = f"**{mention(message.from_user)}**", f"**{tname}**"
        w, l = (a, b) if rng.random() < 0.5 else (b, a)
        await reply(message, "🥊 " + rng.choice(fc.FIGHT_LINES).format(w=w, l=l))

    @fun("duel")
    async def duel_cmd(_, message):
        if not await throttle(message):
            return
        target, tname, rest = await resolve_target(ctx, message)
        digits = (rest.split() or [""])[-1].replace(",", "")
        bet = int(digits) if digits.isdigit() else 0
        me = message.from_user.id
        if not target or target == me or not 1 <= bet <= 1_000_000:
            return await reply(message, "Usage: `/duel @user <bet>` — winner takes the coins.")
        bals = [(await eco.balance(me))["coins"], (await eco.balance(target))["coins"]]
        if min(bals) < bet:
            return await reply(message, "💸 Both players need at least that many coins.")
        token = secrets.token_hex(3)
        ctx.selections.set(f"duel:{token}", {"a": me, "b": target, "bet": bet}, ttl=180)
        kb = M([[B("⚔️ Accept", callback_data=f"duel:acc:{token}"), B("🏳️ Decline", callback_data=f"duel:dec:{token}")]])
        await reply(message, f"⚔️ **DUEL!** {mention(message.from_user)} challenges **{tname}** for **{num(bet)}** 💰\n\n"
                             f"__{tname}, accept within 3 minutes.__", reply_markup=kb)

    @callback(app, r"^duel:(acc|dec):[0-9a-f]{6}$")
    async def duel_cb(_, cb):
        _, action, token = cb.data.split(":")
        d = ctx.selections.get(f"duel:{token}")
        if not d:
            return await cb.answer("This duel expired.", show_alert=True)
        if cb.from_user.id != d["b"]:
            return await cb.answer("Only the challenged player can answer.", show_alert=True)
        ctx.selections.pop(f"duel:{token}")
        if action == "dec":
            await cb.answer()
            return await edit(cb.message, "🏳️ Duel declined.")
        la = (await prog.stats(d["a"]))["level"]
        lb = (await prog.stats(d["b"]))["level"]
        a_wins = rng.random() < (la + 5) / (la + lb + 10)
        win, lose = (d["a"], d["b"]) if a_wins else (d["b"], d["a"])

        def op(c):
            cur = c.execute("UPDATE economy SET coins=coins-? WHERE user_id=? AND coins>=?", (d["bet"], lose, d["bet"]))
            if cur.rowcount != 1:
                raise EconomyError("The loser can't cover the bet anymore.")
            c.execute("UPDATE economy SET coins=coins+? WHERE user_id=?", (d["bet"], win))
        try:
            await ctx.db.transaction(op)
        except EconomyError as exc:
            await cb.answer(clean(exc, 100), show_alert=True)
            return await edit(cb.message, "⚔️ The duel was cancelled.")
        rows = {r["user_id"]: r for r in await ctx.db.fetchall(
            "SELECT user_id, username, first_name FROM users WHERE user_id IN (?,?)", (win, lose))}
        label = lambda u: ("@" + rows[u]["username"]) if rows[u]["username"] else (rows[u]["first_name"] or "Player")
        await cb.answer()
        await edit(cb.message, f"⚔️ **DUEL RESULT**\n\n🏆 **{clean(label(win), 40)}** wins **{num(d['bet'])}** 💰 "
                               f"from {clean(label(lose), 40)}!")

    # ---- pets & adventures -----------------------------------------------------------
    async def get_pet(uid: int) -> dict | None:
        row = await ctx.db.fetchone("SELECT value FROM user_settings WHERE user_id=? AND key='pet'", (uid,))
        return json.loads(row["value"]) if row else None

    async def save_pet(uid: int, pet: dict) -> None:
        await ctx.db.execute("INSERT INTO user_settings (user_id,key,value) VALUES (?,'pet',?) "
                             "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value", (uid, json.dumps(pet)))

    def pet_mood(pet: dict) -> tuple[int, str]:
        hours = (time.time() - pet["fed_at"]) / 3600
        happy = max(0, min(100, pet["happy"] - int(hours * 4)))
        return happy, "😸 Purring" if happy >= 70 else ("😐 Okay" if happy >= 35 else "😿 Hungry!")

    @fun("pet")
    async def pet_cmd(_, message):
        if not await throttle(message):
            return
        uid, arg = message.from_user.id, arg_text(message)
        pet = await get_pet(uid)
        if pet is None:
            if not arg:
                return await reply(message, "🐾 You have no pet yet. Adopt one: `/pet <name>`")
            pet = {"name": clean(arg, 20) or "Buddy", "type": rng.choice(["🐱", "🐶", "🐰", "🦊", "🐹"]),
                   "fed_at": time.time(), "happy": 80}
            await save_pet(uid, pet)
            return await reply(message, f"🎉 You adopted {pet['type']} **{pet['name']}**! Feed it with /feed.")
        happy, mood = pet_mood(pet)
        await reply(message, f"{pet['type']} **{pet['name']}**\n\n{mood}\n❤️ Happiness: **{happy}/100**\n"
                             f"{'▰' * (happy // 10)}{'▱' * (10 - happy // 10)}")

    @fun("feed")
    async def feed_cmd(_, message):
        if not await throttle(message):
            return
        uid = message.from_user.id
        pet = await get_pet(uid)
        if not pet:
            return await reply(message, "🐾 Adopt a pet first: `/pet <name>`")
        try:
            await eco.grant(uid, coins=-20)
        except EconomyError:
            return await reply(message, "💸 Pet food costs **20** 💰 — you don't have enough.")
        happy, _ = pet_mood(pet)
        pet.update(happy=min(100, happy + 30), fed_at=time.time())
        await save_pet(uid, pet)
        await reply(message, f"🍖 {pet['type']} **{pet['name']}** enjoyed the meal! (-20 💰) ❤️ {pet['happy']}/100")

    async def adventure(message, key: str, cooldown: int, table, verb: str, emoji: str):
        if not await throttle(message):
            return
        uid = message.from_user.id
        wait = await ctx.db.check_cooldown(uid, key, cooldown)
        if wait:
            return await reply(message, f"⏳ Rest a bit — try again in **{int(wait) + 1}s**.")
        item = fc.weighted(table, rng)
        coins = rng.randint(item[1], item[2])
        await eco.grant(uid, coins=coins, xp=8)
        await ctx.db.execute("INSERT INTO user_inventory (user_id,item,qty) VALUES (?,?,1) "
                             "ON CONFLICT(user_id,item) DO UPDATE SET qty=qty+1", (uid, item[0]))
        await reply(message, f"{emoji} You {verb} **{item[0]}**!\n\n💰 +{coins}  ✨ +8")

    @fun("hunt")
    async def hunt_cmd(_, message):
        await adventure(message, "hunt", 300, fc.LOOT_HUNT, "hunted down a", "🏹")

    @fun("fish")
    async def fish_cmd(_, message):
        await adventure(message, "fish", 300, fc.LOOT_FISH, "caught a", "🎣")

    @fun("treasure")
    async def treasure_cmd(_, message):
        await adventure(message, "treasure", 900, fc.LOOT_TREASURE, "dug up a", "🗺")

    @fun("inventory")
    async def inventory_cmd(_, message):
        if not await throttle(message):
            return
        rows = await ctx.db.fetchall("SELECT item, qty FROM user_inventory WHERE user_id=? AND qty>0 ORDER BY qty DESC "
                                     "LIMIT 20", (message.from_user.id,))
        if not rows:
            return await reply(message, "🎒 Your inventory is empty. Try /fish, /hunt or /treasure!")
        await reply(message, header("🎒 **INVENTORY**") + "\n\n" + "\n".join(f"{r['item']} × **{r['qty']}**" for r in rows))

    ctx.fun_commands = names_registered


@provider
async def fun_provider(ctx, query: str, user=None):
    q = query.strip().lower()
    table = {"joke": (fc.JOKES, "😄"), "fact": (fc.FACTS, "🧠"), "quote": (fc.QUOTES, "💬"),
             "fortune": (fc.FORTUNES, "🔮"), "truth": (fc.TRUTHS, "🫣"), "dare": (fc.DARES, "😈")}
    if q in table:
        lst, emoji = table[q]
        picks = random.sample(lst, k=min(3, len(lst)))
        return [article(rid("f", q, i, p[:10]), f"{emoji} {q.title()}", p[:100], f"{emoji} {p}") for i, p in enumerate(picks)]
    if q.startswith("anime ") and ctx.anime:
        m = await ctx.anime.search_anime(q[6:])
        if m:
            t = m.get("title") or {}
            title = t.get("english") or t.get("romaji") or "Anime"
            return [article(rid("a", m["id"]), f"📺 {title}", f"Score {m.get('averageScore') or '?'}",
                            f"📺 **{clean(title, 80)}**\n\n__{strip_desc(m.get('description'), 300)}__",
                            (m.get("coverImage") or {}).get("large", ""))]
    return []
