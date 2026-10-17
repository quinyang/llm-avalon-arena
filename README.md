# LLM Avalon Arena

Spec: `PLAN.md` (copy of the build plan).

## Status

| Hours | Block | State |
|---|---|---|
| 0–0.5 | API smoke test | `smoke_test.py` written; needs your key |
| 0.5–2.5 | Rules engine + random bots | **Done.** 11 tests pass, 1000 random games, 0 violations |
| 2.5–4 | `agent.py` + `prompts.py`, one LLM game | Next |

## Run

```bash
cd avalon
python3 test_game.py               # rules tests (no deps)
python3 sim.py --games 1000        # random-bot batches: random / always-reject / always-approve

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python smoke_test.py               # one schema-valid JSON vote from the model
```

## Random-bot baseline (seed 0, 1000 games, approve p=0.5)

Evil 62.8% / Good 37.2%. End reasons: 3 quests failed 39.2%, Good survived assassination 37.2%,
Merlin assassinated 12.6%, five rejections 11.0%. Sanity checks: assassin hit rate = 12.6/49.8 ≈ 25%
(random pick of 4), and five-rejection rate ≈ 4 quests × 1/32 ≈ 12%. Both match theory.
Useful as the "no reasoning" baseline for the Devpost numbers.

## Engine API (`game.py`)

```python
g = AvalonGame(seed=1, discussion_rounds=1)
g.current_phase()        # Phase.PROPOSE | DISCUSS | VOTE | QUEST | ASSASSINATE | DONE
g.pending_actors()       # who must act now (all un-voted players during VOTE)
g.legal_actions(p)       # {"type": "propose", "team_size": 2, ...}
g.apply(p, {"team": [0, 3], "statement": "..."})   # raises IllegalAction
g.public_state()         # safe for every prompt
g.private_state(p)       # safe only for player p's prompt
g.ground_truth()         # roles + individual cards: transcripts only, NEVER prompts
g.result()               # winner, reason, merlin_guess, merlin_actual, merlin_survived
```

Action shapes: `{"team": [...], "statement": str}`, `{"statement": str}`, `{"vote": "approve"|"reject"}`,
`{"card": "success"|"fail"}`, `{"target": int}`.

Deviations from the plan's interface: `apply(player, action)` takes the player explicitly (needed for
simultaneous phases), `pending_actors()` added, the assassination field is `target`, and the engine
records Good fail attempts as `submitted: "fail", played: "success"` in ground truth (free metric).
