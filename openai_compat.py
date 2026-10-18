"""Adapter: makes any OpenAI-compatible chat API (ASI:One, OpenRouter, Groq, ...) look like the
subset of anthropic.Anthropic that agent.py uses: `client.messages.create(...)` with forced tool use.

    client = OpenAICompatClient.for_asi()      # reads ASI_ONE_API_KEY

Two modes:
  "tools": send the tool schema with a forced tool_choice (default).
  "json":  send no tools; ask for a bare JSON object in the reply text and parse it.
In "tools" mode, a reply with no tool call is immediately re-asked ONCE in "json" mode for that call
only (no global switch). Force a mode with env AVALON_TOOL_MODE=tools|json.
"""
from __future__ import annotations

import json
import os
import re
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

    def _blocks(self, resp, tool, required):
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
        return blocks

    def create(self, *, model, max_tokens, system, tools, tool_choice, messages):
        tool = tools[0]
        required = tool["input_schema"].get("required", [])
        args = dict(model=model, max_tokens=max_tokens, system=system, tools=tools,
                    tool_choice=tool_choice, messages=messages)
        resp = self._request(**args, mode=self.p.mode)
        blocks = self._blocks(resp, tool, required)
        n_in, n_out = self._usage(resp)
        finish = resp.choices[0].finish_reason
        self._log(self.p.mode, resp)
        if self.p.mode == "tools" and not any(b.type == "tool_use" for b in blocks):
            # One immediate rescue attempt for this call only, asking for JSON in the text.
            self.p.rescues += 1
            resp2 = self._request(**args, mode="json")
            self._log("json-rescue", resp2)
            blocks2 = self._blocks(resp2, tool, required)
            a, b = self._usage(resp2)
            n_in, n_out = n_in + a, n_out + b
            if any(x.type == "tool_use" for x in blocks2):
                self.p.rescued += 1
                blocks = blocks2
            finish = f"{finish}; rescue={resp2.choices[0].finish_reason}"
        return SimpleNamespace(content=blocks, stop_reason=finish,
                               usage=SimpleNamespace(input_tokens=n_in, output_tokens=n_out))

    @staticmethod
    def _usage(resp):
        u = getattr(resp, "usage", None)
        return (getattr(u, "prompt_tokens", 0) or 0), (getattr(u, "completion_tokens", 0) or 0)

    def _log(self, mode, resp):
        if self.p.debug:
            c = resp.choices[0]
            print(f"[openai_compat] mode={mode} finish={c.finish_reason} "
                  f"tool_calls={len(c.message.tool_calls or [])} usage={self._usage(resp)} "
                  f"content={str(c.message.content)[:200]!r}")


class OpenAICompatClient:
    def __init__(self, client, temperature: float = 0.8, extra_body: dict | None = None,
                 mode: str | None = None, min_max_tokens: int = 0, debug: bool = False):
        self.client = client
        self.temperature = temperature
        self.extra_body = extra_body
        self.mode = mode or os.environ.get("AVALON_TOOL_MODE", "tools")
        self.min_max_tokens = min_max_tokens
        self.debug = debug or bool(os.environ.get("AVALON_DEBUG"))
        self.rescues = 0   # tool-mode replies with no tool call
        self.rescued = 0   # of those, fixed by the JSON re-ask
        self.messages = _Messages(self)

    @classmethod
    def for_asi(cls, api_key: str | None = None) -> "OpenAICompatClient":
        from openai import OpenAI

        key = api_key or os.environ.get("ASI_ONE_API_KEY")
        if not key:
            raise SystemExit("Set ASI_ONE_API_KEY (create one at asi1.ai under your profile / API keys).")
        client = OpenAI(api_key=key, base_url=ASI_BASE_URL, max_retries=3, timeout=180)
        # The documented "web_search" extra field is rejected (400) by the live API, so none is sent.
        # ASI spends ~1-3k output tokens on hidden reasoning before answering; a low cap truncates
        # the answer (no tool call). You only pay for tokens actually generated.
        return cls(client, min_max_tokens=8000)
