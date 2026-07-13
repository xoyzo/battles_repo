"""Background tasks for the battles package.

Started from `cog.py`'s `_post_init` (after `bot.wait_until_ready()`), per
the rule that DB-touching setup must never run inside `cog_load`.
"""
from __future__ import annotations

import logging

from discord.ext import tasks

from battles.models import Battle

from . import helpers

log = logging.getLogger("battles.tasks")


class ExpirationSweeper:
    """Periodically marks inactive `active`/`pending` battles as expired
    and, if possible, edits their Discord message to reflect that.
    """

    def __init__(self, cog):
        self.cog = cog
        self._loop = tasks.loop(seconds=30)(self._sweep)

    def start(self) -> None:
        if not self._loop.is_running():
            self._loop.start()

    def stop(self) -> None:
        if self._loop.is_running():
            self._loop.cancel()

    async def _sweep(self) -> None:
        try:
            stale_battles = [
                b async for b in Battle.objects.filter(
                    status__in=[Battle.Status.LOBBY, Battle.Status.ACTIVE],
                )
            ]
        except Exception:  # noqa: BLE001
            log.exception("Failed to query battles during expiration sweep.")
            return

        for battle in stale_battles:
            expiration_seconds = (battle.config_snapshot or {}).get("battle_expiration_seconds", 300)
            if not helpers.is_expired(battle, expiration_seconds):
                continue

            battle.status = Battle.Status.EXPIRED
            battle.finished_at = helpers.now_utc()
            try:
                await battle.asave(update_fields=["status", "finished_at"])
            except Exception:  # noqa: BLE001
                log.exception("Failed to mark battle %s as expired.", battle.pk)
                continue

            await self.cog.notify_battle_expired(battle)
