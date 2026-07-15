"""Integration with `bd_models` ball species/instances.

Per the "don't build a parallel RPG stat database" correction, and now
confirmed against the real `bd_models.BallInstance` source: `attack` and
`health` are already computed, bonus-adjusted **properties** on every
`BallInstance` (`ball.attack * (1 + attack_bonus/100)`, same for health) —
there's nothing to derive or duplicate. Defense and Speed aren't part of
vanilla BallsDex, so they're derived proportionally from those two real
stats and from rarity, in one place, easy to adjust for your fork.

Every function here is synchronous and expects `ball_instance.ball` to
already be loaded (via `select_related("ball")` on the querying end) —
accessing an unfetched FK from an async context raises
`SynchronousOnlyOperation`, so callers must prefetch, not this module.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResolvedBattleStats:
    hp: int
    attack: int
    defense: int
    speed: int
    battle_power: int


def _rarity_value(ball) -> float:
    try:
        return float(getattr(ball, "rarity", 1.0))
    except (TypeError, ValueError):
        return 1.0


def get_battle_stats(ball_instance) -> ResolvedBattleStats:
    """Derive this specific ball instance's battle stats from its real,
    already bonus-adjusted `attack`/`health` properties. Requires
    `ball_instance.ball` to already be select_related.
    """
    hp = max(1, int(ball_instance.health))
    attack = max(1, int(ball_instance.attack))

    # Defense/Speed aren't first-class BallsDex fields, so they're derived:
    # rarer balls (lower `rarity` value, by BallsDex convention) trend
    # tankier and faster.
    rarity_factor = max(0.5, min(2.0, 10.0 / max(_rarity_value(ball_instance.ball), 1.0)))
    defense = max(1, int(round(hp * 0.4 * rarity_factor / 2)))
    speed = max(1, int(round(attack * 0.5 * rarity_factor / 2)))
    battle_power = hp + attack + defense + speed

    return ResolvedBattleStats(hp=hp, attack=attack, defense=defense, speed=speed, battle_power=battle_power)


def ball_display_name(ball_instance) -> str:
    return ball_instance.countryball.country


def ball_emoji(bot, ball_instance) -> str | None:
    """Resolve the Discord emoji for a ball instance's species, if the bot
    has it cached.
    """
    emoji_id = getattr(ball_instance.countryball, "emoji_id", None)
    if not emoji_id:
        return None
    emoji = bot.get_emoji(emoji_id)
    return str(emoji) if emoji else None


def ball_rarity(ball_instance) -> float | None:
    return getattr(ball_instance.countryball, "rarity", None)


def ball_regime_id(ball_instance) -> int | None:
    return getattr(ball_instance.countryball, "regime_id", None)


def ball_economy_id(ball_instance) -> int | None:
    return getattr(ball_instance.countryball, "economy_id", None)
