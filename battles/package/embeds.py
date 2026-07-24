"""Builds the Discord embeds shown for lobbies, battles, and results.

Generalized to any number of participants: a Duel renders as the familiar
two-column "VS" layout, while Free For All / Team Battle render one field
per participant (grouped by team, when teams are enabled).

Every name/label passed into these functions must already be a resolved
display name, never raw `<@id>` mention syntax — Discord doesn't reliably
render or ping mentions placed inside embeds. That resolution happens in
`cog.py` (see `BattlesCog._display_name`) before anything reaches here.
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
WIN_COLOR = discord.Color.gold()
DRAW_COLOR = discord.Color.light_grey()


def _participant_field(bot, participant, ball_instance, user_display: str, momentum_min: int, momentum_max: int, effects: list[dict], locked_in: bool) -> tuple[str, str]:
    emoji = ball_emoji(bot, ball_instance) or "🔘"
    name = ball_display_name(ball_instance)
    team_note = f" · Team {participant.team}" if participant.team is not None else ""

    if not participant.is_alive:
        status_line = "💀 **Defeated**"
    else:
        status_line = "🔒 Locked in" if locked_in else "⏳ Choosing..."

    value = (
        f"{emoji} **{name}**{team_note}\n"
        f"{helpers.hp_line(max(0, participant.hp), participant.max_hp)}\n"
        f"{helpers.momentum_line(participant.momentum, momentum_min=momentum_min, momentum_max=momentum_max)}\n"
        f"Status: {helpers.status_effects_line(effects)}\n"
        f"{status_line}"
    )
    return user_display, value


def build_lobby_embed(mode: "BattleMode", joined_names: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title=f"{mode.icon} {mode.name} — Lobby",
        description=mode.description or "Waiting for players to join...",
        color=WAITING_COLOR,
    )
    roster = "\n".join(f"• {name}" for name in joined_names) or "*No one has joined yet — use `/battle add`.*"
    embed.add_field(name=f"Roster ({len(joined_names)}/{mode.max_players})", value=roster, inline=False)

    deck_note = f"{mode.min_deck_size}" if mode.min_deck_size == mode.max_deck_size else f"{mode.min_deck_size}–{mode.max_deck_size}"
    embed.add_field(name="Deck size", value=deck_note, inline=True)
    embed.add_field(name="Turn timer", value=f"{mode.turn_timer_seconds}s", inline=True)
    if mode.teams_enabled:
        embed.add_field(name="Teams", value=f"{mode.team_size} per side", inline=True)

    embed.set_footer(text=f"Needs at least {mode.min_players} player(s) to begin.")
    embed.timestamp = discord.utils.utcnow()
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
    mode_name = snapshot.get("mode_name")

    embed = discord.Embed(
        title="⚔️ Battle Arena",
        description=last_turn_summary or f"**Turn {battle.current_turn + 1}** — choose your move.",
        color=ARENA_COLOR,
    )
    if mode_name:
        embed.set_author(name=mode_name)

    active = [p for p in participants if not p.is_spectator]

    if len(active) == 2 and active[0].team != active[1].team:
        # Classic Duel layout.
        p1, p2 = active
        label1, value1 = _participant_field(
            bot, p1, ball_by_participant[p1.pk], user_display_by_participant[p1.pk],
            momentum_min, momentum_max, effects.get(str(p1.pk), []), locked_by_participant.get(p1.pk, False),
        )
        embed.add_field(name=label1, value=value1, inline=True)
        embed.add_field(name="⚡", value="**VS**", inline=True)
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

    embed.set_footer(text=f"⏱️ {snapshot.get('turn_timer_seconds', 15)}s per turn · Turn {battle.current_turn + 1} of {snapshot.get('max_turns', 30)} max")
    embed.timestamp = discord.utils.utcnow()
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
    if winner_text:
        title = f"🏆 {winner_text} wins!"
        color = WIN_COLOR
    else:
        title = "🤝 The battle ended in a draw."
        color = DRAW_COLOR

    embed = discord.Embed(title=title, color=color)

    for participant in sorted(participants, key=lambda p: (p.team if p.team is not None else 0, p.join_order)):
        if participant.is_spectator:
            continue
        ball_instance = ball_by_participant[participant.pk]
        outcome_icon = "🏆" if participant.is_alive else "💀"
        embed.add_field(
            name=f"{outcome_icon} {user_display_by_participant[participant.pk]}",
            value=f"{ball_display_name(ball_instance)} — {helpers.hp_line(max(0, participant.hp), participant.max_hp)}",
            inline=True,
        )

    if reward_text:
        embed.add_field(name="💰 Rewards", value=reward_text, inline=False)
    embed.set_footer(text=f"Battle lasted {battle.current_turn} turn(s).")
    embed.timestamp = discord.utils.utcnow()
    return embed
