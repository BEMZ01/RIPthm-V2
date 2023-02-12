import os
from pprint import pprint

import discord
from discord.ext import commands


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_before_invoke(self, ctx: discord.ApplicationContext):
        print(f'Command {ctx.command.name} invoked by {ctx.author} in guild {ctx.guild.name}')
        if await self.bot.is_owner(ctx.author):
            return True
        else:
            await ctx.respond("This is a restricted command.", ephemeral=True, delete_after=15)
            raise commands.NotOwner()

    @discord.slash_command(name="load", description="Load a cog")
    async def load(self, ctx, extension):
        self.bot.load_extension(f'cogs.{extension}')
        await ctx.respond(f"Loaded {extension}")

    @discord.slash_command(name="unload", description="Unload a cog")
    async def unload(self, ctx, extension):
        self.bot.unload_extension(f'cogs.{extension}')
        await ctx.respond(f"Unloaded {extension}")

    @discord.slash_command(name="reload", description="Reload a cog")
    async def reload(self, ctx, extension):
        self.bot.reload_extension(f'cogs.{extension}')
        await ctx.respond(f"Reloaded {extension}")

    @discord.slash_command(name="reloadall", description="Reload all cogs")
    async def reloadall(self, ctx):
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename.startswith('cog_'):
                self.bot.reload_extension(f'cogs.{filename[:-3]}')
                print(f'Reloaded {filename[:-3]}')
        await ctx.respond(f"Reloaded all cogs")

    @discord.slash_command(name="shutdown", description="Shutdown the bot")
    async def shutdown(self, ctx):
        await ctx.respond("Shutting down...")
        await self.bot.close()

    @discord.slash_command(name="restart", description="Restart the bot")
    async def restart(self, ctx):
        await ctx.respond("Restarting...")
        await self.bot.close()
        os.system("python main.py")

    @discord.slash_command(name="update", description="Update the bot")
    async def update(self, ctx):
        await ctx.respond("Updating...")
        await self.bot.close()
        os.system("git pull")
        os.system("python main.py")


def setup(bot):
    bot.add_cog(Admin(bot))