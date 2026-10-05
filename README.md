# Zenith — Telegram Music + Anime Entertainment Bot

Voice-chat music, an anime waifu collection game, gacha, a virtual economy, quests, achievements and
60+ fun commands — in one modular Python project that runs on **Android Termux** (or Docker/Render).

> **Read this first — things that are not guaranteed**
> * The music backend ([mohd-baquir-qureshi/music-api](https://github.com/mohd-baquir-qureshi/music-api)) is a
>   README-only repository whose own notice says that version was **deprecated on 13 May 2024**. The client is
>   written to its documented routes, but the hosted Worker may be dead. `MUSIC_API_URL` is configurable;
>   if it is down the bot says so instead of crashing. It scrapes commercial services (Gaana, JioSaavn, Hungama,
>   Wynk), so check licensing/ToS before running a public bot with it.
> * Search results from that API contain **title + thumbnail only** (no artist / album / duration). The UI adapts.
> * Voice calls use `py-tgcalls` 2.x, which depends on a native `ntgcalls` wheel. Wheels for **Python 3.14 on
>   Android/ARM64 may not exist** — see *Compatibility*.
> * The Pyrogram/PyTgCalls code was developed without a live Telegram connection. The queue, player logic,
>   database, economy, gacha, waifu and wiring are covered by 58 automated tests; real-world voice playback has
>   to be verified on your device.

## Features
| Area | Highlights |
|---|---|
| 🎵 Music | `/play /search /pause /resume /skip /stop /queue /np /volume /lyrics /clear /replay /shuffle /remove /seek /history /toptracks /mytracks`; independent queue per group; auto-advance; fresh stream URL right before each play; inline mode |
| 🌸 Waifus | Timed spawns (default 120 s, only in active groups), `/g <name>` atomic first-correct-wins claim, aliases, 8 rarities, collection/favorites, `/sell /give /trade` with confirmation |
| 🎴 Gacha | 3 DB-driven banners, 1× / 10× pulls, pity, edit-based animation with Skip |
| 💰 Economy | coins + gems (virtual only), `/daily` streaks, `/weekly /work /earn /pay`, leaderboards |
| 🏆 Progress | XP/levels, daily quests, 10 achievements, `/profile /stats` |
| 🎮 Fun | 60 commands: reactions with anime GIFs, ship/marry, 8ball, dice, `/rps`, `/duel`, `/battle`, pets, fishing… |
| ⚙️ Admin | settings, prefix, feature toggles, maintenance, owner tools |

## Architecture
```
bot.py            entry point: wiring, logging (secret redaction), background tasks, health endpoint
config.py         env-driven settings (+ rarity weights)       database.py   SQLite (WAL), atomic transactions
handlers/         Telegram layer: start help music callbacks inline gacha waifu economy profile fun admin
services/         music_api  player  queue  assistant  anime_service  image_service  waifu_service
                  gacha_service  economy_service  progress_service  context
models/ utils/    dataclasses, formatting, keyboards, permissions, helpers (matching, TTL cache, rate limiter)
tests/            58 tests (stdlib unittest)
```
* **Voice architecture:** a Bot-API bot cannot join voice chats. The bot account handles commands; an
  **assistant user account** (`SESSION_STRING`) joins the voice chat through `py-tgcalls`. The assistant is
  auto-added to the group when the bot may create invite links.
* **Isolation:** every group has its own `GroupQueue` + lock; one group's failure never touches another.
* **One scheduler:** a single loop spawns waifus for all groups; no per-group infinite loops.
* **Safety:** all SQL is parameterized; callback data is validated and owner-locked; stream URLs must be
  public http(s); no shell commands are built from user input; caches are TTL/size bounded.

## Compatibility (verify before installing)
`requirements.txt` lists `pyrofork`, `py-tgcalls`, `httpx`, `python-dotenv`. I could not test installs offline, so
check on the device first:
```bash
python --version
pip download py-tgcalls ntgcalls --no-deps -d /tmp/wheels   # does a wheel exist for your platform?
```
* If `pip` reports "no matching distribution" for `ntgcalls`/`py-tgcalls`, Python 3.14 (or the Android platform tag)
  is not supported yet. **Use Python 3.12 or 3.13**: Termux keeps older versions in its repos
  (`pkg search python`), or use `proot-distro` (Ubuntu) and `apt install python3.12`.
* `tgcrypto` is optional (needs `pkg install clang`). The bot works without it, just slower.
* If voice calls cannot run on your device at all, deploy with Docker/Render (below) — Linux x86-64 wheels exist.

## Setup on Android Termux
```bash
pkg update && pkg upgrade -y
pkg install python git ffmpeg -y
git clone <your-repo-url> telegram-anime-music-bot
cd telegram-anime-music-bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```
If `venv` misbehaves on Termux, skip it: `pip install --user -r requirements.txt` works too.

### 1. BotFather
1. Open **@BotFather** → `/newbot` → name and username → copy the token into `BOT_TOKEN`.
2. `/setprivacy` → *Disable* — **required**, otherwise the bot cannot see group messages (waifu spawns and XP need them).
3. `/setinline` → set a placeholder such as `song, waifu, anime, joke…` to enable inline mode.
4. Optional: `/setjoingroups` → Enable. The bot sets its own command menu on start.

### 2. API ID / API HASH
Log in at <https://my.telegram.org> → *API development tools* → create an app → copy `API_ID` and `API_HASH`.

### 3. Assistant account + SESSION_STRING
Use a **separate** Telegram account (a spare SIM/number) as the assistant — not your personal account.
```bash
python generate_session.py      # enter the assistant's phone number and the login code
```
Paste the printed string into `.env` as `SESSION_STRING`. Treat it like a password.
In each group: start a voice chat, and make the bot an **admin with "Invite users"** so it can add the assistant
(or add the assistant manually).

### 4. FFmpeg
`pkg install ffmpeg -y` — verify with `ffmpeg -version`. The bot checks this itself and tells users if it is missing.

### 5. Configuration (`.env`)
| Variable | Meaning |
|---|---|
| `API_ID`, `API_HASH`, `BOT_TOKEN`, `SESSION_STRING` | Telegram credentials (never printed or logged) |
| `MUSIC_API_URL` | Music API base URL (default `https://musicapi.x007.workers.dev`) |
| `MUSIC_ENGINES` | Engines tried in order: `gaama,seevn,hunjama,mtmusic,wunk` (lyrics only work for `gaama`) |
| `DATABASE_PATH` | SQLite file (default `data/bot.db`) |
| `BOT_NAME` | Branding (default `Zenith`) |
| `OWNER_IDS` | Comma-separated Telegram user IDs allowed to use owner commands |
| `WAIFU_DEFAULT_INTERVAL` / `WAIFU_DEFAULT_ENABLED` / `WAIFU_MIN_MESSAGES` | Spawn defaults for new groups |
| `ANILIST_PAGES` | How many AniList pages (50 chars each) to import (default 12) |
| `RARITY_WEIGHTS` | e.g. `COMMON=40,UNCOMMON=25,RARE=15,EPIC=10,LEGENDARY=6,MYTHIC=2.5,SSR=1,UR=0.5` |
| `IMAGE_API_URL`, `IMAGE_API_KEY` | Optional AI card generator (see below) |

**Anime data:** characters, artwork and aliases come from the public [AniList GraphQL API](https://graphql.anilist.co)
(polite 1.2 s spacing, 7-day SQLite cache, stale cache used if the API is down). Rarity is rank-based on
favourites. The first start imports ~600 characters (~20 s); if the pool is empty an owner can run `/syncwaifus`
or add characters with `/addwaifu`. Waifu artwork is always the real AniList image — never AI-generated.

**AI images (optional):** set `IMAGE_API_URL` to a GET URL template containing `{prompt}` that returns an image,
e.g. `https://your-provider.example/generate?prompt={prompt}`, plus `IMAGE_API_KEY` if it needs a bearer token.
Used only for non-character cards (start/gacha banners); results are cached on disk (40 MB cap) and rate-limited.
Without it the bot sends polished text cards.

### 6. Run in the background (Termux)
```bash
termux-wake-lock                              # keep the CPU awake (also pin Termux in the notification)
nohup python bot.py > bot.log 2>&1 &          # start detached
ps aux | grep bot.py                          # is it running?
tail -f bot.log                               # follow logs
pkill -f bot.py                               # stop
```
Disable battery optimisation for Termux in Android settings, or Android may kill the process.

## Deploy on Render
`render.yaml` + `Dockerfile` are included (Docker is required because Render's native Python runtime has no FFmpeg).
1. Push the repo to GitHub. 2. Render → *New → Blueprint* → select the repo.
3. Enter the secret env vars (`API_ID`, `API_HASH`, `BOT_TOKEN`, `SESSION_STRING`, `OWNER_IDS`) in the dashboard.
4. The blueprint uses a **background worker** (paid) with a 1 GB disk at `/data` so the SQLite database survives
   deploys. Free web services sleep and have an ephemeral disk, so they are not suitable. `PORT` is honoured by
   a tiny health endpoint if you do choose a web service.

## Command reference
Send `/help` in the bot — it is generated from the same table the tests check against the registered handlers.

## Database schema (SQLite)
`users, groups, economy, user_inventory, waifus, user_waifus, banners, gacha_history, daily_rewards,
music_queues, music_history, user_settings, waifu_guesses, active_spawns, cooldowns, achievements, quests,
trades, marriages, api_cache, image_cache` — created automatically with indexes on first start (see `database.py`).
Economy balances have `CHECK (>= 0)` constraints; claims and trades run in `BEGIN IMMEDIATE` transactions.

## Tests
```bash
python -m unittest discover -s tests -t .
```
Covers music API parsing (mock transport), queue ops, player auto-advance/failure handling (fake voice backend),
database + SQL-injection safety, name/alias matching, rarity distribution, cooldowns, economy/daily streaks,
gacha/pity, concurrent claims and trades, callback validation, and handler wiring.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Missing required settings in .env` | Fill `API_ID`, `API_HASH`, `BOT_TOKEN`, `SESSION_STRING` |
| `No matching distribution found for ntgcalls` | Platform/Python unsupported — use Python 3.12/3.13 or Docker |
| "FFmpeg was not found" | `pkg install ffmpeg -y` |
| "There is no active voice chat" | Start a voice chat in the group first |
| "couldn't add my assistant account" | Make the bot admin with *Invite users*, or add the assistant manually |
| "The music service is unreachable" | The music API is down/deprecated — set another `MUSIC_API_URL` |
| Waifus never spawn | Disable bot privacy in BotFather; need ≥5 recent messages; check `/waifutimer`, `/syncwaifus` |
| Inline mode silent | `/setinline` in BotFather |
| Bot stops after a while on Android | `termux-wake-lock`, disable battery optimisation |
| Seek doesn't work on some tracks | Seeking relies on FFmpeg `-ss` via py-tgcalls; HLS streams may ignore it |

## Security notes
Secrets live only in `.env` (git-ignored) and are redacted from logs. Owner-only commands gate anything that
creates currency or characters. Never commit `.env`, `*.session` or a `SESSION_STRING`.
