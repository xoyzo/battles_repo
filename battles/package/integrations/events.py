"""Battle event hooks.

Other packages (or the battles package itself, e.g. for a "Double Damage"
"Chaos Battles" event) register async callbacks against one of the four
lifecycle hooks. All registered callbacks for a hook run in registration
order; a raising callback is logged and skipped rather than aborting the
battle.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger("battles.integrations.events")

HookName = str  # "before_battle" | "before_turn" | "after_turn" | "after_battle"
HookCallback = Callable[..., Awaitable[None]]

_HOOKS: dict[HookName, list[HookCallback]] = {
    "before_battle": [],
    "before_turn": [],
    "after_turn": [],
    "after_battle": [],
}


def on(hook: HookName) -> Callable[[HookCallback], HookCallback]:
    """Decorator to register a callback against a lifecycle hook.

    Example:
        @on("before_battle")
        async def legendary_only_arena(battle, **kwargs):
            ...
    """

    def decorator(func: HookCallback) -> HookCallback:
        _HOOKS.setdefault(hook, []).append(func)
        return func

    return decorator


def register(hook: HookName, callback: HookCallback) -> None:
    _HOOKS.setdefault(hook, []).append(callback)


def unregister(hook: HookName, callback: HookCallback) -> None:
    callbacks = _HOOKS.get(hook, [])
    if callback in callbacks:
        callbacks.remove(callback)


async def dispatch(hook: HookName, /, **kwargs: Any) -> None:
    for callback in list(_HOOKS.get(hook, [])):
        try:
            await callback(**kwargs)
        except Exception:  # noqa: BLE001 - one bad event handler shouldn't break a battle
            log.exception("Event hook %r callback %r raised", hook, getattr(callback, "__name__", callback))
