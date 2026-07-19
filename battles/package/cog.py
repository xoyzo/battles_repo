"""Discord entry point for the battles package."""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from battles.models import Ability, Battle, BattleMode, BattleParticipant, BattleTurn

from . import embeds, helpers, modes, monkeypatch
from .abilities import get_usable_abilities, trigger_ability
from .ability_api import AbilityContext
from .actions import ACTIONS, ActionKey
from .buttons import RematchButton
from .config import get_active_reward_profile
from .engine import ParticipantContext, TurnAction, TurnResult, alive_teams, apply_cooldown_on_use, resolve_max_turns_draw, resolve_turn
from .integrations import events
from .integrations.balls import ball_display_name, ball_economy_id, ball_rarity, ball_regime_id, get_battle_stats
from .integrations.currency import award_currency
from .tasks import ExpirationSweeper
from .transformers import BattleModeTransform
from .views import BattleView, LobbyView
from ballsdex.core.utils.transformers import BallInstanceTransform

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("battles.cog")


class BattlesCog(commands.GroupCog, group_name="battle", group_description="Challenge other players to interactive ball battles."):
    """Interactive, button-driven PvP battles between BallsDex players,
    played under dashboard-authored modes and abilities.

    Command flow mirrors `/trade`: `start` opens a lobby, `add`/`remove`
    build your deck (real autocomplete via `BallInstanceTransform`, no
    raw IDs), and `begin` launches the battle once enough players are in.

    Note: `commands.GroupCog` would otherwise use this docstring itself as
    the Discord-facing group description, which has a hard 100-character
    limit — passing `group_description` explicitly above avoids that.
    """

    def __init__(self, bot: "BallsDexBot") -> None:
        self.bot = bot
        self.sweeper = ExpirationSweeper(self)
        self._post_init_task: object | None = None

    async def cog_load(self) -> None:
        self._post_init_task = self.bot.loop.create_task(self._post_init())

    async def cog_unload(self) -> None:
        self.sweeper.stop()
        task = self._post_init_task
        if task is not None:
            task.cancel()  # type: ignore[attr-defined]

    async def _post_init(self) -> None:
        await self.bot.wait_until_ready()
        monkeypatch.apply_patches()
        await modes.seed_builtin_modes()
        self.sweeper.start()
        log.info("Battles cog ready.")

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    @app_commands.command(name="start", description="Open a battle lobby. Build your deck with /battle add, then /battle begin.")
    @app_commands.describe(mode="Which battle mode to play", opponent="Invite a specific player (leave empty for an open lobby anyone can join)")
    async def start(self, interaction: discord.Interaction, mode: BattleModeTransform, opponent: discord.Member | None = None) -> None:
        if opponent is not None:
            if opponent.bot:
                await interaction.response.send_message("You can't battle a bot.", ephemeral=True)
                return
            if opponent.id == interaction.user.id:
                await interaction.response.send_message("You can't battle yourself.", ephemeral=True)
                return
            if mode.max_players < 2:
                await interaction.response.send_message(f"'{mode.name}' doesn't support more than one player.", ephemeral=True)
                return

        existing = await self._find_open_lobby(interaction.channel_id or 0, interaction.user.id)
        if existing is not None:
            await interaction.response.send_message("You're already in an open battle lobby in this channel.", ephemeral=True)
            return

        battle = await Battle.objects.acreate(
            mode=mode, guild_id=interaction.guild_id or 0, channel_id=interaction.channel_id or 0,
            status=Battle.Status.LOBBY, state={"invited_user_ids": [opponent.id] if opponent else []},
            created_at=helpers.now_utc(), last_action_at=helpers.now_utc(),
        )

        mention = f" {opponent.mention}, you've been invited!" if opponent else ""
        embed = embeds.build_lobby_embed(mode, [])
        view = LobbyView(
            battle_id=battle.pk,
            on_begin=lambda i: self._handle_begin(i, battle.pk),
            on_cancel=lambda i: self._handle_cancel_lobby(i, battle.pk),
        )
        await interaction.response.send_message(
            content=f"{interaction.user.mention} started a **{mode.name}** lobby!{mention} Use `/battle add` to join with a ball.",
            embed=embed, view=view,
        )
        message = await interaction.original_response()
        battle.message_id = message.id
        await battle.asave(update_fields=["message_id"])

    @app_commands.command(name="add", description="Add a ball to your current battle lobby. Call it again to add more, up to the mode's deck size.")
    @app_commands.describe(countryball="The ball you want to fight with")
    async def add(self, interaction: discord.Interaction, countryball: BallInstanceTransform) -> None:
        battle = await self._find_open_lobby(interaction.channel_id or 0, interaction.user.id)
        if battle is None:
            await interaction.response.send_message("You don't have an open battle lobby in this channel. Use `/battle start` first.", ephemeral=True)
            return

        mode = await BattleMode.objects.aget(pk=battle.mode_id)
        my_seats = [p async for p in BattleParticipant.objects.select_related("ball_instance__ball").filter(battle_id=battle.pk, user_id=interaction.user.id)]

        if any(p.ball_instance_id == countryball.pk for p in my_seats):
            await interaction.response.send_message(f"{ball_display_name(countryball)} is already in your deck for this battle.", ephemeral=True)
            return

        if len(my_seats) >= mode.max_deck_size:
            await interaction.response.send_message(f"'{mode.name}' only allows {mode.max_deck_size} ball(s) per player — remove one with `/battle remove` first.", ephemeral=True)
            return

        if not my_seats:
            distinct_players = await BattleParticipant.objects.filter(battle_id=battle.pk).values("user_id").distinct().acount()
            if distinct_players >= mode.max_players:
                await interaction.response.send_message("This lobby is full.", ephemeral=True)
                return

        # Re-validate the whole deck-in-progress (not just the new ball) so
        # duplicate-species rules and deck-size limits are checked against
        # what the player will actually field.
        deck_error = await modes.validate_deck(mode, [p.ball_instance for p in my_seats] + [countryball], enforce_min=False)
        if deck_error is not None:
            await interaction.response.send_message(deck_error.reason, ephemeral=True)
            return

        next_join_order = await BattleParticipant.objects.filter(battle_id=battle.pk).acount()
        await BattleParticipant.objects.acreate(
            battle=battle, user_id=interaction.user.id, ball_instance=countryball, join_order=next_join_order,
        )
        deck_note = f" ({len(my_seats) + 1}/{mode.max_deck_size} balls)" if mode.max_deck_size > 1 else ""
        await interaction.response.send_message(f"{ball_display_name(countryball)} added{deck_note} — you're in the lobby.", ephemeral=True)
        await self._refresh_lobby_message(battle, mode)

    @app_commands.command(name="remove", description="Remove one of your balls from your current battle lobby.")
    @app_commands.describe(countryball="The ball to remove from your deck")
    async def remove(self, interaction: discord.Interaction, countryball: BallInstanceTransform) -> None:
        battle = await self._find_open_lobby(interaction.channel_id or 0, interaction.user.id)
        if battle is None:
            await interaction.response.send_message("You're not in a battle lobby in this channel.", ephemeral=True)
            return

        deleted, _ = await BattleParticipant.objects.filter(battle_id=battle.pk, user_id=interaction.user.id, ball_instance=countryball).adelete()
        if not deleted:
            await interaction.response.send_message("That ball isn't in your deck for this lobby.", ephemeral=True)
            return

        await interaction.response.send_message("You left the lobby.", ephemeral=True)
        mode = await BattleMode.objects.aget(pk=battle.mode_id)
        await self._refresh_lobby_message(battle, mode)

    @app_commands.command(name="begin", description="Start the battle once enough players have added a ball.")
    async def begin(self, interaction: discord.Interaction) -> None:
        battle = await self._find_open_lobby(interaction.channel_id or 0, interaction.user.id)
        if battle is None:
            await interaction.response.send_message("You're not in a battle lobby in this channel.", ephemeral=True)
            return
        await self._handle_begin(interaction, battle.pk)

    @app_commands.command(name="cancel", description="Cancel your current battle lobby.")
    async def cancel(self, interaction: discord.Interaction) -> None:
        battle = await self._find_open_lobby(interaction.channel_id or 0, interaction.user.id)
        if battle is None:
            await interaction.response.send_message("You're not in a battle lobby in this channel.", ephemeral=True)
            return
        await self._handle_cancel_lobby(interaction, battle.pk)

    @app_commands.command(name="stats", description="View a ball's battle stats.")
    @app_commands.describe(countryball="The ball you want to inspect")
    async def stats(self, interaction: discord.Interaction, countryball: BallInstanceTransform) -> None:
        stats = get_battle_stats(countryball)
        await interaction.response.send_message(
            f"**{ball_display_name(countryball)}**\n"
            f"HP: {stats.hp} · Attack: {stats.attack} · Defense: {stats.defense} · "
            f"Speed: {stats.speed} · Battle Power: {stats.battle_power}",
            ephemeral=True,
        )

    @app_commands.command(name="modes", description="List available battle modes.")
    async def list_modes(self, interaction: discord.Interaction) -> None:
        available = await modes.list_available_modes()
        if not available:
            await interaction.response.send_message("No battle modes are currently enabled.", ephemeral=True)
            return
        lines = [f"{m.icon} **{m.name}** — {m.description or 'No description.'}" for m in available]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ------------------------------------------------------------------
    # Lobby helpers
    # ------------------------------------------------------------------
    async def _find_open_lobby(self, channel_id: int, user_id: int) -> Battle | None:
        """Resolve "the lobby this command should act on" for a user: the
        lobby they're already seated in, if any, else the first open,
        joinable lobby in this channel.
        """
        candidates = [
            b async for b in Battle.objects.select_related("mode").filter(
                channel_id=channel_id, status=Battle.Status.LOBBY,
            ).order_by("-created_at")
        ]
        for battle in candidates:
            if await BattleParticipant.objects.filter(battle_id=battle.pk, user_id=user_id).aexists():
                return battle

        for battle in candidates:
            invited = (battle.state or {}).get("invited_user_ids") or []
            if invited and user_id not in invited:
                continue
            distinct_players = await BattleParticipant.objects.filter(battle_id=battle.pk).values("user_id").distinct().acount()
            if distinct_players >= battle.mode.max_players:
                continue
            return battle

        return None

    async def _refresh_lobby_message(self, battle: Battle, mode: BattleMode) -> None:
        try:
            channel = self.bot.get_channel(battle.channel_id) or await self.bot.fetch_channel(battle.channel_id)
            message = await channel.fetch_message(battle.message_id)  # type: ignore[union-attr]
        except discord.HTTPException:
            return
        participants = [p async for p in BattleParticipant.objects.select_related("ball_instance__ball").filter(battle_id=battle.pk).order_by("join_order")]
        balls_by_user: dict[int, list[str]] = {}
        for p in participants:
            balls_by_user.setdefault(p.user_id, []).append(ball_display_name(p.ball_instance))

        names = []
        for user_id, ball_names in balls_by_user.items():
            member = message.guild.get_member(user_id) if message.guild else None
            display = member.display_name if member else f"<@{user_id}>"
            names.append(f"{display} — {', '.join(ball_names)}")
        embed = embeds.build_lobby_embed(mode, names)
        await message.edit(embed=embed)

    async def _handle_begin(self, interaction: discord.Interaction, battle_id: int) -> None:
        battle = await Battle.objects.select_related("mode").aget(pk=battle_id)
        if battle.status != Battle.Status.LOBBY:
            await interaction.response.send_message("This battle already started.", ephemeral=True)
            return

        participants = [p async for p in BattleParticipant.objects.select_related("ball_instance__ball").filter(battle_id=battle.pk).order_by("join_order")]
        distinct_players = len({p.user_id for p in participants})
        if distinct_players < battle.mode.min_players:
            await interaction.response.send_message(f"Need at least {battle.mode.min_players} player(s) to begin — currently {distinct_players}.", ephemeral=True)
            return

        if battle.mode.min_deck_size > 1:
            seats_by_user: dict[int, int] = {}
            for p in participants:
                seats_by_user[p.user_id] = seats_by_user.get(p.user_id, 0) + 1
            short = [uid for uid, n in seats_by_user.items() if n < battle.mode.min_deck_size]
            if short:
                names = ", ".join(f"<@{uid}>" for uid in short)
                await interaction.response.send_message(
                    f"{names} still need at least {battle.mode.min_deck_size} ball(s) added (`/battle add`) before this battle can begin.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message("Starting the battle...", ephemeral=True)
        await self._launch_battle(battle, participants)

    async def _handle_cancel_lobby(self, interaction: discord.Interaction, battle_id: int) -> None:
        battle = await Battle.objects.aget(pk=battle_id)
        if battle.status != Battle.Status.LOBBY:
            await interaction.response.send_message("This battle already started.", ephemeral=True)
            return
        battle.status = Battle.Status.CANCELLED
        battle.finished_at = helpers.now_utc()
        await battle.asave(update_fields=["status", "finished_at"])
        await interaction.response.send_message("Lobby cancelled.", ephemeral=True)
        try:
            channel = self.bot.get_channel(battle.channel_id) or await self.bot.fetch_channel(battle.channel_id)
            message = await channel.fetch_message(battle.message_id)  # type: ignore[union-attr]
            await message.edit(content="🚫 This battle lobby was cancelled.", embed=None, view=None)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------
    # Battle launch
    # ------------------------------------------------------------------
    async def _launch_battle(self, battle: Battle, participants: list[BattleParticipant]) -> None:
        mode = await BattleMode.objects.aget(pk=battle.mode_id)
        snapshot = await modes.build_snapshot(mode)

        if mode.teams_enabled:
            # Assign per *user*, not per seat/ball — otherwise a player
            # fielding more than one ball could end up with their own
            # balls split across opposing teams.
            team_by_user: dict[int, int] = {}
            for participant in participants:
                if participant.user_id not in team_by_user:
                    team_by_user[participant.user_id] = len(team_by_user) % 2
                participant.team = team_by_user[participant.user_id]
                await participant.asave(update_fields=["team"])

        for participant in participants:
            stats = get_battle_stats(participant.ball_instance)
            participant.hp = stats.hp
            participant.max_hp = stats.hp
            participant.attack = stats.attack
            participant.defense = stats.defense
            participant.speed = stats.speed
            participant.momentum = 0
            participant.cooldowns = {}
            participant.heal_uses = 0
            participant.ability_uses = {}
            participant.is_alive = True
            await participant.asave()

        battle.status = Battle.Status.ACTIVE
        battle.current_turn = 0
        battle.config_snapshot = snapshot
        battle.state = {"effects": {}}
        battle.created_at = battle.created_at or helpers.now_utc()
        battle.last_action_at = helpers.now_utc()
        await battle.asave()

        await events.dispatch("before_battle", battle=battle)
        await self._run_passive_hooks(battle, participants, "battle_start")
        await battle.asave()

        user_display = {p.pk: f"<@{p.user_id}>" for p in participants}
        ball_by_participant = {p.pk: p.ball_instance for p in participants}
        embed = embeds.build_battle_embed(self.bot, battle, participants, ball_by_participant, user_display, {p.pk: False for p in participants})
        mentions = " ".join(f"<@{p.user_id}>" for p in participants)

        channel = self.bot.get_channel(battle.channel_id) or await self.bot.fetch_channel(battle.channel_id)
        try:
            old_message = await channel.fetch_message(battle.message_id)  # type: ignore[union-attr]
            await old_message.edit(content=mentions, embed=embed, view=None)
            message = old_message
        except discord.HTTPException:
            message = await channel.send(content=mentions, embed=embed)  # type: ignore[union-attr]
            battle.message_id = message.id
            await battle.asave(update_fields=["message_id"])

        await self._start_turn(battle, message)

    async def _start_turn(self, battle: Battle, message: discord.Message) -> None:
        snapshot = battle.config_snapshot or {}
        timer = snapshot.get("turn_timer_seconds", 15)
        participants = [p async for p in BattleParticipant.objects.filter(battle_id=battle.pk, is_alive=True, is_spectator=False)]
        participant_ids = [p.pk for p in participants]
        user_id_by_participant = {p.pk: p.user_id for p in participants}
        cooldowns = {p.pk: dict(p.cooldowns or {}) for p in participants}
        heal_uses = {p.pk: p.heal_uses for p in participants}

        async def on_turn_complete(view: BattleView) -> None:
            await self._resolve_turn(battle.pk, view, message)

        view = BattleView(
            battle=battle, participant_ids=participant_ids, user_id_by_participant=user_id_by_participant,
            cooldowns=cooldowns, heal_uses=heal_uses, on_turn_complete=on_turn_complete, timeout=timer,
        )
        view.message = message
        await message.edit(view=view)

    # ------------------------------------------------------------------
    # Turn resolution
    # ------------------------------------------------------------------
    async def _resolve_turn(self, battle_id: int, view: BattleView, message: discord.Message) -> None:
        battle = await Battle.objects.aget(pk=battle_id)
        if battle.status != Battle.Status.ACTIVE:
            return

        snapshot = battle.config_snapshot or {}
        all_participants = [p async for p in BattleParticipant.objects.select_related("ball_instance__ball").filter(battle_id=battle_id)]
        participants_by_id = {p.pk: p for p in all_participants}

        for surrendered_pid in view.surrendered_participant_ids:
            loser = participants_by_id.get(surrendered_pid)
            if loser is not None:
                loser.hp = 0
                loser.is_alive = False
                loser.surrendered = True
                await loser.asave(update_fields=["hp", "is_alive", "surrendered"])

        await events.dispatch("before_turn", battle=battle, turn=battle.current_turn)
        await self._run_passive_hooks(battle, all_participants, "before_turn")

        actions: dict[int, TurnAction] = {}
        timeout_behavior = snapshot.get("timeout_behavior", "auto_defend")
        enabled_actions = snapshot.get("enabled_actions") or [a.value for a in ACTIONS]
        for pid in view.participant_ids:
            chosen = view.choices.get(pid)
            target_id = view.target_choice.get(pid)
            if chosen is None and timeout_behavior == "random_action":
                candidates = [a for a in enabled_actions if a not in ("ability",)]
                try:
                    chosen = ActionKey(random.choice(candidates)) if candidates else ActionKey.DEFEND
                except ValueError:
                    chosen = ActionKey.DEFEND
                if chosen is ActionKey.ATTACK and target_id is None:
                    enemies = [e for e in helpers.enemies_of(all_participants, participants_by_id[pid]) if e.is_alive]
                    target_id = random.choice(enemies).pk if enemies else None
                    if target_id is None:
                        chosen = ActionKey.DEFEND
            elif chosen is None:
                # Covers both "auto_defend" and "skip_turn": the engine has
                # no concept of a true no-op turn, so a skipped turn simply
                # can't deal or block anything productive either way.
                chosen = ActionKey.DEFEND
            actions[pid] = TurnAction(action=chosen, target_id=target_id, ability_id=view.ability_choice.get(pid))

        currency_grants: list[tuple[int, int]] = []
        ability_used_this_turn: dict[int, Ability] = {}

        # Resolve active Ability actions first: they bypass the core
        # Attack/Defend/Counter/Heal/Dodge matrix and apply their script's
        # queued effects directly.
        for pid, act in actions.items():
            if act.action is not ActionKey.ABILITY or act.ability_id is None:
                continue
            participant = participants_by_id.get(pid)
            ability = await Ability.objects.filter(pk=act.ability_id, is_enabled=True).afirst()
            if participant is None or ability is None:
                continue
            try:
                ctx = self._build_ability_context(battle, all_participants, pid, "execute", actions=actions)
                if ctx is None:
                    log.warning("Could not build ability context for participant %s (ability %s); skipping activation.", pid, ability.pk)
                    continue
                await trigger_ability(ability, "execute", ctx)
                self._apply_ability_effects(battle, participants_by_id, ctx, currency_grants)
            except Exception:  # noqa: BLE001 - one broken ability activation must never stall the whole turn
                log.exception("Ability %s activation failed for participant %s; skipping.", ability.pk, pid)
                continue
            ability_used_this_turn[pid] = ability
            uses = dict(participant.ability_uses or {})
            uses[str(ability.pk)] = uses.get(str(ability.pk), 0) + 1
            participant.ability_uses = uses

        engine_participants: dict[int, ParticipantContext] = {}
        for pid in view.participant_ids:
            p = participants_by_id[pid]
            shield_hp, vulnerable_to, damage_taken_multiplier, flat_damage_bonus = self._read_status_effects(battle, pid)
            engine_participants[pid] = ParticipantContext(
                participant_id=pid, team=p.team, hp=p.hp, max_hp=p.max_hp,
                attack=p.attack, defense=p.defense, momentum=p.momentum,
                cooldowns=dict(p.cooldowns or {}), heal_uses=p.heal_uses, is_alive=p.is_alive,
                shield_hp=shield_hp, vulnerable_to=vulnerable_to, damage_taken_multiplier=damage_taken_multiplier,
                flat_damage_bonus=flat_damage_bonus,
            )

        result: TurnResult = resolve_turn(engine_participants, actions, snapshot)
        self._tick_status_effects(battle, result)

        for pid in view.participant_ids:
            if view.choices.get(pid) is None and timeout_behavior != "skip_turn":
                result.new_momentum[pid] = max(snapshot.get("momentum_min", -5), result.new_momentum[pid] - snapshot.get("afk_momentum_penalty", 1))

        for pid in view.participant_ids:
            participant = participants_by_id[pid]
            participant.hp = result.new_hp[pid]
            participant.momentum = result.new_momentum[pid]
            cooldowns = apply_cooldown_on_use(dict(result.new_cooldowns[pid]), actions[pid].action, snapshot)
            if pid in ability_used_this_turn:
                # Set *after* the tick above, so a freshly-used ability's
                # cooldown isn't immediately decremented the same turn.
                cooldowns["ability"] = ability_used_this_turn[pid].cooldown_turns
            participant.cooldowns = cooldowns
            participant.heal_uses = result.new_heal_uses[pid]
            participant.is_alive = participant.hp > 0
            await participant.asave()

        battle.current_turn += 1
        battle.last_action_at = helpers.now_utc()

        await BattleTurn.objects.acreate(
            battle=battle, turn_number=battle.current_turn - 1,
            actions={str(pid): {"action": act.action.value, "target_id": act.target_id, "ability_id": act.ability_id} for pid, act in actions.items()},
            result={str(pid): _outcome_dict(o) for pid, o in result.outcomes.items()},
            created_at=helpers.now_utc(),
        )

        await events.dispatch("after_turn", battle=battle, turn=battle.current_turn - 1, result=result)
        await self._run_passive_hooks(battle, all_participants, "after_turn")
        for pid, outcome in result.outcomes.items():
            if outcome.action is ActionKey.ATTACK:
                await self._run_passive_hooks(battle, all_participants, "on_attack", only_pids={pid})
            if outcome.action is ActionKey.DEFEND:
                await self._run_passive_hooks(battle, all_participants, "on_defend", only_pids={pid})
            if outcome.damage_taken > 0:
                await self._run_passive_hooks(battle, all_participants, "on_damage_taken", only_pids={pid})

        for user_id, amount in currency_grants:
            await award_currency(self.bot, user_id, amount, reason="battle-ability")

        turn_limit_hit = resolve_max_turns_draw(battle.current_turn, snapshot.get("max_turns", 30))
        living_teams = alive_teams(engine_participants, result.new_hp)
        battle_finished = turn_limit_hit or len(living_teams) <= 1

        summary_lines = [
            helpers.format_action_summary(f"<@{participants_by_id[pid].user_id}>", ACTIONS[act.action].label, ACTIONS[act.action].emoji, result.outcomes[pid].notes if pid in result.outcomes else [])
            for pid, act in actions.items()
        ]
        summary = "\n".join(summary_lines)

        if battle_finished:
            battle.status = Battle.Status.FINISHED
            battle.finished_at = helpers.now_utc()
            if len(living_teams) == 1:
                winner_marker = next(iter(living_teams))
                if snapshot.get("teams_enabled"):
                    battle.winner_team = winner_marker
                else:
                    battle.winner_participant_id = winner_marker
            await battle.asave()
            await self._finish_battle(battle, all_participants, message, summary)
        else:
            await battle.asave()
            user_display = {p.pk: f"<@{p.user_id}>" for p in all_participants}
            ball_by_participant = {p.pk: p.ball_instance for p in all_participants}
            embed = embeds.build_battle_embed(self.bot, battle, all_participants, ball_by_participant, user_display, {p.pk: False for p in all_participants}, last_turn_summary=summary)
            await message.edit(embed=embed)
            await self._start_turn(battle, message)

    async def _finish_battle(self, battle: Battle, participants: list[BattleParticipant], message: discord.Message, summary: str) -> None:
        reward_profile = await get_active_reward_profile()
        snapshot = battle.config_snapshot or {}
        win_mult = snapshot.get("win_multiplier", 1.0)
        loss_mult = snapshot.get("loss_multiplier", 1.0)
        draw_mult = snapshot.get("draw_multiplier", 1.0)
        streak_mult = snapshot.get("streak_multiplier", 1.0)

        winners: list[BattleParticipant] = []
        losers: list[BattleParticipant] = []
        if battle.winner_team is not None:
            winners = [p for p in participants if p.team == battle.winner_team and not p.is_spectator]
            losers = [p for p in participants if p.team != battle.winner_team and not p.is_spectator]
        elif battle.winner_participant_id is not None:
            winners = [p for p in participants if p.pk == battle.winner_participant_id]
            losers = [p for p in participants if p.pk != battle.winner_participant_id and not p.is_spectator]

        reward_lines = []
        if winners:
            for winner in winners:
                streak_bonus = await self._compute_streak_bonus(winner.pk, reward_profile, streak_mult)
                total = int(round(reward_profile.win_reward * win_mult)) + streak_bonus
                awarded = await award_currency(self.bot, winner.user_id, total, reason="battle-win")
                if awarded:
                    reward_lines.append(f"<@{winner.user_id}>: +{total}")
                await self._run_passive_hooks(battle, participants, "on_win", only_pids={winner.pk})
            for loser in losers:
                total = int(round(reward_profile.loss_reward * loss_mult))
                awarded = await award_currency(self.bot, loser.user_id, total, reason="battle-loss")
                if awarded:
                    reward_lines.append(f"<@{loser.user_id}>: +{total}")
                await self._run_passive_hooks(battle, participants, "on_loss", only_pids={loser.pk})
        else:
            total = int(round(reward_profile.draw_reward * draw_mult))
            for participant in participants:
                if participant.is_spectator:
                    continue
                if await award_currency(self.bot, participant.user_id, total, reason="battle-draw"):
                    reward_lines.append(f"<@{participant.user_id}>: +{total}")

        winner_text = None
        if winners:
            winner_text = " & ".join(f"<@{w.user_id}>" for w in winners)

        user_display = {p.pk: f"<@{p.user_id}>" for p in participants}
        ball_by_participant = {p.pk: p.ball_instance for p in participants}
        embed = embeds.build_result_embed(battle, participants, ball_by_participant, user_display, winner_text=winner_text, reward_text="\n".join(reward_lines) or None)

        rematch_view = None
        mode = await BattleMode.objects.aget(pk=battle.mode_id)
        if mode.allow_rematch:
            fighters = [p for p in participants if not p.is_spectator]

            async def on_rematch(rematch_interaction: discord.Interaction) -> None:
                if rematch_interaction.user.id not in {p.user_id for p in fighters}:
                    await rematch_interaction.response.send_message("Only battle participants can request a rematch.", ephemeral=True)
                    return
                await rematch_interaction.response.send_message("Starting a rematch lobby...", ephemeral=True)
                new_battle = await Battle.objects.acreate(
                    mode=mode, guild_id=rematch_interaction.guild_id or battle.guild_id, channel_id=rematch_interaction.channel_id or battle.channel_id,
                    status=Battle.Status.LOBBY, state={"invited_user_ids": [p.user_id for p in fighters]},
                    created_at=helpers.now_utc(), last_action_at=helpers.now_utc(),
                )
                for i, p in enumerate(fighters):
                    await BattleParticipant.objects.acreate(battle=new_battle, user_id=p.user_id, ball_instance=p.ball_instance, join_order=i)
                lobby_embed = embeds.build_lobby_embed(mode, [f"<@{p.user_id}> — {ball_display_name(p.ball_instance)}" for p in fighters])
                lobby_view = LobbyView(
                    battle_id=new_battle.pk,
                    on_begin=lambda i: self._handle_begin(i, new_battle.pk),
                    on_cancel=lambda i: self._handle_cancel_lobby(i, new_battle.pk),
                )
                mentions = " ".join(f"<@{p.user_id}>" for p in fighters)
                new_message = await rematch_interaction.channel.send(content=f"{mentions} Rematch! Everyone kept their ball — use `/battle begin` when ready.", embed=lobby_embed, view=lobby_view)  # type: ignore[union-attr]
                new_battle.message_id = new_message.id
                await new_battle.asave(update_fields=["message_id"])

            rematch_view = discord.ui.View(timeout=120.0)
            button = RematchButton()
            button.callback = on_rematch  # type: ignore[method-assign]
            rematch_view.add_item(button)

        await message.edit(content=summary, embed=embed, view=rematch_view)
        await events.dispatch("after_battle", battle=battle)

    async def _compute_streak_bonus(self, participant_pk: int, reward_profile, streak_multiplier: float) -> int:
        me = await BattleParticipant.objects.select_related("battle").aget(pk=participant_pk)
        recent = [
            p async for p in BattleParticipant.objects.filter(
                user_id=me.user_id, battle__status=Battle.Status.FINISHED,
            ).select_related("battle").order_by("-battle__finished_at")[:20]
        ]
        streak = 0
        for p in recent:
            if p.battle.winner_participant_id == p.pk or (p.battle.winner_team is not None and p.battle.winner_team == p.team):
                streak += 1
            else:
                break
        if streak >= reward_profile.win_streak_threshold:
            return int(round(reward_profile.win_streak_bonus * streak_multiplier * (streak - reward_profile.win_streak_threshold + 1)))
        return 0

    # ------------------------------------------------------------------
    # Ability hook plumbing
    # ------------------------------------------------------------------
    def _build_ability_context(
        self, battle: Battle, participants: list[BattleParticipant], participant_id: int, hook_name: str,
        *, actions: dict[int, TurnAction] | None = None,
    ) -> AbilityContext | None:
        me = next((p for p in participants if p.pk == participant_id), None)
        if me is None:
            log.warning("Ability hook %r requested for participant %s, but they aren't in the roster passed in.", hook_name, participant_id)
            return None

        teammates = helpers.teammates_of(participants, me)
        enemies = helpers.enemies_of(participants, me)
        opponent = enemies[0] if enemies else None

        def _state(p: BattleParticipant | None) -> dict:
            if p is None:
                return {"hp": 0, "momentum": 0}
            state = {
                "hp": p.hp,
                "max_hp": p.max_hp,
                "momentum": p.momentum,
                "participant_id": p.pk,
                "name": ball_display_name(p.ball_instance),
                "attack": p.attack,
                "defense": p.defense,
                "team": p.team,
            }
            # Only populated when called from the active-ability execution
            # path (where every player's choice for this turn is already
            # locked in) — lets a "reactive" ability check what an
            # opponent is actually doing/targeting this turn, e.g. a parry
            # that only triggers against an attack aimed at *them*.
            if actions is not None and p.pk in actions:
                act = actions[p.pk]
                state["action"] = act.action.value
                state["target_id"] = act.target_id
            return state

        return AbilityContext(
            battle_id=battle.pk, turn_number=battle.current_turn, self_side=str(participant_id),
            opponent_side=str(opponent.pk) if opponent else "",
            self_state=_state(me), opponent_state=_state(opponent),
            hook_name=hook_name,
            teammates=[_state(t) for t in teammates], enemies=[_state(e) for e in enemies],
            self_economy=str(ball_economy_id(me.ball_instance)) if ball_economy_id(me.ball_instance) else None,
            self_regime=str(ball_regime_id(me.ball_instance)) if ball_regime_id(me.ball_instance) else None,
        )

    async def _run_passive_hooks(self, battle: Battle, participants: list[BattleParticipant], hook_name: str, *, only_pids: set[int] | None = None) -> None:
        currency_grants: list[tuple[int, int]] = []
        participants_by_id = {p.pk: p for p in participants}
        acting = [p for p in participants if only_pids is None or p.pk in only_pids]

        for participant in acting:
            if participant.is_spectator or not participant.is_alive:
                continue
            ball = participant.ball_instance.countryball
            abilities = await get_usable_abilities(
                ball, ball_rarity(participant.ball_instance), ball_regime_id(participant.ball_instance),
                ball_economy_id(participant.ball_instance), battle.mode_id, trigger_type=Ability.TriggerType.PASSIVE,
            )
            for ability in abilities:
                key = f"passive:{ability.pk}"
                cooldown_remaining = (participant.cooldowns or {}).get(key, 0)
                uses_so_far = (participant.ability_uses or {}).get(str(ability.pk), 0)
                if cooldown_remaining > 0 or uses_so_far >= ability.uses_per_battle:
                    continue
                ctx = self._build_ability_context(battle, participants, participant.pk, hook_name)
                if ctx is None:
                    continue
                fired = await trigger_ability(ability, hook_name, ctx)
                if not fired:
                    continue
                self._apply_ability_effects(battle, participants_by_id, ctx, currency_grants)
                cooldowns = dict(participant.cooldowns or {})
                cooldowns[key] = ability.cooldown_turns
                participant.cooldowns = cooldowns
                uses = dict(participant.ability_uses or {})
                uses[str(ability.pk)] = uses_so_far + 1
                participant.ability_uses = uses
                await participant.asave(update_fields=["cooldowns", "ability_uses"])

        for participant in participants_by_id.values():
            await participant.asave()

        for user_id, amount in currency_grants:
            await award_currency(self.bot, user_id, amount, reason=f"ability-{hook_name}")

    def _read_status_effects(self, battle: Battle, participant_id: int) -> tuple[int, dict[int, float], float, int]:
        """Collapse a participant's active `Battle.state["effects"]` entries
        into what `engine.ParticipantContext` actually understands: total
        shield HP, a per-attacker vulnerability multiplier map, a generic
        (attacker-agnostic) incoming-damage multiplier, and a flat bonus to
        this participant's own outgoing Attack damage.

        Effect dict shape, as authored by ability scripts via
        `ctx.add_effect({...})`:
          - {"kind": "shield", "amount": <flat HP to absorb>, "duration": <turns>}
          - {"kind": "vulnerable", "amount": <multiplier>, "duration": <turns>,
             "source_participant_id": <int, optional>}
            Omitting `source_participant_id` makes it apply to damage from
            any attacker; setting it restricts the multiplier to hits from
            that one specific participant.
          - {"kind": "protected", "amount": <multiplier, e.g. 0.0-1.0>, "duration": <turns>}
          - {"kind": "attack_buff", "amount": <flat bonus, e.g. 200>, "duration": <turns>}
            A temporary flat addition to this participant's own Attack
            damage (e.g. a "rage mode") — still subject to the target's
            Defend/Counter/Dodge, unlike a permanent `modify_stat` change.
        """
        effects = ((battle.state or {}).get("effects", {})).get(str(participant_id), [])
        shield_hp = 0
        vulnerable_to: dict[int, float] = {}
        damage_taken_multiplier = 1.0
        flat_damage_bonus = 0

        for effect in effects:
            if effect.get("duration", 0) <= 0:
                continue
            kind = effect.get("kind")
            if kind == "shield":
                shield_hp += max(0, int(effect.get("amount", 0)))
            elif kind == "vulnerable":
                multiplier = float(effect.get("amount", 1.0))
                source = effect.get("source_participant_id")
                if source is not None:
                    vulnerable_to[int(source)] = vulnerable_to.get(int(source), 1.0) * multiplier
                else:
                    damage_taken_multiplier *= multiplier
            elif kind == "protected":
                damage_taken_multiplier *= float(effect.get("amount", 1.0))
            elif kind == "attack_buff":
                flat_damage_bonus += int(effect.get("amount", 0))

        return shield_hp, vulnerable_to, damage_taken_multiplier, flat_damage_bonus

    def _tick_status_effects(self, battle: Battle, result: TurnResult) -> None:
        """Decrement every active effect's remaining duration by one turn,
        collapse shield effects down to whatever the engine reports was
        actually left after this turn's damage, and drop anything expired
        or fully depleted.
        """
        state = battle.state or {}
        effects_state = dict(state.get("effects", {}))

        for pid_str in list(effects_state.keys()):
            pid = int(pid_str)
            effects = effects_state.get(pid_str, [])
            shield_effects = [e for e in effects if e.get("kind") == "shield"]
            other_effects = [e for e in effects if e.get("kind") != "shield"]

            updated: list[dict] = []

            remaining_shield = result.new_shield_hp.get(pid)
            if shield_effects and remaining_shield:
                longest_duration = max((e.get("duration", 1) for e in shield_effects), default=1)
                new_duration = longest_duration - 1
                if new_duration > 0 and remaining_shield > 0:
                    updated.append({"kind": "shield", "amount": remaining_shield, "duration": new_duration})

            for effect in other_effects:
                new_duration = effect.get("duration", 1) - 1
                if new_duration <= 0:
                    continue
                effect = dict(effect)
                effect["duration"] = new_duration
                updated.append(effect)

            if updated:
                effects_state[pid_str] = updated
            else:
                effects_state.pop(pid_str, None)

        state["effects"] = effects_state
        battle.state = state

    def _apply_ability_effects(self, battle: Battle, participants_by_id: dict[int, BattleParticipant], ctx: AbilityContext, currency_grants: list[tuple[int, int]]) -> None:
        state = battle.state or {}
        effects_state = state.setdefault("effects", {})

        def _resolve_target(side: str) -> BattleParticipant | None:
            if side == "self":
                side = ctx.self_side
            elif side == "opponent":
                side = ctx.opponent_side
            if not side or not side.isdigit():
                return None
            return participants_by_id.get(int(side))

        for queued in ctx.effects:
            target = _resolve_target(queued.target)
            if target is None:
                continue

            if queued.kind == "damage":
                target.hp = max(0, target.hp - int(queued.payload.get("amount", 0)))
            elif queued.kind == "heal":
                target.hp = min(target.max_hp, target.hp + int(queued.payload.get("amount", 0)))
            elif queued.kind == "momentum":
                snapshot = battle.config_snapshot or {}
                lo, hi = snapshot.get("momentum_min", -5), snapshot.get("momentum_max", 5)
                target.momentum = max(lo, min(hi, target.momentum + int(queued.payload.get("amount", 0))))
            elif queued.kind == "modify_stat":
                # See README: multi-turn percentage buffs are an extension
                # point (attack_pct/defense_pct apply permanently, with no
                # duration tracking). max_hp_flat below is a genuine
                # permanent change by design — it grows the participant's
                # actual HP pool, current HP included, not just a cap.
                stat = str(queued.payload.get("stat", ""))
                amount = float(queued.payload.get("amount", 0))
                if stat == "attack_pct":
                    target.attack = max(1, int(round(target.attack * (1 + amount))))
                elif stat == "defense_pct":
                    target.defense = max(1, int(round(target.defense * (1 + amount))))
                elif stat == "max_hp_flat":
                    increase = int(amount)
                    target.max_hp = max(1, target.max_hp + increase)
                    target.hp = max(0, target.hp + increase)
            elif queued.kind == "status":
                effects_state.setdefault(str(target.pk), []).append(queued.payload.get("effect", {}))
            elif queued.kind == "silence":
                # Blocks the Ability action specifically, by directly
                # setting the target's own "ability" cooldown — the same
                # field/check the turn-selection UI already uses for
                # Counter/Dodge/Ability cooldowns, so no new UI plumbing
                # is needed for a "can't use their ability" trap.
                duration = max(1, int(queued.payload.get("duration", 1)))
                cooldowns = dict(target.cooldowns or {})
                cooldowns["ability"] = max(cooldowns.get("ability", 0), duration)
                target.cooldowns = cooldowns
            elif queued.kind == "cripple":
                # Same trick as "silence", but blocks Attack instead of
                # Ability (see the "attack" cooldown_field added to
                # ActionKey.ATTACK's definition in actions.py).
                duration = max(1, int(queued.payload.get("duration", 1)))
                cooldowns = dict(target.cooldowns or {})
                cooldowns["attack"] = max(cooldowns.get("attack", 0), duration)
                target.cooldowns = cooldowns
            elif queued.kind == "currency":
                currency_grants.append((target.user_id, int(queued.payload.get("amount", 0))))

        state["effects"] = effects_state
        battle.state = state

    async def notify_battle_expired(self, battle: Battle) -> None:
        try:
            channel = self.bot.get_channel(battle.channel_id) or await self.bot.fetch_channel(battle.channel_id)
            if battle.message_id is None:
                return
            message = await channel.fetch_message(battle.message_id)  # type: ignore[union-attr]
            await message.edit(content="⌛ This battle has expired due to inactivity.", embed=None, view=None)
        except discord.HTTPException:
            log.warning("Could not notify channel that battle %s expired.", battle.pk)


def _outcome_dict(outcome) -> dict:
    return {
        "action": outcome.action.value,
        "damage_dealt": outcome.damage_dealt,
        "damage_taken": outcome.damage_taken,
        "healed": outcome.healed,
        "momentum_delta": outcome.momentum_delta,
        "was_blocked": outcome.was_blocked,
        "was_dodged": outcome.was_dodged,
        "was_countered": outcome.was_countered,
        "was_interrupted": outcome.was_interrupted,
        "is_critical": outcome.is_critical,
        "notes": outcome.notes,
    }
