"""Roles, teams, and private knowledge for 5-player Avalon. No LLM code here."""
from __future__ import annotations

import random
from enum import Enum


class Team(str, Enum):
    GOOD = "good"
    EVIL = "evil"


class Role(str, Enum):
    MERLIN = "merlin"
    PERCIVAL = "percival"
    LOYAL_SERVANT = "loyal_servant"
    MORGANA = "morgana"
    ASSASSIN = "assassin"


ROLE_TEAM = {
    Role.MERLIN: Team.GOOD,
    Role.PERCIVAL: Team.GOOD,
    Role.LOYAL_SERVANT: Team.GOOD,
    Role.MORGANA: Team.EVIL,
    Role.ASSASSIN: Team.EVIL,
}

ROLES_5P = (Role.MERLIN, Role.PERCIVAL, Role.LOYAL_SERVANT, Role.MORGANA, Role.ASSASSIN)
QUEST_SIZES_5P = (2, 3, 2, 3, 3)


def team_of(role: Role) -> Team:
    return ROLE_TEAM[role]


def assign_roles(rng: random.Random) -> list[Role]:
    roles = list(ROLES_5P)
    rng.shuffle(roles)
    return roles


def private_knowledge(roles: list[Role], player: int) -> dict:
    """What `player` secretly knows at game start.

    Player lists are always sorted so that list order never leaks role identity
    (e.g. Percival must not be able to infer Merlin from position in the pair).
    """
    role = roles[player]
    info: dict = {"player": player, "role": role.value, "team": team_of(role).value}
    if role is Role.MERLIN:
        info["evil_players"] = sorted(i for i, r in enumerate(roles) if team_of(r) is Team.EVIL)
    elif role is Role.PERCIVAL:
        info["merlin_or_morgana"] = sorted(
            i for i, r in enumerate(roles) if r in (Role.MERLIN, Role.MORGANA)
        )
    elif role in (Role.MORGANA, Role.ASSASSIN):
        info["evil_partner"] = next(
            i for i, r in enumerate(roles) if team_of(r) is Team.EVIL and i != player
        )
    return info
