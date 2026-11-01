"""FastAPI app and LINE webhook.

Run with:  uvicorn app.main:create_app --factory --port 8001
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.bot import HELP_TEXT, Bot, make_now, text_msg
from app.config import Settings, get_settings
from app.db import Database
from app.notion import Notion

log = logging.getLogger("diary_bot")


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


class LineSender:
    """Thin wrapper over the line-bot-sdk v3 Messaging API."""

    def __init__(self, access_token: str):
        from linebot.v3.messaging import Configuration
        self.configuration = Configuration(access_token=access_token)

    def reply(self, reply_token: str, messages: list[dict]) -> None:
        from linebot.v3.messaging import ApiClient, MessagingApi, ReplyMessageRequest
        req = ReplyMessageRequest.from_dict({"replyToken": reply_token, "messages": messages})
        with ApiClient(self.configuration) as client:
            MessagingApi(client).reply_message(req)

    def push(self, to: str, messages: list[dict]) -> None:
        from linebot.v3.messaging import ApiClient, MessagingApi, PushMessageRequest
        req = PushMessageRequest.from_dict({"to": to, "messages": messages})
        with ApiClient(self.configuration) as client:
            MessagingApi(client).push_message(req)


class WebhookHandler:
    def __init__(self, settings: Settings, db: Database, bot: Bot, sender):
        self.settings = settings
        self.db = db
        self.bot = bot
        self.sender = sender

    def process(self, events: list[dict]) -> None:
        for event in events:
            try:
                self._process_one(event)
            except Exception:
                log.exception("error handling %s event", event.get("type"))

    def _process_one(self, event: dict) -> None:
        source = event.get("source") or {}
        user_id = source.get("userId")
        if source.get("type") != "user" or not user_id:
            return
        owner = self.settings.owner_user_id
        if not owner:
            self._setup_mode(event, user_id)
            return
        if user_id != owner:
            return  # silently ignore everyone else

        event_id = event.get("webhookEventId")
        if event_id and not self.db.mark_event_processed(event_id):
            log.info("duplicate event skipped")
            return

        messages = self._dispatch(event)
        if not messages:
            return
        try:
            self.sender.reply(event["replyToken"], messages[:5])
        except Exception:
            log.warning("reply failed; falling back to push")
            self.sender.push(owner, messages[:5])

    def _setup_mode(self, event: dict, user_id: str) -> None:
        if not self.db.claim("setup_notice_sent"):
            return
        log.warning("SETUP MODE: sender user ID is %s — set OWNER_USER_ID in .env", user_id)
        if event.get("replyToken"):
            self.sender.reply(event["replyToken"], [
                text_msg(f"Setup: add this ID to .env\nOWNER_USER_ID={user_id}")])

    def _dispatch(self, event: dict) -> list[dict]:
        etype = event.get("type")
        if etype == "message":
            message = event.get("message") or {}
            if message.get("type") == "text":
                return self.bot.handle_text(message.get("text", ""))
            return [text_msg("I only understand text for now. Type 'help'.")]
        if etype == "postback":
            return self.bot.handle_postback((event.get("postback") or {}).get("data", ""))
        if etype == "follow":
            return [text_msg("Hi! I keep your diary, ideas, shopping list and meal plan in Notion.\n\n"
                             + HELP_TEXT)]
        return []


def create_app(settings: Settings | None = None, sender=None, notion=None) -> FastAPI:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or get_settings()
    db = Database(settings.db_path)
    if notion is None and settings.notion_configured:
        notion = Notion(settings.notion_token)
    if notion is None:
        log.warning("Notion is not configured; run: python3 run.py --notion")
    bot = Bot(db, notion, settings.notion_diary_ds, settings.notion_shopping_ds,
              settings.notion_cooking_ds, settings.notion_ideas_ds,
              settings.notion_lists_ds, settings.currency, now=make_now(settings.tz))
    sender = sender or LineSender(settings.channel_access_token)
    handler = WebhookHandler(settings, db, bot, sender)

    if not settings.channel_secret:
        log.error("LINE_CHANNEL_SECRET is not set; every webhook will be rejected")
    if not settings.owner_user_id:
        log.warning("OWNER_USER_ID is empty: running in setup mode")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.handler = handler

    @app.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        return "ok"

    @app.post("/callback", response_class=PlainTextResponse)
    async def callback(request: Request, background: BackgroundTasks) -> str:
        body = await request.body()
        signature = request.headers.get("X-Line-Signature", "")
        if not verify_signature(settings.channel_secret, body, signature):
            log.warning("rejected webhook: invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid body")
        # Answer LINE right away; Notion calls run afterwards.
        background.add_task(handler.process, payload.get("events") or [])
        return "OK"

    return app
