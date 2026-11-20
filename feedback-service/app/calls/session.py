"""One phone conversation: keeps the dialogue, fills the form, submits it when complete."""
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from .. import db
from . import agent, contacts, form

log = logging.getLogger(__name__)

TROUBLE = "I'm sorry, I'm having a technical problem on my end. We'll try you again another time. Goodbye."
GOODBYE = "Thank you for your time. Goodbye."
STILL_THERE = "Are you still there?"
NO_RESPONSE = "I can't hear you, so I'll let you go. Goodbye."


def greeting(contact: Dict[str, Any], course_title: str) -> str:
    first_name = contact["name"].split()[0]
    if contact.get("answers"):
        return (
            f"Hi {first_name}, this is the DotAI Labs feedback assistant, an AI, calling back to finish "
            f"the short feedback we started on your {course_title} course. Is now a good time?"
        )
    return (
        f"Hi {first_name}, this is the DotAI Labs feedback assistant, an AI, calling about the "
        f"{course_title} course you completed. Do you have two minutes to share some quick feedback?"
    )


def speech_seconds(text: str) -> float:
    """Rough time text-to-speech needs to say `text`."""
    return 1.0 + len(text) / 14


class CallSession:
    """`send` delivers one Conversation Relay message (a dict) to the caller's line."""

    def __init__(
        self,
        database: AsyncDatabase,
        contact: Dict[str, Any],
        course_title: str,
        send: Callable[[Dict[str, Any]], Awaitable[None]],
    ):
        self.db = database
        self.contact = contact
        self.send = send
        self.submitted = False
        self.outcome: Optional[str] = None  # set once the call should end
        self.error: Optional[str] = None
        opening = greeting(contact, course_title)
        context = (
            "<call_context>\n"
            f"Person: {contact['name']}\n"
            f"Course completed: {course_title}\n"
            f"Answers already on the form: {json.dumps(contact.get('answers') or {})}\n"
            f"Next unanswered question: {json.dumps(form.next_question(contact.get('answers') or {}))}\n"
            "</call_context>\n"
            "The call has just connected and your greeting has been played to the person."
        )
        self.messages: List[Dict[str, Any]] = [
            {"role": "user", "content": context},
            {"role": "assistant", "content": opening},
        ]
        self.opening = opening

    async def started(self, call_sid: Optional[str]) -> None:
        await contacts.add_transcript(self.db, self.contact, "agent", self.opening)
        if call_sid:
            await self.db.call_contacts.update_one({"_id": self.contact["_id"]}, {"$set": {"call_sid": call_sid}})

    async def handle_prompt(self, heard: str) -> float:
        """Answer what the caller said. Returns roughly how long the reply takes to speak."""
        await contacts.add_transcript(self.db, self.contact, "caller", heard)
        self.messages.append({"role": "user", "content": heard})

        spoken: List[str] = []
        held: Optional[str] = None  # one chunk is held back so the final one can carry last=True

        async def say(text: str) -> None:
            nonlocal held
            if held is not None:
                await self.send({"type": "text", "token": held, "last": False})
            held = text
            spoken.append(text)

        try:
            await agent.run_turn(self.messages, say, self.run_tool)
        except agent.AgentTurnError as e:
            log.exception("Agent turn failed for contact %s", self.contact["_id"])
            self.error = str(e)[:300]
            self.outcome = self.outcome or "error"
            await say((" " if spoken else "") + TROUBLE)
        if not spoken:
            # The model ended the call (or returned nothing) without saying anything.
            await say(GOODBYE if self.outcome else "Sorry, could you say that again?")
        await self.send({"type": "text", "token": held, "last": True})

        reply = "".join(spoken)
        await contacts.add_transcript(self.db, self.contact, "agent", reply)
        return speech_seconds(reply)

    async def speak(self, text: str) -> float:
        """Say a fixed line (used for silence handling)."""
        self.messages.append({"role": "assistant", "content": text})
        await self.send({"type": "text", "token": text, "last": True})
        await contacts.add_transcript(self.db, self.contact, "agent", text)
        return speech_seconds(text)

    async def run_tool(self, name: str, tool_input: Any) -> Dict[str, Any]:
        if name == "save_answers":
            return await self._save_answers(tool_input)
        if name == "end_call":
            outcome = tool_input.get("outcome") if isinstance(tool_input, dict) else None
            if outcome not in agent.OUTCOMES:
                return {"error": f"outcome must be one of {agent.OUTCOMES}"}
            if outcome == "completed" and not self.submitted:
                return {
                    "error": "The form is not submitted yet, so the call cannot end as completed.",
                    "next_question": form.next_question(self.contact["answers"]),
                }
            self.outcome = outcome
            return {"ok": True, "stop": True}
        return {"error": f"Unknown tool {name}"}

    async def _save_answers(self, tool_input: Any) -> Dict[str, Any]:
        if self.submitted:
            return {"error": "The form has already been submitted and can no longer be changed."}
        accepted, rejected = form.validate_answers(tool_input)
        if accepted:
            self.contact["answers"].update(accepted)
            await self.db.call_contacts.update_one(
                {"_id": self.contact["_id"]},
                {"$set": dict({f"answers.{k}": v for k, v in accepted.items()}, updated_at=db.now())},
            )
        upcoming = form.next_question(self.contact["answers"])
        if upcoming is None:
            await self._submit()
        return {
            "saved": sorted(accepted),
            "rejected": rejected,
            "next_question": upcoming,
            "form_submitted": self.submitted,
        }

    async def _submit(self) -> None:
        feedback_id = None
        try:
            created = await db.insert_feedback(self.db, form.build_feedback(self.contact), source="call")
            feedback_id = created["id"]
        except DuplicateKeyError:
            pass  # they filled in the web form in the meantime; keep that one
        self.submitted = True
        await contacts.set_status(self.db, self.contact["_id"], "completed", feedback_id=feedback_id)

    async def finish(self) -> None:
        """Record how the call ended. Runs when the line closes, however that happened."""
        if self.submitted:
            status = "completed"
        elif self.outcome in ("declined", "wrong_person"):
            status = "declined"
        elif self.outcome == "voicemail":
            status = "no_answer"
        elif self.outcome == "error":
            status = "failed"
        else:
            status = "incomplete"
        await contacts.set_status(
            self.db, self.contact["_id"], status, outcome=self.outcome, last_error=self.error, call_token=None
        )
