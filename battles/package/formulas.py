"""Pure battle math.

Every function here takes its tunables as arguments (usually pulled from a
`Battle.config_snapshot` dict) rather than importing config directly, so the
formulas stay easy to unit test and to override from an ability or regime
modifier stack.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class DamageResult:
    raw_damage: int
    final_damage: int
    is_critical: bool
    was_minimum_clamped: bool
    modifiers_applied: list[str] = field(default_factory=list)


def roll_critical(chance: float, rng: random.Random | None = None) -> bool:
    rng = rng or random
    return rng.random() < max(0.0, min(1.0, chance))


def momentum_damage_modifier(momentum: int, snapshot: dict) -> tuple[float, list[str]]:
    """Return a (multiplier, labels) pair derived from momentum thresholds."""
    multiplier = 1.0
    labels: list[str] = []

    if momentum >= snapshot.get("momentum_high_threshold", 3):
        multiplier += snapshot.get("momentum_high_damage_bonus", 0.10)
        labels.append("momentum_high_bonus")
    if momentum <= snapshot.get("momentum_low_threshold", -3):
        multiplier -= snapshot.get("momentum_low_damage_penalty", 0.10)
        labels.append("momentum_low_penalty")

    return max(0.0, multiplier), labels


def momentum_crit_bonus(momentum: int, snapshot: dict) -> float:
    if momentum >= snapshot.get("momentum_crit_threshold", 5):
        return snapshot.get("momentum_crit_bonus", 0.20)
    return 0.0


def compute_damage(
    attacker_attack: int,
    defender_defense: int,
    snapshot: dict,
    *,
    attacker_momentum: int = 0,
    extra_multiplier: float = 1.0,
    extra_labels: list[str] | None = None,
    rng: random.Random | None = None,
) -> DamageResult:
    """Compute damage for an Attack action landing on an undefended target.

    Uses ratio-based mitigation rather than flat subtraction:

        raw = effective_attack * (effective_attack / (effective_attack + effective_defense))

    Flat subtraction (`attack - defense`) crushes anything whose attack
    isn't comfortably ahead of the opponent's defense down to the damage
    floor on nearly every hit, because Attack and Defense here are both
    derived from the same real ball stats and land in similar ranges —
    a ball with 400 attack facing 350 defense would deal essentially
    nothing under subtraction, even though it's a perfectly fair matchup.
    Ratio-based mitigation keeps damage roughly proportional to the
    attacker's own stat regardless of the absolute scale involved: equal
    attack/defense trades ~50% of attack as damage, and the split shifts
    smoothly as the gap widens either way, so low-attack balls still hit
    meaningfully instead of bottoming out at `minimum_damage` every turn.

    Then momentum and ability/regime multipliers are layered on, a
    critical-hit roll is applied, and the result is clamped to the
    configured minimum.
    """
    attack_multiplier = snapshot.get("attack_multiplier", 1.0)
    defense_multiplier = snapshot.get("defense_multiplier", 1.0)
    minimum_damage = snapshot.get("minimum_damage", 5)
    crit_chance = snapshot.get("critical_hit_chance", 0.10)
    crit_multiplier = snapshot.get("critical_hit_multiplier", 1.5)

    effective_attack = max(0.0, attacker_attack * attack_multiplier)
    effective_defense = max(0.0, defender_defense * defense_multiplier)
    if effective_attack <= 0:
        raw = 0.0
    else:
        raw = effective_attack * (effective_attack / (effective_attack + effective_defense))

    labels: list[str] = list(extra_labels or [])

    mom_multiplier, mom_labels = momentum_damage_modifier(attacker_momentum, snapshot)
    labels.extend(mom_labels)

    total_multiplier = mom_multiplier * extra_multiplier
    damage = raw * total_multiplier

    effective_crit_chance = crit_chance + momentum_crit_bonus(attacker_momentum, snapshot)
    is_critical = roll_critical(effective_crit_chance, rng=rng)
    if is_critical:
        damage *= crit_multiplier
        labels.append("critical_hit")

    final = int(round(damage))
    was_clamped = final < minimum_damage
    final = max(minimum_damage, final)

    return DamageResult(
        raw_damage=int(round(raw)),
        final_damage=final,
        is_critical=is_critical,
        was_minimum_clamped=was_clamped,
        modifiers_applied=labels,
    )


def apply_defend_reduction(damage: int, snapshot: dict) -> int:
    reduction = snapshot.get("defend_damage_reduction", 0.5)
    return max(0, int(round(damage * (1.0 - reduction))))


def apply_counter_reflection(negated_damage: int, snapshot: dict) -> int:
    multiplier = snapshot.get("counter_reflect_multiplier", 1.0)
    return max(0, int(round(negated_damage * multiplier)))


def heal_amount(max_hp: int, snapshot: dict) -> int:
    fraction = snapshot.get("heal_amount_fraction", 0.25)
    return max(1, int(round(max_hp * fraction)))


def clamp_momentum(value: int, snapshot: dict) -> int:
    lo = snapshot.get("momentum_min", -5)
    hi = snapshot.get("momentum_max", 5)
    return max(lo, min(hi, value))


def clamp_hp(value: int, max_hp: int) -> int:
    return max(0, min(max_hp, value))
