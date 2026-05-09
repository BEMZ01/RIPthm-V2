import discord
import asyncio
import dotenv
import os
import logging
import inspect
from discord.ext import commands
import sponsorblock as sb
from utils.persistent_deletes import PersistentDeleteQueue

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
stream_handler.setLevel(logging.INFO)  # Set handler level

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
# Ensure an asyncio event loop exists on the main thread. On Python 3.10+
# asyncio.get_event_loop()/discord internals may expect a loop to be present
# when constructing the bot; create and set one if none is running.
try:
    asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
if os.getenv("DEBUG_GUILDS") is not None:
    bot = commands.AutoShardedBot(intents=intents, owner_id=int(os.getenv('OWNER_ID')), debug_guilds=os.getenv("DEBUG_GUILDS").split(","))
else:
    bot = commands.AutoShardedBot(intents=intents, owner_id=int(os.getenv('OWNER_ID')))
bot.logger = logger
bot.persistent_delete_queue = PersistentDeleteQueue(
    bot,
    file_path=os.path.join("temp", "pending_deletes.json"),
    logger=logger,
)
bot.schedule_persistent_delete = bot.persistent_delete_queue.schedule
# Parse VOTE_BYPASS environment variable (comma-separated guild IDs)
# and attach a list of integer guild IDs to the bot for cogs to consult.
vote_bypass_env = os.getenv('VOTE_BYPASS', '')
vote_bypass_guilds = []
if vote_bypass_env:
    for part in [p.strip() for p in vote_bypass_env.split(',') if p.strip()]:
        try:
            vote_bypass_guilds.append(int(part))
        except ValueError:
            logger.warning(f"Ignoring invalid guild id in VOTE_BYPASS: {part}")
bot.vote_bypass_guilds = vote_bypass_guilds
# bot = commands.Bot()
# read extensions from cogs folder
for filename in os.listdir('./cogs'):
    if filename.endswith('.py') and filename.startswith('cog_'):
        try:
            bot.load_extension(f'cogs.{filename[:-3]}')
            logger.info(f'Loaded {filename[:-3]}')
        except Exception as e:
            # Log the error but continue loading other extensions. Missing optional
            # dependencies in some cogs (e.g. lavalink) should not prevent the bot
            # module from importing / starting in dev environments.
            logger.exception(f'Failed to load extension {filename[:-3]}: {e}')
sbClient = sb.Client()


async def _close_with_cleanup():
    """Wrap bot.close so shutdown cleanup runs on all exit paths (Ctrl+C, reconnect stop, etc.)."""
    logger.info("Running shutdown cleanup before closing bot.")

    # Give cogs a chance to stop loops / tasks.
    for cog in list(bot.cogs.values()):
        try:
            if hasattr(cog, 'shutdown') and inspect.iscoroutinefunction(cog.shutdown):
                await cog.shutdown()
            elif hasattr(cog, '_async_cog_unload') and inspect.iscoroutinefunction(cog._async_cog_unload):
                await cog._async_cog_unload()
        except Exception as e:
            logger.exception(f"Error shutting down cog {cog}: {e}")

    # Close lavalink internal aiohttp session if present.
    try:
        ll_client = getattr(bot, 'lavalink', None)
        if ll_client is not None:
            sess = getattr(ll_client, '_session', None)
            if sess is not None and not sess.closed:
                await sess.close()
    except Exception as e:
        logger.exception(f"Error closing lavalink session: {e}")

    # Stop persistent delete worker.
    try:
        if hasattr(bot, 'persistent_delete_queue'):
            await bot.persistent_delete_queue.stop()
    except Exception as e:
        logger.exception(f"Error stopping persistent delete queue: {e}")

    # Call original close implementation (discord.py session and websocket cleanup).
    await bot._original_close()


# Monkey-patch bot.close so cleanup is guaranteed when bot.run() shuts down.
bot._original_close = bot.close
bot.close = _close_with_cleanup


@bot.slash_command(name="ping", description="See the bot's latency")
async def ping(ctx: discord.ApplicationContext):
    message = await ctx.respond(f"Pong! ({bot.latency * 1000}ms)", delete_after=5)
    await bot.schedule_persistent_delete(message, 5)


@bot.event
async def on_ready():
    await bot.persistent_delete_queue.start()
    logger.info(f"{bot.user} has connected to Discord ({len(bot.guilds)} guilds)!")
    # Build a safe list of guild names (fall back to ID for unknown guilds).
    guild_names = []
    for gid in bot.vote_bypass_guilds:
        g = bot.get_guild(gid)
        if g is not None:
            guild_names.append(g.name)
        else:
            guild_names.append(str(gid))
    logger.debug(f"Bypassing vote on {len(bot.vote_bypass_guilds)} guilds: {', '.join(guild_names) if guild_names else 'none'}")
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
