"""Adapter: makes any OpenAI-compatible chat API (ASI:One, OpenRouter, Groq, ...) look like the
subset of anthropic.Anthropic that agent.py uses: `client.messages.create(...)` with forced tool use.

    client = OpenAICompatClient.for_asi()      # reads ASI_ONE_API_KEY

Two modes:
  "tools": send the tool schema with a forced tool_choice (preferred).
  "json":  send no tools; ask for a bare JSON object in the reply text and parse it.
The client starts in "tools" mode and switches itself to "json" (sticky) after the model fails to
return a tool call twice. Force a mode with env AVALON_TOOL_MODE=tools|json.
"""
from __future__ import annotations

import json
import os
import re
import threading
from types import SimpleNamespace

ASI_BASE_URL = "https://api.asi1.ai/v1"
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def _to_openai_messages(system: str, messages: list) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if m["role"] == "assistant":
            # content is the list of blocks this adapter returned earlier
            calls = [b for b in content if getattr(b, "type", None) == "tool_use" and not getattr(b, "from_text", False)]
            text = " ".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")
            if not calls:  # JSON-in-text reply: replay as plain text
                out.append({"role": "assistant", "content": text or "{}"})
                continue
            out.append({"role": "assistant", "content": text, "tool_calls": [{
                "id": b.id, "type": "function",
                "function": {"name": b.name, "arguments": json.dumps(b.input)},
            } for b in calls]})
        elif isinstance(content, str):
            out.append({"role": "user", "content": content})
        else:  # list of anthropic-style tool_result / text blocks
            for block in content:
                if block.get("type") == "tool_result":
                    if out[-1].get("tool_calls"):
                        out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": str(block["content"])})
                    else:
                        out.append({"role": "user", "content": str(block["content"])})
                else:
                    out.append({"role": "user", "content": block.get("text", "")})
    return out


def _to_openai_tool(tool: dict) -> dict:
    return {"type": "function", "function": {
        "name": tool["name"], "description": tool.get("description", ""), "parameters": tool["input_schema"],
    }}


def _json_instruction(tool: dict) -> str:
    schema = tool["input_schema"]
    return (
        "\n\nOUTPUT FORMAT (mandatory): reply with ONLY one JSON object, no prose before or after, "
        "no markdown fences. Keys: " + ", ".join(schema["required"]) + ".\n"
        "JSON schema: " + json.dumps(schema)
    )


def extract_json_object(text: str | None, required: list[str] | None = None) -> dict | None:
    """Find a JSON object in free text. Prefers the last object that has all required keys."""
    if not text:
        return None
    text = _THINK.sub("", text)
    dec = json.JSONDecoder()
    found = []
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found.append(obj)
    if required:
        full = [o for o in found if all(k in o for k in required)]
        if full:
            return full[-1]
    return max(found, key=len) if found else None


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        obj = extract_json_object(raw)
        return obj if obj is not None else {"_unparseable": str(raw)[:200]}


class _Messages:
    def __init__(self, parent: "OpenAICompatClient"):
        self.p = parent

    def _request(self, *, model, max_tokens, system, tools, tool_choice, messages, mode):
        tool = tools[0]
        kwargs = dict(
            model=model,
            max_tokens=max(max_tokens, self.p.min_max_tokens),
            temperature=self.p.temperature,
        )
        if mode == "tools":
            forced = tool_choice.get("name")
            kwargs["messages"] = _to_openai_messages(system, messages)
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]
            kwargs["tool_choice"] = {"type": "function", "function": {"name": forced}} if forced else "required"
        else:
            kwargs["messages"] = _to_openai_messages(system + _json_instruction(tool), messages)
        if self.p.extra_body:
            kwargs["extra_body"] = self.p.extra_body
        return self.p.client.chat.completions.create(**kwargs)

    def create(self, *, model, max_tokens, system, tools, tool_choice, messages):
        tool = tools[0]
        required = tool["input_schema"].get("required", [])
        mode = self.p.mode
        resp = self._request(model=model, max_tokens=max_tokens, system=system, tools=tools,
                             tool_choice=tool_choice, messages=messages, mode=mode)
        msg = resp.choices[0].message
        blocks = []
        if msg.content:
            blocks.append(SimpleNamespace(type="text", text=msg.content))
        for i, call in enumerate(msg.tool_calls or []):
            blocks.append(SimpleNamespace(
                type="tool_use", id=call.id or f"call_{i}", name=call.function.name,
                input=_parse_arguments(call.function.arguments), from_text=False,
            ))
        if not any(b.type == "tool_use" for b in blocks):
            obj = extract_json_object(msg.content, required)
            if obj is not None:
                blocks.append(SimpleNamespace(type="tool_use", id="call_text", name=tool["name"],
                                              input=obj, from_text=True))
            elif mode == "tools":
                self.p.note_tool_failure()
        usage = getattr(resp, "usage", None)
        if self.p.debug:
            print(f"[openai_compat] mode={mode} finish={resp.choices[0].finish_reason} "
                  f"tool_calls={len(msg.tool_calls or [])} content={str(msg.content)[:300]!r}")
        return SimpleNamespace(content=blocks, usage=SimpleNamespace(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ))


class OpenAICompatClient:
    def __init__(self, client, temperature: float = 0.8, extra_body: dict | None = None,
                 mode: str | None = None, min_max_tokens: int = 0, debug: bool = False):
        self.client = client
        self.temperature = temperature
        self.extra_body = extra_body
        self.mode = mode or os.environ.get("AVALON_TOOL_MODE", "tools")
        self.auto_switch = os.environ.get("AVALON_TOOL_MODE") is None and mode is None
        self.min_max_tokens = min_max_tokens
        self.debug = debug or bool(os.environ.get("AVALON_DEBUG"))
        self._tool_failures = 0
        self._lock = threading.Lock()
        self.messages = _Messages(self)

    def note_tool_failure(self) -> None:
        with self._lock:
            self._tool_failures += 1
            if self.auto_switch and self.mode == "tools" and self._tool_failures >= 2:
                self.mode = "json"
                print("[openai_compat] model is not returning tool calls; switching to JSON-in-text mode")

    @classmethod
    def for_asi(cls, api_key: str | None = None) -> "OpenAICompatClient":
        from openai import OpenAI

        key = api_key or os.environ.get("ASI_ONE_API_KEY")
        if not key:
            raise SystemExit("Set ASI_ONE_API_KEY (create one at asi1.ai under your profile / API keys).")
        client = OpenAI(api_key=key, base_url=ASI_BASE_URL, max_retries=3, timeout=60)
        # The documented "web_search" extra field is rejected (400) by the live API, so none is sent.
        # ASI models may emit long reasoning text; give them room so the answer isn't truncated.
        return cls(client, min_max_tokens=2000)
