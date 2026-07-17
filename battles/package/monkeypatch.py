"""Class-level extensions to existing BallsDex objects.

Kept intentionally small: the battles package doesn't need to hijack core
spawn/catch flow the way an economy package might, but it's convenient for
`BallInstance` to know how to fetch its own battle stats. Patches are
applied once, idempotently, from `cog.py`'s `_post_init` task (never from
`cog_load`, to avoid touching the DB/other packages before the bot — and
their cogs — are fully ready).
"""
from __future__ import annotations

import logging

log = logging.getLogger("battles.monkeypatch")

_APPLIED = False


def apply_patches() -> None:
    global _APPLIED
    if _APPLIED:
        return

    try:
        from bd_models.models import BallInstance
    except ImportError:
        log.warning("bd_models.BallInstance not importable; skipping battle monkeypatches.")
        return

    if not hasattr(BallInstance, "get_battle_stats"):
        def get_battle_stats(self):  # noqa: ANN001 - patched onto BallInstance
            from .integrations.balls import get_battle_stats as _get_battle_stats

            return _get_battle_stats(self)

        BallInstance.get_battle_stats = get_battle_stats  # type: ignore[attr-defined]

    if not hasattr(BallInstance, "in_active_battle"):
        async def in_active_battle(self) -> bool:  # noqa: ANN001
            from battles.models import Battle, BattleParticipant

            return await BattleParticipant.objects.filter(
                ball_instance_id=self.pk, battle__status=Battle.Status.ACTIVE,
            ).aexists()

        BallInstance.in_active_battle = in_active_battle  # type: ignore[attr-defined]

    _APPLIED = True
    log.info("Battle monkeypatches applied to BallInstance.")
