"""Callback-query handlers for the music UI. Every payload is validated."""
from __future__ import annotations

import logging

from models.music import Track
from services.queue import QueueError
from utils.formatting import clean, np_text, queue_page
from utils.helpers import safe_callback_parts
from utils.keyboards import np_keyboard, queue_keyboard
from utils.permissions import can_control, cb_gate
from utils.tg import callback, delete_quietly, edit

from handlers.music import lyrics_for, send_lyrics

log = logging.getLogger(__name__)


def register(app, ctx):
    @callback(app, r"^music:sel:[0-9a-f]{6}:\d{1,2}$")
    async def select_cb(_, cb):
        parts = safe_callback_parts(cb.data, "music", 4)
        if not parts or not parts[3].isdigit():
            return await cb.answer("Invalid button.", show_alert=True)
        token, idx = parts[2], int(parts[3])
        sel = ctx.selections.get(token)
        if sel is None:
            return await cb.answer("This search expired. Run the command again.", show_alert=True)
        if sel["user"] != cb.from_user.id:
            return await cb.answer("That's not your search — start your own with /play.", show_alert=True)
        if cb.message is None or cb.message.chat.id != sel["chat"] or not 0 <= idx < len(sel["tracks"]):
            return await cb.answer("Invalid button.", show_alert=True)
        if not await cb_gate(ctx, cb, "music"):
            return
        ctx.selections.pop(token)  # one use: blocks double-click duplicates
        track = Track.from_dict(sel["tracks"][idx].to_dict())
        track.requester_id = cb.from_user.id
        track.requester_name = ("@" + cb.from_user.username) if cb.from_user.username else cb.from_user.first_name
        log.info("Track selected: %s", track.title)
        await cb.answer("Adding…")
        try:
            pos = await ctx.player.enqueue(cb.message.chat.id, track)
        except QueueError as exc:
            return await edit(cb.message, f"⚠️ {clean(exc, 200)}")
        if pos == 0:
            await delete_quietly(cb.message)  # the now-playing card replaces it
        else:
            await edit(cb.message, f"✅ Added **{clean(track.title, 70)}** to the queue at position **{pos}**.")

    @callback(app, r"^music:close:[0-9a-f]{6}$")
    async def close_cb(_, cb):
        parts = safe_callback_parts(cb.data, "music", 3)
        sel = ctx.selections.get(parts[2]) if parts else None
        if sel is not None and sel["user"] != cb.from_user.id:
            return await cb.answer("That's not your search.", show_alert=True)
        if parts:
            ctx.selections.pop(parts[2])
        await cb.answer()
        await delete_quietly(cb.message)

    async def controlled(cb, action: str, fn, done: str):
        if not await cb_gate(ctx, cb, "music"):
            return
        chat_id = cb.message.chat.id
        if not await can_control(ctx, chat_id, cb.from_user.id):
            return await cb.answer("Only admins or the requester can use this.", show_alert=True)
        try:
            await fn(chat_id)
        except QueueError as exc:
            return await cb.answer(clean(exc, 150), show_alert=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Callback %s failed: %s", action, type(exc).__name__)
            return await cb.answer("That did not work. Try again.", show_alert=True)
        await cb.answer(done)

    @callback(app, r"^music:pause$")
    async def pause_cb(_, cb):
        await controlled(cb, "pause", ctx.player.pause, "⏸ Paused")

    @callback(app, r"^music:resume$")
    async def resume_cb(_, cb):
        await controlled(cb, "resume", ctx.player.resume, "▶️ Resumed")

    @callback(app, r"^music:skip$")
    async def skip_cb(_, cb):
        await controlled(cb, "skip", ctx.player.skip, "⏭ Skipped")

    @callback(app, r"^music:stop$")
    async def stop_cb(_, cb):
        await controlled(cb, "stop", ctx.player.stop, "⏹ Stopped")

    @callback(app, r"^music:lyrics$")
    async def lyrics_cb(_, cb):
        if not await cb_gate(ctx, cb, "music"):
            return
        wait = ctx.limiter.check("lyrics", cb.from_user.id, 3, 30)
        if wait:
            return await cb.answer(f"Try again in {int(wait) + 1}s.", show_alert=True)
        cur = ctx.player.queues.get(cb.message.chat.id).current
        if not cur:
            return await cb.answer("Nothing is playing.", show_alert=True)
        await cb.answer("Fetching lyrics…")
        try:
            text = await lyrics_for(ctx, cur)
        except Exception:  # noqa: BLE001
            text = None
        await send_lyrics(ctx, cb.message.chat.id, text)

    @callback(app, r"^queue:show$")
    async def queue_show_cb(_, cb):
        if not await cb_gate(ctx, cb, "music"):
            return
        q = ctx.player.queues.get(cb.message.chat.id)
        text, page, pages = queue_page(q.current, q.upcoming, 1)
        await cb.answer()
        await ctx.bot.send_message(cb.message.chat.id, text, reply_markup=queue_keyboard(page, pages))

    @callback(app, r"^queue:pg:\d{1,3}$")
    async def queue_page_cb(_, cb):
        parts = safe_callback_parts(cb.data, "queue", 3)
        if not parts or not parts[2].isdigit():
            return await cb.answer("Invalid button.", show_alert=True)
        q = ctx.player.queues.get(cb.message.chat.id)
        text, page, pages = queue_page(q.current, q.upcoming, int(parts[2]))
        await cb.answer()
        await edit(cb.message, text, reply_markup=queue_keyboard(page, pages))

    @callback(app, r"^queue:(noop|close)$")
    async def queue_misc_cb(_, cb):
        await cb.answer()
        if cb.data == "queue:close":
            await delete_quietly(cb.message)
