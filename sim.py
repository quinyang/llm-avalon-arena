"""Random-bot driver: validates the rules engine with zero LLM calls.

    python sim.py --games 1000 --seed 0
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter

from game import MAX_REJECTIONS, AvalonGame, Phase
from roles import Role, Team, team_of

MAX_STEPS = 2000  # a 5-player game can't need more than ~5 quests * 5 proposals * 12 actions


def random_action(game: AvalonGame, player: int, rng: random.Random, approve_p: float) -> dict:
    la = game.legal_actions(player)
    t = la["type"]
    if t == "propose":
        return {"team": rng.sample(la["candidates"], la["team_size"]), "statement": f"P{player} proposes."}
    if t == "discuss":
        return {"statement": f"P{player} speaks."}
    if t == "vote":
        return {"vote": "approve" if rng.random() < approve_p else "reject"}
    if t == "quest":
        # Deliberately let Good bots *try* to fail so the engine override path is exercised.
        return {"card": rng.choice(["success", "fail"])}
    if t == "assassinate":
        return {"target": rng.choice(la["targets"])}
    raise AssertionError(f"unknown action type {t}")


def play_random_game(seed: int, approve_p: float = 0.5, discussion_rounds: int = 1) -> AvalonGame:
    rng = random.Random(seed)
    game = AvalonGame(seed=seed, discussion_rounds=discussion_rounds, first_leader=rng.randrange(5))
    steps = 0
    while not game.is_over():
        actors = game.pending_actors()
        assert actors, f"no pending actors in phase {game.phase}"
        if game.phase in (Phase.VOTE, Phase.QUEST):
            rng.shuffle(actors)  # simultaneous phases must be order-independent
        for p in actors:
            game.apply(p, random_action(game, p, rng, approve_p))
            steps += 1
            if game.phase not in (Phase.VOTE, Phase.QUEST):
                break
        if steps > MAX_STEPS:
            raise AssertionError(f"game {seed} did not terminate")
    return game


def check_invariants(game: AvalonGame) -> list[str]:
    errs: list[str] = []
    res = game.result()
    if res is None:
        return ["finished game has no result"]

    # Quest teams have the right size; Good players never play fail; public fail count matches truth.
    for q, cards in zip(game.quest_history, game.quest_cards):
        if len(q["team"]) != game.quest_sizes[q["quest"] - 1]:
            errs.append(f"quest {q['quest']} wrong team size {q['team']}")
        true_fails = 0
        for p, c in cards.items():
            if c["played"] == "fail":
                true_fails += 1
                if team_of(game.roles[int(p)]) is Team.GOOD:
                    errs.append(f"good player {p} played fail on quest {q['quest']}")
        if true_fails != q["fails"]:
            errs.append(f"quest {q['quest']} fail count mismatch")
        if q["succeeded"] != (q["fails"] == 0):
            errs.append(f"quest {q['quest']} success flag wrong")

    # Every proposal has n votes; at most MAX_REJECTIONS consecutive rejections per quest.
    run = 0
    last_quest = None
    for prop in game.proposal_history:
        if len(prop["votes"]) != game.n:
            errs.append("proposal missing votes")
        approvals = sum(v == "approve" for v in prop["votes"].values())
        if prop["approved"] != (approvals >= 3):
            errs.append("approval majority wrong")
        if prop["quest"] != last_quest:
            run, last_quest = 0, prop["quest"]
        run = 0 if prop["approved"] else run + 1
        if run > MAX_REJECTIONS:
            errs.append("more than 5 consecutive rejections")

    succ = sum(res["quests"])
    fail = len(res["quests"]) - succ
    if len(res["quests"]) > 5:
        errs.append("more than 5 quests")
    r = res["reason"]
    if r == "three_quests_failed" and not (fail == 3 and res["winner"] == "evil"):
        errs.append("bad three_quests_failed ending")
    if r == "five_rejections" and not (run == MAX_REJECTIONS and res["winner"] == "evil"):
        errs.append("bad five_rejections ending")
    if r in ("merlin_assassinated", "three_quests_succeeded"):
        if succ != 3 or res["merlin_guess"] is None:
            errs.append("assassination without three successes")
        hit = game.roles[res["merlin_guess"]] is Role.MERLIN
        if (r == "merlin_assassinated") != hit or res["winner"] != ("evil" if hit else "good"):
            errs.append("assassination outcome wrong")
        if res["merlin_guess"] == game.roles.index(Role.ASSASSIN):
            errs.append("assassin targeted self")
    if res["winner"] == "good" and r != "three_quests_succeeded":
        errs.append("good won by a non-assassination path")

    # Information leak check: role names must never appear in public state.
    blob = json.dumps(game.public_state())
    for role in Role:
        if f'"{role.value}"' in blob:
            errs.append(f"role '{role.value}' leaked into public_state")
    if "submitted" in blob or "played" in blob:
        errs.append("individual quest cards leaked into public_state")
    return errs


def run_batch(n_games: int, seed: int, approve_p: float) -> dict:
    winners, reasons = Counter(), Counter()
    proposals = quests = overrides = 0
    violations: list[str] = []
    for i in range(n_games):
        g = play_random_game(seed + i, approve_p=approve_p)
        for e in check_invariants(g):
            violations.append(f"game {seed + i}: {e}")
        res = g.result()
        winners[res["winner"]] += 1
        reasons[res["reason"]] += 1
        proposals += res["proposals"]
        quests += len(res["quests"])
        overrides += sum(
            c["submitted"] != c["played"] for qc in g.quest_cards for c in qc.values()
        )
    return {
        "games": n_games,
        "approve_p": approve_p,
        "winrate": {k: round(v / n_games, 3) for k, v in sorted(winners.items())},
        "end_reasons": {k: round(v / n_games, 3) for k, v in sorted(reasons.items())},
        "avg_proposals": round(proposals / n_games, 2),
        "avg_quests": round(quests / n_games, 2),
        "good_fail_attempts_overridden": overrides,
        "violations": violations[:20],
        "violation_count": len(violations),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    for p in (0.5, 0.0, 1.0):  # random, always-reject, always-approve
        print(json.dumps(run_batch(args.games, args.seed, p), indent=2))


if __name__ == "__main__":
    main()
