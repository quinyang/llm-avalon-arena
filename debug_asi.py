"""Print the raw ASI:One response for one forced tool call, so we can see what the model returns.

    python debug_asi.py                # model asi1-fast
    python debug_asi.py asi1
"""
from __future__ import annotations

import json
import os
import sys

from openai import OpenAI

model = sys.argv[1] if len(sys.argv) > 1 else "asi1-fast"
client = OpenAI(api_key=os.environ["ASI_ONE_API_KEY"], base_url="https://api.asi1.ai/v1", max_retries=0, timeout=90)

tool = {"type": "function", "function": {
    "name": "vote", "description": "Vote on the proposed team.",
    "parameters": {"type": "object", "properties": {
        "private_reasoning": {"type": "string"},
        "vote": {"type": "string", "enum": ["approve", "reject"]},
    }, "required": ["private_reasoning", "vote"]},
}}
messages = [
    {"role": "system", "content": "You are Player 2 in a game of Avalon (Loyal Servant, Good team). Call the vote tool."},
    {"role": "user", "content": "Quest 1 needs 2 players. Leader P0 proposes [P0, P3]. Vote now."},
]

for label, extra in [("forced tool", {"tool_choice": {"type": "function", "function": {"name": "vote"}}}),
                     ("required", {"tool_choice": "required"}),
                     ("auto", {"tool_choice": "auto"})]:
    print(f"\n===== {label} =====")
    try:
        r = client.chat.completions.create(model=model, messages=messages, tools=[tool], max_tokens=1500, **extra)
    except Exception as e:  # show the API's complaint and keep going
        print("ERROR:", e)
        continue
    c = r.choices[0]
    print("finish_reason:", c.finish_reason)
    print("tool_calls:", [(t.function.name, t.function.arguments) for t in (c.message.tool_calls or [])])
    print("content:", (c.message.content or "")[:1200])
    print("usage:", r.usage)
    extra_fields = {k: v for k, v in (r.model_extra or {}).items()}
    if extra_fields:
        print("extra fields:", json.dumps(extra_fields, default=str)[:800])
