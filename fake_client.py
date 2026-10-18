"""Offline stand-in for anthropic.Anthropic: returns random (sometimes invalid) tool calls.

Lets you test run.py end to end with zero API spend: `python run.py --fake --games 20`.
"""
from __future__ import annotations

import random
import threading
from types import SimpleNamespace


class _Messages:
    def __init__(self, parent: "FakeClient"):
        self.parent = parent

    def create(self, *, model, max_tokens, system, tools, tool_choice, messages):
        p = self.parent
        with p.lock:
            p.calls.append({"system": system, "messages": messages, "tool": tools[0]["name"]})
            rng = p.rng
            schema = tools[0]["input_schema"]["properties"]
            inp = {}
            for name, spec in schema.items():
                if name == "team":
                    size = spec["minItems"]
                    if rng.random() < p.invalid_rate:
                        size += 1  # deliberately wrong to exercise retry/fallback
                    inp[name] = rng.sample(range(5), size)
                elif "enum" in spec:
                    inp[name] = rng.choice(spec["enum"])
                elif name == "private_reasoning":
                    inp[name] = f"fake reasoning {rng.randrange(1000)}"
                else:
                    inp[name] = f"fake statement {rng.randrange(1000)}"
            if "vote" in inp and rng.random() < p.invalid_rate:
                inp["vote"] = "maybe"
            block = SimpleNamespace(type="tool_use", id=f"toolu_{len(p.calls)}", name=tools[0]["name"], input=inp)
            n_in = (len(system) + sum(len(str(m["content"])) for m in messages)) // 4
            return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=n_in, output_tokens=60))


class FakeClient:
    def __init__(self, seed: int = 0, invalid_rate: float = 0.1):
        self.rng = random.Random(seed)
        self.invalid_rate = invalid_rate
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self.messages = _Messages(self)
