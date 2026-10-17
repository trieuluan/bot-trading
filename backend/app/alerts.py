"""API-side worker watchdog. Requires TELEGRAM_* env; no exchange credentials."""

import asyncio
import json
import logging
import os
import time
from datetime import timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select

from .db import BotState, Session

STALE_SECONDS = 90
CHECK_SECONDS = 30


def send_alert(message):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": message}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response).get("ok") is True
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        logging.error("Telegram alert failed; retrying without logging credentials")
        return False


def worker_down(now=None):
    with Session() as session:
        beat = session.scalar(select(BotState.heartbeat).where(BotState.id == 1))
    now = time.time() if now is None else now
    return beat is None or now - beat.replace(tzinfo=timezone.utc).timestamp() > STALE_SECONDS


async def watchdog():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        return
    down = False
    await asyncio.sleep(STALE_SECONDS)
    while True:
        try:
            now_down = await asyncio.to_thread(worker_down)
            if now_down != down:
                message = ("Worker bot-trading DỪNG: không có heartbeat > 90s" if now_down
                           else "Worker bot-trading đã hoạt động lại")
                if await asyncio.to_thread(send_alert, message):
                    down = now_down
        except Exception:
            logging.error("Worker watchdog unavailable; check database")
        await asyncio.sleep(CHECK_SECONDS)
