"""Async accessors for the global damage-formula configuration and base
reward amounts. Per-mode settings (players, deck, turn rules, battle
rules, reward multipliers) live in `modes.py` instead — see
`modes.build_snapshot` for how the two are merged into what the engine
actually reads.

Nothing here is cached at import time — every call hits the database so
dashboard edits take effect on the very next battle/turn.
"""
from __future__ import annotations

from battles.models import BattleConfig, BattleReward

_DEFAULT_CONFIG_NAME = "default"
_DEFAULT_REWARD_NAME = "default"


async def get_active_config() -> BattleConfig:
    """Return the active BattleConfig row, creating a default one if missing."""
    config = await BattleConfig.objects.filter(is_active=True).afirst()
    if config is not None:
        return config

    config, _ = await BattleConfig.objects.aget_or_create(
        name=_DEFAULT_CONFIG_NAME,
        defaults={"is_active": True},
    )
    return config


async def get_active_reward_profile() -> BattleReward:
    """Return the active BattleReward row, creating a default one if missing."""
    reward = await BattleReward.objects.filter(is_active=True).afirst()
    if reward is not None:
        return reward

    reward, _ = await BattleReward.objects.aget_or_create(
        name=_DEFAULT_REWARD_NAME,
        defaults={"is_active": True},
    )
    return reward


def config_to_snapshot(config: BattleConfig) -> dict:
    """Serialize the damage-formula knobs a running battle needs into a
    plain dict. Merged with a mode snapshot by `modes.build_snapshot`.
    """
    return {
        "attack_multiplier": config.attack_multiplier,
        "defense_multiplier": config.defense_multiplier,
        "minimum_damage": config.minimum_damage,
        "critical_hit_chance": config.critical_hit_chance,
        "critical_hit_multiplier": config.critical_hit_multiplier,
        "defend_damage_reduction": config.defend_damage_reduction,
        "counter_cooldown_turns": config.counter_cooldown_turns,
        "counter_reflect_multiplier": config.counter_reflect_multiplier,
        "dodge_cooldown_turns": config.dodge_cooldown_turns,
        "heal_uses_per_battle": config.heal_uses_per_battle,
        "heal_amount_fraction": config.heal_amount_fraction,
        "momentum_min": config.momentum_min,
        "momentum_max": config.momentum_max,
        "momentum_high_threshold": config.momentum_high_threshold,
        "momentum_high_damage_bonus": config.momentum_high_damage_bonus,
        "momentum_crit_threshold": config.momentum_crit_threshold,
        "momentum_crit_bonus": config.momentum_crit_bonus,
        "momentum_low_threshold": config.momentum_low_threshold,
        "momentum_low_damage_penalty": config.momentum_low_damage_penalty,
        "afk_momentum_penalty": config.afk_momentum_penalty,
    }
