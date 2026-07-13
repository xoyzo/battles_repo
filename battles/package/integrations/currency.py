"""Integration with the server's actual currency system.

Not to be confused with `integrations/economy.py` — in this bot, "Economy"
is a Ball classification (Capitalism/Socialism/Mixed Economy, alongside
Regime), not a money system. Currency payouts are looked up by trying a
money-flavoured cog name explicitly, so a classification cog named
"Economy" is never mistaken for a wallet.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("battles.integrations.currency")

_CURRENCY_COG_NAMES = ("Money", "Currency", "Wallet", "Bank")


async def award_currency(bot: "BallsDexBot", user_id: int, amount: int, *, reason: str = "battle") -> bool:
    """Grant `amount` currency to `user_id`. Returns True if a currency
    system handled the grant, False if none was available (logged, not
    raised, so a missing currency cog never crashes a battle payout).
    """
    if amount <= 0:
        return True

    for cog_name in _CURRENCY_COG_NAMES:
        cog = bot.get_cog(cog_name)
        if cog is None:
            continue
        for method_name in ("add_currency", "add_balance", "deposit", "give"):
            method = getattr(cog, method_name, None)
            if callable(method):
                try:
                    await method(user_id, amount)
                    return True
                except Exception:  # noqa: BLE001 - never let a payout error break a battle
                    log.exception("%s.%s failed for %s", cog_name, method_name, user_id)

    log.warning("No currency integration available to award %s currency to %s (reason=%s)", amount, user_id, reason)
    return False
