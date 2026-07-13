"""Small stateless helpers shared by `cog.py`, `views.py`, and `embeds.py`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

HP_BAR_LENGTH = 12
_FILLED = "█"
_EMPTY = "░"


def hp_bar(current: int, maximum: int, *, length: int = HP_BAR_LENGTH) -> str:
    if maximum <= 0:
        return _EMPTY * length
    ratio = max(0.0, min(1.0, current / maximum))
    filled = round(ratio * length)
    return _FILLED * filled + _EMPTY * (length - filled)


def hp_line(current: int, maximum: int) -> str:
    return f"{hp_bar(current, maximum)}  `{max(0, current)}/{maximum}`"


def momentum_line(momentum: int, *, momentum_min: int, momentum_max: int) -> str:
    span = momentum_max - momentum_min
    if span <= 0:
        return f"Momentum: {momentum}"
    filled = round((momentum - momentum_min) / span * 10)
    bar = "▮" * filled + "▯" * (10 - filled)
    return f"Momentum `{momentum:+d}` {bar}"


def status_effects_line(effects: list[dict]) -> str:
    if not effects:
        return "—"
    parts = []
    for effect in effects:
        kind = effect.get("kind", "effect")
        duration = effect.get("duration")
        label = kind.replace("_", " ").title()
        parts.append(f"{label} ({duration}t)" if duration else label)
    return ", ".join(parts)


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def is_expired(battle, expiration_seconds: int) -> bool:
    reference = battle.last_action_at or battle.created_at
    if reference is None:
        return False
    return now_utc() - reference > timedelta(seconds=expiration_seconds)


def participant_for_user(participants: list, user_id: int):
    """Find the (non-spectator) participant seat belonging to `user_id`."""
    for participant in participants:
        if participant.user_id == user_id and not participant.is_spectator:
            return participant
    return None


def other_participants(participants: list, participant_id: int, *, alive_only: bool = True) -> list:
    return [
        p for p in participants
        if p.pk != participant_id and not p.is_spectator and (not alive_only or p.is_alive)
    ]


def teammates_of(participants: list, participant) -> list:
    if participant.team is None:
        return []
    return [
        p for p in participants
        if p.pk != participant.pk and p.team == participant.team and not p.is_spectator
    ]


def enemies_of(participants: list, participant) -> list:
    return [
        p for p in participants
        if p.pk != participant.pk and not p.is_spectator
        and (participant.team is None or p.team != participant.team)
    ]


def format_action_summary(actor_label: str, action_label: str, emoji: str, outcome_notes: list[str]) -> str:
    notes = f" ({', '.join(outcome_notes)})" if outcome_notes else ""
    return f"{emoji} **{actor_label}** used **{action_label}**{notes}"
