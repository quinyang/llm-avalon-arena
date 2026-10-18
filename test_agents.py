"""Agent-layer tests using the offline fake client. Run with `python test_agents.py`."""
from __future__ import annotations

import re

from agent import LLMAgent, parse_tool_input
from fake_client import FakeClient
from game import AvalonGame
from prompts import knowledge_block, render_state
from run import play_game

CFG = {"models": {r: "fake" for r in ("merlin", "percival", "loyal_servant", "morgana", "assassin")},
       "discussion_rounds": 1, "word_limit": 40, "fake": True}


def test_fake_games_complete_with_full_transcripts():
    for seed in range(30):
        tr = play_game(CFG, FakeClient(seed=seed, invalid_rate=0.2), seed, verbose=False)
        assert tr["result"]["winner"] in ("good", "evil")
        assert set(tr["roles"]) == {"0", "1", "2", "3", "4"}
        phases = {e["phase"] for e in tr["events"]}
        assert {"propose", "vote"} <= phases
        for e in tr["events"]:
            if e["phase"] in ("propose", "discuss", "assassinate"):
                assert isinstance(e["private_reasoning"], str) and e["private_reasoning"]
            if e["phase"] == "vote":
                assert len(e["votes"]) == 5 and len(e["reasoning"]) == 5
            if e["phase"] == "quest":
                for p, card in e["cards"].items():
                    if tr["roles"][p] in ("merlin", "percival", "loyal_servant"):
                        assert card == "success"


def test_no_private_knowledge_leaks_into_other_prompts():
    """Each call's prompt may contain its own player's knowledge block and nobody else's."""
    for seed in range(15):
        client = FakeClient(seed=seed)
        play_game(CFG, client, seed, verbose=False)
        game = AvalonGame(seed=seed)  # same seed -> same role assignment as play_game
        blocks = {p: knowledge_block(game.private_state(p)) for p in range(5)}
        for call in client.calls:
            me = int(re.match(r"You are Player (\d)", call["system"]).group(1))
            prompt = call["system"] + str(call["messages"])
            assert blocks[me] in prompt
            for q, b in blocks.items():
                if q != me and b != blocks[me]:
                    first_line = b.splitlines()[0]
                    assert first_line not in prompt, (seed, me, q, first_line)


def test_retry_then_fallback():
    client = FakeClient(seed=0, invalid_rate=1.0)  # every team is the wrong size
    agent = LLMAgent(0, "fake", "terse", client, seed=0)
    g = AvalonGame(seed=0)
    leader = g.current_actor()
    agent.player = leader
    d = agent.decide(g.public_state(), g.private_state(leader), g.legal_actions(leader))
    assert d.fallback and d.calls == 2 and len(d.action["team"]) == 2
    g.apply(leader, d.action)  # fallback is legal


def test_parse_rejects_illegal():
    legal = {"type": "propose", "team_size": 2, "candidates": [0, 1, 2, 3, 4]}
    for bad in ({"team": [1]}, {"team": [1, 1]}, {"team": [1, 9]}, {"team": [True, 1]}, {"team": "1,2"}):
        try:
            parse_tool_input(legal, bad, 40)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    action, _, stmt = parse_tool_input(legal, {"team": [3, 1], "public_statement": "w " * 60}, 40)
    assert action["team"] == [1, 3] and len(stmt.split()) == 40


def test_render_state_smoke():
    g = AvalonGame(seed=3)
    leader = g.current_actor()
    g.apply(leader, {"team": [0, 1], "statement": "trust me"})
    text = render_state(g.public_state(), 2)
    assert "TEAM ON THE TABLE: [P0,P1]" in text and '"trust me"' in text


if __name__ == "__main__":
    tests = [(k, v) for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")
