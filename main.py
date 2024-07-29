import discord
import dotenv
import os
import logging
from discord.ext import commands
import sponsorblock as sb

if os.path.exists("debug.log"):
    print(
        f"debug.log exists, size: {os.path.getsize('debug.log')} bytes > 1MiB? {os.path.getsize('debug.log') > 1048576}")
    if os.path.getsize("debug.log") > 1048576:
        open("debug.log", "w").close()
        print("Cleared debug.log")

# Set up discord's built-in logging
discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.DEBUG)  # or INFO
discord_logger.propagate = False

# Your existing logger setup
logger = logging.getLogger('main')
logger.setLevel(logging.DEBUG)  # Set logger level
logger.propagate = False

# Create a FileHandler
file_handler = logging.FileHandler('debug.log')
file_handler.setLevel(logging.DEBUG)  # Set handler level

# Create a StreamHandler for STDOUT
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)  # Set handler level

# Create a Formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Set the Formatter for the handlers
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

discord_logger.addHandler(file_handler)
discord_logger.addHandler(stream_handler)


dotenv.load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_SECRET')

intents = discord.Intents.default()
intents.dm_messages = True
intents.presences = True
# allow the bot to get member's activities
intents.members = True
bot = commands.AutoShardedBot(intents=intents, owner_id=int(os.getenv('OWNER_ID')))
bot.logger = logger
# bot = commands.Bot()
# read extensions from cogs folder
for filename in os.listdir('./cogs'):
    if filename.endswith('.py') and filename.startswith('cog_'):
        bot.load_extension(f'cogs.{filename[:-3]}')
        logger.info(f'Loaded {filename[:-3]}')
sbClient = sb.Client()


@bot.slash_command(name="ping", description="See the bot's latency")
async def ping(ctx: discord.ApplicationContext):
    await ctx.respond(f"Pong! ({bot.latency * 1000}ms)", delete_after=5)


@bot.event
async def on_ready():
    logger.info(f"{bot.user} has connected to Discord ({len(bot.guilds)} guilds)!")
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
    logging.info("Starting bot")
    bot.run(TOKEN, reconnect=True)
