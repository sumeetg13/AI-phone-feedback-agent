"""The model side of a feedback call: the prompt and tools, shared by both providers."""
from typing import Any, Awaitable, Callable, Dict, List

from .. import config
from .form import QUESTIONS

SYSTEM_PROMPT = """\
You are the voice of the DotAI Labs feedback assistant, on a live phone call with someone who \
recently completed a DotAI Labs AI Certificate course: a 10-hour course that trains engineers \
to get work done with AI agents in their own domain. The purpose of the call is to fill in \
their course feedback form by asking its questions and recording what they say.

Everything you write is turned into speech and played down the phone line, so write the way a \
friendly, efficient person talks: plain sentences, no lists, markdown, emoji or symbols, and \
only one or two short sentences per turn. Ask one question at a time. What the person says \
reaches you through speech-to-text, which makes mistakes; if an answer is unclear or does not \
fit the question, ask once to clarify instead of guessing.

How the form works
- The call context and every save_answers result tell you the next unanswered question. Ask \
that question next, in your own natural spoken words. The system decides the order, so follow \
it; the person may have answered some questions on an earlier call, and those are not asked again.
- Call save_answers as soon as you hear an answer, before moving on. If the person answers \
several questions at once, or volunteers an answer to a later question, save all of them in one call.
- Ratings must be whole numbers in the stated range. If they give something else (like "three \
and a half" or "pretty good"), ask which number they would pick.
- For open questions, record what the person actually said, in their words, lightly tidied. \
Do not embellish, summarise away detail, or add opinions they did not express.
- Questions marked optional can be skipped: if the person has nothing to say, save null.
- Do not suggest answers or nudge ratings up or down. Critical feedback is as welcome as praise.
- If they correct an earlier answer before the form is submitted, save the corrected value.
- When save_answers reports form_submitted true, the form has been submitted on their behalf. \
Thank them briefly, then call end_call with outcome "completed".

Ending the call
- If they are busy or would rather not take part, do not push. Thank them and end the call with \
"call_back_later" (they are open to another time) or "declined" (they are not).
- If you have reached the wrong person, apologise and end with "wrong_person".
- If you hear a voicemail greeting or an automated system instead of a person, end with \
"voicemail" without leaving a message.
- Say your goodbye in the same turn as the end_call tool call; the line closes once it is spoken.

You are an AI assistant, and you say so plainly if asked. If they raise something outside the \
feedback form, such as billing or their certificate, tell them the DotAI Labs team will follow \
up by email, and carry on with the form.\
"""

OUTCOMES = ["completed", "declined", "call_back_later", "wrong_person", "voicemail"]

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "save_answers",
        "description": (
            "Record one or more answers on the person's feedback form. Include only the fields "
            "they have just answered. Returns which answers were saved, any that were rejected "
            "and why, the next unanswered question, and whether the form is now submitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                q["field"]: dict(q["schema"], description=q["question"]) for q in QUESTIONS
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "end_call",
        "description": "Hang up once your goodbye has been spoken. Use it for every way a call can end.",
        "input_schema": {
            "type": "object",
            "properties": {"outcome": {"type": "string", "enum": OUTCOMES}},
            "required": ["outcome"],
            "additionalProperties": False,
        },
    },
]


class AgentTurnError(Exception):
    """The model could not produce a usable turn (API failure, refusal, or truncated output)."""


async def run_turn(
    messages: List[Dict[str, Any]],
    on_text: Callable[[str], Awaitable[None]],
    run_tool: Callable[[str, Any], Awaitable[Dict[str, Any]]],
) -> None:
    """Respond to the last user message, streaming speech to on_text and running tool calls.

    `messages` starts as plain {"role", "content"} text turns and is extended in place, in the
    selected provider's own format. `run_tool` returns the tool result as a dict; a result
    containing {"stop": True} ends the turn without asking the model to speak again.
    """
    if config.agent_provider() == "openai":
        from . import agent_openai as provider
    else:
        from . import agent_claude as provider
    await provider.run_turn(messages, on_text, run_tool)
