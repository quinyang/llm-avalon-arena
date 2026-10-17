"""Hour 0-0.5 check: one model call that returns a schema-valid JSON action.

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    python smoke_test.py

Uses tool use with a forced tool_choice, so the API returns parsed JSON matching the
schema instead of free text we have to json.loads. Keep retry + random-legal-action
fallback in agent.py as a backstop anyway.
"""
from __future__ import annotations

import json
import os

import anthropic

MODEL = os.environ.get("AVALON_MODEL", "claude-haiku-4-5")

VOTE_TOOL = {
    "name": "submit_vote",
    "description": "Submit your private reasoning and your vote on the proposed team.",
    "input_schema": {
        "type": "object",
        "properties": {
            "private_reasoning": {"type": "string", "description": "Never shown to other players."},
            "vote": {"type": "string", "enum": ["approve", "reject"]},
        },
        "required": ["private_reasoning", "vote"],
    },
}

SYSTEM = (
    "You are Player 2 in a game of Avalon, a social deduction board game. "
    "Your secret role: Loyal Servant of Arthur. Your team: GOOD. You have no special information."
)

STATE = """QUEST HISTORY:  (none yet)
VOTE HISTORY:   (none yet)
CURRENT:        Quest 1, needs 2 players, leader P0, rejections this quest: 0
PROPOSAL:       P0 proposes team [0, 3]
SCORE:          Good 0 - Evil 0
Vote on the proposed team."""


def main() -> None:
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=SYSTEM,
        tools=[VOTE_TOOL],
        tool_choice={"type": "tool", "name": "submit_vote"},
        messages=[{"role": "user", "content": STATE}],
    )
    action = next(b.input for b in resp.content if b.type == "tool_use")
    assert action["vote"] in ("approve", "reject"), action
    print(json.dumps(action, indent=2))
    print(f"model={MODEL} input_tokens={resp.usage.input_tokens} output_tokens={resp.usage.output_tokens}")


if __name__ == "__main__":
    main()
