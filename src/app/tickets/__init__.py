from .commands import setup as setup_commands
from .retention import start_retention_loop


async def setup(bot):
    await setup_commands(bot)
    start_retention_loop(bot)
