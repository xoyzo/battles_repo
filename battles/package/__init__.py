from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot") -> None:
    from .cog import BattlesCog

    await bot.add_cog(BattlesCog(bot))

