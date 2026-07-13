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
from .config import get_active_reward_profile
from .engine import ParticipantContext, TurnAction, TurnResult, apply_cooldown_on_use, alive_teams, resolve_max_turns_draw, resolve_turn
from .integrations import events
from .integrations.balls import ball_display_name, ball_economy_id, ball_rarity, ball_regime_id, get_battle_stats
from .integrations.currency import award_currency
from .tasks import ExpirationSweeper
from .buttons import RematchButton
from .views import BattleView, ChallengeView, LobbyView

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("battles.cog")


class BattlesCog(commands.Cog):
    """Interactive, button-driven PvP battles between BallsDex players,
    played under dashboard-authored modes and abilities.
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
    battle_group = app_commands.Group(name="battle", description="Challenge other players to a ball battle.")

    @battle_group.command(name="start", description="Start a battle. The mode you pick supplies deck size, timers, rules, and rewards.")
    @app_commands.describe(mode="Which battle mode to play", opponent="Challenge a specific player directly (Duel-style modes only)", ball_id="The ID of your ball")
    async def start(self, interaction: discord.Interaction, mode: str, ball_id: int, opponent: discord.Member | None = None) -> None:
        battle_mode = await modes.get_mode_by_name(mode)
        if battle_mode is None:
            available = await modes.list_available_modes()
            names = ", ".join(m.name for m in available) or "none configured"
            await interaction.response.send_message(f"No enabled mode named '{mode}'. Available modes: {names}", ephemeral=True)
            return

        ball_instance = await self._get_owned_ball_instance(interaction.user.id, ball_id)
        if ball_instance is None:
            await interaction.response.send_message("You don't own a ball with that ID.", ephemeral=True)
            return

        deck_error = await modes.validate_deck(battle_mode, [ball_instance])
        if deck_error is not None:
            await interaction.response.send_message(deck_error.reason, ephemeral=True)
            return

        if opponent is not None:
            if battle_mode.max_players != 2 or battle_mode.teams_enabled:
                await interaction.response.send_message(
                    f"'{battle_mode.name}' supports up to {battle_mode.max_players} players — use the lobby instead of challenging one opponent directly.",
                    ephemeral=True,
                )
                return
            await self._start_direct_challenge(interaction, battle_mode, opponent, ball_instance)
            return

        await self._start_lobby(interaction, battle_mode, ball_instance)

    @battle_group.command(name="stats", description="View a ball's battle stats.")
    @app_commands.describe(ball_id="The ID of your ball")
    async def stats(self, interaction: discord.Interaction, ball_id: int) -> None:
        ball_instance = await self._get_owned_ball_instance(interaction.user.id, ball_id)
        if ball_instance is None:
            await interaction.response.send_message("You don't own a ball with that ID.", ephemeral=True)
            return
        stats = await get_battle_stats(ball_instance.ball)
        await interaction.response.send_message(
            f"**{ball_display_name(ball_instance)}**\n"
            f"HP: {stats.hp} · Attack: {stats.attack} · Defense: {stats.defense} · "
            f"Speed: {stats.speed} · Battle Power: {stats.battle_power}",
            ephemeral=True,
        )

    @battle_group.command(name="modes", description="List available battle modes.")
    async def list_modes(self, interaction: discord.Interaction) -> None:
        available = await modes.list_available_modes()
        if not available:
            await interaction.response.send_message("No battle modes are currently enabled.", ephemeral=True)
            return
        lines = [f"{m.icon} **{m.name}** — {m.description or 'No description.'}" for m in available]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ------------------------------------------------------------------
    # Ball / lobby helpers
    # ------------------------------------------------------------------
    async def _get_owned_ball_instance(self, user_id: int, ball_id: int):
        from ballsdex.core.models import BallInstance

        return await BallInstance.objects.select_related("ball").filter(
            pk=ball_id, player__discord_id=user_id,
        ).afirst()

    async def _prompt_ball_pick(self, interaction: discord.Interaction, mode: BattleMode):
        from ballsdex.core.models import BallInstance

        balls = [
            b async for b in BallInstance.objects.select_related("ball").filter(
                player__discord_id=interaction.user.id
            ).order_by("-catch_date")[:25]
        ]
        if not balls:
            await interaction.followup.send("You don't own any balls to battle with.", ephemeral=True)
            return None

        options = [discord.SelectOption(label=ball_display_name(b), value=str(b.pk)) for b in balls]
        select = discord.ui.Select(placeholder="Choose your ball...", options=options)
        picked: dict[str, int] = {}

        async def select_callback(select_interaction: discord.Interaction) -> None:
            picked["id"] = int(select.values[0])
            await select_interaction.response.edit_message(content="Ball selected!", view=None)
            view.stop()

        select.callback = select_callback  # type: ignore[method-assign]
        view = discord.ui.View(timeout=60.0)
        view.add_item(select)
        await interaction.followup.send("Pick your ball:", view=view, ephemeral=True)
        await view.wait()

        if "id" not in picked:
            return None
        chosen = next((b for b in balls if b.pk == picked["id"]), None)
        if chosen is None:
            return None
        error = await modes.validate_deck(mode, [chosen])
        if error is not None:
            await interaction.followup.send(error.reason, ephemeral=True)
            return None
        return chosen

    # ------------------------------------------------------------------
    # Direct challenge (Duel-style, two players)
    # ------------------------------------------------------------------
    async def _start_direct_challenge(self, interaction: discord.Interaction, mode: BattleMode, opponent: discord.Member, ball_instance) -> None:
        if opponent.bot:
            await interaction.response.send_message("You can't battle a bot.", ephemeral=True)
            return
        if opponent.id == interaction.user.id:
            await interaction.response.send_message("You can't battle yourself.", ephemeral=True)
            return

        async def on_accept(accept_interaction: discord.Interaction) -> None:
            await accept_interaction.response.edit_message(content=f"{opponent.mention} accepted!", embed=None, view=None)
            opponent_ball = await self._prompt_ball_pick(accept_interaction, mode)
            if opponent_ball is None:
                return
            await self._create_and_launch_battle(
                accept_interaction, mode,
                [(interaction.user, ball_instance, None), (opponent, opponent_ball, None)],
            )

        async def on_decline(decline_interaction: discord.Interaction) -> None:
            await decline_interaction.response.edit_message(content=f"{opponent.mention} declined the battle.", embed=None, view=None)

        view = ChallengeView(challenger_id=interaction.user.id, opponent_id=opponent.id, on_accept=on_accept, on_decline=on_decline, timeout=60.0)
        embed = embeds.build_challenge_embed(interaction.user.display_name, opponent.display_name, ball_instance, expires_in_seconds=60)
        await interaction.response.send_message(content=opponent.mention, embed=embed, view=view)

    # ------------------------------------------------------------------
    # Lobby (Free For All / Team Battle / any 3+ player mode)
    # ------------------------------------------------------------------
    async def _start_lobby(self, interaction: discord.Interaction, mode: BattleMode, ball_instance) -> None:
        battle = await Battle.objects.acreate(
            mode=mode, guild_id=interaction.guild_id or 0, channel_id=interaction.channel_id or 0,
            status=Battle.Status.LOBBY, created_at=helpers.now_utc(), last_action_at=helpers.now_utc(),
        )
        await BattleParticipant.objects.acreate(
            battle=battle, user_id=interaction.user.id, ball_instance=ball_instance, join_order=0,
        )

        seats: dict[int, tuple[discord.abc.User, object]] = {interaction.user.id: (interaction.user, ball_instance)}

        async def on_join(join_interaction: discord.Interaction) -> None:
            if join_interaction.user.id in seats:
                await join_interaction.response.send_message("You're already in this lobby.", ephemeral=True)
                return
            current_count = await BattleParticipant.objects.filter(battle_id=battle.pk).acount()
            if current_count >= mode.max_players:
                await join_interaction.response.send_message("This lobby is full.", ephemeral=True)
                return
            await join_interaction.response.send_message("Pick your ball to join:", ephemeral=True)
            picked = await self._prompt_ball_pick(join_interaction, mode)
            if picked is None:
                return
            join_order = await BattleParticipant.objects.filter(battle_id=battle.pk).acount()
            await BattleParticipant.objects.acreate(battle=battle, user_id=join_interaction.user.id, ball_instance=picked, join_order=join_order)
            seats[join_interaction.user.id] = (join_interaction.user, picked)
            await self._refresh_lobby_message(message, mode, seats)

        async def on_leave(leave_interaction: discord.Interaction) -> None:
            if leave_interaction.user.id not in seats or leave_interaction.user.id == interaction.user.id:
                await leave_interaction.response.send_message("You can't leave (either you're not in, or you're the host).", ephemeral=True)
                return
            await BattleParticipant.objects.filter(battle_id=battle.pk, user_id=leave_interaction.user.id).adelete()
            del seats[leave_interaction.user.id]
            await leave_interaction.response.send_message("You left the lobby.", ephemeral=True)
            await self._refresh_lobby_message(message, mode, seats)

        async def on_start(start_interaction: discord.Interaction) -> None:
            if len(seats) < mode.min_players:
                await start_interaction.response.send_message(f"Need at least {mode.min_players} players to start.", ephemeral=True)
                return
            await start_interaction.response.edit_message(content="Starting battle...", embed=None, view=None)
            entries = [(user, ball, None) for user, ball in seats.values()]
            await self._create_and_launch_battle(start_interaction, mode, entries, existing_battle=battle)

        view = LobbyView(mode=mode, host_id=interaction.user.id, on_join=on_join, on_leave=on_leave, on_start=on_start)
        embed = embeds.build_lobby_embed(mode, [interaction.user.display_name])
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        battle.message_id = message.id
        await battle.asave(update_fields=["message_id"])

    async def _refresh_lobby_message(self, message: discord.Message, mode: BattleMode, seats: dict) -> None:
        embed = embeds.build_lobby_embed(mode, [user.display_name for user, _ in seats.values()])
        await message.edit(embed=embed)

    # ------------------------------------------------------------------
    # Battle launch
    # ------------------------------------------------------------------
    async def _create_and_launch_battle(
        self, interaction: discord.Interaction, mode: BattleMode,
        entries: list[tuple[discord.abc.User, object, int | None]],
        *, existing_battle: Battle | None = None,
    ) -> None:
        snapshot = await modes.build_snapshot(mode)

        if existing_battle is not None:
            battle = existing_battle
        else:
            battle = await Battle.objects.acreate(
                mode=mode, guild_id=interaction.guild_id or 0, channel_id=interaction.channel_id or 0,
                status=Battle.Status.LOBBY, created_at=helpers.now_utc(), last_action_at=helpers.now_utc(),
            )
            for join_order, (user, ball_instance, _team) in enumerate(entries):
                await BattleParticipant.objects.acreate(battle=battle, user_id=user.id, ball_instance=ball_instance, join_order=join_order)

        participants = [p async for p in BattleParticipant.objects.select_related("ball_instance__ball").filter(battle_id=battle.pk).order_by("join_order")]
        users_by_id = {user.id: user for user, _ball, _team in entries}

        if mode.teams_enabled:
            for i, participant in enumerate(participants):
                participant.team = i % 2
                await participant.asave(update_fields=["team"])

        for participant in participants:
            stats = await get_battle_stats(participant.ball_instance.ball)
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

        user_display = {p.pk: (users_by_id.get(p.user_id).display_name if p.user_id in users_by_id else f"<@{p.user_id}>") for p in participants}
        ball_by_participant = {p.pk: p.ball_instance for p in participants}
        embed = embeds.build_battle_embed(
            self.bot, battle, participants, ball_by_participant, user_display,
            {p.pk: False for p in participants},
        )
        mentions = " ".join(f"<@{p.user_id}>" for p in participants)
        channel = interaction.channel
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

        async def on_turn_complete(view: BattleView) -> None:
            await self._resolve_turn(battle.pk, view, message)

        view = BattleView(battle=battle, participant_ids=participant_ids, user_id_by_participant=user_id_by_participant, on_turn_complete=on_turn_complete, timeout=timer)
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

        if view.surrendered_participant_id is not None:
            loser = participants_by_id.get(view.surrendered_participant_id)
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
                chosen = ActionKey(random.choice(candidates)) if candidates else ActionKey.DEFEND
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
            ctx = self._build_ability_context(battle, all_participants, pid, "execute")
            await trigger_ability(ability, "execute", ctx)
            self._apply_ability_effects(battle, participants_by_id, ctx, currency_grants)
            participant.cooldowns["ability"] = ability.cooldown_turns
            uses = participant.ability_uses or {}
            uses[str(ability.pk)] = uses.get(str(ability.pk), 0) + 1
            participant.ability_uses = uses

        engine_participants: dict[int, ParticipantContext] = {}
        for pid in view.participant_ids:
            p = participants_by_id[pid]
            engine_participants[pid] = ParticipantContext(
                participant_id=pid, team=p.team, hp=p.hp, max_hp=p.max_hp,
                attack=p.attack, defense=p.defense, momentum=p.momentum,
                cooldowns=dict(p.cooldowns or {}), heal_uses=p.heal_uses, is_alive=p.is_alive,
            )

        result: TurnResult = resolve_turn(engine_participants, actions, snapshot)

        for pid in view.participant_ids:
            if view.choices.get(pid) is None and timeout_behavior != "skip_turn":
                result.new_momentum[pid] = max(snapshot.get("momentum_min", -5), result.new_momentum[pid] - snapshot.get("afk_momentum_penalty", 1))

        for pid in view.participant_ids:
            participant = participants_by_id[pid]
            participant.hp = result.new_hp[pid]
            participant.momentum = result.new_momentum[pid]
            participant.cooldowns = apply_cooldown_on_use(dict(result.new_cooldowns[pid]), actions[pid].action, snapshot)
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
                await self._run_passive_hooks(battle, [participants_by_id[pid]], "on_attack")
            if outcome.action is ActionKey.DEFEND:
                await self._run_passive_hooks(battle, [participants_by_id[pid]], "on_defend")
            if outcome.damage_taken > 0:
                await self._run_passive_hooks(battle, [participants_by_id[pid]], "on_damage_taken")

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
                    battle.winner_team = winner_marker if isinstance(winner_marker, int) else None
                else:
                    battle.winner_participant_id = winner_marker if isinstance(winner_marker, int) else None
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
        else:
            losers = []  # draw: everyone gets the draw reward below

        reward_lines = []
        if winners:
            for winner in winners:
                streak_bonus = await self._compute_streak_bonus(winner.user_id, reward_profile, streak_mult)
                total = int(round(reward_profile.win_reward * win_mult)) + streak_bonus
                await award_currency(self.bot, winner.user_id, total, reason="battle-win")
                reward_lines.append(f"<@{winner.user_id}>: +{total}")
                await self._run_passive_hooks(battle, [winner], "on_win")
            for loser in losers:
                total = int(round(reward_profile.loss_reward * loss_mult))
                await award_currency(self.bot, loser.user_id, total, reason="battle-loss")
                reward_lines.append(f"<@{loser.user_id}>: +{total}")
                await self._run_passive_hooks(battle, [loser], "on_loss")
        else:
            total = int(round(reward_profile.draw_reward * draw_mult))
            for participant in participants:
                if participant.is_spectator:
                    continue
                await award_currency(self.bot, participant.user_id, total, reason="battle-draw")
            reward_lines.append(f"Everyone: +{total}")

        winner_text = None
        if winners:
            winner_text = " & ".join(f"<@{w.user_id}>" for w in winners)

        user_display = {p.pk: f"<@{p.user_id}>" for p in participants}
        ball_by_participant = {p.pk: p.ball_instance for p in participants}
        embed = embeds.build_result_embed(battle, participants, ball_by_participant, user_display, winner_text=winner_text, reward_text="\n".join(reward_lines))

        rematch_view = None
        mode = await BattleMode.objects.aget(pk=battle.mode_id)
        if mode.allow_rematch:
            fighters = [p for p in participants if not p.is_spectator]

            async def on_rematch(rematch_interaction: discord.Interaction) -> None:
                if rematch_interaction.user.id not in {p.user_id for p in fighters}:
                    await rematch_interaction.response.send_message("Only battle participants can request a rematch.", ephemeral=True)
                    return
                await rematch_interaction.response.send_message("Starting a rematch...", ephemeral=True)
                entries = []
                for p in fighters:
                    member = rematch_interaction.guild.get_member(p.user_id) if rematch_interaction.guild else None
                    user = member or self.bot.get_user(p.user_id) or await self.bot.fetch_user(p.user_id)
                    entries.append((user, p.ball_instance, None))
                await self._create_and_launch_battle(rematch_interaction, mode, entries)

            rematch_view = discord.ui.View(timeout=120.0)
            button = RematchButton()
            button.callback = on_rematch  # type: ignore[method-assign]
            rematch_view.add_item(button)

        await message.edit(content=summary, embed=embed, view=rematch_view)
        await events.dispatch("after_battle", battle=battle)

    async def _compute_streak_bonus(self, user_id: int, reward_profile, streak_multiplier: float) -> int:
        recent = [
            p async for p in BattleParticipant.objects.filter(
                user_id=user_id, battle__status=Battle.Status.FINISHED,
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
    def _build_ability_context(self, battle: Battle, participants: list[BattleParticipant], participant_id: int, hook_name: str) -> AbilityContext:
        me = next(p for p in participants if p.pk == participant_id)
        teammates = helpers.teammates_of(participants, me)
        enemies = helpers.enemies_of(participants, me)
        opponent = enemies[0] if enemies else None

        def _state(p: BattleParticipant | None) -> dict:
            if p is None:
                return {"hp": 0, "momentum": 0}
            return {"hp": p.hp, "momentum": p.momentum, "participant_id": p.pk}

        return AbilityContext(
            battle_id=battle.pk, turn_number=battle.current_turn, self_side=str(participant_id),
            opponent_side=str(opponent.pk) if opponent else "",
            self_state=_state(me), opponent_state=_state(opponent),
            hook_name=hook_name,
            teammates=[_state(t) for t in teammates], enemies=[_state(e) for e in enemies],
            self_economy=ball_economy_id(me.ball_instance) and str(ball_economy_id(me.ball_instance)),
            self_regime=ball_regime_id(me.ball_instance) and str(ball_regime_id(me.ball_instance)),
        )

    async def _run_passive_hooks(self, battle: Battle, participants: list[BattleParticipant], hook_name: str) -> None:
        currency_grants: list[tuple[int, int]] = []
        participants_by_id = {p.pk: p for p in participants}
        for participant in participants:
            if participant.is_spectator or not participant.is_alive:
                continue
            ball = participant.ball_instance.ball
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
            if participant.pk in {p.pk for p in participants}:
                await participant.asave()

        for user_id, amount in currency_grants:
            await award_currency(self.bot, user_id, amount, reason=f"ability-{hook_name}")

    def _apply_ability_effects(self, battle: Battle, participants_by_id: dict[int, BattleParticipant], ctx: AbilityContext, currency_grants: list[tuple[int, int]]) -> None:
        state = battle.state or {}
        effects_state = state.setdefault("effects", {})

        def _resolve_target(side: str) -> BattleParticipant | None:
            if side == ctx.self_side:
                return participants_by_id.get(int(ctx.self_side)) if ctx.self_side.isdigit() else None
            if side == ctx.opponent_side and ctx.opponent_side:
                return participants_by_id.get(int(ctx.opponent_side))
            return None

        for queued in ctx.effects:
            target_side = ctx.self_side if queued.target == "self" else ctx.opponent_side
            target = _resolve_target(target_side)
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
                # See README: multi-turn stat buffs are an extension point.
                # Applied for the remainder of this action only today.
                stat = str(queued.payload.get("stat", ""))
                amount = float(queued.payload.get("amount", 0))
                if stat == "attack_pct":
                    target.attack = max(1, int(round(target.attack * (1 + amount))))
                elif stat == "defense_pct":
                    target.defense = max(1, int(round(target.defense * (1 + amount))))
            elif queued.kind == "status":
                effects_state.setdefault(str(target.pk), []).append(queued.payload.get("effect", {}))
            elif queued.kind == "currency":
                currency_grants.append((target.user_id, int(queued.payload.get("amount", 0))))

        state["effects"] = effects_state
        battle.state = state

    async def notify_battle_expired(self, battle: Battle) -> None:
        try:
            channel = self.bot.get_channel(battle.channel_id)
            if channel is None or battle.message_id is None:
                return
            message = await channel.fetch_message(battle.message_id)  # type: ignore[union-attr]
            await message.edit(content="⌛ This battle has expired due to inactivity.", view=None)
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
