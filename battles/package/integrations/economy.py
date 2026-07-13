"""Integration with BallsDex Economies.

Economy is a Ball *classification* (e.g. Capitalism / Socialism / Mixed
Economy), exactly like Regime — **not** a currency system. See
`integrations/currency.py` for actual currency payouts.

Abilities and battle rules can use the helpers below to build effects
like "buff allied balls sharing an Economy", "bonus damage against a
specific Economy", "team bonus if every ball shares an Economy", or
"bonus for a diverse team of Economies".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_NAME_BASED_DEFAULTS: dict[str, dict[str, float]] = {}


@dataclass
class EconomyModifiers:
    damage_multiplier: float = 1.0
    defense_multiplier: float = 1.0
    reward_multiplier: float = 1.0
    raw: dict[str, Any] = field(default_factory=dict)


def _from_dict(data: dict[str, Any]) -> EconomyModifiers:
    return EconomyModifiers(
        damage_multiplier=float(data.get("damage_multiplier", 1.0)),
        defense_multiplier=float(data.get("defense_multiplier", 1.0)),
        reward_multiplier=float(data.get("reward_multiplier", 1.0)),
        raw=data,
    )


def get_economy_modifiers(economy) -> EconomyModifiers:
    """Resolve battle modifiers for a `bd_models.Economy` instance, if the
    host project has extended it with a `battle_modifiers` JSON field.
    """
    if economy is None:
        return EconomyModifiers()

    custom = getattr(economy, "battle_modifiers", None)
    if isinstance(custom, dict) and custom:
        return _from_dict(custom)

    name = str(getattr(economy, "name", "")).strip().lower()
    if name in _NAME_BASED_DEFAULTS:
        return _from_dict(_NAME_BASED_DEFAULTS[name])

    return EconomyModifiers()


def economy_id_of(ball_instance) -> int | None:
    ball = getattr(ball_instance, "ball", None)
    economy = getattr(ball, "economy", None)
    return getattr(economy, "pk", None)


def economy_name_of(ball_instance) -> str | None:
    ball = getattr(ball_instance, "ball", None)
    economy = getattr(ball, "economy", None)
    name = getattr(economy, "name", None)
    return str(name) if name is not None else None


def team_shares_economy(economy_ids: list[int | None]) -> bool:
    """True if every participant on a team fields the same (non-null) Economy."""
    values = [e for e in economy_ids if e is not None]
    return bool(values) and len(set(values)) == 1 and len(values) == len(economy_ids)


def team_economy_diversity(economy_ids: list[int | None]) -> int:
    """Count of distinct Economies represented on a team, for "diverse
    team" bonuses.
    """
    return len({e for e in economy_ids if e is not None})


def bonus_multiplier_vs_economy(attacker_economy_id: int | None, defender_economy_id: int | None, matchups: dict[int, dict[int, float]]) -> float:
    """Look up a configured attacker-economy-vs-defender-economy damage
    multiplier, e.g. matchups={CAPITALISM_ID: {SOCIALISM_ID: 1.2}}.
    Defaults to 1.0 (no bonus) for any pairing not explicitly configured.
    """
    if attacker_economy_id is None or defender_economy_id is None:
        return 1.0
    return matchups.get(attacker_economy_id, {}).get(defender_economy_id, 1.0)
