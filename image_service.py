"""Images: real character artwork (sent by URL once, then by cached Telegram file_id) and
optional AI-generated *non-character* cards. Falls back silently when nothing is configured."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from urllib.parse import quote

import httpx

from utils.helpers import is_safe_url

log = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 5_000_000
MAX_CACHE_BYTES = 40_000_000
MAX_FILE_IDS = 5000
# Telegram errors that mean "this image is bad" (vs. chat-level problems, which must propagate).
IMAGE_ERRORS = {"WebpageCurlFailed", "WebpageMediaEmpty", "MediaEmpty", "PhotoInvalidDimensions",
                "PhotoSaveFileInvalid", "WebpageNotFound", "MediaInvalid", "ImageProcessFailed",
                "PhotoExtInvalid", "PhotoInvalid", "PhotoCropSizeSmall"}
STALE_FILE_ID_ERRORS = {"FileIdInvalid", "FileReferenceExpired", "MediaEmpty", "FileReferenceEmpty"}


class ImageService:
    def __init__(self, db, config, http: httpx.AsyncClient | None = None, cache_dir: str = "data/cache"):
        self.db, self.cfg, self.http = db, config, http
        self.cache_dir = cache_dir
        self._last_generate = 0.0

    # ---- character artwork ----------------------------------------
    async def send_photo(self, bot, chat_id: int, url: str, caption: str, reply_markup=None, reply_to: int | None = None):
        """Send artwork. Returns the sent Message, or None if the image can't be sent
        (caller should fall back to a text message)."""
        if not url or not is_safe_url(url):
            return None
        kw = {"caption": caption, "reply_markup": reply_markup}
        if reply_to:
            kw["reply_to_message_id"] = reply_to
        row = await self.db.fetchone("SELECT file_id FROM image_cache WHERE url=?", (url,))
        if row:
            try:
                return await bot.send_photo(chat_id, row["file_id"], **kw)
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ not in STALE_FILE_ID_ERRORS:
                    raise
                log.info("Cached file_id rejected (%s); refetching", type(exc).__name__)
                await self.db.execute("DELETE FROM image_cache WHERE url=?", (url,))
        try:
            msg = await bot.send_photo(chat_id, url, **kw)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ not in IMAGE_ERRORS:
                raise  # chat-level problem (flood, forbidden, ...): let the caller decide
            log.warning("Image URL rejected (%s): %s", type(exc).__name__, url[:80])
            return None
        try:
            file_id = msg.photo.file_id
            await self.db.execute(
                "INSERT INTO image_cache (url,file_id,updated) VALUES (?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET file_id=excluded.file_id, updated=excluded.updated",
                (url, file_id, time.time()))
        except Exception:  # noqa: BLE001
            pass
        return msg

    async def prune(self) -> None:
        await self.db.execute(
            "DELETE FROM image_cache WHERE url NOT IN (SELECT url FROM image_cache ORDER BY updated DESC LIMIT ?)",
            (MAX_FILE_IDS,))
        self._evict_disk()

    # ---- AI cards (never used for waifu characters) -----------------
    def ai_enabled(self) -> bool:
        return bool(self.cfg.image_api_url and "{prompt}" in self.cfg.image_api_url and self.http)

    async def generate_card(self, prompt: str) -> str | None:
        """Fetch an AI image for `prompt` from IMAGE_API_URL (a URL template containing {prompt}).
        Returns a local file path or None. Cached on disk by prompt hash."""
        if not self.ai_enabled():
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        path = os.path.join(self.cache_dir, hashlib.sha1(prompt.encode()).hexdigest()[:20] + ".img")
        if os.path.exists(path):
            return path
        if time.monotonic() - self._last_generate < 10:  # cost guard
            return None
        self._last_generate = time.monotonic()
        url = self.cfg.image_api_url.replace("{prompt}", quote(prompt[:300]))
        headers = {"Authorization": f"Bearer {self.cfg.image_api_key}"} if self.cfg.image_api_key else {}
        try:
            resp = await self.http.get(url, headers=headers, timeout=60, follow_redirects=True)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code != 200 or not ctype.startswith("image/") or len(resp.content) > MAX_IMAGE_BYTES:
                return None
            with open(path, "wb") as fh:
                fh.write(resp.content)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("AI image request failed: %s", type(exc).__name__)
            return None
        self._evict_disk()
        return path

    def _evict_disk(self) -> None:
        try:
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir)]
            files = [(os.path.getmtime(f), os.path.getsize(f), f) for f in files if os.path.isfile(f)]
        except OSError:
            return
        total = sum(s for _, s, _ in files)
        for _, size, f in sorted(files):
            if total <= MAX_CACHE_BYTES:
                break
            try:
                os.remove(f)
                total -= size
            except OSError:
                pass

    async def send_card(self, bot, chat_id: int, prompt: str, caption: str, reply_markup=None):
        """AI card if available, else None (caller sends plain text)."""
        path = await self.generate_card(prompt)
        if not path:
            return None
        try:
            return await bot.send_photo(chat_id, path, caption=caption, reply_markup=reply_markup)
        except Exception as exc:  # noqa: BLE001
            log.warning("Card send failed: %s", type(exc).__name__)
            return None
