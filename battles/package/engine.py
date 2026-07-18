"""Core battle engine: turn resolution.

Framework-agnostic (no discord.py, no Django ORM) so it can be unit tested
in isolation. Generalized to any number of participants (Duel, Free For
All, Team Battle all flow through the same `resolve_turn`): each
participant who chose an offensive action targets exactly one other
participant, and that pair is resolved with the same Attack/Defend/
Counter/Heal/Dodge interaction rules a Duel uses. Ability actions bypass
that matrix entirely (see `abilities.py` / `ability_sandbox.py`) since
their effect is arbitrary dashboard-authored script, not a fixed formula.

Note: the original Duel-only "Defend is weak against Counter" chip-damage
flavor rule (punishing two passive players facing only each other) doesn't
generalize cleanly to N players with independent targets, so it isn't
part of the general resolver. It's a natural mode-specific rule to layer
back in for `mode_type == "duel"` if desired.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import formulas
from .actions import ActionKey

ParticipantId = int


@dataclass
class ParticipantContext:
    """Everything the engine needs about one participant for a single turn.

    `shield_hp`, `vulnerable_to`, and `damage_taken_multiplier` are how
    ability-applied status effects (see `ctx.add_effect()` in
    `ability_api.py`) actually reach combat resolution, rather than just
    being cosmetically recorded. They're populated by the caller from
    each participant's active `Battle.state["effects"]` entries before
    `resolve_turn` runs.
    """

    participant_id: ParticipantId
    team: int | None
    hp: int
    max_hp: int
    attack: int
    defense: int
    momentum: int
    cooldowns: dict[str, int] = field(default_factory=dict)  # action key -> turns remaining
    heal_uses: int = 0
    ability_bonus_damage_multiplier: float = 1.0
    is_alive: bool = True
    shield_hp: int = 0
    # attacker participant_id -> multiplier applied only when *that*
    # attacker lands a hit (e.g. "takes 2x damage from Larry specifically").
    vulnerable_to: dict[ParticipantId, float] = field(default_factory=dict)
    # Applied to incoming damage regardless of attacker (e.g. a generic
    # "protected" buff at <1.0, or a generic "vulnerable" debuff at >1.0).
    damage_taken_multiplier: float = 1.0


@dataclass
class TurnAction:
    action: ActionKey
    target_id: ParticipantId | None = None
    ability_id: int | None = None


@dataclass
class ParticipantOutcome:
    participant_id: ParticipantId
    action: ActionKey
    damage_dealt: int = 0
    damage_taken: int = 0
    healed: int = 0
    momentum_delta: int = 0
    was_blocked: bool = False
    was_dodged: bool = False
    was_countered: bool = False
    was_interrupted: bool = False
    is_critical: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    outcomes: dict[ParticipantId, ParticipantOutcome]
    new_hp: dict[ParticipantId, int]
    new_momentum: dict[ParticipantId, int]
    new_cooldowns: dict[ParticipantId, dict[str, int]]
    new_heal_uses: dict[ParticipantId, int]
    new_shield_hp: dict[ParticipantId, int]
    deaths: list[ParticipantId]


def _resolve_attacker_vs(
    attacker: ParticipantContext,
    defender: ParticipantContext,
    defender_action: ActionKey,
    snapshot: dict,
    rng: random.Random | None,
    out_attacker: ParticipantOutcome,
    out_defender: ParticipantOutcome,
) -> None:
    """Resolve an Attack action against whatever its target chose."""
    dmg = formulas.compute_damage(
        attacker.attack,
        defender.defense,
        snapshot,
        attacker_momentum=attacker.momentum,
        extra_multiplier=attacker.ability_bonus_damage_multiplier,
        rng=rng,
    )
    out_attacker.is_critical = dmg.is_critical

    if defender_action is ActionKey.DEFEND:
        final = formulas.apply_defend_reduction(dmg.final_damage, snapshot)
        out_defender.was_blocked = True
        out_defender.notes.append("blocked")
    elif defender_action is ActionKey.COUNTER and defender.cooldowns.get("counter", 0) == 0:
        reflected = formulas.apply_counter_reflection(dmg.final_damage, snapshot)
        out_attacker.damage_taken += reflected
        out_defender.was_countered = True
        out_attacker.notes.append("countered")
        final = 0
    elif defender_action is ActionKey.DODGE and defender.cooldowns.get("dodge", 0) == 0:
        final = 0
        out_defender.was_dodged = True
        out_defender.notes.append("dodged")
    elif defender_action is ActionKey.HEAL:
        final = dmg.final_damage
        out_defender.was_interrupted = True
        out_defender.notes.append("heal_interrupted")
    else:
        # Attack vs Attack, Attack vs Ability, or a Counter/Dodge still on
        # cooldown all fall through to full damage.
        final = dmg.final_damage

    # Status effects: a generic protection/vulnerability multiplier, an
    # attacker-specific vulnerability multiplier (e.g. "takes 2x damage
    # specifically from Larry"), then a shield that absorbs whatever's
    # left before it reaches HP.
    if final > 0:
        final = int(round(final * defender.damage_taken_multiplier))
        final = int(round(final * defender.vulnerable_to.get(attacker.participant_id, 1.0)))
        if defender.shield_hp > 0:
            absorbed = min(defender.shield_hp, final)
            defender.shield_hp -= absorbed
            final -= absorbed
            if absorbed:
                out_defender.notes.append("shielded")

    out_defender.damage_taken += final
    out_attacker.damage_dealt += final


def resolve_turn(
    participants: dict[ParticipantId, ParticipantContext],
    actions: dict[ParticipantId, TurnAction],
    snapshot: dict,
    *,
    rng: random.Random | None = None,
) -> TurnResult:
    """Resolve one simultaneous-reveal turn across every acting participant."""

    outcomes: dict[ParticipantId, ParticipantOutcome] = {
        pid: ParticipantOutcome(participant_id=pid, action=act.action) for pid, act in actions.items()
    }

    # Step 1: resolve every Attack against its chosen target.
    for pid, act in actions.items():
        if act.action is not ActionKey.ATTACK:
            continue
        attacker = participants.get(pid)
        target_id = act.target_id
        if attacker is None or target_id is None or target_id not in participants:
            continue
        defender = participants[target_id]
        defender_action = actions[target_id].action if target_id in actions else ActionKey.DEFEND
        _resolve_attacker_vs(attacker, defender, defender_action, snapshot, rng, outcomes[pid], outcomes[target_id])

    # Step 2: resolve Heals that weren't interrupted by an incoming Attack.
    for pid, act in actions.items():
        if act.action is not ActionKey.HEAL:
            continue
        ctx = participants.get(pid)
        outcome = outcomes[pid]
        if ctx is None or outcome.was_interrupted:
            continue
        if ctx.heal_uses < snapshot.get("heal_uses_per_battle", 3):
            outcome.healed = formulas.heal_amount(ctx.max_hp, snapshot)

    # Momentum: net damage dealt minus taken (plus healing) drives momentum
    # up or down for whoever acted this turn.
    for pid, outcome in outcomes.items():
        net = outcome.damage_dealt - outcome.damage_taken + outcome.healed
        if net > 0:
            outcome.momentum_delta = 1
        elif net < 0:
            outcome.momentum_delta = -1

    new_hp: dict[ParticipantId, int] = {}
    new_momentum: dict[ParticipantId, int] = {}
    new_cooldowns: dict[ParticipantId, dict[str, int]] = {}
    new_heal_uses: dict[ParticipantId, int] = {}
    new_shield_hp: dict[ParticipantId, int] = {}
    deaths: list[ParticipantId] = []

    for pid, ctx in participants.items():
        outcome = outcomes.get(pid)
        damage_taken = outcome.damage_taken if outcome else 0
        healed = outcome.healed if outcome else 0
        momentum_delta = outcome.momentum_delta if outcome else 0

        hp_after = formulas.clamp_hp(ctx.hp - damage_taken + healed, ctx.max_hp)
        new_hp[pid] = hp_after
        if hp_after <= 0:
            deaths.append(pid)

        new_momentum[pid] = formulas.clamp_momentum(ctx.momentum + momentum_delta, snapshot)

        used_action = actions[pid].action if pid in actions else None
        new_cooldowns[pid] = _tick_cooldowns(ctx.cooldowns, used_action)

        new_heal_uses[pid] = ctx.heal_uses + (1 if outcome and outcome.healed > 0 else 0)
        # `ctx.shield_hp` was mutated in place by `_resolve_attacker_vs` as
        # damage was absorbed against it this turn.
        new_shield_hp[pid] = ctx.shield_hp

    return TurnResult(
        outcomes=outcomes,
        new_hp=new_hp,
        new_momentum=new_momentum,
        new_cooldowns=new_cooldowns,
        new_heal_uses=new_heal_uses,
        new_shield_hp=new_shield_hp,
        deaths=deaths,
    )


def _tick_cooldowns(cooldowns: dict[str, int], used_action: ActionKey | None) -> dict[str, int]:
    """Decrement all active cooldowns by one turn."""
    from .actions import ACTIONS

    ticked = {k: max(0, v - 1) for k, v in cooldowns.items()}
    if used_action is not None:
        definition = ACTIONS.get(used_action)
        if definition and definition.cooldown_field:
            ticked.setdefault(definition.cooldown_field, 0)
    return ticked


def apply_cooldown_on_use(cooldowns: dict[str, int], action: ActionKey, snapshot: dict) -> dict[str, int]:
    """Set the post-use cooldown length for an action that was just used."""
    from .actions import ACTIONS

    definition = ACTIONS.get(action)
    if not definition or not definition.cooldown_field:
        return cooldowns
    length_map = {
        "counter": snapshot.get("counter_cooldown_turns", 2),
        "dodge": snapshot.get("dodge_cooldown_turns", 2),
    }
    length = length_map.get(definition.cooldown_field)
    if length is not None:
        cooldowns[definition.cooldown_field] = length
    return cooldowns


def resolve_max_turns_draw(current_turn: int, max_turns: int) -> bool:
    return current_turn >= max_turns


def alive_teams(participants: dict[ParticipantId, ParticipantContext], hp_by_id: dict[ParticipantId, int]) -> set[int | None]:
    """Distinct teams (or participant ids, if `team` is None/FFA) that
    still have at least one living member after `hp_by_id` is applied.
    """
    teams: set[int | None] = set()
    for pid, ctx in participants.items():
        if hp_by_id.get(pid, ctx.hp) > 0:
            teams.add(ctx.team if ctx.team is not None else pid)
    return teams
