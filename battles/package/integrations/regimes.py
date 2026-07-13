"""Integration with BallsDex regimes.

Regimes can modify damage, defense, healing, ability behaviour, rewards,
and cooldowns. Modifiers are read from a JSON blob stored either on the
regime model itself (if the host bot has extended it) or from a
package-local override table via `Regime.pk`, so this stays functional
even if `bd_models.Regime` hasn't been extended with battle-specific
fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Fallback modifiers for regimes by name, used only if the regime model
# doesn't carry its own `battle_modifiers` JSON field. Dashboard admins can
# still override any of this by adding a `battle_modifiers` field to their
# Regime model/admin if they want per-regime control beyond these defaults.
_NAME_BASED_DEFAULTS: dict[str, dict[str, float]] = {
    "war": {"damage_multiplier": 1.20},
    "peace": {"healing_multiplier": 1.20},
    "chaos": {"chaos": True},
}


@dataclass
class RegimeModifiers:
    damage_multiplier: float = 1.0
    defense_multiplier: float = 1.0
    healing_multiplier: float = 1.0
    reward_multiplier: float = 1.0
    cooldown_multiplier: float = 1.0
    chaos: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def _from_dict(data: dict[str, Any]) -> RegimeModifiers:
    return RegimeModifiers(
        damage_multiplier=float(data.get("damage_multiplier", 1.0)),
        defense_multiplier=float(data.get("defense_multiplier", 1.0)),
        healing_multiplier=float(data.get("healing_multiplier", 1.0)),
        reward_multiplier=float(data.get("reward_multiplier", 1.0)),
        cooldown_multiplier=float(data.get("cooldown_multiplier", 1.0)),
        chaos=bool(data.get("chaos", False)),
        raw=data,
    )


def get_regime_modifiers(regime) -> RegimeModifiers:
    """Resolve the battle modifiers for a `bd_models.Regime` instance."""
    if regime is None:
        return RegimeModifiers()

    custom = getattr(regime, "battle_modifiers", None)
    if isinstance(custom, dict) and custom:
        return _from_dict(custom)

    name = str(getattr(regime, "name", "")).strip().lower()
    if name in _NAME_BASED_DEFAULTS:
        return _from_dict(_NAME_BASED_DEFAULTS[name])

    return RegimeModifiers()


def apply_chaos_roll(modifiers: RegimeModifiers, rng) -> RegimeModifiers:
    """Chaos regime: randomize the multipliers within a modest band each
    time it's rolled, instead of using a fixed value.
    """
    if not modifiers.chaos:
        return modifiers
    return RegimeModifiers(
        damage_multiplier=rng.uniform(0.8, 1.4),
        defense_multiplier=rng.uniform(0.8, 1.4),
        healing_multiplier=rng.uniform(0.8, 1.4),
        reward_multiplier=rng.uniform(0.8, 1.4),
        cooldown_multiplier=rng.uniform(0.8, 1.2),
        chaos=True,
        raw=modifiers.raw,
    )
