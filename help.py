"""/help with inline category buttons. COMMANDS is the single source of truth for command docs."""
from __future__ import annotations

from pyrogram.types import InlineKeyboardButton as B
from pyrogram.types import InlineKeyboardMarkup as M

from utils.formatting import SEP, header
from utils.strings import t
from utils.tg import callback, command, edit_any, reply

CATEGORIES = [("music", "🎵 MUSIC"), ("waifu", "🌸 WAIFU"), ("gacha", "🎴 GACHA"), ("economy", "💰 ECONOMY"),
              ("fun", "🎮 FUN"), ("profile", "👤 PROFILE"), ("achievements", "🏆 ACHIEVEMENTS"), ("admin", "⚙️ ADMIN")]

COMMANDS: dict[str, list[tuple[str, str]]] = {
    "music": [("/play <song>", "search & queue a track"), ("/search <song>", "search only"), ("/pause /resume", "control playback"),
              ("/skip /stop", "next track / stop"), ("/queue", "show the queue"), ("/np", "now playing"),
              ("/volume <1-200>", "set volume"), ("/seek <time>", "jump, e.g. 1:30"), ("/replay", "restart the track"),
              ("/shuffle /clear", "manage the queue"), ("/remove <n>", "remove a queued track"),
              ("/lyrics [song]", "show lyrics when available"), ("/history /toptracks /mytracks", "play statistics")],
    "waifu": [("/g <name>", "guess the waifu that appeared"), ("/collection /harem /waifus", "your characters"),
              ("/waifu <name>", "character details"), ("/favwaifu /setwaifu <name>", "profile favorite"),
              ("/favorite /unfavorite <name>", "protect a character"), ("/sell <name>", "sell for coins"),
              ("/give @user <name>", "gift a character"), ("/trade @user <mine> | <theirs>", "propose a trade"),
              ("/waifutimer /waifuon /waifuoff", "spawn settings (admins)"), ("/randomwaifu /waifuworld", "explore")],
    "gacha": [("/gacha", "banners & balance"), ("/pull /summon /roll", "single pull"), ("/10pull /multi", "ten pulls (10% off)")],
    "economy": [("/balance", "coins & gems"), ("/daily /weekly", "streak rewards"), ("/work /earn", "earn coins"),
                ("/pay @user <n>", "send coins"), ("/leaderboard /topcoins /topxp /topwaifus /topmusic", "rankings"),
                ("/quests /claim", "daily quests"), ("/level /xp /rank", "your level")],
    "fun": [("/hug /kiss /slap /pat /cuddle /poke /bonk /highfive", "react to someone"),
            ("/dance /wave /smile /laugh /blush /cry /pout /shrug /stare /yeet /tickle /bite", "express yourself"),
            ("/crush /love /ship /shipname /marry /divorce", "romance"), ("/rate /roast /compliment /kill", "playful"),
            ("/8ball /coinflip /dice /choose /rps /fortune", "games of chance"),
            ("/truth /dare /wouldyourather /joke /fact /quote /meme", "party"),
            ("/anime /character /randomanime /avatar", "anime lookups"),
            ("/battle /fight /duel", "combat"), ("/pet /feed /hunt /fish /treasure /inventory", "adventure")],
    "profile": [("/profile [@user]", "your card"), ("/stats", "detailed statistics")],
    "achievements": [("/achievements", "your trophies"), ("/quests", "daily quests")],
    "admin": [("/settings", "group settings"), ("/setprefix <p>", "extra command prefix"), ("/setlang en", "language"),
              ("/setwaifutimer <sec>", "spawn interval"), ("/enablegacha /disablegacha", "toggle gacha"),
              ("/enablemusic /disablemusic", "toggle music"), ("/maintenance on|off", "pause the bot here"),
              ("/resetgroup", "reset group data"), ("Owner only", "/broadcast /addwaifu /removewaifu /addcoins /addgems /givewaifu /resetuser /syncwaifus")],
}


def home_text(name: str) -> str:
    return f"{header(t('help_title'))}\n\n{t('help_pick')}\n\n__{name} commands are grouped by feature.__"


def home_kb() -> M:
    rows, row = [], []
    for key, label in CATEGORIES:
        row.append(B(label, callback_data=f"help:cat:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return M(rows)


def category_text(key: str) -> str:
    label = dict(CATEGORIES).get(key, key)
    lines = [header(f"**{label}**"), ""]
    lines += [f"`{c}` — {d}" for c, d in COMMANDS.get(key, [])]
    return "\n".join(lines) + f"\n\n{SEP}"


def register(app, ctx):
    @command(app, ["help", "commands"])
    async def help_cmd(_, message):
        await reply(message, home_text(ctx.config.bot_name), reply_markup=home_kb())

    @callback(app, r"^help:(home|cat:[a-z]{3,14})$")
    async def help_cb(_, cb):
        parts = cb.data.split(":")
        await cb.answer()
        if parts[1] == "home":
            await edit_any(cb.message, home_text(ctx.config.bot_name), reply_markup=home_kb())
            return
        key = parts[2]
        if key not in COMMANDS:
            return await cb.answer("Unknown category.", show_alert=True)
        await edit_any(cb.message, category_text(key),
                       reply_markup=M([[B("◀️ Back", callback_data="help:home")]]))
