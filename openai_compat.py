"""Adapter: makes any OpenAI-compatible chat API (ASI:One, OpenRouter, Groq, ...) look like the
subset of anthropic.Anthropic that agent.py uses: `client.messages.create(...)` with forced tool use.

    client = OpenAICompatClient.for_asi()      # reads ASI_ONE_API_KEY
"""
from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace

ASI_BASE_URL = "https://api.asi1.ai/v1"


def _to_openai_messages(system: str, messages: list) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if m["role"] == "assistant":
            # content is the list of blocks this adapter returned earlier
            calls = [b for b in content if getattr(b, "type", None) == "tool_use"]
            text = " ".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")
            msg: dict = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = [{
                    "id": b.id, "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input)},
                } for b in calls]
            out.append(msg)
        elif isinstance(content, str):
            out.append({"role": "user", "content": content})
        else:  # list of anthropic-style tool_result / text blocks
            for block in content:
                if block.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": str(block["content"])})
                else:
                    out.append({"role": "user", "content": block.get("text", "")})
    return out


def _to_openai_tool(tool: dict) -> dict:
    return {"type": "function", "function": {
        "name": tool["name"], "description": tool.get("description", ""), "parameters": tool["input_schema"],
    }}


def _parse_arguments(raw) -> dict:
    """Tool arguments arrive as a JSON string. Fall back to the outermost {...} if the model wrapped it."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        m = re.search(r"\{.*\}", raw or "", re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"_unparseable": str(raw)[:200]}


class _Messages:
    def __init__(self, parent: "OpenAICompatClient"):
        self.p = parent

    def create(self, *, model, max_tokens, system, tools, tool_choice, messages):
        forced = tool_choice.get("name")
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            messages=_to_openai_messages(system, messages),
            tools=[_to_openai_tool(t) for t in tools],
            tool_choice={"type": "function", "function": {"name": forced}} if forced else "required",
            temperature=self.p.temperature,
        )
        if self.p.extra_body:
            kwargs["extra_body"] = self.p.extra_body
        resp = self.p.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        blocks = []
        if msg.content:
            blocks.append(SimpleNamespace(type="text", text=msg.content))
        for i, call in enumerate(msg.tool_calls or []):
            blocks.append(SimpleNamespace(
                type="tool_use", id=call.id or f"call_{i}", name=call.function.name,
                input=_parse_arguments(call.function.arguments),
            ))
        if not any(b.type == "tool_use" for b in blocks) and msg.content and forced:
            # Model ignored forced tool use but may have written the JSON as text.
            args = _parse_arguments(msg.content)
            if "_unparseable" not in args:
                blocks.append(SimpleNamespace(type="tool_use", id="call_text", name=forced, input=args))
        usage = getattr(resp, "usage", None)
        return SimpleNamespace(content=blocks, usage=SimpleNamespace(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ))


class OpenAICompatClient:
    def __init__(self, client, temperature: float = 0.8, extra_body: dict | None = None):
        self.client = client
        self.temperature = temperature
        self.extra_body = extra_body
        self.messages = _Messages(self)

    @classmethod
    def for_asi(cls, api_key: str | None = None) -> "OpenAICompatClient":
        from openai import OpenAI

        key = api_key or os.environ.get("ASI_ONE_API_KEY")
        if not key:
            raise SystemExit("Set ASI_ONE_API_KEY (create one at asi1.ai under your profile / API keys).")
        client = OpenAI(api_key=key, base_url=ASI_BASE_URL, max_retries=6, timeout=120)
        return cls(client, extra_body={"web_search": False})
