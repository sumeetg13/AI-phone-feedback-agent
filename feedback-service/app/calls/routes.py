import asyncio
import logging
from typing import Set

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from .. import config, db
from ..deps import load_courses, require_admin
from ..schemas import Contact, ContactDetail, ContactEdit, ContactList, StartCalls, StartCallsResult, UploadResult
from . import contacts, session, telephony

log = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 1_000_000
SILENCE_SECONDS = 12.0  # how long to wait for the caller after the agent stops speaking
_dial_tasks: Set[asyncio.Task] = set()

# --- admin: manage who gets called ------------------------------------------

admin = APIRouter(prefix="/admin/calls", dependencies=[Depends(require_admin)])


@admin.post("/contacts/upload", response_model=UploadResult)
async def upload_contacts(file: UploadFile = File(...), database: AsyncDatabase = Depends(db.get_db)):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "CSV file is too large (limit 1 MB)")
    rows, errors = contacts.parse_csv(content, load_courses())
    counts = await contacts.import_rows(database, rows)
    return dict(counts, errors=errors)


@admin.get("/contacts", response_model=ContactList)
async def list_contacts(database: AsyncDatabase = Depends(db.get_db)):
    await telephony.sweep_stale(database)
    return {
        "contacts": await contacts.list_contacts(database),
        "missing_settings": config.missing_call_settings(),
    }


@admin.get("/contacts/{contact_id}", response_model=ContactDetail)
async def get_contact(contact_id: str, database: AsyncDatabase = Depends(db.get_db)):
    contact = await database.call_contacts.find_one({"_id": db.object_id(contact_id)})
    if not contact:
        raise HTTPException(404, "Contact not found")
    return contacts.public(contact, detail=True)


@admin.patch("/contacts/{contact_id}", response_model=Contact)
async def edit_contact(contact_id: str, body: ContactEdit, database: AsyncDatabase = Depends(db.get_db)):
    """Correct a person's details. Email and course identify whose form it is, so they have rules."""
    oid = db.object_id(contact_id)
    contact = await database.call_contacts.find_one({"_id": oid})
    if not contact:
        raise HTTPException(404, "Contact not found")
    if contact["status"] == "calling":
        raise HTTPException(409, "This person is on a call right now. Try again when it ends.")
    if body.course_id not in {c.id for c in load_courses()}:
        raise HTTPException(422, "Unknown course")
    phone = contacts.normalize_phone(body.phone)
    if not phone:
        raise HTTPException(422, "Phone number must include the country code, e.g. +919876543210")

    email = body.email.lower()
    changes = {"name": body.name, "email": email, "course_id": body.course_id, "phone": phone, "updated_at": db.now()}
    if (email, body.course_id) != (contact["email"], contact["course_id"]):
        if contact["status"] == "completed":
            raise HTTPException(
                409, "Feedback is already submitted for this person, so only the name and phone can be changed."
            )
        if await database.feedbacks.find_one({"email": email, "course_id": body.course_id}, {"_id": 1}):
            raise HTTPException(409, "Feedback for this course was already submitted with this email.")
        if body.course_id != contact["course_id"]:
            changes["answers"] = {}  # answers given so far were about the other course
    try:
        await database.call_contacts.update_one({"_id": oid}, {"$set": changes})
    except DuplicateKeyError:
        raise HTTPException(409, "Another person in the list already has this email and course.")
    return contacts.public(await database.call_contacts.find_one({"_id": oid}))


@admin.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(contact_id: str, database: AsyncDatabase = Depends(db.get_db)):
    """Remove a person and their call history. Feedback they already submitted is kept."""
    oid = db.object_id(contact_id)
    result = await database.call_contacts.delete_one({"_id": oid, "status": {"$ne": "calling"}})
    if result.deleted_count == 0:
        if await database.call_contacts.find_one({"_id": oid}, {"_id": 1}):
            raise HTTPException(409, "This person is on a call right now. Try again when it ends.")
        raise HTTPException(404, "Contact not found")
    return Response(status_code=204)


@admin.post("/start", response_model=StartCallsResult)
async def start_calls(body: StartCalls, database: AsyncDatabase = Depends(db.get_db)):
    missing = config.missing_call_settings()
    if missing:
        raise HTTPException(503, f"Calling is not configured. Missing: {', '.join(missing)}")
    query = {"status": {"$in": contacts.CALLABLE}}
    if body.contact_ids is not None:
        query["_id"] = {"$in": [oid for oid in map(db.object_id, body.contact_ids) if oid]}
    ids = [c["_id"] async for c in database.call_contacts.find(query, {"_id": 1}).sort("_id", 1)]
    await database.call_contacts.update_many(
        {"_id": {"$in": ids}, "status": {"$in": contacts.CALLABLE}},
        {"$set": {"status": "queued", "updated_at": db.now()}},
    )
    titles = {c.id: c.title for c in load_courses()}
    task = asyncio.create_task(telephony.dial(database, ids, titles))
    _dial_tasks.add(task)
    task.add_done_callback(_dial_tasks.discard)
    return {"queued": len(ids)}


router.include_router(admin)

# --- Twilio-facing endpoints -------------------------------------------------


@router.post("/voice/status/{contact_id}", include_in_schema=False)
async def call_status(contact_id: str, request: Request, database: AsyncDatabase = Depends(db.get_db)):
    """Twilio reports how a call ended (not answered, busy, hung up...)."""
    params = dict(await request.form())
    if not telephony.valid_signature(request.url.path, params, request.headers.get("x-twilio-signature")):
        raise HTTPException(403, "Invalid Twilio signature")
    status = {
        "busy": "no_answer",
        "no-answer": "no_answer",
        "canceled": "no_answer",
        "failed": "failed",
        "completed": "incomplete",  # the conversation, if there was one, has already set a final status
    }.get(str(params.get("CallStatus")))
    if status:
        await database.call_contacts.update_one(
            {"_id": db.object_id(contact_id), "status": "calling"},
            {"$set": {"status": status, "call_token": None, "updated_at": db.now()}},
        )
    return Response(status_code=204)


@router.websocket("/voice/ws/{token}")
async def voice_ws(ws: WebSocket, token: str):
    """Twilio Conversation Relay: we receive the caller's speech as text and send text to speak."""
    database = db.get_db()
    if not telephony.valid_signature(ws.url.path, {}, ws.headers.get("x-twilio-signature")):
        log.warning("Rejected call WebSocket: invalid Twilio signature")
        await ws.close(code=1008)
        return
    contact = await database.call_contacts.find_one({"call_token": token, "status": "calling"})
    if not contact:
        await ws.close(code=1008)
        return
    await ws.accept()

    titles = {c.id: c.title for c in load_courses()}
    call = session.CallSession(database, contact, titles.get(contact["course_id"], contact["course_id"]), ws.send_json)
    speaking = session.speech_seconds(call.opening)  # how long the agent's last words take to play
    silences = 0
    try:
        while call.outcome is None:
            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=speaking + SILENCE_SECONDS)
            except asyncio.TimeoutError:
                # Conversation Relay only sends a prompt when the caller speaks, so silence is ours to notice.
                silences += 1
                if silences >= 2:
                    call.outcome = "no_response"
                    speaking = await call.speak(session.NO_RESPONSE)
                else:
                    speaking = await call.speak(session.STILL_THERE)
                continue
            kind = message.get("type")
            if kind == "setup":
                await call.started(message.get("callSid"))
            elif kind == "prompt" and message.get("last", True) and message.get("voicePrompt", "").strip():
                silences = 0
                speaking = await call.handle_prompt(message["voicePrompt"])
            elif kind == "error":
                log.warning("Conversation Relay error: %s", message.get("description"))
        await asyncio.sleep(speaking)  # let the goodbye finish playing before hanging up
        await ws.send_json({"type": "end"})
    except WebSocketDisconnect:
        pass  # the caller hung up
    finally:
        await call.finish()
