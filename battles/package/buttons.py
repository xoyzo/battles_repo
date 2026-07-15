"""Discord button/select components used across the battle lobby and the
per-turn action-selection view.

Deck-building (picking which ball to fight with) happens through
`/battle add` and `/battle remove` slash commands — which get real
autocomplete via `BallInstanceTransform` — rather than through a button
that pops up an ephemeral picker; chaining ephemeral views inside a button
callback inside another view is exactly the kind of thing that's fragile
in practice. Buttons here are only used for choices that make sense as a
one-tap interaction: battle turn actions, and starting/cancelling a lobby.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .actions import ACTIONS, ActionKey

if TYPE_CHECKING:
    from .views import BattleView, LobbyView


class ActionButton(discord.ui.Button["BattleView"]):
    def __init__(self, action: ActionKey, *, row: int | None = None, disabled: bool = False):
        definition = ACTIONS[action]
        super().__init__(
            label=definition.label,
            emoji=definition.emoji,
            style=discord.ButtonStyle.secondary,
            row=row,
            disabled=disabled,
            custom_id=f"battles:action:{action.value}",
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_action_choice(interaction, self.action)


class AbilitySelect(discord.ui.Select["BattleView"]):
    """Populated dynamically with the acting ball's currently usable
    active abilities when the player clicks the Ability button.
    """

    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Choose an ability...",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No abilities available", value="none")],
            disabled=not options,
            custom_id="battles:ability_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_ability_choice(interaction, self.values[0])


class TargetSelect(discord.ui.Select["BattleView"]):
    """Populated with valid targets when a mode has more than one possible
    opponent (Free For All, Team Battle). Not shown at all in a Duel,
    where the target is implicit.
    """

    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Choose a target...",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No valid targets", value="none")],
            disabled=not options,
            custom_id="battles:target_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_target_choice(interaction, self.values[0])


class BeginButton(discord.ui.Button["LobbyView"]):
    def __init__(self):
        super().__init__(label="Begin Battle", emoji="🏁", style=discord.ButtonStyle.primary, custom_id="battles:begin")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_begin(interaction)


class CancelLobbyButton(discord.ui.Button["LobbyView"]):
    def __init__(self):
        super().__init__(label="Cancel", emoji="🚫", style=discord.ButtonStyle.danger, custom_id="battles:cancel_lobby")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_cancel(interaction)


class SurrenderButton(discord.ui.Button["BattleView"]):
    def __init__(self):
        super().__init__(label="Surrender", emoji="🏳️", style=discord.ButtonStyle.danger, custom_id="battles:surrender")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_surrender(interaction)


class RematchButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Rematch", emoji="🔁", style=discord.ButtonStyle.primary, custom_id="battles:rematch")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_rematch(interaction)  # type: ignore[attr-defined]
