"""Run N LLM games and write one transcript JSON per game.

    python run.py --fake --games 5                     # offline plumbing test, no API spend
    python run.py                                      # one real game, printed live
    python run.py --games 20 --parallel 4              # batch for the stats
    python run.py --role-model assassin=claude-sonnet-4-5 --games 10
    python run.py --provider asi                       # Fetch.ai ASI:One (needs ASI_ONE_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from agent import Decision, LLMAgent, cost_usd, random_legal
from game import AvalonGame, IllegalAction, Phase
from prompts import PERSONALITIES
from roles import ROLES_5P, Team, team_of

PROVIDER_DEFAULT_MODELS = {"anthropic": "claude-haiku-4-5", "asi": "asi1-fast", "fake": "fake"}

# ANSI colors for the live terminal view
RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
GOOD_C, EVIL_C, SYS_C = "\033[94m", "\033[91m", "\033[93m"


def make_client(provider: str, seed: int):
    if provider == "fake":
        from fake_client import FakeClient
        return FakeClient(seed=seed)
    if provider == "asi":
        from openai_compat import OpenAICompatClient
        return OpenAICompatClient.for_asi()
    import anthropic
    return anthropic.Anthropic(max_retries=6)  # SDK backs off on 429/5xx


class Printer:
    def __init__(self, game: AvalonGame, enabled: bool):
        self.game, self.enabled = game, enabled

    def name(self, p: int) -> str:
        role = self.game.roles[p]
        c = EVIL_C if team_of(role) is Team.EVIL else GOOD_C
        return f"{c}P{p} ({role.value}){RESET}"

    def sys(self, msg: str) -> None:
        if self.enabled:
            print(f"{SYS_C}{msg}{RESET}")

    def act(self, p: int, d: Decision, what: str = "") -> None:
        if not self.enabled:
            return
        print(f"{self.name(p)} {what}")
        print(f"   {DIM}thinks: {d.private_reasoning}{RESET}")
        if d.public_statement:
            print(f"   {BOLD}says:   {d.public_statement}{RESET}")
        if d.fallback:
            print(f"   {SYS_C}[fallback: {d.error}]{RESET}")


def safe_apply(game: AvalonGame, p: int, d: Decision, legal: dict, rng: random.Random) -> dict:
    """Apply the decision; if the engine still rejects it, fall back to a random legal action."""
    try:
        return game.apply(p, d.action)
    except IllegalAction as e:
        d.fallback, d.error = True, f"engine rejected: {e}"
        d.action = random_legal(legal, rng)
        return game.apply(p, d.action)


def play_game(cfg: dict, client, seed: int, verbose: bool) -> dict:
    t0 = time.time()
    rng = random.Random(seed)
    game = AvalonGame(seed=seed, discussion_rounds=cfg["discussion_rounds"], first_leader=rng.randrange(5))
    personalities = rng.sample(PERSONALITIES, game.n)
    seat_models = {p: cfg["models"][game.roles[p].value] for p in range(game.n)}
    agents = [
        LLMAgent(p, seat_models[p], personalities[p], client, cfg["word_limit"], seed=seed * 10 + p)
        for p in range(game.n)
    ]
    out = Printer(game, verbose)
    events: list[dict] = []
    decisions: list[Decision] = []

    def decide(p: int, pub: dict) -> Decision:
        legal = game.legal_actions(p)
        if legal["type"] == "quest" and legal["options"] == ["success"]:
            # Good players have no choice; skip the API call (engine enforces this anyway).
            return Decision(action={"card": "success"}, private_reasoning="(automatic: Good can only play Success)")
        return agents[p].decide(pub, game.private_state(p), legal)

    with ThreadPoolExecutor(max_workers=game.n) as pool:
        while not game.is_over():
            phase = game.current_phase()
            pub = game.public_state()

            if phase in (Phase.VOTE, Phase.QUEST):
                # Simultaneous: everyone decides from the same snapshot, then we apply.
                actors = game.pending_actors()
                legal = {p: game.legal_actions(p) for p in actors}
                ds = dict(zip(actors, pool.map(lambda p: decide(p, pub), actors)))
                resolved = None
                for p in actors:
                    resolved = safe_apply(game, p, ds[p], legal[p], rng)
                decisions.extend(ds.values())
                reasoning = {str(p): ds[p].private_reasoning for p in actors}
                if phase is Phase.VOTE:
                    events.append({
                        "t": len(events), "phase": "vote", "quest": resolved["quest"], "attempt": resolved["attempt"],
                        "leader": resolved["leader"], "team": resolved["team"], "votes": resolved["votes"],
                        "approved": resolved["approved"], "reasoning": reasoning,
                        "fallbacks": [p for p in actors if ds[p].fallback],
                    })
                    yes = [k for k, v in resolved["votes"].items() if v == "approve"]
                    out.sys(f"VOTE on {resolved['team']}: {'APPROVED' if resolved['approved'] else 'REJECTED'}"
                            f" (approve: {','.join(yes) or '-'})")
                    if verbose:
                        for p in actors:
                            print(f"   {out.name(p)} {ds[p].action['vote']}: {DIM}{ds[p].private_reasoning}{RESET}")
                else:
                    cards = game.quest_cards[-1]
                    events.append({
                        "t": len(events), "phase": "quest", "quest": resolved["quest"], "team": resolved["team"],
                        "fails": resolved["fails"], "succeeded": resolved["succeeded"],
                        "cards": {k: c["played"] for k, c in cards.items()},
                        "submitted": {k: c["submitted"] for k, c in cards.items()},
                        "reasoning": reasoning,
                    })
                    out.sys(f"QUEST {resolved['quest']} {resolved['team']}: "
                            f"{'SUCCESS' if resolved['succeeded'] else 'FAILED'} ({resolved['fails']} fail)  "
                            f"score {game.score()}")
                    if verbose:
                        for p in actors:
                            if not ds[p].private_reasoning.startswith("(automatic"):
                                print(f"   {out.name(p)} {ds[p].action['card']}: {DIM}{ds[p].private_reasoning}{RESET}")
                continue

            p = game.current_actor()
            legal = game.legal_actions(p)
            d = decide(p, pub)
            safe_apply(game, p, d, legal, rng)
            decisions.append(d)
            ev = {
                "t": len(events), "phase": phase.value, "quest": pub["quest"], "attempt": pub["attempt"],
                "actor": p, "private_reasoning": d.private_reasoning, "public_statement": d.public_statement,
                "fallback": d.fallback,
            }
            if phase is Phase.PROPOSE:
                ev["action"] = {"team": d.action["team"]}
                out.sys(f"--- Quest {pub['quest']} attempt {pub['attempt']}, leader P{p} ---")
                out.act(p, d, f"proposes {d.action['team']}")
            elif phase is Phase.DISCUSS:
                out.act(p, d)
            elif phase is Phase.ASSASSINATE:
                ev["action"] = {"target": d.action["target"]}
                out.sys("=== ASSASSINATION ===")
                out.act(p, d, f"names P{d.action['target']} as Merlin")
            events.append(ev)

    res = game.result()
    out.sys(f"RESULT: {res['winner'].upper()} wins ({res['reason']}). Merlin was P{res['merlin_actual']}.")

    tok_in = sum(d.input_tokens for d in decisions)
    tok_out = sum(d.output_tokens for d in decisions)
    costs = [cost_usd(d.model, d.input_tokens, d.output_tokens) for d in decisions if d.model]
    return {
        "game_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "models": cfg["models"],
            "seat_models": {str(p): m for p, m in seat_models.items()},
            "personalities": {str(p): s for p, s in enumerate(personalities)},
            "discussion_rounds": cfg["discussion_rounds"],
            "word_limit": cfg["word_limit"],
            "seed": seed,
            "fake": cfg["fake"],
            "provider": cfg.get("provider"),
        },
        "roles": game.ground_truth()["roles"],
        "events": events,
        "result": res,
        "stats": {
            "proposals": res["proposals"],
            "total_rejections": sum(not pr["approved"] for pr in game.proposal_history),
            "llm_calls": sum(d.calls for d in decisions),
            "input_tokens": tok_in,
            "output_tokens": tok_out,
            "tokens": tok_in + tok_out,
            "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
            "retries": sum(d.retries for d in decisions if not d.fallback),
            "parse_failures": sum(d.fallback for d in decisions),
            "duration_s": round(time.time() - t0, 1),
        },
    }


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=1, help="games to run concurrently")
    ap.add_argument("--provider", choices=["anthropic", "asi", "fake"],
                    default=os.environ.get("AVALON_PROVIDER", "anthropic"))
    ap.add_argument("--model", default=os.environ.get("AVALON_MODEL"),
                    help="model for every role (default depends on provider)")
    ap.add_argument("--role-model", action="append", default=[], metavar="ROLE=MODEL",
                    help=f"override one role, e.g. assassin=claude-sonnet-4-5. Roles: {[r.value for r in ROLES_5P]}")
    ap.add_argument("--discussion-rounds", type=int, default=1)
    ap.add_argument("--word-limit", type=int, default=40)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default="transcripts")
    ap.add_argument("--fake", action="store_true", help="shortcut for --provider fake (no API calls)")
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    if args.fake:
        args.provider = "fake"
    args.fake = args.provider == "fake"
    model = args.model or PROVIDER_DEFAULT_MODELS[args.provider]
    models = {r.value: model for r in ROLES_5P}
    for spec in args.role_model:
        role, _, model = spec.partition("=")
        if role not in models or not model:
            raise SystemExit(f"bad --role-model {spec!r}")
        models[role] = model
    cfg = {"models": models, "discussion_rounds": args.discussion_rounds,
           "word_limit": args.word_limit, "fake": args.fake, "provider": args.provider}
    base_seed = args.seed if args.seed is not None else random.randrange(10**6)
    client = make_client(args.provider, base_seed)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    verbose = not args.quiet and args.games == 1

    def one(i: int) -> dict:
        tr = play_game(cfg, client, base_seed + i, verbose)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = "fake_" if args.fake else ""
        path = outdir / f"{prefix}{stamp}_{tr['game_id'][:8]}.json"
        path.write_text(json.dumps(tr, indent=2))
        r, s = tr["result"], tr["stats"]
        print(f"[game {i + 1}/{args.games}] {r['winner']:>4} via {r['reason']:<24} "
              f"proposals={s['proposals']} calls={s['llm_calls']} cost=${s['cost_usd']} "
              f"fallbacks={s['parse_failures']} {s['duration_s']}s -> {path}")
        return tr

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        results = list(pool.map(one, range(args.games)))

    winners = Counter(t["result"]["winner"] for t in results)
    assassinations = [t["result"] for t in results if t["result"]["merlin_guess"] is not None]
    survived = sum(r["merlin_survived"] for r in assassinations)
    costs = [t["stats"]["cost_usd"] for t in results]
    print(f"\n{len(results)} games: good {winners['good']}, evil {winners['evil']}. "
          f"Merlin survived {survived} of {len(assassinations)} assassinations. "
          f"Total cost: {'$%.3f' % sum(costs) if None not in costs else 'unknown (model not in price table)'}")
    if hasattr(client, "rescues"):
        fb = sum(t["stats"]["parse_failures"] for t in results)
        calls = sum(t["stats"]["llm_calls"] for t in results)
        print(f"Format health: {fb} fallbacks / {calls} calls; "
              f"{client.rescues} replies lacked a tool call, {client.rescued} fixed by JSON re-ask.")


if __name__ == "__main__":
    main()
