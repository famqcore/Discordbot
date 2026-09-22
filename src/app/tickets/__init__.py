"""Расширение заявок: команды, ретенция и reconciliation.

Все фоновые задачи — Cog, поэтому ``bot.unload_extension("tickets")``
корректно их отменяет (issue #21).
"""

from .commands import TicketsCog, setup as setup_commands
from .reconcile import TicketReconcileCog, setup_reconcile_loop
from .retention import TicketRetentionCog, setup_retention_loop

__all__ = [
    "TicketReconcileCog",
    "TicketRetentionCog",
    "TicketsCog",
    "setup",
    "setup_reconcile_loop",
    "setup_retention_loop",
    "teardown",
]


async def setup(bot):
    await setup_commands(bot)
    await setup_retention_loop(bot)
    await setup_reconcile_loop(bot)


async def teardown(bot):
    for name in ("TicketReconcileCog", "TicketRetentionCog", "TicketsCog"):
        if bot.get_cog(name) is not None:
            await bot.remove_cog(name)
