"""Entry point: python bot.py"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from config import Config
from database import Database
import time

import httpx

from services.anime_service import AnimeService
from services.assistant import make_assistant_check
from services.context import Ctx
from services.economy_service import Economy
from services.gacha_service import GachaService
from services.image_service import ImageService
from services.music_api import MusicAPI
from services.progress_service import Activity, Progress
from services.waifu_service import WaifuService
from services.player import PyTgCallsBackend, Player
from services.queue import QueueManager

log = logging.getLogger("bot")


class RedactFilter(logging.Filter):
    """Removes secrets from every log line."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) > 6]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self.secrets:
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, None
        return True


def setup_logging(cfg: Config) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    handler.addFilter(RedactFilter([cfg.bot_token, cfg.api_hash, cfg.session_string]))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "pyrogram", "pytgcalls", "ntgcalls"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


BOT_COMMANDS = [
    ("start", "Open the main menu"), ("help", "All commands"), ("play", "Play a song in the voice chat"),
    ("search", "Search for music"), ("pause", "Pause playback"), ("resume", "Resume playback"),
    ("skip", "Skip the current track"), ("stop", "Stop and leave"), ("queue", "Show the queue"),
    ("np", "Now playing"), ("volume", "Set the volume"), ("lyrics", "Show lyrics"),
    ("g", "Guess the waifu"), ("collection", "Your waifu collection"), ("waifu", "Character details"),
    ("gacha", "Open the gacha"), ("pull", "Single gacha pull"), ("10pull", "Ten gacha pulls"),
    ("daily", "Daily reward"), ("weekly", "Weekly reward"), ("balance", "Coins and gems"),
    ("profile", "Your profile"), ("quests", "Daily quests"), ("achievements", "Your achievements"),
    ("leaderboard", "Rankings"), ("rps", "Rock paper scissors"), ("trade", "Trade waifus"),
    ("settings", "Group settings"),
]


async def health_server(ctx: Ctx) -> None:
    """Tiny HTTP responder so hosts that require an open port (e.g. Render web services) stay happy."""
    port = os.getenv("PORT")
    if not port or not port.isdigit():
        return

    async def handle(reader, writer):
        try:
            await asyncio.wait_for(reader.read(1024), timeout=5)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", int(port))
    log.info("Health endpoint listening on port %s", port)
    async with server:
        await server.serve_forever()


async def housekeeping(ctx: Ctx) -> None:
    while True:
        await asyncio.sleep(600)
        try:
            await ctx.db.purge_expired()
            week_ago = time.time() - 7 * 86400
            await ctx.db.execute("DELETE FROM waifu_guesses WHERE created_at<?", (week_ago,))
            await ctx.db.execute("DELETE FROM active_spawns WHERE claimed_by IS NOT NULL AND claimed_at<?", (week_ago,))
            await ctx.db.execute("DELETE FROM trades WHERE status!='pending' AND created_at<?", (week_ago,))
            await ctx.images.prune()
        except Exception:
            log.exception("Housekeeping failed")


async def main() -> None:
    cfg = Config.from_env()
    setup_logging(cfg)
    missing = cfg.missing_required()
    if missing:
        log.error("Missing required settings in .env: %s", ", ".join(missing))
        sys.exit(1)

    from pyrogram import Client, idle
    from pyrogram.enums import ParseMode
    from handlers import register_all
    from handlers.music import make_notifier

    db = Database(cfg.database_path)
    await db.connect()
    music = MusicAPI(cfg.music_api_url, cfg.music_engines, cfg.search_cache_ttl)

    bot = Client("bot", api_id=cfg.api_id, api_hash=cfg.api_hash, bot_token=cfg.bot_token,
                 in_memory=True, parse_mode=ParseMode.MARKDOWN)
    assistant = Client("assistant", api_id=cfg.api_id, api_hash=cfg.api_hash,
                       session_string=cfg.session_string, in_memory=True)

    http = httpx.AsyncClient(timeout=20, headers={"User-Agent": "ZenithBot/1.0"}, follow_redirects=True,
                             limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
    ctx = Ctx(config=cfg, db=db, music=music, player=None, bot=bot, assistant=assistant, http=http)
    ctx.economy, ctx.progress = Economy(db), Progress(db)
    ctx.waifus = WaifuService(db, cfg.rarity_weights)
    ctx.gacha = GachaService(db, ctx.waifus, cfg.rarity_weights, cfg.pity_threshold, cfg.ten_pull_discount)
    ctx.anime = AnimeService(db, http, cfg.rarity_weights, cfg.anilist_pages)
    ctx.images = ImageService(db, cfg, http)
    ctx.activity = Activity(cfg.xp_cooldown_seconds)
    ctx.player = Player(QueueManager(cfg.max_queue_size), music, db, PyTgCallsBackend(assistant),
                        notifier=make_notifier(ctx), assistant_check=make_assistant_check(ctx))
    register_all(bot, ctx)
    ctx.background.append(housekeeping)
    ctx.background.append(health_server)

    await bot.start()
    await assistant.start()
    await ctx.player.start()
    ctx.bot_username = (await bot.get_me()).username or ""
    try:
        from pyrogram.types import BotCommand
        await bot.set_bot_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
    except Exception:
        log.warning("Could not set the bot command menu (non-fatal)")
    log.info("%s is online as @%s", cfg.bot_name, ctx.bot_username)

    tasks = [asyncio.create_task(fn(ctx), name=fn.__name__) for fn in ctx.background]
    try:
        await idle()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for stop in (assistant.stop, bot.stop, music.close, http.aclose, db.close):
            try:
                await stop()
            except Exception:
                log.exception("Shutdown step failed")
        log.info("Stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
