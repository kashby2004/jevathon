"""Stagehand LLM callback that routes model calls through GMI Cloud.

Stagehand's built-in model config can't take a custom base URL, so instead we
pass this callback as `model=` and translate Stagehand's LLM requests to GMI's
OpenAI-compatible chat completions API (https://api.gmi-serving.com/v1).
"""

import json
import os
from typing import Any

from openai import AsyncOpenAI
from stagehand import LLMGenerateInput, LLMGenerateOutput
from stagehand._generated.models import (
    LLMMessageGenerateResult,
    LLMStructuredGenerateResult,
)

GMI_BASE_URL = "https://api.gmi-serving.com/v1"
DEFAULT_GMI_MODEL = "anthropic/claude-sonnet-4.5"

_FINISH_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}


def _image_part(block: dict[str, Any]) -> dict[str, Any]:
    url = f"data:{block['mime_type']};base64,{block['data']}"
    return {"type": "image_url", "image_url": {"url": url}}


def _to_openai_messages(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Stagehand's content-block messages to OpenAI chat messages."""
    messages: list[dict[str, Any]] = []
    if params.get("system_prompt"):
        messages.append({"role": "system", "content": params["system_prompt"]})

    for message in params["messages"]:
        blocks = message["content"]
        if isinstance(blocks, dict):
            blocks = [blocks]

        parts: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        for block in blocks:
            kind = block["type"]
            if kind == "text":
                parts.append({"type": "text", "text": block["text"]})
            elif kind == "image":
                parts.append(_image_part(block))
            elif kind == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {"name": block["name"], "arguments": json.dumps(block["input"])},
                })
            elif kind == "tool_result":
                # OpenAI tool messages are text-only; images go in a follow-up user message.
                text = "\n".join(c["text"] for c in block["content"] if c["type"] == "text")
                messages.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": text})
                parts.extend(_image_part(c) for c in block["content"] if c["type"] == "image")

        if message["role"] == "assistant":
            out: dict[str, Any] = {"role": "assistant", "content": parts or None}
            if tool_calls:
                out["tool_calls"] = tool_calls
            messages.append(out)
        elif parts:
            messages.append({"role": "user", "content": parts})
    return messages


def gmi_llm(model: str | None = None, api_key: str | None = None):
    """Build a Stagehand `model=` callback backed by GMI Cloud."""
    client = AsyncOpenAI(base_url=GMI_BASE_URL, api_key=api_key or os.environ["GMI_API_KEY"])
    model = model or os.getenv("GMI_MODEL", DEFAULT_GMI_MODEL)

    async def generate(request: LLMGenerateInput) -> LLMGenerateOutput:
        params = request.model_dump(mode="json", by_alias=True, exclude_unset=True)
        kwargs: dict[str, Any] = {"model": model, "messages": _to_openai_messages(params)}
        if params.get("temperature") is not None:
            kwargs["temperature"] = params["temperature"]
        if params.get("stop_sequences"):
            kwargs["stop"] = params["stop_sequences"]
        if params.get("tools"):
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in params["tools"]
            ]
            if mode := (params.get("tool_choice") or {}).get("mode"):
                kwargs["tool_choice"] = mode

        response_format = params.get("response_format") or {}
        structured = response_format.get("type") == "json_schema"
        if structured:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_format["name"],
                    "schema": response_format.get("schema") or {},
                },
            }

        completion = await client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        text = choice.message.content or ""

        content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        for call in choice.message.tool_calls or []:
            content.append({
                "type": "tool_use",
                "id": call.id,
                "name": call.function.name,
                "input": json.loads(call.function.arguments or "{}"),
            })

        result: dict[str, Any] = {
            "role": "assistant",
            "content": content or [{"type": "text", "text": ""}],
            "stop_reason": _FINISH_REASONS.get(choice.finish_reason, choice.finish_reason),
        }
        if completion.usage:
            result["usage"] = {
                "input_tokens": completion.usage.prompt_tokens,
                "output_tokens": completion.usage.completion_tokens,
                "total_tokens": completion.usage.total_tokens,
            }

        if structured:
            return LLMStructuredGenerateResult.model_validate(
                {**result, "output_format": "json_schema", "structured_content": json.loads(text)}
            )
        return LLMMessageGenerateResult.model_validate({**result, "output_format": "text"})

    return generate
