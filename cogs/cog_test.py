import discord
from discord.ext import commands

class Test(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.guild is None and message.activity is not None:
            if message.activity["type"] == 3:
                await message.channel.send("You are listening to music")
            print(message.activity)

def setup(bot):
    bot.add_cog(Test(bot))