"""Discord app_commands transformers for battles-specific parameters.

Follows the exact pattern BallsDex core uses for its own model parameters
(`ballsdex.core.utils.transformers.TTLModelTransformer`) so `mode:` gets
the same kind of autocomplete, TTL-cached, no-typing-an-ID experience as
every other BallsDex slash command parameter.
"""
from __future__ import annotations

from discord import app_commands

from ballsdex.core.utils.transformers import TTLModelTransformer

from battles.models import BattleMode


class BattleModeTransformer(TTLModelTransformer[BattleMode]):
    name = "battle mode"
    column = "name"
    model = BattleMode


# Only enabled modes are offered — mirrors `modes.list_available_modes`.
BattleModeTransform = app_commands.Transform[BattleMode, BattleModeTransformer(is_enabled=True)]
