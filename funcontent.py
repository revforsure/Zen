"""Static, original content for the fun commands (kept out of handler code)."""
from __future__ import annotations

import hashlib
import random
import re

# name: (emoji, text with {a} actor / {b} target, solo text with {a}, nekos.best gif category or None, needs_target)
ACTIONS: dict[str, tuple[str, str, str, str | None, bool]] = {
    "hug": ("🤗", "{a} gives {b} a warm hug!", "{a} hugs the air. Someone get this person a hug!", "hug", True),
    "kiss": ("💋", "{a} kisses {b} on the cheek!", "{a} blows a kiss into the void.", "kiss", True),
    "slap": ("👋", "{a} slaps {b}! That's gonna leave a mark.", "{a} slaps the table. Dramatic.", "slap", True),
    "pat": ("🫶", "{a} pats {b} on the head. Good job!", "{a} pats their own head. Proud of you.", "pat", True),
    "cuddle": ("🥰", "{a} cuddles up with {b}.", "{a} cuddles a pillow.", "cuddle", True),
    "poke": ("👉", "{a} pokes {b}. Poke poke!", "{a} pokes the screen.", "poke", True),
    "bonk": ("🔨", "{a} bonks {b} on the head. Bad behavior!", "{a} bonks themselves. Why?", None, True),
    "highfive": ("🙌", "{a} high-fives {b}! 🙌", "{a} high-fives the air. Awkward.", "highfive", True),
    "dance": ("💃", "{a} starts dancing!", "{a} starts dancing!", "dance", False),
    "wave": ("👋", "{a} waves hello!", "{a} waves hello!", "wave", False),
    "smile": ("😊", "{a} smiles warmly.", "{a} smiles warmly.", "smile", False),
    "laugh": ("😂", "{a} bursts out laughing!", "{a} bursts out laughing!", "laugh", False),
    "blush": ("😳", "{a} blushes.", "{a} blushes.", "blush", False),
    "cry": ("😭", "{a} is crying. Someone pass the tissues.", "{a} is crying. Someone pass the tissues.", "cry", False),
    "pout": ("😤", "{a} pouts.", "{a} pouts.", "pout", False),
    "shrug": ("🤷", "{a} shrugs.", "{a} shrugs.", "shrug", False),
    "stare": ("👀", "{a} stares at {b}...", "{a} stares into the distance.", "stare", True),
    "yeet": ("🚀", "{a} yeets {b} into orbit!", "{a} yeets themselves. Bold.", "yeet", True),
    "tickle": ("🤭", "{a} tickles {b}!", "{a} tries to tickle themselves. It doesn't work.", "tickle", True),
    "bite": ("😬", "{a} nibbles {b}. Nom!", "{a} bites their lip.", "bite", True),
}

JOKES = [
    "Why don't scientists trust atoms? Because they make up everything.",
    "I told my computer I needed a break. Now it won't stop sending me vacation ads.",
    "Why did the scarecrow win an award? He was outstanding in his field.",
    "I would tell you a UDP joke, but you might not get it.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "What do you call a fake noodle? An impasta.",
    "Why did the developer go broke? He used up all his cache.",
    "I'm reading a book about anti-gravity. It's impossible to put down.",
    "Why was the math book sad? It had too many problems.",
    "What's a ninja's favorite shoe? Sneakers.",
    "How does a penguin build its house? Igloos it together.",
    "Why did the bot cross the road? To get to the other thread.",
]
FACTS = [
    "Octopuses have three hearts and blue blood.",
    "Honey found in ancient tombs was still edible, thanks to its low water content.",
    "A day on Venus is longer than a year on Venus.",
    "Sea otters sometimes hold hands while sleeping so they don't drift apart.",
    "Botanically, bananas are berries but strawberries are not.",
    "Wombat droppings are cube-shaped.",
    "Sharks are older than trees: they appeared hundreds of millions of years ago.",
    "The Japanese word 'tsunami' means 'harbor wave'.",
    "Astro Boy (1963) is often credited with popularising the TV anime format.",
    "Lightning can heat the air around it to hotter than the surface of the Sun.",
    "Crows can recognise individual human faces.",
    "The Eiffel Tower grows a few centimetres taller in summer from heat expansion.",
]
QUOTES = [
    "Small steps still move you forward.",
    "A bad day is just a chapter, not the whole story.",
    "Practice beats talent when talent doesn't practice.",
    "Be the kind of friend your past self needed.",
    "The plot twist is that you were capable all along.",
    "Rest is part of the training arc.",
    "Every expert once played on easy mode.",
    "Courage is doing it with shaky hands.",
    "Level up a little every day.",
    "Even the longest opening theme ends eventually — keep going.",
]
TRUTHS = [
    "What's the most embarrassing song on your playlist?", "What's a skill you pretend to have?",
    "What's the last thing you searched for?", "Which anime character do you secretly relate to the most?",
    "What's your most irrational fear?", "What's the weirdest dream you remember?",
    "What's a habit you'd like to quit?", "Who was your first celebrity crush?",
    "What's the silliest thing you've cried about?", "What's your guilty-pleasure show?",
    "What's the worst gift you ever received?", "What would you do with a free day and no limits?",
]
DARES = [
    "Send the last emoji you used, then explain why.", "Talk like a pirate for your next 3 messages.",
    "Change your profile picture to a potato for 10 minutes.", "Write a haiku about the person above you.",
    "Compliment every person who spoke in the last 5 minutes.", "Describe your day using only emojis.",
    "Sing the first line of your favorite song (in text).", "Type your next message with your eyes closed.",
    "Share the most recent photo of your pet or a plant.", "Make up a superhero name for yourself.",
]
WYR = [
    "Would you rather always be 10 minutes late or 20 minutes early?",
    "Would you rather lose your phone or your wallet for a week?",
    "Would you rather fight one horse-sized duck or a hundred duck-sized horses?",
    "Would you rather have unlimited ramen or unlimited pizza for life?",
    "Would you rather be able to talk to animals or speak every language?",
    "Would you rather live in an anime world or a video game world?",
    "Would you rather never use the internet again or never watch a screen again?",
    "Would you rather have a rewind button or a pause button for your life?",
]
ROASTS = [
    "{t} is the reason the 'skip intro' button exists.", "{t} has the energy of a loading screen at 99%.",
    "{t} brings a spoon to a sword fight and still tries to win.", "{t} types 'brb' and returns three days later.",
    "{t} is like a software update: nobody asked, and it always arrives at the worst time.",
    "{t} could get lost in a one-room tutorial level.", "{t} treats every group chat like a podcast.",
    "{t} puts the 'pro' in procrastinate.",
]
COMPLIMENTS = [
    "{t} makes this chat a better place.", "{t} has main-character energy.", "{t} is the kind of person who makes people smile.",
    "{t} is smarter than they give themselves credit for.", "{t} could carry any team — and still share the loot.",
    "{t} has excellent taste.", "{t} is a genuine S-tier friend.", "{t} always brings good vibes.",
]
FORTUNES = [
    "A pleasant surprise is waiting in a place you least expect.", "Today, patience will pay off.",
    "Someone is thinking of you kindly.", "A small risk will lead to a big win.",
    "Your next idea will be a good one — write it down.", "Good news arrives before the weekend.",
    "Listen closely; a friend has something important to say.", "Luck is on your side when you start before you're ready.",
    "An old problem will finally make sense.", "A new opportunity knocks softly. Open the door.",
]
EIGHTBALL = ["It is certain.", "Without a doubt.", "Yes — definitely.", "You may rely on it.", "As I see it, yes.",
             "Most likely.", "Outlook good.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
             "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.", "Don't count on it.",
             "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."]
MONSTERS = [("Slime", 1), ("Goblin Scout", 3), ("Dire Wolf", 5), ("Stone Golem", 8), ("Shadow Mage", 12), ("Sky Dragon", 18)]
FIGHT_LINES = ["{w} lands a perfect combo on {l}!", "{l} trips over their own cape; {w} wins!",
               "{w} dodges, counters, and {l} is down!", "A dramatic wind blows... {w} stands victorious over {l}!"]
KILL_LINES = ["{b} was defeated by {a} in a pillow fight. 🛏️", "{a} sent {b} to the shadow realm. (They'll be back after lunch.)",
              "{a} challenged {b} to a dance battle and won. 💃", "{b} got out-memed by {a} and respawned at the lobby.",
              "{a} used Overly Dramatic Anime Attack! {b} is now lying on the floor, clutching their chest."]
LOOT_FISH = [("🐟 Fish", 40, 80, 60), ("🐠 Tropical fish", 70, 130, 25), ("🦀 Crab", 90, 160, 10), ("🐡 Pufferfish", 150, 260, 4), ("👑 Golden carp", 400, 700, 1)]
LOOT_HUNT = [("🐇 Rabbit", 40, 90, 55), ("🦌 Deer", 90, 160, 25), ("🐗 Boar", 120, 200, 14), ("🦅 Rare falcon", 300, 500, 5), ("🦄 Mystic unicorn", 600, 900, 1)]
LOOT_TREASURE = [("🪙 Pouch of coins", 100, 220, 55), ("📜 Old map", 150, 300, 25), ("💍 Ancient ring", 300, 500, 15), ("👑 Royal crown", 700, 1100, 5)]


def stable_percent(*parts: object) -> int:
    h = hashlib.md5("|".join(str(p).lower() for p in parts).encode()).hexdigest()
    return int(h[:8], 16) % 101


def stable_rating(text: str) -> int:
    return int(hashlib.md5(text.strip().lower().encode()).hexdigest()[:6], 16) % 11


def ship_name(a: str, b: str) -> str:
    a, b = re.sub(r"[^\w]", "", a) or "A", re.sub(r"[^\w]", "", b) or "B"
    return a[: max(1, len(a) // 2 + len(a) % 2)] + b[len(b) // 2:]


def love_bar(pct: int) -> str:
    n = pct // 10
    return "💖" * n + "🖤" * (10 - n)


def parse_dice(arg: str) -> tuple[int, int] | None:
    """'2d6' -> (2, 6); '' -> (1, 6); '20' -> (1, 20). Limits: 1-10 dice, 2-1000 sides."""
    arg = (arg or "").strip().lower()
    if not arg:
        return 1, 6
    m = re.fullmatch(r"(\d{1,2})?d(\d{1,4})", arg) or re.fullmatch(r"()(\d{1,4})", arg)
    if not m:
        return None
    n, s = int(m.group(1) or 1), int(m.group(2))
    return (n, s) if 1 <= n <= 10 and 2 <= s <= 1000 else None


def split_choices(text: str) -> list[str]:
    parts = re.split(r"\s*(?:\||,|\bor\b)\s*", text.strip(), flags=re.I)
    return [p.strip() for p in parts if p.strip()][:10]


def rps_result(user: str, bot: str) -> str:
    if user == bot:
        return "tie"
    return "win" if (user, bot) in {("r", "s"), ("p", "r"), ("s", "p")} else "lose"


def weighted(table: list[tuple], rng: random.Random):
    return rng.choices(table, weights=[row[3] for row in table], k=1)[0]
