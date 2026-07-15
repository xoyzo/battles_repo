"""Integration with the server's actual currency system.

Confirmed against real `bd_models` source: BallsDex core has no "Money"
cog — currency lives directly on `bd_models.Player` (`.money`,
`await player.add_money(amount)`, `await player.remove_money(amount)`),
and is entirely optional (`settings.currency_enabled`, false until an
admin sets a currency name in the dashboard). Not to be confused with
`integrations/economy.py` — "Economy" in this bot is a Ball
*classification* (Capitalism/Socialism/...), not a wallet.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bd_models.models import Player
from settings.models import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("battles.integrations.currency")


async def award_currency(bot: "BallsDexBot", user_id: int, amount: int, *, reason: str = "battle") -> bool:
    """Grant `amount` currency to the player with this Discord user ID.

    Returns False (without raising) if currency is disabled for this bot
    or `amount` isn't positive, so a battle payout can never crash a turn
    or a result screen just because currency hasn't been configured.
    """
    if not settings.currency_enabled or amount <= 0:
        return False

    try:
        player, _ = await Player.objects.aget_or_create(discord_id=user_id)
        await player.add_money(amount)
        return True
    except Exception:  # noqa: BLE001 - a payout error must never break a battle
        log.exception("Failed to award %s currency to %s (reason=%s)", amount, user_id, reason)
        return False


def currency_label(bot: "BallsDexBot", amount: int) -> str:
    """Human-readable amount using the dashboard-configured currency name/emoji."""
    from settings.utils import format_currency

    return format_currency(amount, shortened=True, bot=bot)
