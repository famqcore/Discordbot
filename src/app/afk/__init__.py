from .commands import AfkCog
from .events import setup_afk_events
from .tasks import start_expiry_loop


async def setup(bot):
    await bot.add_cog(AfkCog(bot))
    setup_afk_events(bot)
    start_expiry_loop(bot)
