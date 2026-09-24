"""Расширение AFK: команды, слушатель сообщений и цикл авто-снятия.

Каждый компонент — Cog, поэтому ``bot.unload_extension("afk")`` корректно
снимает слушателя и отменяет фоновую задачу.
"""

from .commands import AfkCog
from .events import AfkEventsCog, setup_afk_events
from .tasks import AfkExpiryCog, setup_expiry_loop

__all__ = [
    "AfkCog",
    "AfkEventsCog",
    "AfkExpiryCog",
    "setup",
    "setup_afk_events",
    "setup_expiry_loop",
    "teardown",
]


async def setup(bot):
    await bot.add_cog(AfkCog(bot))
    await setup_afk_events(bot)
    await setup_expiry_loop(bot)


async def teardown(bot):
    for name in ("AfkExpiryCog", "AfkEventsCog", "AfkCog"):
        if bot.get_cog(name) is not None:
            await bot.remove_cog(name)
