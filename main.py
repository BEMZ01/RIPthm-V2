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

intents = discord.Intents.default()
intents.dm_messages = True
bot = commands.AutoShardedBot(intents=intents)
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

@bot.slash_command(name="shard", description="Get the shard ID and info for the current guild")
async def shard(ctx: discord.ApplicationContext):
    shard: discord.ShardInfo = bot.get_shard(ctx.guild.shard_id)
    shard_count: int = shard.shard_count
    shard_ping: float = round(shard.latency * 1000, 1)
    num_servers = len([guild for guild in bot.guilds if guild.shard_id == ctx.guild.shard_id])
    em = discord.Embed(title=f"RIPthm Shard Info", description=f"Shard ID: {ctx.guild.shard_id}")
    em.add_field(name="Shard Count", value=f"{shard_count}")
    em.add_field(name="Shard Ping", value=f"{shard_ping}ms")
    em.add_field(name="Servers", value=f"{num_servers}")
    em.add_field(name="Total Servers", value=f"{len(bot.guilds)}")
    await ctx.respond(embed=em)


if __name__ == "__main__":
    # Run the bot with DM message intents
    bot.run(TOKEN, reconnect=True)
