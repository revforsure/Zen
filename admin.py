"""Group-admin settings and owner-only management commands.

Permission model
  * Group admins (checked live via Telegram): settings, prefix, language, timers, feature toggles,
    maintenance, /resetgroup.
  * Bot owners (OWNER_IDS in .env): anything that creates/destroys currency or characters
    (/addcoins /addgems /addwaifu /removewaifu /givewaifu /resetuser /broadcast /syncwaifus).
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from config import RARITY_ORDER, RARITY_VALUES
from services.economy_service import EconomyError
from utils.formatting import clean, header
from utils.helpers import TTLCache, is_safe_url
from utils.permissions import FEATURES, group_gate, is_chat_admin, is_owner, require_admin, require_owner
from utils.strings import SUPPORTED
from utils.targets import resolve_target
from utils.tg import arg_text, callback, command, delete_quietly, edit, reply
import utils.tg as tg_helpers
from utils.tg import ALLOWED_PREFIXES

log = logging.getLogger(__name__)
PREFIX_RE = re.compile("^[" + re.escape(ALLOWED_PREFIXES) + "]$")
ID_RE = re.compile(r"^-?\d{1,10}$")


async def _row(ctx, chat_id: int) -> dict:
    return await ctx.db.fetchone("SELECT * FROM groups WHERE chat_id=?", (chat_id,))


def settings_view(row: dict):
    on = lambda v: "✅ ON" if v else "❌ OFF"
    text = "\n".join([header("⚙️ **SETTINGS**"), "",
                      f"🌸 Waifu spawns: **{on(row['waifu_enabled'])}**  (every {row['waifu_interval']}s)",
                      f"🎴 Gacha: **{on(row['gacha_enabled'])}**", f"🎵 Music: **{on(row['music_enabled'])}**",
                      f"🛠 Maintenance: **{on(row['maintenance'])}**", f"⌨️ Prefix: `{clean(row['prefix'], 4)}`",
                      f"🌐 Language: `{row['lang']}`"])
    cid = row["chat_id"]
    kb = M([[B("🌸 Waifu", callback_data=f"set:waifu:{cid}"), B("🎴 Gacha", callback_data=f"set:gacha:{cid}"),
             B("🎵 Music", callback_data=f"set:music:{cid}")]])
    return text, kb


def register(app, ctx):
    cfg, db = ctx.config, ctx.db
    prefix_cache = TTLCache(ttl=120, max_size=2000)

    # ---- custom command prefix: utils.tg.guard asks this lookup which prefix a group uses ----
    async def lookup_prefix(chat_id: int) -> str:
        prefix = prefix_cache.get(chat_id)
        if prefix is None:
            row = await db.fetchone("SELECT prefix FROM groups WHERE chat_id=?", (chat_id,))
            prefix = row["prefix"] if row else "/"
            prefix_cache.set(chat_id, prefix)
        return prefix

    tg_helpers.prefix_lookup = lookup_prefix

    # ---- settings --------------------------------------------------------------------
    @command(app, "settings")
    async def settings_cmd(_, message):
        if not await group_gate(ctx, message):
            return
        text, kb = settings_view(await _row(ctx, message.chat.id))
        await reply(message, text, reply_markup=kb)

    @callback(app, r"^set:(waifu|gacha|music):-?\d{1,15}$")
    async def settings_cb(_, cb):
        _, key, cid = cb.data.split(":")
        if cb.message is None or int(cid) != cb.message.chat.id:
            return await cb.answer("Invalid button.", show_alert=True)
        if not await is_chat_admin(ctx, int(cid), cb.from_user.id):
            return await cb.answer("Only group admins can change settings.", show_alert=True)
        col = FEATURES[key]
        await db.execute(f"UPDATE groups SET {col}=1-{col} WHERE chat_id=?", (int(cid),))  # col is whitelisted
        text, kb = settings_view(await _row(ctx, int(cid)))
        await cb.answer("Updated")
        await edit(cb.message, text, reply_markup=kb)

    async def set_value(message, column: str, value, ok: str):
        await db.execute(f"UPDATE groups SET {column}=? WHERE chat_id=?", (value, message.chat.id))
        await reply(message, ok)

    @command(app, "setprefix")
    async def setprefix_cmd(_, message):
        if not await group_gate(ctx, message) or not await require_admin(ctx, message):
            return
        p = arg_text(message)
        if p == "/":
            await set_value(message, "prefix", "/", "⌨️ Prefix reset to `/`.")
            prefix_cache.set(message.chat.id, "/")
            return
        if not PREFIX_RE.match(p):
            return await reply(message, f"Usage: `/setprefix <one of {ALLOWED_PREFIXES}>` — `/` always keeps working. `/setprefix /` resets.")
        await set_value(message, "prefix", p, f"⌨️ Extra prefix set to `{p}` — try `{p}play song`. `/` still works.")
        prefix_cache.set(message.chat.id, p)

    @command(app, "setlang")
    async def setlang_cmd(_, message):
        if not await group_gate(ctx, message) or not await require_admin(ctx, message):
            return
        lang = arg_text(message).lower()
        if lang not in SUPPORTED:
            return await reply(message, f"Supported languages: {', '.join(SUPPORTED)}")
        await set_value(message, "lang", lang, f"🌐 Language set to `{lang}`.")

    @command(app, "setwaifutimer")
    async def setwaifutimer_cmd(_, message):
        if not await group_gate(ctx, message) or not await require_admin(ctx, message):
            return
        a = arg_text(message)
        if not a.isdigit() or not 30 <= int(a) <= 3600:
            return await reply(message, "Usage: `/setwaifutimer <30-3600>` (seconds)")
        await set_value(message, "waifu_interval", int(a), f"⏱ Waifu timer set to **{int(a)}s**.")

    def toggle(name: str, column: str, value: int, ok: str):
        @command(app, name)
        async def handler(_, message):
            if not await group_gate(ctx, message) or not await require_admin(ctx, message):
                return
            await set_value(message, column, value, ok)
        return handler

    toggle("enablegacha", "gacha_enabled", 1, "🎴 Gacha is now **ON**.")
    toggle("disablegacha", "gacha_enabled", 0, "🎴 Gacha is now **OFF**.")
    toggle("enablemusic", "music_enabled", 1, "🎵 Music is now **ON**.")
    toggle("disablemusic", "music_enabled", 0, "🎵 Music is now **OFF**.")

    @command(app, "maintenance")
    async def maintenance_cmd(_, message):
        # Deliberately not behind group_gate: admins must be able to switch it back off.
        if message.chat.type.name not in {"GROUP", "SUPERGROUP"}:
            return await reply(message, "👥 This command only works in groups.")
        if not await require_admin(ctx, message):
            return
        a = arg_text(message).lower()
        if a not in ("on", "off"):
            return await reply(message, "Usage: `/maintenance on` or `/maintenance off`")
        await db.ensure_group(message.chat.id, message.chat.title, cfg.waifu_default_interval, cfg.waifu_default_enabled)
        await set_value(message, "maintenance", 1 if a == "on" else 0,
                        "🛠 Maintenance mode is **ON** — features are paused here." if a == "on"
                        else "✅ Maintenance mode is **OFF**.")

    # ---- confirmed destructive actions ---------------------------------------------------
    def confirm_kb(kind: str, token: str) -> M:
        return M([[B("✅ Confirm", callback_data=f"reset:{kind}:y:{token}"), B("❌ Cancel", callback_data=f"reset:{kind}:n:{token}")]])

    @command(app, "resetgroup")
    async def resetgroup_cmd(_, message):
        if not await group_gate(ctx, message) or not await require_admin(ctx, message):
            return
        token = secrets.token_hex(3)
        ctx.selections.set(f"reset:{token}", {"admin": message.from_user.id, "chat": message.chat.id}, ttl=60)
        await reply(message, "⚠️ **Reset this group?**\n\nSettings, active waifu spawn, queue and music history for this "
                             "chat will be cleared. Users' collections and balances are **not** touched.",
                    reply_markup=confirm_kb("grp", token))

    @command(app, "resetuser")
    async def resetuser_cmd(_, message):
        if not await require_owner(ctx, message):
            return
        target, tname, _ = await resolve_target(ctx, message)
        if not target:
            return await reply(message, "Usage: `/resetuser @user` (or reply)")
        token = secrets.token_hex(3)
        ctx.selections.set(f"reset:{token}", {"admin": message.from_user.id, "user": target, "name": tname}, ttl=60)
        await reply(message, f"⚠️ **Erase ALL data for {tname}?** Coins, gems, XP, waifus, achievements — everything.",
                    reply_markup=confirm_kb("usr", token))

    @callback(app, r"^reset:(grp|usr):(y|n):[0-9a-f]{6}$")
    async def reset_cb(_, cb):
        _, kind, ans, token = cb.data.split(":")
        st = ctx.selections.get(f"reset:{token}")
        if not st:
            return await cb.answer("This confirmation expired.", show_alert=True)
        if cb.from_user.id != st["admin"]:
            return await cb.answer("Only the person who ran the command can confirm.", show_alert=True)
        ctx.selections.pop(f"reset:{token}")
        if ans == "n":
            await cb.answer("Cancelled")
            return await delete_quietly(cb.message)
        if kind == "grp":
            chat = st["chat"]
            if cb.message is None or cb.message.chat.id != chat or not await is_chat_admin(ctx, chat, cb.from_user.id):
                return await cb.answer("Not allowed.", show_alert=True)
            try:
                await ctx.player.stop(chat)
            except Exception:  # noqa: BLE001
                pass

            def op(c):
                c.execute("UPDATE groups SET prefix='/', lang='en', waifu_enabled=?, waifu_interval=?, gacha_enabled=1, "
                          "music_enabled=1, maintenance=0, last_spawn_at=0 WHERE chat_id=?",
                          (int(cfg.waifu_default_enabled), cfg.waifu_default_interval, chat))
                c.execute("DELETE FROM active_spawns WHERE chat_id=?", (chat,))
                c.execute("DELETE FROM music_queues WHERE chat_id=?", (chat,))
                c.execute("DELETE FROM music_history WHERE chat_id=?", (chat,))
                c.execute("DELETE FROM waifu_guesses WHERE chat_id=?", (chat,))
            await db.transaction(op)
            prefix_cache.set(chat, "/")
            await cb.answer("Done")
            return await edit(cb.message, "🧹 **Group reset.**")
        if not is_owner(ctx, cb.from_user.id):
            return await cb.answer("Owner only.", show_alert=True)
        uid = st["user"]

        def wipe(c):
            for table in ("user_waifus", "achievements", "quests", "user_inventory", "user_settings", "cooldowns",
                          "gacha_history", "waifu_guesses", "daily_rewards", "economy"):
                c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))  # fixed table names, not user input
            c.execute("DELETE FROM marriages WHERE user_id=? OR partner_id=?", (uid, uid))
            c.execute("UPDATE trades SET status='cancelled' WHERE status='pending' AND (from_user=? OR to_user=?)", (uid, uid))
            c.execute("UPDATE users SET xp=0, level=1, songs_played=0, messages_count=0, favorite_waifu_id=NULL WHERE user_id=?", (uid,))
        await db.transaction(wipe)
        await db.ensure_user(uid)
        await cb.answer("Done")
        await edit(cb.message, f"🧹 **{clean(st['name'], 40)}** has been reset.")

    # ---- owner: economy / catalogue ----------------------------------------------------------
    async def grant_cmd(message, field: str):
        if not await require_owner(ctx, message):
            return
        target, tname, rest = await resolve_target(ctx, message)
        amt = (rest.split() or [""])[-1].replace(",", "")
        if not target or not ID_RE.match(amt) or not -1_000_000_000 <= int(amt) <= 1_000_000_000:
            return await reply(message, f"Usage: `/{message.command[0]} @user <amount>` (negative removes)")
        try:
            await ctx.economy.grant(target, **{field: int(amt)})
        except EconomyError:
            return await reply(message, "⚠️ That would make the balance negative.")
        icon = "💰" if field == "coins" else "💎"
        await reply(message, f"{icon} Gave **{tname}** **{int(amt):,}** {field}.")
        log.info("Owner %s adjusted %s of %s by %s", message.from_user.id, field, target, amt)

    @command(app, "addcoins")
    async def addcoins_cmd(_, message):
        await grant_cmd(message, "coins")

    @command(app, "addgems")
    async def addgems_cmd(_, message):
        await grant_cmd(message, "gems")

    @command(app, "addwaifu")
    async def addwaifu_cmd(_, message):
        if not await require_owner(ctx, message):
            return
        parts = [p.strip() for p in arg_text(message).split("|")]
        if len(parts) < 4:
            return await reply(message, "Usage: `/addwaifu name | anime | rarity | image_url | alias1, alias2`\n"
                                        f"Rarities: {', '.join(RARITY_ORDER)}")
        name, anime, rarity, url = parts[0], parts[1], parts[2].upper(), parts[3]
        aliases = [a.strip() for a in parts[4].split(",") if a.strip()] if len(parts) > 4 else []
        if rarity not in RARITY_ORDER or not name or not anime or not url.startswith("https://") or not is_safe_url(url):
            return await reply(message, "❌ Check the rarity and use a public https image URL.")
        import json
        await db.execute("INSERT INTO waifus (name,anime,image_url,rarity,aliases,value,source,source_id) "
                         "VALUES (?,?,?,?,?,?, 'manual', ?)",
                         (name[:80], anime[:80], url, rarity, json.dumps(aliases[:12]), RARITY_VALUES[rarity],
                          secrets.token_hex(6)))
        await reply(message, f"✅ Added **{clean(name, 60)}** ({rarity}).")

    @command(app, "removewaifu")
    async def removewaifu_cmd(_, message):
        if not await require_owner(ctx, message):
            return
        q = arg_text(message)
        if not q:
            return await reply(message, "Usage: `/removewaifu <id or name>` (disables it; collections stay intact)")
        if q.isdigit():
            n = await db.execute("UPDATE waifus SET enabled=0 WHERE id=?", (int(q),))
        else:
            found = await ctx.waifus.search_catalogue(q, 1)
            n = await db.execute("UPDATE waifus SET enabled=0 WHERE id=?", (found[0].id,)) if found else 0
        await reply(message, "🗑 Character disabled." if n else "❌ Character not found.")

    @command(app, "givewaifu")
    async def givewaifu_cmd(_, message):
        if not await require_owner(ctx, message):
            return
        target, tname, rest = await resolve_target(ctx, message)
        if not target or not rest:
            return await reply(message, "Usage: `/givewaifu @user <name or id>`")
        w = await ctx.waifus.get(int(rest)) if rest.isdigit() else (await ctx.waifus.search_catalogue(rest, 1) or [None])[0]
        if not w:
            return await reply(message, "❌ Character not found.")
        await db.execute("INSERT INTO user_waifus (user_id, waifu_id) VALUES (?,?)", (target, w.id))
        await reply(message, f"🎁 Gave **{clean(w.name, 60)}** to **{tname}**.")

    @command(app, "syncwaifus")
    async def sync_cmd(_, message):
        if not await require_owner(ctx, message):
            return
        status = await reply(message, "🔄 Syncing characters from AniList… this can take about 20 seconds.")
        n = await ctx.anime.sync_characters()
        await edit(status, f"✅ Character pool now has **{n}** characters.")

    @command(app, "broadcast")
    async def broadcast_cmd(_, message):
        if not await require_owner(ctx, message):
            return
        text = arg_text(message)
        if not text or len(text) > 3500:
            return await reply(message, "Usage: `/broadcast <message>` (max 3500 chars)")
        groups = await db.fetchall("SELECT chat_id FROM groups")
        status = await reply(message, f"📣 Sending to **{len(groups)}** groups…")
        ok = failed = 0
        for g in groups:
            try:
                await ctx.bot.send_message(g["chat_id"], f"📣 **Announcement**\n\n{text}")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                wait = getattr(exc, "value", None)
                if isinstance(wait, int):
                    await asyncio.sleep(min(wait, 30))
            await asyncio.sleep(0.1)
        await edit(status, f"📣 Broadcast done: **{ok}** delivered, **{failed}** failed.")
