"""The ability execution API.

Every ability (declarative or custom-scripted) runs against an
`AbilityContext`, which exposes the primitives abilities are allowed to
touch: damage, healing, stat modification, status effects, momentum, and
currency. Abilities never mutate `Battle`/`PlayerContext` directly — they
queue effects onto the context, and the engine applies them after the
ability resolves. This keeps ability logic sandboxed and easy to reason
about/replay.
"""
from __future__ import annotations

import random as _random
from dataclasses import dataclass, field
from typing import Any

Target = str
"""`"self"`, `"opponent"` (the primary detected opponent), or a specific
participant's id as a string (e.g. `str(ally["participant_id"])` from
`ctx.get_teammates()` / `ctx.get_enemies()`) — for abilities that affect
more than one target at once."""


@dataclass
class QueuedEffect:
    kind: str
    target: Target
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AbilityContext:
    """Passed to every ability hook (`execute`, `before_turn`, `on_attack`, ...).

    `self_state` / `opponent_state` are plain dicts mirroring the relevant
    participant fields (kept as dicts, not model/engine instances, so a
    sandboxed script can't hold a reference into engine internals across
    turns). For non-Duel modes, `teammates`/`enemies` list every other
    participant's state the same way.
    """

    battle_id: int
    turn_number: int
    self_side: str
    opponent_side: str
    self_state: dict[str, Any]
    opponent_state: dict[str, Any]
    hook_name: str = "execute"
    teammates: list[dict[str, Any]] = field(default_factory=list)
    enemies: list[dict[str, Any]] = field(default_factory=list)
    self_economy: str | None = None
    self_regime: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    ability_settings: dict[str, Any] = field(default_factory=dict)
    effects: list[QueuedEffect] = field(default_factory=list, init=False)

    # -- Battle-facing helpers -------------------------------------------------
    def damage(self, amount: int, *, target: Target = "opponent") -> None:
        """Queue direct damage to self or opponent."""
        self.effects.append(QueuedEffect("damage", target, {"amount": int(amount)}))

    def heal(self, amount: int, *, target: Target = "self") -> None:
        """Queue direct healing to self or opponent."""
        self.effects.append(QueuedEffect("heal", target, {"amount": int(amount)}))

    def modify_stat(self, stat: str, amount: float, *, duration: int | None = None, target: Target = "self") -> None:
        """Queue a temporary (or permanent, if duration is None) stat modifier.

        `stat` is one of "attack", "defense", "speed". `amount` is additive
        for flat modifiers or expressed as a fraction (e.g. 0.25) when
        `stat` is suffixed with `_pct` (e.g. "attack_pct").
        """
        self.effects.append(
            QueuedEffect("modify_stat", target, {"stat": stat, "amount": amount, "duration": duration})
        )

    def add_effect(self, effect: dict[str, Any], *, target: Target = "self") -> None:
        """Queue an arbitrary status effect (e.g. a damage-over-time tick,
        a shield, a stun) to be tracked in `Battle.state["effects"]`.
        """
        self.effects.append(QueuedEffect("status", target, {"effect": effect}))

    def change_momentum(self, amount: int, *, target: Target = "self") -> None:
        self.effects.append(QueuedEffect("momentum", target, {"amount": int(amount)}))

    def give_currency(self, amount: int, *, target: Target = "self") -> None:
        """Queue a currency grant, applied via `integrations/currency.py`
        after the battle turn (or battle) resolves. (Not to be confused
        with a Ball's Economy classification.)
        """
        self.effects.append(QueuedEffect("currency", target, {"amount": int(amount)}))

    # -- Read-only convenience accessors ---------------------------------------
    @property
    def self_hp(self) -> int:
        return self.self_state.get("hp", 0)

    @property
    def opponent_hp(self) -> int:
        return self.opponent_state.get("hp", 0)

    @property
    def self_momentum(self) -> int:
        return self.self_state.get("momentum", 0)

    def get_teammates(self) -> list[dict[str, Any]]:
        return list(self.teammates)

    def get_enemies(self) -> list[dict[str, Any]]:
        return list(self.enemies)

    def pick_random(self, items: list[Any]) -> Any | None:
        """Pick a uniformly random element from a list the script provides
        (e.g. `ctx.pick_random(ctx.get_enemies())`). Ability scripts can't
        `import random` themselves — the sandbox blocks every import — so
        this is the sanctioned way for an ability to make a random choice.
        Returns None if `items` is empty.
        """
        if not items:
            return None
        return _random.choice(items)
