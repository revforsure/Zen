"""Create the assistant account's SESSION_STRING.

Run:  python generate_session.py
You will be asked for the assistant account's phone number and the login code Telegram sends.
Use a SEPARATE Telegram account for the assistant, not your personal one.
The string is printed once; paste it into .env as SESSION_STRING. Never share it:
anyone holding it has full access to that account.
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from pyrogram import Client

    api_id = os.getenv("API_ID") or input("API_ID: ").strip()
    api_hash = os.getenv("API_HASH") or input("API_HASH: ").strip()
    async with Client("session-gen", api_id=int(api_id), api_hash=api_hash, in_memory=True) as app:
        session = await app.export_session_string()
    print("\nYour SESSION_STRING (keep it secret):\n")
    print(session)
    print("\nPaste it into .env as SESSION_STRING=...")


if __name__ == "__main__":
    asyncio.run(main())
