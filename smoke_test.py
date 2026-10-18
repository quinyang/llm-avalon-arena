"""One real model call per action type, through the same agent code the game uses.

    python smoke_test.py --provider asi          # needs ASI_ONE_API_KEY
    python smoke_test.py --provider anthropic    # needs ANTHROPIC_API_KEY
"""
from __future__ import annotations

import argparse
import json

from agent import LLMAgent
from game import AvalonGame
from run import PROVIDER_DEFAULT_MODELS, default_provider, make_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["anthropic", "asi", "fake"], default=default_provider())
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    model = args.model or PROVIDER_DEFAULT_MODELS[args.provider]
    client = make_client(args.provider, seed=0)

    game = AvalonGame(seed=42)
    leader = game.current_actor()
    agents = {p: LLMAgent(p, model, "terse and suspicious", client, seed=p) for p in range(5)}

    def show(label, p, d):
        if not d.fallback:
            status = f"ok (retries={d.retries})"
        elif (d.error or "").startswith("api_error"):
            status = "API ERROR (request rejected; see error below)"
        else:
            status = "FALLBACK (model output was invalid twice)"
        print(f"\n[{label}] P{p} {game.roles[p].value}: {status}")
        print(json.dumps({"action": d.action, "private_reasoning": d.private_reasoning,
                          "public_statement": d.public_statement, "error": d.error,
                          "tokens": [d.input_tokens, d.output_tokens]}, indent=2))
        return d

    d = show("propose", leader, agents[leader].decide(game.public_state(), game.private_state(leader), game.legal_actions(leader)))
    game.apply(leader, d.action)
    speaker = game.current_actor()
    d = show("discuss", speaker, agents[speaker].decide(game.public_state(), game.private_state(speaker), game.legal_actions(speaker)))
    game.apply(speaker, d.action)
    while game.current_phase().value == "discuss":  # skip the rest of the discussion
        game.apply(game.current_actor(), {"statement": "Seems fine to me."})
    voter = 0
    show("vote", voter, agents[voter].decide(game.public_state(), game.private_state(voter), game.legal_actions(voter)))
    print(f"\nprovider={args.provider} model={model}")


if __name__ == "__main__":
    main()
