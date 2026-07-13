"""Integration with `bd_models` ball species/instances.

Per the "don't build a parallel RPG stat database" correction, battle
stats are derived directly from whatever numeric fields the host bot's
`bd_models.Ball` already carries (most BallsDex forks that support combat
flavor already expose `health` and `attack`), rather than a dedicated
`BallBattleStats` table. Defense and Speed aren't part of vanilla
BallsDex, so they're derived proportionally from Health/Attack and from
rarity. If your fork already has explicit defense/speed-like fields,
adjust `_STAT_ATTRS` below — that's the one place stat sourcing lives.
"""
from __future__ import annotations

from dataclasses import dataclass

# Attribute name candidates to try, in order, for each battle stat. Kept as
# a single lookup table so adapting to a fork's actual field names is a
# one-line change rather than a hunt through the package.
_STAT_ATTRS: dict[str, tuple[str, ...]] = {
    "hp": ("health", "hp", "base_health"),
    "attack": ("attack", "base_attack", "power"),
}
_FALLBACK_HP = 100
_FALLBACK_ATTACK = 50


@dataclass
class ResolvedBattleStats:
    hp: int
    attack: int
    defense: int
    speed: int
    battle_power: int


def _first_attr(obj, names: tuple[str, ...], default: int) -> int:
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return default


def _rarity_value(ball) -> float:
    rarity = getattr(ball, "rarity", None)
    try:
        return float(rarity)
    except (TypeError, ValueError):
        return 1.0


async def get_battle_stats(ball) -> ResolvedBattleStats:
    """Derive battle stats for a `bd_models.Ball` (species) from its
    existing fields — no separate battle-stat table involved.
    """
    hp = _first_attr(ball, _STAT_ATTRS["hp"], _FALLBACK_HP)
    attack = _first_attr(ball, _STAT_ATTRS["attack"], _FALLBACK_ATTACK)

    # Defense/Speed aren't first-class BallsDex fields, so they're derived:
    # rarer balls (lower `rarity` value, by BallsDex convention) trend
    # tankier and faster. This keeps every ball meaningfully different
    # without inventing a whole second stat block to maintain.
    rarity_factor = max(0.5, min(2.0, 10.0 / max(_rarity_value(ball), 1.0)))
    defense = max(1, int(round(hp * 0.4 * rarity_factor / 2)))
    speed = max(1, int(round(attack * 0.5 * rarity_factor / 2)))
    battle_power = hp + attack + defense + speed

    return ResolvedBattleStats(hp=hp, attack=attack, defense=defense, speed=speed, battle_power=battle_power)


def ball_display_name(ball_instance) -> str:
    """Best-effort display name for a BallInstance, tolerant of whichever
    naming convention the host bot's models use.
    """
    ball = getattr(ball_instance, "ball", None)
    for attr in ("country", "name", "short_name"):
        value = getattr(ball, attr, None)
        if value:
            return str(value)
    return f"Ball #{getattr(ball_instance, 'pk', '?')}"


def ball_emoji(bot, ball_instance) -> str | None:
    """Resolve the Discord emoji for a ball instance's species, if the bot
    has it cached.
    """
    ball = getattr(ball_instance, "ball", None)
    emoji_id = getattr(ball, "emoji_id", None)
    if not emoji_id:
        return None
    emoji = bot.get_emoji(emoji_id)
    return str(emoji) if emoji else None


def ball_rarity(ball_instance) -> str | None:
    ball = getattr(ball_instance, "ball", None)
    rarity = getattr(ball, "rarity", None)
    return str(rarity) if rarity is not None else None


def ball_regime_id(ball_instance) -> int | None:
    ball = getattr(ball_instance, "ball", None)
    regime = getattr(ball, "regime", None)
    return getattr(regime, "pk", None)


def ball_economy_id(ball_instance) -> int | None:
    ball = getattr(ball_instance, "ball", None)
    economy = getattr(ball, "economy", None)
    return getattr(economy, "pk", None)
