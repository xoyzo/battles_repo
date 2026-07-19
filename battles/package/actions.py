"""Modular battle action definitions.

Each action (Attack, Defend, Counter, Heal, Dodge, Ability) is represented
by an `ActionDefinition` describing its Discord button, cooldown key, and
per-battle use limits. The actual head-to-head resolution rules (what
happens when Attack meets Defend, Counter meets Attack, etc.) live in the
`INTERACTIONS` dispatch table at the bottom of this file and are consumed
by `package/engine.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class ActionKey(str, Enum):
    ATTACK = "attack"
    DEFEND = "defend"
    COUNTER = "counter"
    HEAL = "heal"
    DODGE = "dodge"
    ABILITY = "ability"


@dataclass(frozen=True)
class ActionDefinition:
    key: ActionKey
    label: str
    emoji: str
    description: str
    cooldown_field: str | None = None  # key in Battle.state["cooldowns"][player]
    uses_field: str | None = None  # key in Battle.state["uses"][player]
    max_uses_snapshot_key: str | None = None  # key in config_snapshot for max uses


ACTIONS: dict[ActionKey, ActionDefinition] = {
    ActionKey.ATTACK: ActionDefinition(
        key=ActionKey.ATTACK,
        label="Attack",
        emoji="⚔️",
        description="Deal damage. Beats Heal, blocked by Defend, loses to Counter, missed by Dodge.",
        # Attack itself never sets this cooldown through normal use (it's
        # not in `engine.apply_cooldown_on_use`'s length map) — it only
        # exists so an ability effect like a "cripple" debuff has a slot
        # to block the Attack button through, the same way "silence"
        # blocks Ability via its own cooldown field.
        cooldown_field="attack",
    ),
    ActionKey.DEFEND: ActionDefinition(
        key=ActionKey.DEFEND,
        label="Defend",
        emoji="🛡️",
        description="Reduce incoming damage and build guard. Weak against Counter.",
    ),
    ActionKey.COUNTER: ActionDefinition(
        key=ActionKey.COUNTER,
        label="Counter",
        emoji="🔄",
        description="Negate and reflect Attack damage. Has a cooldown.",
        cooldown_field="counter",
    ),
    ActionKey.HEAL: ActionDefinition(
        key=ActionKey.HEAL,
        label="Heal",
        emoji="💚",
        description="Restore HP, up to a limited number of uses. Interrupted by Attack.",
        uses_field="heal",
        max_uses_snapshot_key="heal_uses_per_battle",
    ),
    ActionKey.DODGE: ActionDefinition(
        key=ActionKey.DODGE,
        label="Dodge",
        emoji="💨",
        description="Avoid incoming damage entirely. Has a cooldown.",
        cooldown_field="dodge",
    ),
    ActionKey.ABILITY: ActionDefinition(
        key=ActionKey.ABILITY,
        label="Ability",
        emoji="✨",
        description="Use your ball's unique ability. Has a cooldown, governed by the ability framework.",
        cooldown_field="ability",
    ),
}


def default_enabled_actions() -> list[ActionKey]:
    return list(ACTIONS.keys())


@dataclass
class ActionOutcome:
    """Result of one player's action for a single turn, before the
    opposing action is factored in. Mutated in place by interaction
    handlers as the matchup is resolved.
    """

    actor: str  # "player_one" | "player_two"
    action: ActionKey
    damage_dealt: int = 0
    damage_taken: int = 0
    healed: int = 0
    momentum_delta: int = 0
    was_blocked: bool = False
    was_dodged: bool = False
    was_countered: bool = False
    was_interrupted: bool = False
    is_critical: bool = False
    notes: list[str] = field(default_factory=list)


InteractionHandler = Callable[..., None]

# Populated by engine.py at resolution time; kept here only as a type alias
# so the two modules share a common contract without circular imports.
InteractionKey = tuple[ActionKey, ActionKey]
