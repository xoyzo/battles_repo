"""Ability eligibility checks and hook dispatch.

Ability *behaviour* lives entirely in dashboard-authored Python, executed
through `ability_sandbox.run_hook`. This module is responsible for the
surrounding bookkeeping: which abilities a ball is even allowed to use
(species/rarity/regime/economy/mode restrictions) and firing the right
hook at the right lifecycle moment.
"""
from __future__ import annotations

from battles.models import Ability

from .ability_sandbox import run_hook


async def get_usable_abilities(
    ball, rarity: str | None, regime_id: int | None, economy_id: int | None, mode_id: int | None,
    *, trigger_type: str = Ability.TriggerType.ACTIVE,
) -> list[Ability]:
    """Return enabled abilities of `trigger_type` whose restrictions
    (species/rarity/regime/economy/mode) permit them on the given ball.
    """
    abilities = [
        a async for a in Ability.objects.filter(is_enabled=True, trigger_type=trigger_type).prefetch_related(
            "allowed_balls", "allowed_regimes", "allowed_economies", "allowed_modes",
        )
    ]

    usable: list[Ability] = []
    for ability in abilities:
        allowed_balls = [b async for b in ability.allowed_balls.all()]
        if allowed_balls and ball not in allowed_balls:
            continue

        if ability.allowed_rarities and rarity is not None and rarity not in ability.allowed_rarities:
            continue

        allowed_regimes = [r.pk async for r in ability.allowed_regimes.all()]
        if allowed_regimes and regime_id is not None and regime_id not in allowed_regimes:
            continue

        allowed_economies = [e.pk async for e in ability.allowed_economies.all()]
        if allowed_economies and economy_id is not None and economy_id not in allowed_economies:
            continue

        allowed_modes = [m.pk async for m in ability.allowed_modes.all()]
        if allowed_modes and mode_id is not None and mode_id not in allowed_modes:
            continue

        usable.append(ability)

    return usable


async def trigger_ability(ability: Ability, hook_name: str, ctx) -> bool:
    """Run one hook of one ability's script against `ctx`. Returns True if
    the hook fired (i.e. was defined in the script and executed cleanly).
    """
    ctx.hook_name = hook_name
    ctx.ability_settings = ability.settings or {}
    ctx.settings = ability.settings or {}
    return await run_hook(ability.script, hook_name, ctx)


async def dispatch_passive_hook(
    hook_name: str,
    abilities_by_participant: dict[int, list[Ability]],
    context_factory,
) -> None:
    """Fire `hook_name` for every passive ability equipped across
    participants. `context_factory(participant_id)` must return a fresh
    `AbilityContext` for that participant; callers are responsible for
    applying whatever effects end up queued on it.
    """
    for participant_id, abilities in abilities_by_participant.items():
        for ability in abilities:
            if ability.trigger_type != Ability.TriggerType.PASSIVE:
                continue
            ctx = context_factory(participant_id)
            if ctx is None:
                continue
            await trigger_ability(ability, hook_name, ctx)
