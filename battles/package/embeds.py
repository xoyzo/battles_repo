"""Builds the Discord embeds shown for lobbies, battles, and results.

Generalized to any number of participants: a Duel renders as the familiar
two-column "VS" layout, while Free For All / Team Battle render one field
per participant (grouped by team, when teams are enabled).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from . import helpers
from .integrations.balls import ball_display_name, ball_emoji

if TYPE_CHECKING:
    from battles.models import Battle, BattleMode

ARENA_COLOR = discord.Color.dark_red()
WAITING_COLOR = discord.Color.blurple()
FINISHED_COLOR = discord.Color.green()


def _participant_field(bot, participant, ball_instance, user_display: str, momentum_min: int, momentum_max: int, effects: list[dict], locked_in: bool) -> tuple[str, str]:
    emoji = ball_emoji(bot, ball_instance) or "🔘"
    name = ball_display_name(ball_instance)
    lock_note = "🔒 locked in" if locked_in else "⏳ choosing..."
    team_note = f" · Team {participant.team}" if participant.team is not None else ""
    alive_note = "" if participant.is_alive else " · 💀 defeated"
    value = (
        f"{emoji} **{name}**\n"
        f"{helpers.hp_line(max(0, participant.hp), participant.max_hp)}\n"
        f"{helpers.momentum_line(participant.momentum, momentum_min=momentum_min, momentum_max=momentum_max)}\n"
        f"Status: {helpers.status_effects_line(effects)}\n"
        f"{lock_note}{team_note}{alive_note}"
    )
    return f"{user_display}", value


def build_lobby_embed(mode: "BattleMode", joined_names: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title=f"{mode.icon} {mode.name} — Lobby",
        description=mode.description or "Waiting for players to join...",
        color=WAITING_COLOR,
    )
    roster = "\n".join(f"• {name}" for name in joined_names) or "*No one has joined yet.*"
    embed.add_field(name=f"Players ({len(joined_names)}/{mode.max_players})", value=roster, inline=False)
    embed.set_footer(text=f"Needs at least {mode.min_players} player(s) to start.")
    return embed


def build_battle_embed(
    bot,
    battle: "Battle",
    participants: list,
    ball_by_participant: dict[int, object],
    user_display_by_participant: dict[int, str],
    locked_by_participant: dict[int, bool],
    *,
    last_turn_summary: str | None = None,
) -> discord.Embed:
    snapshot = battle.config_snapshot or {}
    momentum_min = snapshot.get("momentum_min", -5)
    momentum_max = snapshot.get("momentum_max", 5)
    effects = (battle.state or {}).get("effects", {})

    embed = discord.Embed(
        title="⚔️ Battle Arena",
        description=last_turn_summary or f"Turn **{battle.current_turn + 1}** — choose your move.",
        color=ARENA_COLOR,
    )

    active = [p for p in participants if not p.is_spectator]

    if len(active) == 2 and not any(p.team is not None for p in active):
        # Classic Duel layout.
        p1, p2 = active
        label1, value1 = _participant_field(
            bot, p1, ball_by_participant[p1.pk], user_display_by_participant[p1.pk],
            momentum_min, momentum_max, effects.get(str(p1.pk), []), locked_by_participant.get(p1.pk, False),
        )
        embed.add_field(name=label1, value=value1, inline=True)
        embed.add_field(name="⚡ VS ⚡", value="\u200b", inline=True)
        label2, value2 = _participant_field(
            bot, p2, ball_by_participant[p2.pk], user_display_by_participant[p2.pk],
            momentum_min, momentum_max, effects.get(str(p2.pk), []), locked_by_participant.get(p2.pk, False),
        )
        embed.add_field(name=label2, value=value2, inline=True)
    else:
        # Free For All / Team Battle: one field per participant, grouped
        # by team when teams are in play.
        ordered = sorted(active, key=lambda p: (p.team if p.team is not None else 0, p.join_order))
        for participant in ordered:
            label, value = _participant_field(
                bot, participant, ball_by_participant[participant.pk], user_display_by_participant[participant.pk],
                momentum_min, momentum_max, effects.get(str(participant.pk), []), locked_by_participant.get(participant.pk, False),
            )
            embed.add_field(name=label, value=value, inline=True)

    embed.set_footer(text=f"Turn timer: {snapshot.get('turn_timer_seconds', 15)}s · Max turns: {snapshot.get('max_turns', 30)}")
    return embed


def build_challenge_embed(challenger_name: str, opponent_name: str, ball_instance, expires_in_seconds: int) -> discord.Embed:
    embed = discord.Embed(
        title="⚔️ Battle Challenge",
        description=(
            f"**{challenger_name}** has challenged **{opponent_name}** to a Duel "
            f"with {ball_display_name(ball_instance)}!\n\nAccept to choose your own ball and begin."
        ),
        color=WAITING_COLOR,
    )
    embed.set_footer(text=f"This challenge expires in {expires_in_seconds}s.")
    return embed


def build_result_embed(
    battle: "Battle",
    participants: list,
    ball_by_participant: dict[int, object],
    user_display_by_participant: dict[int, str],
    *,
    winner_text: str | None,
    reward_text: str | None = None,
) -> discord.Embed:
    title = f"🏆 {winner_text} wins!" if winner_text else "🤝 The battle ended in a draw."
    embed = discord.Embed(title=title, color=FINISHED_COLOR)

    for participant in sorted(participants, key=lambda p: (p.team if p.team is not None else 0, p.join_order)):
        if participant.is_spectator:
            continue
        ball_instance = ball_by_participant[participant.pk]
        embed.add_field(
            name=user_display_by_participant[participant.pk],
            value=f"{ball_display_name(ball_instance)} — {helpers.hp_line(max(0, participant.hp), participant.max_hp)}",
            inline=True,
        )

    if reward_text:
        embed.add_field(name="Rewards", value=reward_text, inline=False)
    embed.set_footer(text=f"Battle lasted {battle.current_turn} turn(s).")
    return embed
