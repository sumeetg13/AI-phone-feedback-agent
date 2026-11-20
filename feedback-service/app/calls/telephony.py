"""Twilio: placing outbound calls, the Conversation Relay TwiML, and request signatures."""
import asyncio
import logging
import secrets
from datetime import timedelta
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, ConversationRelay, VoiceResponse

from .. import config, db
from . import contacts
from .session import greeting

log = logging.getLogger(__name__)

MAX_CALL_SECONDS = 600
RING_SECONDS = 30
STALE_AFTER = timedelta(seconds=MAX_CALL_SECONDS + RING_SECONDS + 120)


def valid_signature(path: str, params: Dict[str, Any], signature: Optional[str]) -> bool:
    """True if the request to `path` on our public URL was signed by Twilio."""
    token, base = config.env("TWILIO_AUTH_TOKEN"), config.public_base_url()
    if not (token and base and signature):
        return False
    validator = RequestValidator(token)
    # The WebSocket handshake is signed with the wss:// form of the URL.
    urls = [base + path, base.replace("https://", "wss://", 1) + path]
    return any(validator.validate(url, params, signature) for url in urls)


def twiml(contact: Dict[str, Any], token: str, course_title: str) -> str:
    relay_args = {
        "url": config.public_base_url().replace("https://", "wss://", 1) + f"/voice/ws/{token}",
        "welcome_greeting": greeting(contact, course_title),
    }
    language = config.env("CALL_LANGUAGE")  # e.g. en-IN; Twilio's default applies when unset
    if language:
        relay_args["language"] = language
    connect = Connect()
    connect.append(ConversationRelay(**relay_args))
    response = VoiceResponse()
    response.append(connect)
    return str(response)


def place_call(contact: Dict[str, Any], token: str, course_title: str) -> str:
    """Start the outbound call (blocking). Returns Twilio's call SID."""
    account_sid = config.env("TWILIO_ACCOUNT_SID")
    key_sid, key_secret = config.env("TWILIO_API_KEY_SID"), config.env("TWILIO_API_KEY_SECRET")
    if key_sid and key_secret:
        client = Client(key_sid, key_secret, account_sid)
    else:
        client = Client(account_sid, config.env("TWILIO_AUTH_TOKEN"))
    call = client.calls.create(
        to=contact["phone"],
        from_=config.env("TWILIO_FROM_NUMBER"),
        twiml=twiml(contact, token, course_title),
        status_callback=f"{config.public_base_url()}/voice/status/{contact['_id']}",
        status_callback_event=["completed"],
        status_callback_method="POST",
        timeout=RING_SECONDS,
        time_limit=MAX_CALL_SECONDS,
    )
    return call.sid


async def sweep_stale(database: AsyncDatabase) -> None:
    """A contact stuck in `calling` long past the call time limit lost its status update."""
    await database.call_contacts.update_many(
        {"status": "calling", "updated_at": {"$lt": db.now() - STALE_AFTER}},
        {"$set": {"status": "failed", "last_error": "No call result was received from Twilio", "updated_at": db.now()}},
    )


async def dial(database: AsyncDatabase, contact_ids: List[Any], course_titles: Dict[str, str]) -> None:
    """Background task: call each queued contact, keeping a cap on simultaneous calls."""
    for contact_id in contact_ids:
        try:
            while await database.call_contacts.count_documents({"status": "calling"}) >= config.max_concurrent_calls():
                await sweep_stale(database)
                await asyncio.sleep(3)
            token = secrets.token_urlsafe(24)
            contact = await database.call_contacts.find_one_and_update(
                {"_id": contact_id, "status": "queued"},
                {
                    "$set": {
                        "status": "calling",
                        "call_token": token,
                        "outcome": None,
                        "last_error": None,
                        "updated_at": db.now(),
                    },
                    "$inc": {"attempts": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if contact is None:
                continue  # no longer queued
            title = course_titles.get(contact["course_id"], contact["course_id"])
            try:
                sid = await asyncio.to_thread(place_call, contact, token, title)
                await database.call_contacts.update_one({"_id": contact_id}, {"$set": {"call_sid": sid}})
            except Exception as e:  # Twilio rejected the call; record why and move on
                log.exception("Could not call contact %s", contact_id)
                # Twilio's own exception text is wrapped in terminal colour codes; use its plain fields.
                reason = getattr(e, "msg", None) or str(e)
                if getattr(e, "code", None):
                    reason = f"Twilio error {e.code}: {reason}"
                await contacts.set_status(database, contact_id, "failed", last_error=reason[:500], call_token=None)
            await asyncio.sleep(1)
        except Exception:
            log.exception("Dialler error on contact %s", contact_id)
