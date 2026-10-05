"""Inline mode: @YourBot <query>. Other modules add providers with @provider."""
from __future__ import annotations

import hashlib
import logging

from pyrogram.handlers import InlineQueryHandler
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
                            InputTextMessageContent)

from services.music_api import MusicAPIError
from utils.formatting import clean
from utils.tg import guard

log = logging.getLogger(__name__)
PROVIDERS = []  # async fn(ctx, query, user) -> list[InlineQueryResult]


def provider(fn):
    PROVIDERS.append(fn)
    return fn


def rid(*parts: object) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()


def article(result_id: str, title: str, description: str, text: str, thumb: str = "",
            switch_query: str | None = None) -> InlineQueryResultArticle:
    markup = None
    if switch_query is not None:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔎 Search again", switch_inline_query_current_chat=switch_query)]])
    kwargs = {"thumb_url": thumb} if thumb else {}
    return InlineQueryResultArticle(id=result_id, title=title[:100], description=description[:200],
                                    input_message_content=InputTextMessageContent(text),
                                    reply_markup=markup, **kwargs)


@provider
async def music_provider(ctx, query: str, user=None):
    if len(query) < 2:
        return [article("help-music", "🎵 Type a song name",
                        f"Search music with {ctx.config.bot_name}",
                        f"🎵 **{clean(ctx.config.bot_name)}**\nType `@{ctx.bot_username} song name` to search music.")]
    if ctx.limiter.check("inline-search", id(ctx), 30, 30):  # soft global guard for the API
        return []
    try:
        tracks = await ctx.music.search(query, 8)
    except MusicAPIError:
        return []
    out = []
    for t in tracks:
        lines = [f"🎵 **{clean(t.title, 100)}**"]
        if t.artist and t.artist != "Unknown":
            lines.append(f"👤 {clean(t.artist)}")
        if t.album:
            lines.append(f"💿 {clean(t.album)}")
        lines += ["", f"__Play it in a group with__ `/play {clean(t.title, 60)}`"]
        out.append(article(rid("m", t.source, t.source_id), t.title, f"💿 {t.album}" if t.album else "Music",
                           "\n".join(lines), t.thumbnail, switch_query=query))
    return out


def register(app, ctx):
    async def on_inline(_, iq):
        query = (iq.query or "").strip()[:100]
        results = []
        for p in PROVIDERS:
            try:
                results += await p(ctx, query, iq.from_user)
            except Exception:
                log.exception("Inline provider %s failed", getattr(p, "__name__", "?"))
        try:
            await iq.answer(results[:50], cache_time=30, is_personal=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Inline answer failed: %s", type(exc).__name__)

    app.add_handler(InlineQueryHandler(guard(on_inline)))
