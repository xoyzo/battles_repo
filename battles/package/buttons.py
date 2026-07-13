"""Discord button/select components used across the battle lobby and the
per-turn action-selection view.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .actions import ACTIONS, ActionKey

if TYPE_CHECKING:
    from .views import BattleView, LobbyView


class ActionButton(discord.ui.Button["BattleView"]):
    def __init__(self, action: ActionKey, *, row: int = 0, disabled: bool = False):
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


class ModeSelect(discord.ui.Select["LobbyView"]):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Choose a battle mode...",
            min_values=1, max_values=1, options=options,
            custom_id="battles:mode_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_mode_choice(interaction, self.values[0])


class JoinButton(discord.ui.Button["LobbyView"]):
    def __init__(self):
        super().__init__(label="Join", emoji="🙋", style=discord.ButtonStyle.success, custom_id="battles:join")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_join(interaction)


class LeaveButton(discord.ui.Button["LobbyView"]):
    def __init__(self):
        super().__init__(label="Leave", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="battles:leave")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_leave(interaction)


class StartButton(discord.ui.Button["LobbyView"]):
    def __init__(self):
        super().__init__(label="Start Battle", emoji="🏁", style=discord.ButtonStyle.primary, custom_id="battles:start")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_start(interaction)


class SurrenderButton(discord.ui.Button["BattleView"]):
    def __init__(self):
        super().__init__(label="Surrender", emoji="🏳️", style=discord.ButtonStyle.danger, row=1, custom_id="battles:surrender")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_surrender(interaction)


class ConfirmButton(discord.ui.Button):
    def __init__(self, *, label: str = "Accept", style: discord.ButtonStyle = discord.ButtonStyle.success):
        super().__init__(label=label, style=style, custom_id="battles:confirm")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_confirm(interaction)  # type: ignore[attr-defined]


class DeclineButton(discord.ui.Button):
    def __init__(self, *, label: str = "Decline", style: discord.ButtonStyle = discord.ButtonStyle.danger):
        super().__init__(label=label, style=style, custom_id="battles:decline")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_decline(interaction)  # type: ignore[attr-defined]


class RematchButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Rematch", emoji="🔁", style=discord.ButtonStyle.primary, custom_id="battles:rematch")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_rematch(interaction)  # type: ignore[attr-defined]
