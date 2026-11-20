"""Conversation turns on Claude (FEEDBACK_AGENT_PROVIDER=claude)."""
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

import anthropic

from .. import config
from .agent import SYSTEM_PROMPT, TOOLS, AgentTurnError

# Streamed tool inputs arrive as they are generated; form.validate_answers checks them.
_TOOLS = [dict(tool, eager_input_streaming=True) for tool in TOOLS]

# Models that accept the server-side refusal fallback (Haiku and older models do not).
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5", "claude-mythos-5")

_client: Optional[anthropic.AsyncAnthropic] = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def _stream(messages: List[Dict[str, Any]]):
    model = config.agent_model()
    kwargs: Dict[str, Any] = dict(
        model=model,
        max_tokens=4096,  # spoken replies are a sentence or two
        system=SYSTEM_PROMPT,
        tools=_TOOLS,
        messages=messages,
        output_config={"effort": config.agent_effort()},
        cache_control={"type": "ephemeral"},
    )
    if model.startswith(_FALLBACK_MODELS):
        # If a safety classifier declines a turn, the API retries it on a fallback model.
        return client().beta.messages.stream(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    return client().messages.stream(**kwargs)


async def run_turn(
    messages: List[Dict[str, Any]],
    on_text: Callable[[str], Awaitable[None]],
    run_tool: Callable[[str, Any], Awaitable[Dict[str, Any]]],
) -> None:
    json_retries = 0
    spoke = False  # text from an earlier model call in this turn needs a space before more
    while True:
        try:
            async with _stream(messages) as stream:
                first = True
                async for event in stream:
                    if event.type == "text" and event.text:
                        await on_text((" " if spoke and first else "") + event.text)
                        first = False
                response = await stream.get_final_message()
            spoke = spoke or not first
        except ValueError:
            # A tool input the SDK could not parse at all; re-issue the turn (bounded).
            json_retries += 1
            if json_retries > 2:
                raise AgentTurnError("unparseable tool input")
            continue
        except anthropic.APIError as e:
            raise AgentTurnError(f"{type(e).__name__}: {e}") from e
        json_retries = 0

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason in ("refusal", "max_tokens"):
            raise AgentTurnError(response.stop_reason)
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            return

        results = []
        stop = False
        for block in tool_uses:
            result = await run_tool(block.name, block.input)
            stop = stop or bool(result.pop("stop", False))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                    "is_error": bool(result.get("error")),
                }
            )
        messages.append({"role": "user", "content": results})
        if stop:
            return
