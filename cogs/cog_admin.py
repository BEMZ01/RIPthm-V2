import os
from pprint import pprint
import discord
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)

DBUS_FOUND = True
try:
    import dbus
except ImportError:
    logger.warn("dbus-python not found, restart command will not work. This could be a windows machine.")
    DBUS_FOUND = False
    pass

PROJECT_NAME = "Ripthm"


def restart_service():
    if not DBUS_FOUND:
        logger.error("dbus-python not found, restart command will not work. This could be a windows machine.")
        return
    bus = dbus.SystemBus()
    systemd1 = bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1')
    manager = dbus.Interface(systemd1, 'org.freedesktop.systemd1.Manager')
    manager.RestartUnit(f'{PROJECT_NAME}.service', 'replace')


class Admin(commands.Cog):
    def __init__(self, bot, logger):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.handlers = logger.handlers
        self.logger.setLevel(logger.level)
        self.logger.propagate = False
        self.bot = bot

    async def cog_before_invoke(self, ctx: discord.ApplicationContext):
        self.logger.info(f'Command {ctx.command.name} invoked by {ctx.author} in guild {ctx.guild.name}')
        if ctx.author.id == self.bot.owner_id or await self.bot.is_owner(ctx.author):
            return True
        else:
            await ctx.respond("This is a restricted command.", ephemeral=True, delete_after=15)
            raise commands.NotOwner()

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info('onready event received')

    group = discord.SlashCommandGroup(name="admin", description="Admin commands")

    @group.command(name="load", description="Load a cog")
    async def load(self, ctx, extension):
        self.bot.load_extension(f'cogs.{extension}')
        await ctx.respond(f"Loaded {extension}")

    @group.command(name="unload", description="Unload a cog")
    async def unload(self, ctx, extension):
        self.bot.unload_extension(f'cogs.{extension}')
        await ctx.respond(f"Unloaded {extension}")

    @group.command(name="reload", description="Reload a cog")
    async def reload(self, ctx, extension):
        self.bot.reload_extension(f'cogs.{extension}')
        await ctx.respond(f"Reloaded {extension}")

    @group.command(name="reloadall", description="Reload all cogs")
    async def reloadall(self, ctx):
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename.startswith('cog_'):
                self.bot.reload_extension(f'cogs.{filename[:-3]}')
                logger.info(f'Reloaded {filename[:-3]}')
        await ctx.respond(f"Reloaded all cogs")

    @group.command(name="shutdown", description="Shutdown the bot")
    async def shutdown(self, ctx):
        await ctx.respond("Shutting down...")
        await self.bot.close()

    @group.command(name="restart", description="Restart the bot")
    async def restart(self, ctx):
        await ctx.respond("Restarting...")
        restart_service()

    @group.command(name="update", description="Update the bot")
    async def update(self, ctx):
        await ctx.respond("Updating...")
        await self.bot.close()
        os.system("git pull")
        restart_service()

    @group.command(name="logs", description="View the STDOUT logs")
    async def logs(self, ctx):
        await ctx.respond("Logs", files=[discord.File("debug.log")])


def setup(bot):
    bot.add_cog(Admin(bot, bot.logger))
