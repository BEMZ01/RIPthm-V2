import asyncio
from functools import partial
from pprint import pprint
from queue import Queue
import random
import re
import discord
import dotenv
import os
import sys
import traceback
from discord.ext import tasks, commands
import logging
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from youtube_dl import YoutubeDL

# Discord bot to play music in voice channels
logging.basicConfig(level=logging.INFO)
dotenv.load_dotenv()
# Load the token from the .env file
TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_SECRET')
bot = discord.Bot()

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID,
                                                           client_secret=SPOTIFY_CLIENT_SECRET))
######### NOTES ##########
# The Queue holds YouTube URLs
# The Current Playing holds Specific YT video info
# This program is a mess
# I'm sorry
##########################
######### GLOBAL #########
ytdlopts = {
    'format': 'bestaudio[ext!=webm]',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # ipv6 addresses cause issues sometimes
    'reconnect': 1,
    'reconnect_streamed': 1,
    'reconnect_delay_max': 5
}

ffmpegopts = {
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = YoutubeDL(ytdlopts)

QUEUE = Queue(maxsize=0)
CP = {"Playing": None}  # Current playing song

url_rx = re.compile('https?:\\/\\/(?:www\\.)?.+')  # https://regex101.com/r/4Q5Z5C/1


##########################


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")


@bot.slash_command(name="help", description="Shows all possible commands and a description of what they do.")
async def helpCmd(ctx):
    # list all commands and their descriptions
    embed = discord.Embed(title="Help", description="List of commands", color=0xeee657)
    embed.add_field(name="/help", value="Shows all possible commands and a description of what they do.", inline=False)
    embed.add_field(name="/join", value="Joins the voice channel you are currently in.", inline=False)
    embed.add_field(name="/leave", value="Leaves the voice channel.", inline=False)
    embed.add_field(name="/play", value="Plays a song from a youtube link or a search query.", inline=False)
    embed.add_field(name="/pause", value="Pauses the current song.", inline=False)
    embed.add_field(name="/resume", value="Resumes the current song.", inline=False)
    embed.add_field(name="/skip", value="Skips the current song.", inline=False)
    embed.add_field(name="/queue", value="Shows the current queue.", inline=False)
    embed.add_field(name="/clear", value="Clears the current queue.", inline=False)
    await ctx.respond(embed=embed)


@bot.slash_command()
async def ping(ctx):
    await ctx.respond(f"Pong! {round(bot.latency * 1000)}ms")


@bot.slash_command(name="join", description="Joins the voice channel you are currently in.")
async def join(ctx):
    """Joins the voice channel you are currently in."""
    if ctx.voice_client is not None:
        await ctx.respond("I am already in a voice channel")
    if ctx.author.voice is None:
        await ctx.respond("You are not in a voice channel")
    await ctx.author.voice.channel.connect()
    await ctx.respond(f"Joined {ctx.author.voice.channel}")


@bot.slash_command(name="play", description="Plays a song from a youtube link or a search query.")
async def play(ctx, url: str):
    with ctx.typing():
        if url_rx.match(url):
            if "youtube" in url:
                song_data = ytdl.extract_info(url, download=False)
                if song_data is None:
                    await ctx.respond("Invalid URL")
                if ctx.voice_client is None:
                    await ctx.author.voice.channel.connect()
                await __queueManager(ctx, url)
        else:
            results = sp.search(q=url, limit=1)
            try:
                song_data = results['tracks']['items'][0]
            except IndexError:
                await ctx.respond("No results found")
                pass
            # search Youtubedl for the song
            song_data = ytdl.extract_info(f"ytsearch:{song_data['name']} by {song_data['artists'][0]['name']}. Music",
                                          download=False)
            if ctx.voice_client is None:
                await ctx.author.voice.channel.connect()
            print("Found: " + song_data['entries'][0]['webpage_url'])
            await __queueManager(ctx, song_data['entries'][0]['webpage_url'])


@bot.slash_command()
async def leave(ctx):
    try:
        await ctx.guild.voice_client.disconnect()
        await ctx.respond("Disconnected")
    except AttributeError:
        await ctx.respond("I am not connected to a voice channel")


@bot.slash_command(name="pause", description="Pauses the current song.", aliases=["continue", "p"])
async def pause(ctx):
    try:
        if ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.respond("Paused")
        elif ctx.voice_client.is_paused():
            try:
                ctx.voice_client.resume()
                await ctx.respond("Resumed")
            except discord.ClientException:
                await ctx.respond("I am not connected to a voice channel")
        else:
            await ctx.respond("I am not playing anything")
    except AttributeError:
        await ctx.respond("I am not connected to a voice channel")


@bot.slash_command(name="queue", description="Show the queue")
async def viewQueue(ctx):
    global QUEUE
    global CP
    embed = discord.Embed(title="Queue", description="List of songs in the queue", color=0xeee657)
    try:
        embed.add_field(name=f"0. {CP['title']}", value=f"Duration: {parse_duration(CP['duration'])}",
                        inline=False)
    except KeyError:
        await ctx.respond("The queue is empty")
    for index, song in zip(range(50), QUEUE.queue):
        with ytdl:
            song_info = ytdl.extract_info(song, download=False)
        embed.add_field(name=f"{index + 1}. {song_info['title']}",
                        value=f"Duration: {parse_duration(song_info['duration'])}",
                        inline=False)
    await ctx.respond(embed=embed)


@bot.slash_command(name="skip", description="Skip the current song")
async def skip(ctx):
    global QUEUE
    await ctx.respond("Not Implemented")


@bot.slash_command(name="clear", description="Clear the queue")
async def clear(ctx):
    global QUEUE
    QUEUE.queue.clear()
    await ctx.respond("Cleared the queue")


@bot.slash_command(name="stop", description="Stop the music")
async def stop(ctx):
    global QUEUE
    QUEUE.queue.clear()
    try:
        ctx.voice_client.stop()
        await ctx.respond("Stopped")
    except AttributeError:
        await ctx.respond("I am not connected to a voice channel")


@bot.slash_command(name="shuffle", description="Shuffle the queue")
async def shuffle(ctx):
    global QUEUE
    # convert queue to list
    queue_list = list(QUEUE.queue)
    # shuffle list
    random.shuffle(queue_list)
    # convert list back to queue
    QUEUE.queue = queue_list
    await ctx.respond("Shuffled the queue")


@bot.slash_command(name="nowplaying", description="Query the song playing")
async def nowplaying(ctx):
    global CP
    embed = discord.Embed(title="Now Playing", description=f"Currently playing {CP['title']}",
                          color=0xeee657)
    try:
        embed.add_field(name="Album", value=f"{CP['album']}", inline=False)
    except KeyError:
        embed.add_field(name="Album", value="Unknown", inline=False)
    try:
        embed.add_field(name="Artists", value=f"{CP['artists']}", inline=False)
    except KeyError:
        embed.add_field(name="Artists", value="Unknown", inline=False)
    embed.add_field(name="Duration", value=f"{parse_duration(CP['duration'])}", inline=False)
    embed.add_field(name="Views", value=f"{CP['view_count']}", inline=False)
    embed.add_field(name="URL", value=f"{CP['webpage_url']}", inline=False)
    embed.add_field(name="Upload Date", value=f"{CP['upload_date']}", inline=False)
    embed.set_thumbnail(url=CP['thumbnail'])
    await ctx.respond(embed=embed)


@bot.slash_command(name="add", description="Recursively add songs to the queue from a playlist")  # Not complete!
async def recursiveAdd(ctx, url: str):
    await ctx.respond("Not Implemented")


@bot.application_command(name="devqueue", description="PRINT QUEUE TO STDOUT")
async def printQueue(ctx):
    global QUEUE
    print(QUEUE.queue)


async def process_audio(ctx, url):
    """Raw audio wrapper
    ctx: context
    url: url of the song (Direct link to the song)"""
    global QUEUE
    print("Told to play \n" + url)
    try:
        if ctx.voice_client is None:
            try:
                await ctx.author.voice.channel.join()
            except TypeError:
                pass
        print(ffmpegopts['before_options'], ffmpegopts['options'], url)
        try:
            ctx.voice_client.play(discord.FFmpegPCMAudio(url, before_options=ffmpegopts['before_options'],
                                                               options=ffmpegopts['options']),
                                        after=lambda o: asyncio.run_coroutine_threadsafe(__queueManager
                                                                                         (ctx, None), bot.loop))
        except discord.errors.ApplicationCommandInvokeError as e:
            await ctx.channel.send("Something went wrong " + str(e))
    except discord.ClientException:
        pass


async def __queueManager(ctx, song_data=None):
    global QUEUE
    global CP
    if song_data is not None:  # Chill it's for song name
        with ytdl:
            song_info = ytdl.extract_info(song_data, download=False)
    print(ctx.voice_client.is_paused(), ctx.voice_client.is_playing())
    if not ctx.voice_client.is_paused():
        if QUEUE.empty() and song_data is not None:
            QUEUE.put(song_data)
            await ctx.channel.send(f"Added {song_info['title']} to the queue")
            ######################
            if not QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
                song = QUEUE.get()
                with ytdl:
                    CP = ytdl.extract_info(song, download=False)
                await ctx.channel.send(f"Now playing {CP['title']}")
                await process_audio(ctx, CP['formats'][0]['url'])
        elif not QUEUE.empty() and song_data is not None:
            QUEUE.put(song_data)
            await ctx.channel.send(f"Added {song_info['title']} to the queue at position {QUEUE.qsize()}")
        elif not QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
            # OMG it's the actual bot requesting more music better give it some
            song = QUEUE.get()
            with ytdl:
                CP = ytdl.extract_info(song, download=False)
            await ctx.channel.send(f"Now playing {CP['title']}")
            await process_audio(ctx, CP['formats'][0]['url'])
        elif QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
            await ctx.channel.send("The queue is empty")
            await leave(ctx)


#def _handle_error(ctx, error):



# Loop every 1 second to check what the progress of music is
@tasks.loop(seconds=1)
async def progress():
    global CP
    if CP is not None:
        print(f"{CP['title']} is {CP['duration']}")


def parse_duration(duration: int):
    minutes, seconds = divmod(duration, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    duration = []
    if days > 0:
        duration.append('{} days'.format(days))
    if hours > 0:
        duration.append('{} hours'.format(hours))
    if minutes > 0:
        duration.append('{} minutes'.format(minutes))
    if seconds > 0:
        duration.append('{} seconds'.format(seconds))

    return ', '.join(duration)


bot.run(TOKEN)
