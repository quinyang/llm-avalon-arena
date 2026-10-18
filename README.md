# LLM Avalon Arena

Five LLM agents play *The Resistance: Avalon*. Each turn every agent produces private reasoning and a
public statement, stored separately. Spec: `PLAN.md`.

## Status

| Hours | Block | State |
|---|---|---|
| 0–0.5 | API smoke test | Done |
| 0.5–2.5 | Rules engine + random bots | Done. 11 tests, 1000 random games, 0 violations |
| 2.5–5 | Agents, prompts, transcripts | Built and tested offline (fake client). **Needs first real game** |
| 5–7 | `viewer.html` replay | Next |

## Run

```bash
python test_game.py                 # rules tests (no deps)
python test_agents.py               # agent layer vs a fake model (no API spend)
python sim.py --games 1000          # random-bot baselines

python run.py --fake                # one offline game printed live: checks plumbing
python run.py                       # one REAL game printed live (~3-5 min, ~$0.20 on Haiku 4.5)
python run.py --games 20 --parallel 4 --quiet
python run.py --role-model assassin=claude-sonnet-4-5 --games 10 --parallel 4
```

Transcripts go to `transcripts/` (one JSON per game; `fake_*` files are gitignored).
`AVALON_MODEL` env var or `--model` sets the default model for every role.

## Files

| File | What |
|---|---|
| `roles.py` | Roles, teams, private knowledge |
| `game.py` | Pure rules state machine. No LLM code |
| `prompts.py` | System prompts, state table rendering, tool schemas |
| `agent.py` | Forced tool call → validate → retry once → random legal fallback |
| `run.py` | Game loop, parallel votes/quest cards, live colored output, transcripts |
| `fake_client.py` | Offline stand-in for the Anthropic client (random, sometimes invalid, tool calls) |
| `sim.py` | Random-bot driver + invariant checks |

## Design notes

- Agents only receive `public_state()` + their own `private_state()` + legal actions; `test_agents.py`
  checks that no player's secret knowledge appears in another player's prompt.
- Votes and quest cards are decided in parallel from one snapshot, then applied (truly simultaneous).
- Good players' quest cards are automatic (no API call); Evil players choose.
- Leader speaks when proposing and again last in discussion. The Assassin sees all table talk;
  other prompts see the last 10 earlier statements plus the current discussion.
- Evil convention in prompts: if both Evil are on a team, only the Assassin fails.

## Random-bot baseline (seed 0, 1000 games, approve p=0.5)

Evil 62.8% / Good 37.2%. Assassin hit rate ≈ 25% (random pick of 4), so **Merlin survival of 75% is the
no-reasoning baseline** to compare LLM results against.

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
