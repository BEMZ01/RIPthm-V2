import discord
import dotenv
import os
import logging
from discord.ext import commands
import sponsorblock as sb

# Discord bot to play music in voice channels
logging.basicConfig(level=logging.INFO)
dotenv.load_dotenv()
# Load the token from the .env file
TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_SECRET')

guild_ids = [730859265249509386, ]
bot = commands.Bot(debug_guilds=[730859265249509386])
#bot = commands.Bot()
# read extensions from cogs folder
for filename in os.listdir('./cogs'):
    if filename.endswith('.py') and filename.startswith('cog_'):
        bot.load_extension(f'cogs.{filename[:-3]}')
        print(f'Loaded {filename[:-3]}')
sbClient = sb.Client()


@bot.slash_command(name="ping", description="See the bot's latency")
async def ping(ctx: discord.ApplicationContext):
    await ctx.respond(f"Pong! ({bot.latency * 1000}ms)", delete_after=5)


@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="music"))


if __name__ == "__main__":
    bot.run(TOKEN)
