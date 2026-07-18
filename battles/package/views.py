"""Discord UI views.

- `LobbyView`: shown while a battle is in `LOBBY` status. Deck-building
  happens entirely through `/battle add` / `/battle remove` (real
  autocomplete via `BallInstanceTransform`, no view chained inside a
  button callback); this view just shows the roster and offers Begin /
  Cancel.
- `BattleView`: the per-turn action-selection view. A *new* `BattleView`
  instance is created for every turn (with `timeout=turn_timer_seconds`),
  attached to the same public battle message, so the countdown naturally
  resets each turn.

Discord doesn't support disabling a shared message's components for only
one viewer, so "the opponent can't see your choice" is achieved by
acknowledging each click with an ephemeral confirmation rather than by
revealing the choice on the public message, and by ignoring a second click
from a player who already locked in that turn (see `interaction_check`).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, TYPE_CHECKING

import discord

from .actions import ACTIONS, ActionKey
from .buttons import AbilitySelect, ActionButton, BeginButton, CancelLobbyButton, SurrenderButton, TargetSelect

if TYPE_CHECKING:
    from battles.models import Battle

log = logging.getLogger("battles.views")

TurnCompleteCallback = Callable[["BattleView"], Awaitable[None]]


class LobbyView(discord.ui.View):
    """Begin/Cancel prompt for a battle waiting on players to `/battle add`.

    Restricted to users who've actually seated themselves in *this*
    lobby via `/battle add` — without this check, anyone who can see the
    message (i.e. anyone in the channel) could Begin or Cancel a lobby
    they were never part of.
    """

    def __init__(
        self,
        *,
        battle_id: int,
        on_begin: Callable[[discord.Interaction], Awaitable[None]],
        on_cancel: Callable[[discord.Interaction], Awaitable[None]],
        timeout: float = 600.0,
    ):
        super().__init__(timeout=timeout)
        self.battle_id = battle_id
        self._on_begin = on_begin
        self._on_cancel = on_cancel
        self.add_item(BeginButton())
        self.add_item(CancelLobbyButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from battles.models import BattleParticipant

        is_seated = await BattleParticipant.objects.filter(
            battle_id=self.battle_id, user_id=interaction.user.id,
        ).aexists()
        if not is_seated:
            await interaction.response.send_message(
                "You need to `/battle add` a ball to this lobby before you can do that.", ephemeral=True,
            )
            return False
        return True

    async def handle_begin(self, interaction: discord.Interaction) -> None:
        await self._on_begin(interaction)

    async def handle_cancel(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class AbilityChoiceView(discord.ui.View):
    """Ephemeral view letting a player pick which usable active ability to fire."""

    def __init__(self, options: list[discord.SelectOption], *, on_choice: Callable[[discord.Interaction, str], Awaitable[None]], timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self._on_choice = on_choice
        select = AbilitySelect(options)
        select.callback = self._callback  # type: ignore[method-assign]
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction) -> None:
        select: AbilitySelect = self.children[0]  # type: ignore[assignment]
        await self._on_choice(interaction, select.values[0])
        self.stop()


class TargetChoiceView(discord.ui.View):
    """Ephemeral view letting a player pick who to Attack, shown only in
    modes where more than one valid target exists (FFA / Team Battle).
    """

    def __init__(self, options: list[discord.SelectOption], *, on_choice: Callable[[discord.Interaction, str], Awaitable[None]], timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self._on_choice = on_choice
        select = TargetSelect(options)
        select.callback = self._callback  # type: ignore[method-assign]
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction) -> None:
        select: TargetSelect = self.children[0]  # type: ignore[assignment]
        await self._on_choice(interaction, select.values[0])
        self.stop()


class BattleView(discord.ui.View):
    """Collects every alive participant's action choice for a single turn."""

    def __init__(
        self,
        *,
        battle: "Battle",
        participant_ids: list[int],
        user_id_by_participant: dict[int, int],
        cooldowns: dict[int, dict[str, int]],
        heal_uses: dict[int, int],
        on_turn_complete: TurnCompleteCallback,
        timeout: float,
    ):
        super().__init__(timeout=timeout)
        self.battle_id = battle.pk
        self.participant_ids = participant_ids
        self.user_id_by_participant = user_id_by_participant
        self.user_id_to_participant = {v: k for k, v in user_id_by_participant.items()}

        self.enabled_actions: list[str] = (battle.config_snapshot or {}).get("enabled_actions") or [a.value for a in ACTIONS]
        # Cooldowns/heal-uses come straight from `BattleParticipant` rows
        # (passed in by the caller) — not from `Battle.state`, which never
        # stores per-participant cooldown data.
        self.cooldowns: dict[int, dict[str, int]] = {pid: dict(cooldowns.get(pid, {})) for pid in participant_ids}
        self.heal_uses: dict[int, int] = {pid: heal_uses.get(pid, 0) for pid in participant_ids}
        self.max_heal_uses = (battle.config_snapshot or {}).get("heal_uses_per_battle", 3)

        self.choices: dict[int, ActionKey | None] = {pid: None for pid in participant_ids}
        self.target_choice: dict[int, int | None] = {pid: None for pid in participant_ids}
        self.ability_choice: dict[int, int | None] = {pid: None for pid in participant_ids}
        self.surrendered_participant_id: int | None = None

        self._on_turn_complete = on_turn_complete
        self._resolved = False
        self.message: discord.Message | None = None

        for action in ACTIONS.values():
            if action.key.value not in self.enabled_actions:
                continue
            self.add_item(ActionButton(action.key))
        self.add_item(SurrenderButton())

    # -- validation -------------------------------------------------------
    def _participant_for(self, user_id: int) -> int | None:
        return self.user_id_to_participant.get(user_id)

    def _on_cooldown(self, participant_id: int, action: ActionKey) -> bool:
        definition = ACTIONS[action]
        if not definition.cooldown_field:
            return False
        return self.cooldowns.get(participant_id, {}).get(definition.cooldown_field, 0) > 0

    def _heal_exhausted(self, participant_id: int) -> bool:
        return self.heal_uses.get(participant_id, 0) >= self.max_heal_uses

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        participant_id = self._participant_for(interaction.user.id)
        if participant_id is None:
            await interaction.response.send_message("You're not part of this battle.", ephemeral=True)
            return False
        if self.choices[participant_id] is not None:
            await interaction.response.send_message("You've already locked in your move this turn.", ephemeral=True)
            return False
        return True

    # -- action handling ----------------------------------------------------
    async def handle_action_choice(self, interaction: discord.Interaction, action: ActionKey) -> None:
        participant_id = self._participant_for(interaction.user.id)
        assert participant_id is not None

        if self._on_cooldown(participant_id, action):
            await interaction.response.send_message(f"{ACTIONS[action].label} is on cooldown for you this turn.", ephemeral=True)
            return
        if action is ActionKey.HEAL and self._heal_exhausted(participant_id):
            await interaction.response.send_message("You have no Heal uses left this battle.", ephemeral=True)
            return

        if action is ActionKey.ABILITY:
            await self._prompt_ability(interaction, participant_id)
            return

        if action is ActionKey.ATTACK:
            await self._prompt_target_or_lock(interaction, participant_id, action)
            return

        await self._lock_in(interaction, participant_id, action)

    async def _prompt_target_or_lock(self, interaction: discord.Interaction, participant_id: int, action: ActionKey) -> None:
        from battles.models import BattleParticipant

        me_row = await BattleParticipant.objects.aget(pk=participant_id)
        enemies = [
            p async for p in BattleParticipant.objects.select_related("ball_instance__ball").filter(
                battle_id=self.battle_id, is_alive=True, is_spectator=False,
            ) if p.pk != participant_id and (me_row.team is None or p.team != me_row.team)
        ]

        if len(enemies) <= 1:
            target_id = enemies[0].pk if enemies else None
            self.target_choice[participant_id] = target_id
            await self._lock_in(interaction, participant_id, action)
            return

        from .integrations.balls import ball_display_name

        options = [
            discord.SelectOption(label=ball_display_name(e.ball_instance), description=f"HP {max(0, e.hp)}/{e.max_hp}", value=str(e.pk))
            for e in enemies
        ]

        async def on_choice(inner_interaction: discord.Interaction, value: str) -> None:
            self.target_choice[participant_id] = int(value)
            await self._lock_in(inner_interaction, participant_id, action)

        view = TargetChoiceView(options, on_choice=on_choice)
        await interaction.response.send_message("Choose a target:", view=view, ephemeral=True)

    async def _prompt_ability(self, interaction: discord.Interaction, participant_id: int) -> None:
        from battles.models import Ability, BattleParticipant

        participant = await BattleParticipant.objects.select_related("ball_instance__ball").aget(pk=participant_id)
        ball = participant.ball_instance.countryball

        from .abilities import get_usable_abilities
        from .integrations.balls import ball_economy_id, ball_rarity, ball_regime_id

        battle = await self._fetch_battle()
        abilities = await get_usable_abilities(
            ball, ball_rarity(participant.ball_instance), ball_regime_id(participant.ball_instance),
            ball_economy_id(participant.ball_instance), battle.mode_id,
            trigger_type=Ability.TriggerType.ACTIVE,
        )
        abilities = [a for a in abilities if self.cooldowns.get(participant_id, {}).get("ability", 0) == 0]

        if not abilities:
            await interaction.response.send_message("You have no usable abilities right now.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=a.name, description=(a.description or "")[:100], value=str(a.pk), emoji=a.icon or None)
            for a in abilities
        ]

        async def on_choice(inner_interaction: discord.Interaction, value: str) -> None:
            self.ability_choice[participant_id] = int(value)
            await self._lock_in(inner_interaction, participant_id, ActionKey.ABILITY)

        view = AbilityChoiceView(options, on_choice=on_choice)
        await interaction.response.send_message("Choose an ability:", view=view, ephemeral=True)

    async def _fetch_battle(self) -> "Battle":
        from battles.models import Battle as BattleModel

        return await BattleModel.objects.aget(pk=self.battle_id)

    async def _lock_in(self, interaction: discord.Interaction, participant_id: int, action: ActionKey) -> None:
        self.choices[participant_id] = action
        message = f"{ACTIONS[action].emoji} Locked in **{ACTIONS[action].label}**. Waiting on other players..."
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)

        if not self._resolved and all(self.choices[pid] is not None for pid in self.participant_ids):
            self._resolved = True
            self.stop()
            await self._on_turn_complete(self)

    async def handle_surrender(self, interaction: discord.Interaction) -> None:
        participant_id = self._participant_for(interaction.user.id)
        if participant_id is None:
            await interaction.response.send_message("You're not part of this battle.", ephemeral=True)
            return
        await interaction.response.send_message("You surrendered.", ephemeral=True)
        if not self._resolved:
            self._resolved = True
            self.surrendered_participant_id = participant_id
            self.stop()
            await self._on_turn_complete(self)

    async def on_timeout(self) -> None:
        if self._resolved:
            return
        self._resolved = True
        for pid in self.participant_ids:
            if self.choices[pid] is None:
                self.choices[pid] = ActionKey.DEFEND
        await self._on_turn_complete(self)
