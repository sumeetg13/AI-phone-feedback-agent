"""Conversation turns on OpenAI (FEEDBACK_AGENT_PROVIDER=openai), via Chat Completions."""
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

import openai

from .. import config
from .agent import SYSTEM_PROMPT, TOOLS, AgentTurnError

_TOOLS = [
    {
        "type": "function",
        "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
    }
    for t in TOOLS
]

_client: Optional[openai.AsyncOpenAI] = None


def client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI()  # reads OPENAI_API_KEY
    return _client


async def _stream(messages: List[Dict[str, Any]]):
    return await client().chat.completions.create(
        model=config.agent_model(),
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        tools=_TOOLS,
        reasoning_effort=config.agent_effort(),
        stream=True,
    )


async def run_turn(
    messages: List[Dict[str, Any]],
    on_text: Callable[[str], Awaitable[None]],
    run_tool: Callable[[str, Any], Awaitable[Dict[str, Any]]],
) -> None:
    spoke = False  # text from an earlier model call in this turn needs a space before more
    while True:
        text: List[str] = []
        calls: Dict[int, Dict[str, str]] = {}  # tool calls arrive in fragments, keyed by index
        finish = None
        try:
            async for chunk in await _stream(messages):
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    await on_text((" " if spoke and not text else "") + choice.delta.content)
                    text.append(choice.delta.content)
                for part in choice.delta.tool_calls or []:
                    call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    call["id"] = part.id or call["id"]
                    if part.function:
                        call["name"] = part.function.name or call["name"]
                        call["arguments"] += part.function.arguments or ""
                finish = choice.finish_reason or finish
        except openai.APIError as e:
            raise AgentTurnError(f"{type(e).__name__}: {e}") from e
        spoke = spoke or bool(text)

        if finish in ("length", "content_filter"):
            raise AgentTurnError(finish)
        ordered = [calls[i] for i in sorted(calls)]
        reply: Dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
        if ordered:
            reply["tool_calls"] = [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in ordered
            ]
        messages.append(reply)
        if not ordered:
            return

        stop = False
        for call in ordered:
            try:
                result = await run_tool(call["name"], json.loads(call["arguments"] or "{}"))
            except json.JSONDecodeError:
                result = {"error": "The tool arguments were not valid JSON. Call the tool again."}
            stop = stop or bool(result.pop("stop", False))
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
        if stop:
            return
