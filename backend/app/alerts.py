"""API-side worker watchdog. Requires TELEGRAM_* env; no exchange credentials."""

import asyncio
import json
import logging
import os
import time
from datetime import timezone
import re
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select

from .db import BotState, Session

STALE_SECONDS = 90
CHECK_SECONDS = 30


def worker_message(down):
    if down:
        return "🚨 <b>CẢNH BÁO: WORKER BOT DỪNG</b>\nKhông nhận được heartbeat &gt; 90s. Tiến trình có thể đã crash hoặc máy ngủ."
    return "✅ <b>THÔNG BÁO: WORKER BOT HOẠT ĐỘNG LẠI</b>\nHeartbeat đã phục hồi bình thường."


def send_alert(message, parse_mode="HTML"):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    use_html = parse_mode == "HTML" and "<b>" in message
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": message, "disable_web_page_preview": True,
                         **({"parse_mode": "HTML"} if use_html else {})}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response).get("ok") is True
    except HTTPError as exc:
        if use_html and exc.code == 400:
            return send_alert(unescape(re.sub(r"</?[a-z][^>]*>", "", message)), parse_mode=None)
        logging.error("Telegram alert failed; retrying without logging credentials")
        return False
    except (URLError, TimeoutError, ValueError, OSError):
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
                message = worker_message(now_down)
                if await asyncio.to_thread(send_alert, message):
                    down = now_down
        except Exception:
            logging.error("Worker watchdog unavailable; check database")
        await asyncio.sleep(CHECK_SECONDS)
