"""Avalon rules engine: a pure state machine. NO LLM calls and NO prompt text in this file.

Agents must only ever see `public_state()` plus their own `private_state(player)`.
Ground truth (roles, individual quest cards) is exposed only via `ground_truth()`,
which is for transcripts/analysis, never for prompts.
"""
from __future__ import annotations

import copy
import random
from enum import Enum

from roles import (
    QUEST_SIZES_5P,
    ROLES_5P,
    Role,
    Team,
    assign_roles,
    private_knowledge,
    team_of,
)

WINS_NEEDED = 3
MAX_REJECTIONS = 5
VOTES = ("approve", "reject")
CARDS = ("success", "fail")


class Phase(str, Enum):
    PROPOSE = "propose"
    DISCUSS = "discuss"
    VOTE = "vote"
    QUEST = "quest"
    ASSASSINATE = "assassinate"
    DONE = "done"


class IllegalAction(ValueError):
    pass


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


class AvalonGame:
    def __init__(
        self,
        roles: list[Role] | None = None,
        seed: int | None = None,
        discussion_rounds: int = 1,
        first_leader: int = 0,
    ):
        rng = random.Random(seed)
        self.roles: list[Role] = list(roles) if roles is not None else assign_roles(rng)
        if sorted(r.value for r in self.roles) != sorted(r.value for r in ROLES_5P):
            raise ValueError(f"5-player game needs exactly roles {[r.value for r in ROLES_5P]}")
        self.n = len(self.roles)
        self.quest_sizes = QUEST_SIZES_5P
        self.discussion_rounds = discussion_rounds

        self.phase = Phase.PROPOSE
        self.leader = first_leader % self.n
        self.quest_index = 0          # 0-based internally, 1-based in public state
        self.attempt = 1              # proposal attempt number within the current quest
        self.rejections = 0           # consecutive rejections on the current quest
        self.current_team: list[int] | None = None
        self.speaking_order: list[int] = []
        self._votes: dict[int, str] = {}                 # hidden until all are in
        self._cards: dict[int, dict] = {}                # hidden forever (only fail count is public)

        self.quest_history: list[dict] = []              # public
        self.proposal_history: list[dict] = []           # public
        self.statements: list[dict] = []                 # public
        self.quest_cards: list[dict] = []                # ground truth
        self.assassin_target: int | None = None
        self._result: dict | None = None

    # ------------------------------------------------------------------ queries
    def current_phase(self) -> Phase:
        return self.phase

    def current_actor(self) -> int | None:
        """Single actor for sequential phases; None for simultaneous phases (VOTE, QUEST) and DONE."""
        if self.phase is Phase.PROPOSE:
            return self.leader
        if self.phase is Phase.DISCUSS:
            return self.speaking_order[0]
        if self.phase is Phase.ASSASSINATE:
            return self.roles.index(Role.ASSASSIN)
        return None

    def pending_actors(self) -> list[int]:
        """Everyone who still has to act in the current phase."""
        if self.phase in (Phase.PROPOSE, Phase.DISCUSS, Phase.ASSASSINATE):
            return [self.current_actor()]
        if self.phase is Phase.VOTE:
            return [p for p in range(self.n) if p not in self._votes]
        if self.phase is Phase.QUEST:
            return [p for p in self.current_team if p not in self._cards]
        return []

    def legal_actions(self, player: int) -> dict:
        """What `player` may do right now. Empty dict = nothing."""
        if player not in self.pending_actors():
            return {}
        if self.phase is Phase.PROPOSE:
            return {"type": "propose", "team_size": self.quest_sizes[self.quest_index],
                    "candidates": list(range(self.n))}
        if self.phase is Phase.DISCUSS:
            return {"type": "discuss"}
        if self.phase is Phase.VOTE:
            return {"type": "vote", "options": list(VOTES)}
        if self.phase is Phase.QUEST:
            # Good players can only play success. Engine enforces this in apply() too.
            options = list(CARDS) if team_of(self.roles[player]) is Team.EVIL else ["success"]
            return {"type": "quest", "options": options}
        if self.phase is Phase.ASSASSINATE:
            return {"type": "assassinate", "targets": [p for p in range(self.n) if p != player]}
        return {}

    def score(self) -> dict:
        good = sum(q["succeeded"] for q in self.quest_history)
        return {"good": good, "evil": len(self.quest_history) - good}

    def public_state(self) -> dict:
        """Everything every player can see. Deep-copied so callers can't mutate the engine."""
        current_statements = [
            s for s in self.statements
            if s["quest"] == self.quest_index + 1 and s["attempt"] == self.attempt
        ]
        return copy.deepcopy({
            "n_players": self.n,
            "quest_sizes": list(self.quest_sizes),
            "phase": self.phase.value,
            "quest": self.quest_index + 1,
            "attempt": self.attempt,
            "leader": self.leader,
            "team_size": self.quest_sizes[self.quest_index],
            "rejections_this_quest": self.rejections,
            "score": self.score(),
            "current_team": self.current_team,
            "current_discussion": current_statements,
            "quest_history": self.quest_history,
            "proposal_history": self.proposal_history,
            "statements": self.statements,
        })

    def private_state(self, player: int) -> dict:
        if not (_is_int(player) and 0 <= player < self.n):
            raise IllegalAction(f"no such player {player!r}")
        return private_knowledge(self.roles, player)

    def is_over(self) -> bool:
        return self.phase is Phase.DONE

    def result(self) -> dict | None:
        return copy.deepcopy(self._result)

    def ground_truth(self) -> dict:
        """Omniscient view for transcripts and analysis. Never pass this to an agent."""
        return copy.deepcopy({
            "roles": {str(i): r.value for i, r in enumerate(self.roles)},
            "quest_cards": self.quest_cards,
            "assassin_target": self.assassin_target,
        })

    # ------------------------------------------------------------------ actions
    def apply(self, player: int, action: dict) -> dict:
        """Validate and apply one action. Raises IllegalAction. Returns a small event dict."""
        if self.phase is Phase.DONE:
            raise IllegalAction("game is over")
        if not isinstance(action, dict):
            raise IllegalAction("action must be a dict")
        if player not in self.pending_actors():
            raise IllegalAction(f"player {player!r} cannot act in phase {self.phase.value}")
        handler = {
            Phase.PROPOSE: self._propose,
            Phase.DISCUSS: self._discuss,
            Phase.VOTE: self._vote,
            Phase.QUEST: self._quest,
            Phase.ASSASSINATE: self._assassinate,
        }[self.phase]
        return handler(player, action)

    def _propose(self, player: int, action: dict) -> dict:
        team = action.get("team")
        size = self.quest_sizes[self.quest_index]
        if not isinstance(team, list) or not all(_is_int(p) for p in team):
            raise IllegalAction(f"team must be a list of ints, got {team!r}")
        if len(team) != size:
            raise IllegalAction(f"quest {self.quest_index + 1} needs {size} players, got {len(team)}")
        if len(set(team)) != len(team):
            raise IllegalAction(f"duplicate players in team {team}")
        if not all(0 <= p < self.n for p in team):
            raise IllegalAction(f"player out of range in team {team}")

        self.current_team = sorted(team)
        self._record_statement(player, "proposal", action.get("statement"))
        if self.discussion_rounds > 0:
            # Leader already spoke with the proposal; others speak in order, leader closes.
            order = [(self.leader + k) % self.n for k in range(1, self.n + 1)]
            self.speaking_order = order * self.discussion_rounds
            self.phase = Phase.DISCUSS
        else:
            self._start_vote()
        return {"phase": "propose", "actor": player, "team": list(self.current_team)}

    def _discuss(self, player: int, action: dict) -> dict:
        statement = action.get("statement")
        if not isinstance(statement, str):
            raise IllegalAction("discuss action needs a 'statement' string")
        self._record_statement(player, "discussion", statement)
        self.speaking_order.pop(0)
        if not self.speaking_order:
            self._start_vote()
        return {"phase": "discuss", "actor": player}

    def _start_vote(self) -> None:
        self._votes = {}
        self.phase = Phase.VOTE

    def _vote(self, player: int, action: dict) -> dict:
        vote = action.get("vote")
        if vote not in VOTES:
            raise IllegalAction(f"vote must be one of {VOTES}, got {vote!r}")
        self._votes[player] = vote
        if len(self._votes) < self.n:
            return {"phase": "vote", "actor": player, "resolved": False}

        votes = {p: self._votes[p] for p in range(self.n)}
        approved = sum(v == "approve" for v in votes.values()) * 2 > self.n
        record = {
            "quest": self.quest_index + 1,
            "attempt": self.attempt,
            "leader": self.leader,
            "team": list(self.current_team),
            "votes": {str(p): v for p, v in votes.items()},
            "approved": approved,
        }
        self.proposal_history.append(record)
        self._votes = {}

        if approved:
            self.rejections = 0
            self._cards = {}
            self.phase = Phase.QUEST
        else:
            self.rejections += 1
            if self.rejections >= MAX_REJECTIONS:
                self._finish(Team.EVIL, "five_rejections")
            else:
                self._next_leader()
                self.attempt += 1
                self.current_team = None
                self.phase = Phase.PROPOSE
        return {"phase": "vote", "resolved": True, **copy.deepcopy(record)}

    def _quest(self, player: int, action: dict) -> dict:
        card = action.get("card")
        if card not in CARDS:
            raise IllegalAction(f"card must be one of {CARDS}, got {card!r}")
        played = card if team_of(self.roles[player]) is Team.EVIL else "success"
        self._cards[player] = {"submitted": card, "played": played}
        if len(self._cards) < len(self.current_team):
            return {"phase": "quest", "actor": player, "resolved": False}

        team = list(self.current_team)
        fails = sum(c["played"] == "fail" for c in self._cards.values())
        summary = {"quest": self.quest_index + 1, "team": team, "fails": fails, "succeeded": fails == 0}
        self.quest_history.append(summary)
        self.quest_cards.append({str(p): self._cards[p] for p in team})
        self._cards = {}

        s = self.score()
        if s["evil"] >= WINS_NEEDED:
            self._finish(Team.EVIL, "three_quests_failed")
        elif s["good"] >= WINS_NEEDED:
            self.phase = Phase.ASSASSINATE
        else:
            self.quest_index += 1
            self.attempt = 1
            self.current_team = None
            self._next_leader()
            self.phase = Phase.PROPOSE
        return {"phase": "quest", "resolved": True, **copy.deepcopy(summary)}

    def _assassinate(self, player: int, action: dict) -> dict:
        target = action.get("target")
        if not _is_int(target) or not (0 <= target < self.n) or target == player:
            raise IllegalAction(f"invalid assassination target {target!r}")
        self.assassin_target = target
        if self.roles[target] is Role.MERLIN:
            self._finish(Team.EVIL, "merlin_assassinated")
        else:
            self._finish(Team.GOOD, "three_quests_succeeded")
        return {"phase": "assassinate", "actor": player, "target": target}

    # ------------------------------------------------------------------ helpers
    def _next_leader(self) -> None:
        self.leader = (self.leader + 1) % self.n

    def _record_statement(self, player: int, kind: str, statement) -> None:
        if statement is None:
            return
        self.statements.append({
            "quest": self.quest_index + 1,
            "attempt": self.attempt,
            "speaker": player,
            "kind": kind,
            "text": str(statement),
        })

    def _finish(self, winner: Team, reason: str) -> None:
        merlin = self.roles.index(Role.MERLIN)
        assassinated = self.assassin_target is not None
        self._result = {
            "winner": winner.value,
            "reason": reason,
            "merlin_actual": merlin,
            "merlin_guess": self.assassin_target,
            # None when there was no assassination phase
            "merlin_survived": (self.assassin_target != merlin) if assassinated else None,
            "quests": [q["succeeded"] for q in self.quest_history],
            "proposals": len(self.proposal_history),
        }
        self.phase = Phase.DONE
