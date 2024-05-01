import discord
import dotenv
import os
import logging
from discord.ext import commands
import sponsorblock as sb

# if the log file is over 1MiB, clear it
if os.path.exists("debug.log"):
    if os.path.getsize("debug.log") > 1048576:
        open("debug.log", "w").close()

# Define the log file name and level
LOG_FILE = "debug.log"
LOG_LEVEL = logging.DEBUG

# Create a logger object
logger = logging.getLogger()
stream_handler = logging.StreamHandler()
file_handler = logging.FileHandler(LOG_FILE)
discord_logs = logging.getLogger("discord")
discord_logs.setLevel(logging.DEBUG)

# Set the logging level for each handler
stream_handler.setLevel(logging.INFO)
file_handler.setLevel(LOG_LEVEL)

# Define a formatter for the log messages
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
stream_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(stream_handler)
logger.addHandler(file_handler)


dotenv.load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_SECRET')

intents = discord.Intents.default()
intents.dm_messages = True
intents.presences = True
# allow the bot to get member's activities
intents.members = True
bot = commands.AutoShardedBot(intents=intents, command_prefix="!")
# bot = commands.Bot()
# read extensions from cogs folder
for filename in os.listdir('./cogs'):
    if filename.endswith('.py') and filename.startswith('cog_'):
        bot.load_extension(f'cogs.{filename[:-3]}')
        logger.log(logging.INFO, f'Loaded {filename[:-3]}')
sbClient = sb.Client()


@bot.slash_command(name="ping", description="See the bot's latency")
async def ping(ctx: discord.ApplicationContext):
    await ctx.respond(f"Pong! ({bot.latency * 1000}ms)", delete_after=5)


@bot.event
async def on_ready():
    logger.log(logging.INFO, f'{bot.user} has connected to Discord!')
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
    logger.info("Starting bot")
    bot.run(TOKEN, reconnect=True)
