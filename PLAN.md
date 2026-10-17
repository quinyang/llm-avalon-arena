# LLM Avalon Arena — Complete Build Plan (Handoff Document)

**Read this first if you're a fresh agent picking up this project.** This document is self-contained. It specifies a complete project: five LLM agents play *The Resistance: Avalon* against each other, with a spectator view showing each agent's private reasoning next to what it says out loud. Everything you need — game rules, architecture, JSON contracts, prompt design, build order, and failure modes — is below.

**Context:** solo developer, hackathon (UCLA AI Hackathon 2026, Oct 17–18), starting late with roughly 16 working hours. Goal is a working, fun, demoable project — not a prize. Prioritize shipping something complete over shipping something sophisticated.

---

## 1. What we're building

Five LLM agents play a full game of Avalon: secret roles, team proposals, public voting, secret quest outcomes, and a final assassination attempt on Merlin. Every agent produces **private reasoning** and a **public statement** at each step, stored separately. A single-file HTML viewer replays the game with private thoughts on the left and public speech on the right, so you can literally watch an agent decide to lie and then lie.

Then we run many games across different model configurations and report a leaderboard.

### Why this isn't just another Avalon demo

LLM Avalon has been done. These four things are what make it ours, and all four are cheap:

1. **The two-column replay** — private reasoning next to public speech, synchronized. Most projects only show the chat log.
2. **Merlin survival rate as the headline metric** — how often does the Assassin correctly identify Merlin? This is the single most interesting number in Avalon and it measures whether Merlin can lead subtly, which is a real reasoning skill.
3. **The model-mix leaderboard** — does a smarter Merlin survive longer? Does a smarter Assassin find Merlin more often? Swap models per *role*, not just per team.
4. **A deception index** — a cheap judge-model pass that scores how far each public statement diverges from the agent's own private reasoning. Plot it per role. Evil roles should score high; Merlin should score moderately (concealing without lying); loyal servants near zero. If that pattern shows up in the data, it's a genuinely interesting result.

---

## 2. Game rules (authoritative spec — implement exactly this)

Use the **5-player configuration**. It's the smallest that supports the interesting roles, it's cheapest to run, and it fits on one screen.

### Roles (5 players: 3 Good, 2 Evil)

| Role | Team | Private knowledge at game start |
|---|---|---|
| Merlin | Good | Knows the identities of both Evil players |
| Percival | Good | Knows that Merlin and Morgana are two specific players, but not which is which |
| Loyal Servant of Arthur | Good | Nothing |
| Morgana | Evil | Knows the other Evil player; appears as Merlin to Percival |
| Assassin | Evil | Knows the other Evil player |

Merlin's win condition has a sting: if Good wins three quests, the Assassin gets one guess at who Merlin is. A correct guess means Evil wins anyway. So Merlin must steer the team toward good outcomes **without revealing that they know too much**.

### Quest sizes (5-player game)

Quest 1: 2 players · Quest 2: 3 · Quest 3: 2 · Quest 4: 3 · Quest 5: 3

In a 5-player game, **every quest fails if it contains one or more fail cards.** (The "two fails required" rule applies only to quest 4 in games of 7+ players — do not implement it.)

### Turn structure

Repeat until three quests have succeeded or three have failed:

1. **Leader proposes.** Leadership rotates in fixed player order. The leader selects a team of the required size (may include themselves).
2. **Discussion.** Round-robin, each agent speaks once, in player order starting from the leader. Cap statements at **40 words**. (One discussion round is the default; make the number of rounds a config value.)
3. **Vote.** All five agents vote Approve or Reject **simultaneously** — collect all votes before revealing any. Then reveal all votes publicly, with names attached. Strict majority (3 of 5) approves.
4. **On rejection:** leadership passes to the next player and we return to step 1. **Five consecutive rejections on the same quest means Evil wins immediately.** Reset the rejection counter whenever a team is approved.
5. **On approval:** each team member secretly submits Success or Fail. **The engine must enforce that Good players can only submit Success** — never trust the model for this. Evil players may submit either.
6. **Reveal** only the *number* of fail cards, never who played them. One or more fails means the quest failed.

### Endgame

- Three failed quests → **Evil wins immediately**, no assassination.
- Three successful quests → **assassination phase**: the Assassin sees the full public game history and names one player as Merlin. Correct → Evil wins. Incorrect → Good wins.

Vote history and quest history are **public and permanent**. Every agent can always see who proposed what, who voted how, and how many fails each quest had.

---

## 3. Architecture

```
avalon/
  roles.py         # role enums, team assignment, private knowledge construction
  game.py          # state machine + rules enforcement. NO LLM calls in this file.
  agent.py         # LLM wrapper: prompt assembly, JSON parsing, retry, fallback
  prompts.py       # system prompts per role, phase-specific user prompts
  run.py           # CLI entry: run N games with a config, write transcripts
  analyze.py       # aggregate transcripts -> stats + one matplotlib chart
  viewer.html      # single-file spectator replay, fetches transcript JSON
  transcripts/     # one JSON per game
```

**The most important architectural rule: `game.py` contains no LLM calls and no prompt text.** It exposes a pure state machine. That separation is what lets you test the entire rules engine with random bots in minutes instead of burning API spend and wall-clock time debugging rules through a model.

### Core interfaces

```python
# game.py
class AvalonGame:
    def legal_actions(self) -> dict      # what the current actor may do
    def current_phase(self) -> Phase     # PROPOSE | DISCUSS | VOTE | QUEST | ASSASSINATE | DONE
    def current_actor(self) -> int | None # None during simultaneous phases
    def apply(self, action: dict) -> None # validates, raises on illegal action
    def public_state(self) -> dict        # everything all players can see
    def private_state(self, player: int) -> dict  # that player's secret knowledge
    def is_over(self) -> bool
    def result(self) -> dict              # winner, reason, merlin_guess, etc.
```

Agents **only ever** receive `public_state(...)` plus their own `private_state(...)`. Enforce this at the function boundary; it is the one bug class that silently invalidates the entire project.

### Agent JSON contracts

Request exactly one of these per call. Every response carries `private_reasoning`.

```jsonc
// PROPOSE
{ "private_reasoning": "...", "public_statement": "...", "team": [0, 3] }

// DISCUSS
{ "private_reasoning": "...", "public_statement": "..." }

// VOTE
{ "private_reasoning": "...", "vote": "approve" }   // or "reject"

// QUEST  (engine overrides to "success" for Good players)
{ "private_reasoning": "...", "card": "fail" }      // or "success"

// ASSASSINATE
{ "private_reasoning": "...", "merlin_guess": 2 }
```

Parsing: attempt `json.loads`. On failure, retry **once** with the error message appended. On second failure, log a `parse_failure` event and take a **random legal action**. Do not write a tolerant parser; do not let a malformed response crash a 40-minute run.

### Transcript format

One JSON file per game. This is both the demo data and the analysis data, so get it right early.

```jsonc
{
  "game_id": "uuid",
  "config": { "models": {"merlin": "claude-haiku-4-5", "...": "..."},
              "discussion_rounds": 1, "word_limit": 40 },
  "roles": { "0": "merlin", "1": "percival", "2": "loyal_servant",
             "3": "morgana", "4": "assassin" },
  "events": [
    { "t": 0, "phase": "propose", "actor": 0,
      "private_reasoning": "...", "public_statement": "...",
      "action": {"team": [0,3]} },
    { "t": 1, "phase": "vote", "votes": {"0":"approve","1":"reject"},
      "reasoning": {"0":"...","1":"..."} },
    { "t": 2, "phase": "quest", "team": [0,3], "fails": 1,
      "cards": {"0":"success","3":"fail"} }
  ],
  "result": { "winner": "good", "reason": "three_quests_succeeded",
              "merlin_guess": 1, "merlin_actual": 0, "merlin_survived": true },
  "stats": { "rounds": 4, "total_rejections": 3,
             "tokens": 48200, "cost_usd": 0.07, "parse_failures": 0 }
}
```

Note the transcript stores **ground truth** — real roles, actual quest cards, every private thought. The viewer can reveal all of it after the game. That's the whole appeal: you get the omniscient view the players never had.

---

## 4. Prompt design

### System prompt skeleton (per agent)

```
You are Player {i} in a game of Avalon, a social deduction game.
Your secret role: {role}. Your team: {GOOD|EVIL}.
{role_specific_knowledge_block}
{role_specific_strategy_block}

RULES: {condensed rules — quest sizes, voting, 5-reject rule, assassination}

You must respond with ONLY valid JSON matching this schema: {schema}

"private_reasoning" is never shown to other players. Be honest and
strategic here — this is your actual thinking.
"public_statement" is shown to everyone. It may differ from your
reasoning. Keep it under {word_limit} words and speak naturally,
like a player at a table.
```

### Role-specific knowledge blocks

- **Merlin:** "Players {x} and {y} are Evil. You must guide Good to victory without revealing that you know. If the Assassin identifies you at the end, Evil wins even if Good succeeded on three quests. Being too obviously well-informed loses the game."
- **Percival:** "Players {a} and {b} are Merlin and Morgana, in some order. You do not know which is which."
- **Loyal Servant:** "You have no special information. You must deduce who is trustworthy from proposals, votes, and quest outcomes."
- **Morgana:** "You are Evil. Player {z} is your Evil partner. Percival sees you as a possible Merlin — use that. Good players must never confirm who you are."
- **Assassin:** "You are Evil. Player {z} is your Evil partner. If Good succeeds on three quests, you get one guess at Merlin's identity — a correct guess wins the game for Evil. Track who seems suspiciously well-informed."

### Critical: inject structured state, don't rely on transcript reading

Do **not** hand the model a raw chat log and hope it tracks the history. Every prompt should include a rendered, compact state summary:

```
QUEST HISTORY:  Q1 team [0,3] -> FAILED (1 fail)   Q2 team [1,2,4] -> SUCCESS
VOTE HISTORY:   Q1 proposal [0,3] by P0: approve P0,P2,P3 / reject P1,P4
CURRENT:        Quest 3, needs 2 players, leader P2, rejections this quest: 1
SCORE:          Good 1 — Evil 1
```

Structured state injection is the single highest-leverage thing in this build. Agents reason far better about a table than about forty turns of prose, and it caps your context growth.

---

## 5. Build order

Submit a working ugly version the moment you have one, then keep improving. Most solo hackers lose by polishing something unsubmitted.

| Hours | Task | Done when |
|---|---|---|
| 0–0.5 | Env setup, API key, one successful model call that returns parseable JSON | A smoke-test script prints valid JSON |
| 0.5–2.5 | **`game.py` + `roles.py` with random bots only.** No LLM. Run 1000 random games. | 1000 games complete with zero illegal states; Good/Evil winrate is plausible |
| 2.5–4 | `agent.py` + `prompts.py`. One full LLM game start to finish. | One game reaches a winner without crashing |
| 4–5 | Private reasoning capture, full transcript JSON written to disk | A transcript file contains every field in §3 |
| 5–7 | `viewer.html` — two-column replay with a step slider | You can replay a saved game in the browser |
| 7–8 | Assassination phase polish, run a batch of 20 games | 20 transcripts on disk |
| 8–9.5 | `analyze.py` — winrates, Merlin survival rate, one chart | A PNG chart exists |
| 9.5–11 | **Record the demo, write the Devpost page, submit.** | Submitted |
| 11+ | Stretch goals (§7), or sleep | — |

**The hour 0.5–2.5 block is non-negotiable.** Validating the rules engine with random bots is the difference between a working project and a 4am rules bug you can't find because every test run costs three minutes and forty cents.

---

## 6. Failure modes you will actually hit

**Evil agents refuse to deceive.** The model breaks character and announces it's being asked to lie. Fix: state plainly in the system prompt that this is a well-known social deduction board game, that bluffing is an ordinary game mechanic like bluffing in poker, and that all players consented by sitting down. Usually sufficient. If a specific model keeps refusing, use a different model for Evil roles and mention the asymmetry in your writeup — it's an interesting finding, not a defect.

**Merlin is instantly obvious.** LLM Merlin says "I have a strong feeling about Player 3" in round one and the Assassin snaps them up. This is the most common failure and also your most interesting metric — don't fully suppress it, measure it. Mitigate with explicit instruction about subtlety, then report Merlin survival rate as a headline number.

**Agents lose track of vote history.** Fix: structured state injection (§4). Do not skip this.

**Good players submit fail cards.** Fix: the engine overrides. Never trust the model with a rule it can break.

**Infinite rejection loops.** Agents reject everything and the game stalls. The 5-rejection rule terminates it, but verify this path works — test it explicitly with random bots.

**JSON parse failures mid-batch.** Fix: retry once, then random legal action, and log it. A 20-game batch must never die on game 14.

**Context balloons and cost climbs.** Fix: structured state summary instead of full transcript, one discussion round by default, and Haiku for most roles. Track `cost_usd` per game in the transcript from the start so you notice before it matters.

**All five agents sound identical.** Fix: give each a short personality line in its system prompt ("terse and suspicious", "overexplains", "aggressively confident"). Costs nothing, makes transcripts dramatically more readable, and makes the demo funnier.

---

## 7. Stretch goals, in priority order

1. **Model-mix leaderboard** — run batches varying which model plays which role. The pitch-ready result: "a Sonnet Assassin finds Merlin X% of the time; a Haiku Assassin, Y%."
2. **Deception index** — a judge-model pass scoring divergence between private reasoning and public statement, averaged per role. Plot it. Cheap and genuinely novel.
3. **Human seat** — let a person take one player slot via the terminal. Unbeatable at a demo table, because judges can play.
4. **Commentator agent** — a sixth agent with omniscient view narrating the game like a sports broadcast. Pure fun, about 30 minutes of work, great for the demo video.
5. **Larger games** — 7-player config with Mordred and Oberon. Only after everything else works; it means implementing the two-fails rule.

---

## 8. Scope cuts, in the order to make them

1. Deception index and model-mix leaderboard — pure bonus.
2. Statistics and chart — nice, not required.
3. `viewer.html` — fall back to colored terminal output, which is a perfectly acceptable demo.
4. Discussion phase — go straight from proposal to vote. The game still works and still produces deduction.
5. Percival and Morgana — reduce to Merlin, two Loyal Servants, Assassin, and one Minion. Simpler roles, same core game.

Cut from the bottom of your ambitions, never from the top of your working build.

---

## 9. Demo plan

**Do not do a live run on stage.** Games take minutes and are non-deterministic. Save transcripts and demo the replay viewer — you control the pacing and you pick a good game.

Save a **library of transcripts** and hand-pick two: one where Merlin steers Good to victory and survives the assassination, and one where Merlin gets caught. The contrast is the story.

The 90-second script:

> "Five AI agents are playing Avalon. Two are secretly evil. One is Merlin — he knows who the evil players are, but if he's too obvious about it, the Assassin kills him at the end and evil wins anyway. On the left you're seeing what each agent privately thinks. On the right, what it actually says. Watch Morgana here — she privately notes she needs to look trustworthy for one more round, then publicly volunteers to be excluded from the next quest. And watch Merlin trying to steer the vote without explaining how he knows anything."

Then the number: Merlin survival rate across N games, and one chart.

**Record a backup video of the working demo.** Hackathon Wi-Fi fails at exactly the wrong moment.

---

## 10. Starting instruction for the next session

> Build the project specified in `avalon-agents-build-plan.md`. Start with §5 hour-block 0.5–2.5: implement `roles.py` and `game.py` as a pure state machine with no LLM calls, following the rules in §2 exactly, plus a random-bot driver. Run 1000 random games and report the Good/Evil winrate and any illegal states before writing any agent or prompt code.

Use `claude-haiku-4-5` for agents by default — fast, cheap, and slightly looser reasoning actually makes for more entertaining play. Keep a `models` dict in the config so any role can be upgraded to Sonnet for the leaderboard runs.

---

*Game credit for the Devpost page: Avalon is a social deduction game designed by Don Eskridge, published by Indie Boards & Cards. This is a non-commercial hackathon project using the game's public rules.*
