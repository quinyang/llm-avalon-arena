"""LLM wrapper: prompt assembly, forced tool call, validation, one retry, random fallback.

Information boundary: `decide()` only ever receives public_state, this player's private_state,
and this player's legal actions. It never sees the game object.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from prompts import phase_instruction, render_state, system_prompt, tool_for

API_ERRORS: tuple = ()  # provider errors that mean "give up on this action", not "crash the batch"
try:
    import anthropic

    API_ERRORS += (anthropic.APIError,)
except ImportError:  # SDKs are optional; the fake-client tests need neither
    pass
try:
    import openai

    API_ERRORS += (openai.APIError,)
except ImportError:
    pass

# USD per million tokens (input, output). Verify against anthropic.com/pricing; unknown models -> cost None.
PRICES_PER_MTOK = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}


@dataclass
class Decision:
    action: dict
    private_reasoning: str
    public_statement: str | None = None
    fallback: bool = False          # true if we gave up on the model and took a random legal action
    retries: int = 0
    error: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    meta: dict = field(default_factory=dict)


def clip_words(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    words = str(text).split()
    return " ".join(words) if len(words) <= limit else " ".join(words[:limit]) + "..."


def random_legal(legal: dict, rng: random.Random) -> dict:
    t = legal["type"]
    if t == "propose":
        return {"team": sorted(rng.sample(legal["candidates"], legal["team_size"])), "statement": "..."}
    if t == "discuss":
        return {"statement": "..."}
    if t == "vote":
        return {"vote": rng.choice(legal["options"])}
    if t == "quest":
        return {"card": rng.choice(legal["options"])}
    if t == "assassinate":
        return {"target": rng.choice(legal["targets"])}
    raise ValueError(t)


def parse_tool_input(legal: dict, inp: dict, word_limit: int) -> tuple[dict, str, str | None]:
    """Validate tool input against legal actions. Returns (engine_action, reasoning, statement)."""
    if not isinstance(inp, dict):
        raise ValueError("tool input must be an object")
    reasoning = str(inp.get("private_reasoning", "")).strip()
    statement = clip_words(inp.get("public_statement"), word_limit)
    t = legal["type"]
    if t == "propose":
        team = inp.get("team")
        size = legal["team_size"]
        if not isinstance(team, list) or not all(isinstance(p, int) and not isinstance(p, bool) for p in team):
            raise ValueError(f"'team' must be a list of player numbers, got {team!r}")
        if len(team) != size or len(set(team)) != size:
            raise ValueError(f"'team' must contain exactly {size} distinct players, got {team}")
        if not all(p in legal["candidates"] for p in team):
            raise ValueError(f"'team' players must be in {legal['candidates']}, got {team}")
        return {"team": sorted(team), "statement": statement or ""}, reasoning, statement
    if t == "discuss":
        if not statement:
            raise ValueError("'public_statement' must be a non-empty string")
        return {"statement": statement}, reasoning, statement
    if t == "vote":
        if inp.get("vote") not in legal["options"]:
            raise ValueError(f"'vote' must be one of {legal['options']}")
        return {"vote": inp["vote"]}, reasoning, None
    if t == "quest":
        if inp.get("card") not in legal["options"]:
            raise ValueError(f"'card' must be one of {legal['options']}")
        return {"card": inp["card"]}, reasoning, None
    if t == "assassinate":
        g = inp.get("merlin_guess")
        if isinstance(g, bool) or not isinstance(g, int) or g not in legal["targets"]:
            raise ValueError(f"'merlin_guess' must be one of {legal['targets']}")
        return {"target": g}, reasoning, None
    raise ValueError(t)


class LLMAgent:
    def __init__(
        self,
        player: int,
        model: str,
        personality: str,
        client,
        word_limit: int = 40,
        max_tokens: int = 700,
        seed: int | None = None,
    ):
        self.player = player
        self.model = model
        self.personality = personality
        self.client = client
        self.word_limit = word_limit
        self.max_tokens = max_tokens
        self.rng = random.Random(seed)

    def decide(self, public_state: dict, private_state: dict, legal: dict) -> Decision:
        assert private_state["player"] == self.player, "agent got another player's private state"
        system = system_prompt(private_state, self.personality, self.word_limit)
        history = None if legal["type"] == "assassinate" else 10  # Assassin sees all table talk
        user = render_state(public_state, self.player, history) + "\n\n" + phase_instruction(public_state, self.player, legal)
        tool = tool_for(legal, self.word_limit)
        messages: list = [{"role": "user", "content": user}]
        d = Decision(action={}, private_reasoning="", model=self.model)

        for attempt in range(2):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool["name"]},
                    messages=messages,
                )
            except API_ERRORS as e:  # SDK already retried transient errors; give up on this action
                d.error = f"api_error: {type(e).__name__}: {e}"
                break
            d.calls += 1
            d.input_tokens += resp.usage.input_tokens
            d.output_tokens += resp.usage.output_tokens
            block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
            try:
                if block is None:
                    raise ValueError("no tool call in response")
                d.action, d.private_reasoning, d.public_statement = parse_tool_input(legal, block.input, self.word_limit)
                d.retries = attempt
                return d
            except ValueError as e:
                d.error = str(e)
                if attempt == 0:
                    messages.append({"role": "assistant", "content": resp.content})
                    fix = f"Invalid action: {e}. Call {tool['name']} again with a legal action."
                    if block is not None:
                        messages.append({"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": block.id, "content": fix, "is_error": True}
                        ]})
                    else:
                        messages.append({"role": "user", "content": fix})

        d.fallback = True
        d.retries = 1
        d.action = random_legal(legal, self.rng)
        d.private_reasoning = d.private_reasoning or f"(fallback: random legal action after error: {d.error})"
        d.public_statement = d.action.get("statement")
        return d


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES_PER_MTOK:
        return None
    pin, pout = PRICES_PER_MTOK[model]
    return (input_tokens * pin + output_tokens * pout) / 1_000_000
