"""Rules-engine tests. Run with `python test_game.py` (or `pytest` if installed)."""
from __future__ import annotations

import json

from game import AvalonGame, IllegalAction, Phase
from roles import Role
from sim import check_invariants, play_random_game

M, P, L, MO, A = Role.MERLIN, Role.PERCIVAL, Role.LOYAL_SERVANT, Role.MORGANA, Role.ASSASSIN
FIXED = [M, P, L, MO, A]  # P0 Merlin, P1 Percival, P2 Servant, P3 Morgana, P4 Assassin


def raises(fn, *args) -> bool:
    try:
        fn(*args)
    except IllegalAction:
        return True
    return False


def propose_and_vote(g: AvalonGame, team: list[int], approve: bool) -> None:
    g.apply(g.leader, {"team": team, "statement": "x"})
    while g.phase is Phase.DISCUSS:
        g.apply(g.current_actor(), {"statement": "y"})
    for p in range(g.n):
        g.apply(p, {"vote": "approve" if approve else "reject"})


def run_quest(g: AvalonGame, cards: dict[int, str]) -> None:
    for p in g.current_team:
        g.apply(p, {"card": cards.get(p, "success")})


def test_private_knowledge():
    g = AvalonGame(roles=FIXED)
    assert g.private_state(0)["evil_players"] == [3, 4]
    assert g.private_state(1)["merlin_or_morgana"] == [0, 3]
    assert set(g.private_state(2)) == {"player", "role", "team"}
    assert g.private_state(3)["evil_partner"] == 4
    assert g.private_state(4)["evil_partner"] == 3
    # Percival's pair is sorted, so order can't reveal Merlin.
    g2 = AvalonGame(roles=[MO, P, M, L, A])
    assert g2.private_state(1)["merlin_or_morgana"] == [0, 2]


def test_five_rejections_evil_wins():
    g = AvalonGame(roles=FIXED)
    for i in range(5):
        assert g.leader == i % 5
        propose_and_vote(g, [0, 1], approve=False)
    assert g.is_over() and g.result()["winner"] == "evil"
    assert g.result()["reason"] == "five_rejections"


def test_rejection_counter_resets_on_approval():
    g = AvalonGame(roles=FIXED)
    for _ in range(4):
        propose_and_vote(g, [0, 1], approve=False)
    propose_and_vote(g, [0, 1], approve=True)
    assert g.rejections == 0
    run_quest(g, {})
    for _ in range(4):
        propose_and_vote(g, [0, 1, 2], approve=False)
    assert not g.is_over() and g.rejections == 4


def test_good_cannot_fail():
    g = AvalonGame(roles=FIXED)
    propose_and_vote(g, [0, 1], approve=True)
    run_quest(g, {0: "fail", 1: "fail"})
    assert g.quest_history[0]["fails"] == 0 and g.quest_history[0]["succeeded"]
    assert g.quest_cards[0]["0"] == {"submitted": "fail", "played": "success"}


def test_evil_fail_fails_quest_and_cards_hidden():
    g = AvalonGame(roles=FIXED)
    propose_and_vote(g, [2, 3], approve=True)
    run_quest(g, {3: "fail"})
    pub = g.public_state()
    assert pub["quest_history"][0] == {"quest": 1, "team": [2, 3], "fails": 1, "succeeded": False}
    assert "played" not in json.dumps(pub)


def test_votes_hidden_until_all_in():
    g = AvalonGame(roles=FIXED, discussion_rounds=0)
    g.apply(0, {"team": [0, 1]})
    for p in range(4):
        g.apply(p, {"vote": "approve"})
    pub = g.public_state()
    assert pub["proposal_history"] == [] and "approve" not in json.dumps(pub)
    g.apply(4, {"vote": "reject"})
    assert g.public_state()["proposal_history"][0]["votes"]["4"] == "reject"


def test_illegal_actions():
    g = AvalonGame(roles=FIXED)
    assert raises(g.apply, 1, {"team": [0, 1]})          # not the leader
    assert raises(g.apply, 0, {"team": [0, 1, 2]})       # wrong size
    assert raises(g.apply, 0, {"team": [1, 1]})          # duplicate
    assert raises(g.apply, 0, {"team": [0, 7]})          # out of range
    assert raises(g.apply, 0, {"team": [0, True]})       # bool is not a player
    assert raises(g.apply, 0, {"team": "0,1"})           # wrong type
    g.apply(0, {"team": [0, 1]})
    assert raises(g.apply, 0, {"statement": "out of turn"})  # P1 speaks first, leader last
    order = list(g.speaking_order)
    assert order == [1, 2, 3, 4, 0]
    for p in order:
        g.apply(p, {"statement": "s"})
    g.apply(2, {"vote": "approve"})
    assert raises(g.apply, 2, {"vote": "reject"})        # double vote
    assert raises(g.apply, 3, {"vote": "maybe"})         # bad value
    for p in (0, 1, 3, 4):
        g.apply(p, {"vote": "approve"})
    assert raises(g.apply, 3, {"card": "fail"})          # not on the team
    assert raises(g.apply, 0, {"card": "pass"})          # bad card


def win_three_quests(g: AvalonGame) -> None:
    for team in ([0, 1], [0, 1, 2], [1, 2]):
        propose_and_vote(g, team, approve=True)
        run_quest(g, {})


def test_assassination_hit_and_miss():
    g = AvalonGame(roles=FIXED)
    win_three_quests(g)
    assert g.phase is Phase.ASSASSINATE and g.current_actor() == 4
    assert raises(g.apply, 4, {"target": 4})             # can't target self
    assert raises(g.apply, 3, {"target": 0})             # only the assassin acts
    g.apply(4, {"target": 0})
    r = g.result()
    assert r["winner"] == "evil" and r["reason"] == "merlin_assassinated" and r["merlin_survived"] is False

    g = AvalonGame(roles=FIXED)
    win_three_quests(g)
    g.apply(4, {"target": 1})
    r = g.result()
    assert r["winner"] == "good" and r["merlin_survived"] is True
    assert raises(g.apply, 0, {"team": [0, 1]})          # game over


def test_three_fails_no_assassination():
    g = AvalonGame(roles=FIXED)
    for team in ([0, 3], [0, 1, 3], [2, 4]):
        propose_and_vote(g, team, approve=True)
        run_quest(g, {3: "fail", 4: "fail"})
    r = g.result()
    assert r["reason"] == "three_quests_failed" and r["merlin_guess"] is None and r["merlin_survived"] is None


def test_public_state_is_a_copy():
    g = AvalonGame(roles=FIXED)
    pub = g.public_state()
    pub["quest_history"].append("junk")
    assert g.public_state()["quest_history"] == []


def test_random_games_have_no_violations():
    for seed in range(300):
        g = play_random_game(seed)
        assert check_invariants(g) == [], (seed, check_invariants(g))


if __name__ == "__main__":
    tests = [(k, v) for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")
