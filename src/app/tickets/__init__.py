from .commands import setup as setup_commands


async def setup(bot):
    await setup_commands(bot)
