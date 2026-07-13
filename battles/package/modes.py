"""Battle mode resolution: seeding the built-in presets, listing what's
available in the mode selector, validating a player's chosen deck against
a mode's deck rules, and building the combined engine snapshot.

Duel / Free For All / Team Battle are **not** hardcoded battle logic —
they're just three `BattleMode` rows seeded on first run (`is_builtin=True`)
so they show up next to any custom mode an admin creates. Nothing in the
engine special-cases them beyond reading `mode_type` to know how targets
and win conditions work.
"""
from __future__ import annotations

from dataclasses import dataclass

from battles.models import BattleMode

from . import config as config_module

BUILTIN_MODES: list[dict] = [
    {
        "name": "Duel",
        "description": "Classic one-on-one, single ball each.",
        "icon": "⚔️",
        "mode_type": BattleMode.ModeType.DUEL,
        "min_players": 2, "max_players": 2,
        "teams_enabled": False, "team_size": 1,
        "min_deck_size": 1, "max_deck_size": 1,
    },
    {
        "name": "Free For All",
        "description": "Every player for themselves. Last ball standing wins.",
        "icon": "🎯",
        "mode_type": BattleMode.ModeType.FREE_FOR_ALL,
        "min_players": 3, "max_players": 8,
        "teams_enabled": False, "team_size": 1,
        "min_deck_size": 1, "max_deck_size": 1,
    },
    {
        "name": "Team Battle",
        "description": "Even teams, fighting until one side is fully defeated.",
        "icon": "🛡️",
        "mode_type": BattleMode.ModeType.TEAM,
        "min_players": 4, "max_players": 8,
        "teams_enabled": True, "team_size": 2,
        "min_deck_size": 1, "max_deck_size": 1,
    },
]


async def seed_builtin_modes() -> None:
    """Idempotently ensure the built-in modes exist as ordinary rows.
    Safe to call on every startup — never overwrites an admin's edits to
    an existing mode, only fills in modes that don't exist yet.
    """
    for preset in BUILTIN_MODES:
        exists = await BattleMode.objects.filter(name=preset["name"]).aexists()
        if exists:
            continue
        await BattleMode.objects.acreate(is_builtin=True, **preset)


async def list_available_modes() -> list[BattleMode]:
    return [m async for m in BattleMode.objects.filter(is_enabled=True).order_by("-is_builtin", "name")]


async def get_mode_by_name(name: str) -> BattleMode | None:
    return await BattleMode.objects.filter(name__iexact=name, is_enabled=True).afirst()


def mode_to_snapshot(mode: BattleMode) -> dict:
    return {
        "mode_id": mode.pk,
        "mode_name": mode.name,
        "mode_type": mode.mode_type,
        "min_players": mode.min_players,
        "max_players": mode.max_players,
        "teams_enabled": mode.teams_enabled,
        "team_size": mode.team_size,
        "allow_spectators": mode.allow_spectators,
        "is_public": mode.is_public,
        "min_deck_size": mode.min_deck_size,
        "max_deck_size": mode.max_deck_size,
        "allow_duplicate_balls": mode.allow_duplicate_balls,
        "allowed_rarities": list(mode.allowed_rarities or []),
        "blocked_rarities": list(mode.blocked_rarities or []),
        "turn_timer_seconds": mode.turn_timer_seconds,
        "max_turns": mode.max_turns,
        "battle_expiration_seconds": mode.battle_expiration_seconds,
        "timeout_behavior": mode.timeout_behavior,
        "enabled_actions": list(mode.enabled_actions or []),
        "win_multiplier": mode.win_multiplier,
        "loss_multiplier": mode.loss_multiplier,
        "draw_multiplier": mode.draw_multiplier,
        "streak_multiplier": mode.streak_multiplier,
        "allow_surrender": mode.allow_surrender,
        "allow_rematch": mode.allow_rematch,
    }


async def build_snapshot(mode: BattleMode) -> dict:
    """The single dict the engine/views/cog read from for a battle: global
    formula constants plus this mode's rules, merged (mode keys win on
    overlap, though today the two field sets don't overlap).
    """
    config = await config_module.get_active_config()
    snapshot = config_module.config_to_snapshot(config)
    snapshot.update(mode_to_snapshot(mode))
    return snapshot


@dataclass
class DeckValidationError:
    reason: str


async def validate_deck(mode: BattleMode, ball_instances: list) -> DeckValidationError | None:
    """Check a player's chosen ball(s) against a mode's deck rules. Returns
    None if valid, or a `DeckValidationError` describing the first problem.
    """
    count = len(ball_instances)
    if count < mode.min_deck_size:
        return DeckValidationError(f"This mode requires at least {mode.min_deck_size} ball(s).")
    if count > mode.max_deck_size:
        return DeckValidationError(f"This mode allows at most {mode.max_deck_size} ball(s).")

    allowed_balls = {b.pk async for b in mode.allowed_balls.all()}
    banned_balls = {b.pk async for b in mode.banned_balls.all()}
    seen_species: list[int] = []

    for instance in ball_instances:
        ball = instance.ball
        species_id = ball.pk

        if allowed_balls and species_id not in allowed_balls:
            return DeckValidationError(f"{getattr(ball, 'country', ball)} isn't allowed in this mode.")
        if species_id in banned_balls:
            return DeckValidationError(f"{getattr(ball, 'country', ball)} is banned in this mode.")

        rarity = getattr(ball, "rarity", None)
        if mode.allowed_rarities and rarity is not None and rarity not in mode.allowed_rarities:
            return DeckValidationError("One of your balls has a rarity that isn't allowed in this mode.")
        if mode.blocked_rarities and rarity is not None and rarity in mode.blocked_rarities:
            return DeckValidationError("One of your balls has a rarity that's blocked in this mode.")

        if not mode.allow_duplicate_balls and species_id in seen_species:
            return DeckValidationError("Duplicate balls of the same species aren't allowed in this mode.")
        seen_species.append(species_id)

    return None
