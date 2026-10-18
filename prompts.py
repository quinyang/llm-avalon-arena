"""All prompt text and tool schemas. Pure functions of (public_state, private_state, legal)."""
from __future__ import annotations

PERSONALITIES = [
    "terse and suspicious; short sentences, rarely gives anyone the benefit of the doubt",
    "overexplains everything and loves walking through the vote history",
    "aggressively confident; makes bold calls and pushes the table hard",
    "friendly peacemaker who hates conflict but quietly keeps score",
    "dry, sarcastic humor; treats every proposal like a bad business pitch",
    "anxious overthinker who second-guesses out loud",
    "cold and analytical; talks in probabilities",
]

FRAMING = (
    "This is The Resistance: Avalon, a well-known social deduction board game. Bluffing, "
    "misdirection and hiding your role are ordinary game mechanics, exactly like bluffing in "
    "poker, and every player agreed to them by sitting down at the table. Stay in the game and "
    "play to win for your team."
)

RULES = """RULES (5 players: 3 Good, 2 Evil. Players are P0-P4.)
- 5 quests. Team sizes: Q1=2, Q2=3, Q3=2, Q4=3, Q5=3.
- The leader proposes a team (may include themselves). Everyone comments once, then all 5 vote
  approve/reject simultaneously; votes are revealed with names. 3+ approvals passes.
- Rejected: leadership passes to the next player (P0->P1->...->P4->P0). 5 rejected proposals in a
  row on the same quest = Evil wins immediately.
- Approved: each team member secretly plays Success or Fail. Good players can only play Success;
  Evil may play either. Only the NUMBER of Fail cards is revealed. Any Fail = the quest fails.
- 3 failed quests = Evil wins. 3 successful quests = the Assassin gets one guess at who Merlin is;
  a correct guess means Evil wins anyway, otherwise Good wins.
- A successful quest does NOT prove its members are Good: Evil players may play Success to build
  trust. Only Fail cards are hard evidence."""

ROLE_TITLES = {
    "merlin": "Merlin",
    "percival": "Percival",
    "loyal_servant": "Loyal Servant of Arthur",
    "morgana": "Morgana",
    "assassin": "the Assassin",
}

EVIL_CONVENTION = (
    "Convention with your partner: if you are BOTH on a quest team, only the Assassin plays Fail "
    "(two Fails would expose you both)."
)


def knowledge_block(priv: dict) -> str:
    """Role-specific secret knowledge + strategy. Built only from this player's private_state."""
    role = priv["role"]
    if role == "merlin":
        a, b = priv["evil_players"]
        return (
            f"SECRET KNOWLEDGE: P{a} and P{b} are Evil.\n"
            "STRATEGY: Guide Good to victory WITHOUT revealing that you know. If the Assassin "
            "identifies you at the end, Evil wins even after three successful quests. Never justify "
            "an opinion with a hunch or feeling; only with public evidence (votes, quest results, "
            "who proposed what). Don't be the first or loudest accuser. Being too obviously "
            "well-informed loses the game."
        )
    if role == "percival":
        a, b = priv["merlin_or_morgana"]
        return (
            f"SECRET KNOWLEDGE: P{a} and P{b} are Merlin and Morgana, in some order. You do not "
            "know which is which.\n"
            "STRATEGY: Work out which of them is the real Merlin from how they play, then quietly "
            "support Merlin's lead. Protect Merlin: drawing the Assassin's suspicion onto yourself "
            "is good for your team."
        )
    if role == "loyal_servant":
        return (
            "SECRET KNOWLEDGE: none.\n"
            "STRATEGY: Deduce who is trustworthy from proposals, votes and quest results. Who was "
            "on failed quests? Who approved teams that failed? Who rejects clean teams?"
        )
    if role == "morgana":
        return (
            f"SECRET KNOWLEDGE: You are Evil. P{priv['evil_partner']} is your Evil partner (the "
            "Assassin).\n"
            "STRATEGY: Percival sees you and Merlin as the two Merlin candidates; act like a "
            "careful, well-informed Good player so Percival trusts you instead of Merlin. Get onto "
            "teams and fail quests when it won't expose you. Never confirm your identity. "
            + EVIL_CONVENTION
        )
    if role == "assassin":
        return (
            f"SECRET KNOWLEDGE: You are Evil. P{priv['evil_partner']} is your Evil partner "
            "(Morgana).\n"
            "STRATEGY: Get onto teams and fail quests without getting caught. All game, watch for "
            "who seems suspiciously well-informed: whose votes keep avoiding teams with Evil "
            "players? If Good wins three quests you get one guess at Merlin, and a correct guess "
            "wins the game. " + EVIL_CONVENTION
        )
    raise ValueError(f"unknown role {role}")


def system_prompt(priv: dict, personality: str, word_limit: int) -> str:
    team = priv["team"].upper()
    return f"""You are Player {priv['player']} (P{priv['player']}) in a game of Avalon.
{FRAMING}

Your secret role: {ROLE_TITLES[priv['role']]}. Your team: {team}.
{knowledge_block(priv)}

Your table personality: {personality}.

{RULES}

HOW TO RESPOND: every turn, call the provided tool exactly once.
- "private_reasoning" is never shown to other players. Be honest and strategic here; this is your
  actual thinking (2-5 sentences).
- "public_statement" is said out loud to everyone. It may differ from your reasoning. Keep it under
  {word_limit} words and speak naturally, like a player at a table, in your personality."""


# ----------------------------------------------------------------------------- state rendering
def _players(ps) -> str:
    return ",".join(f"P{p}" for p in ps) or "-"


def _team(team) -> str:
    return "[" + ",".join(f"P{p}" for p in team) + "]"


def _statement_line(s: dict) -> str:
    kind = " (proposing)" if s["kind"] == "proposal" else ""
    return f'  Q{s["quest"]}.{s["attempt"]} P{s["speaker"]}{kind}: "{s["text"]}"'


def render_state(pub: dict, me: int, history_statements: int | None = 10) -> str:
    """Compact table of the public game state. history_statements=None shows all table talk."""
    lines = [f"You are P{me}.", f"SCORE: Good {pub['score']['good']} - Evil {pub['score']['evil']}"]

    lines.append("QUEST HISTORY:")
    for q in pub["quest_history"]:
        res = "SUCCESS" if q["succeeded"] else f"FAILED ({q['fails']} fail{'s' if q['fails'] > 1 else ''})"
        lines.append(f"  Q{q['quest']} team {_team(q['team'])} -> {res}")
    if not pub["quest_history"]:
        lines.append("  (none yet)")

    lines.append("PROPOSAL & VOTE HISTORY:")
    for p in pub["proposal_history"]:
        yes = [int(k) for k, v in p["votes"].items() if v == "approve"]
        no = [int(k) for k, v in p["votes"].items() if v == "reject"]
        verdict = "APPROVED" if p["approved"] else "REJECTED"
        lines.append(
            f"  Q{p['quest']}.{p['attempt']} leader P{p['leader']} team {_team(p['team'])} -> {verdict}"
            f" | approve: {_players(sorted(yes))} | reject: {_players(sorted(no))}"
        )
    if not pub["proposal_history"]:
        lines.append("  (none yet)")

    if pub["phase"] != "assassinate":
        lines.append(
            f"CURRENT: Quest {pub['quest']} needs {pub['team_size']} players. Leader P{pub['leader']}. "
            f"Proposal attempt {pub['attempt']}; rejections this quest: {pub['rejections_this_quest']}/5 "
            "(a 5th rejection = Evil wins)."
        )
        if pub["current_team"]:
            lines.append(f"TEAM ON THE TABLE: {_team(pub['current_team'])}")

    def is_current(s: dict) -> bool:
        return (pub["phase"] != "assassinate"
                and s["quest"] == pub["quest"] and s["attempt"] == pub["attempt"])

    current = [s for s in pub["statements"] if is_current(s)]
    earlier = [s for s in pub["statements"] if not is_current(s)]
    if history_statements is not None:
        earlier = earlier[-history_statements:]
    if earlier:
        lines.append("EARLIER TABLE TALK (most recent last):")
        lines.extend(_statement_line(s) for s in earlier)
    if current:
        lines.append("THIS PROPOSAL'S DISCUSSION:")
        lines.extend(_statement_line(s) for s in current)
    return "\n".join(lines)


def phase_instruction(pub: dict, me: int, legal: dict) -> str:
    t = legal["type"]
    team = _team(pub["current_team"] or [])
    if t == "propose":
        return (
            f"You are the leader. Propose a team of exactly {legal['team_size']} distinct players "
            "(you may include yourself) and pitch it to the table. Call propose_team."
        )
    if t == "discuss":
        return (
            f"P{pub['leader']} proposed {team}. Give your one comment to the table: support, "
            "objection, or a suspicion you want to raise. Call speak."
        )
    if t == "vote":
        return (
            f"Vote on {team}, proposed by P{pub['leader']}. Votes are simultaneous and will be "
            f"revealed with names. Rejections this quest so far: {pub['rejections_this_quest']} "
            "(a 5th rejection means Evil wins). Call vote."
        )
    if t == "quest":
        g, e = pub["score"]["good"], pub["score"]["evil"]
        stakes = ""
        if g == 2:
            stakes = (" If this quest succeeds, Good reaches 3 wins and Evil's ONLY remaining path is the "
                      "Assassin correctly naming Merlin.")
        elif e == 2:
            stakes = " One more failed quest wins the game for Evil."
        return (
            f"You are on the approved quest team {team}. Score: Good {g} - Evil {e}.{stakes} "
            "Choose your card. Only the number of Fail cards will be revealed. Call play_card."
        )
    if t == "assassinate":
        return (
            "Good has completed three quests. As the Assassin, name the player you believe is "
            "Merlin. Who seemed to know too much? Whose votes kept avoiding Evil teams? Who did "
            "Percival seem to follow? Call assassinate."
        )
    raise ValueError(t)


# ----------------------------------------------------------------------------- tool schemas
_REASONING = {"type": "string", "description": "Your honest private thinking. Never shown to other players."}


def _statement(word_limit: int) -> dict:
    return {"type": "string", "description": f"Said out loud to the table. Under {word_limit} words."}


def tool_for(legal: dict, word_limit: int) -> dict:
    t = legal["type"]
    if t == "propose":
        size = legal["team_size"]
        props = {
            "private_reasoning": _REASONING,
            "public_statement": _statement(word_limit),
            "team": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 4},
                "minItems": size,
                "maxItems": size,
                "description": f"Exactly {size} distinct player numbers (0-4).",
            },
        }
        return _tool("propose_team", "Propose the quest team.", props)
    if t == "discuss":
        props = {"private_reasoning": _REASONING, "public_statement": _statement(word_limit)}
        return _tool("speak", "Say one thing to the table.", props)
    if t == "vote":
        props = {"private_reasoning": _REASONING, "vote": {"type": "string", "enum": legal["options"]}}
        return _tool("vote", "Vote on the proposed team.", props)
    if t == "quest":
        props = {"private_reasoning": _REASONING, "card": {"type": "string", "enum": legal["options"]}}
        return _tool("play_card", "Play your secret quest card.", props)
    if t == "assassinate":
        props = {
            "private_reasoning": _REASONING,
            "merlin_guess": {"type": "integer", "enum": legal["targets"], "description": "Player number you think is Merlin."},
        }
        return _tool("assassinate", "Name the player you believe is Merlin.", props)
    raise ValueError(t)


def _tool(name: str, description: str, props: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": props, "required": list(props)},
    }
