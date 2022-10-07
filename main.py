import asyncio
from functools import partial
from pprint import pprint
from queue import Queue
import random

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

######### GLOBAL #########
ytdlopts = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'  # ipv6 addresses cause issues sometimes
}

ffmpegopts = {
    'before_options': '-nostdin',
    'options': '-vn'
}

ytdl = YoutubeDL(ytdlopts)

QUEUE = Queue(maxsize=0)
##########################


@bot.event
async def on_ready():
    global QUEUE
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


@bot.slash_command()
async def join(ctx):
    if ctx.author.voice is None:
        await ctx.respond("You are not connected to a voice channel")
        return
    else:
        channel = ctx.author.voice.channel
    try:
        await channel.connect()
        await ctx.respond(f"Connected to {channel}")
    except discord.ClientException:
        await ctx.respond("I am already connected to a voice channel")


@bot.slash_command()
async def play(ctx, url: str):
    # get song title from url
    song_data = ytdl.extract_info(url, download=False)
    if song_data is None:
        await ctx.respond("Invalid URL")
        return
    if ctx.voice_client is None:
        await join(ctx)
    await __queueManager(ctx, url)


@bot.slash_command()
async def leave(ctx):
    try:
        await ctx.guild.voice_client.disconnect()
        await ctx.respond("Disconnected")
    except AttributeError:
        await ctx.respond("I am not connected to a voice channel")


@bot.slash_command()
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


@bot.slash_command()
async def resume(ctx):
    await pause(ctx)


@bot.slash_command(name="search", description="Search for a song")
async def search(ctx, query: str):
    global QUEUE
    await ctx.respond(f"Searching for {query}.")
    # Use spotify's API to search for a song
    results = sp.search(q=query, limit=1)
    for idx, track in enumerate(results['tracks']['items']):
        term = f"{track['name']} by {track['artists'][0]['name']}"
        # Get the url of the song from YouTube
        url = YoutubeDL(ytdlopts).extract_info(f"ytsearch:{term}", download=False)['entries'][0]['webpage_url']
        # pprint(song_info)
        # holy fuck that's a lot of info
        await play(ctx, url)


@bot.slash_command(name="queue", description="Show the queue")
async def viewQueue(ctx):
    global QUEUE
    embed = discord.Embed(title="Queue", description="List of songs in the queue", color=0xeee657)
    for index, song in zip(range(50), QUEUE.queue):
        with ytdl:
            song_info = ytdl.extract_info(song, download=False)
        embed.add_field(name=f"{index + 1}. {song_info['title']}", value=f"Duration: {song_info['duration']}",
                        inline=False)
    await ctx.respond(embed=embed)


@bot.slash_command(name="skip", description="Skip the current song")
async def skip(ctx):
    global QUEUE
    try:
        ctx.voice_client.stop()
        await ctx.respond("Skipped")
    except AttributeError:
        await ctx.respond("I am not connected to a voice channel")


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


@bot.slash_command(name="add", description="Recursively add songs to the queue from a playlist") # Not complete!
async def recursiveAdd(ctx, url: str):
    global QUEUE
    await ctx.respond(f"Adding songs from {url} to the queue.")
    # if bot not in voice channel, join
    if ctx.voice_client is None:
        await join(ctx)
    if url.__contains__("youtube.com/playlist?list="):
        with ytdl:
            result = ytdl.extract_info(url, download=False)  # We just want to extract the info
            if 'entries' in result:
                # Can be a playlist or a list of videos
                video = result['entries']
                # loops entries to grab each video_url
                async for i, item in video:
                    video = result['entries'][i]
                    await search(ctx, video['title'])
    elif url.__contains__("spotify.com/playlist/"):
        # use spotify's API to get the playlist
        playlist = sp.playlist(url)
        # loop through each song in the playlist
        print(len(playlist['tracks']['items']))
        for x in range(len(playlist['tracks']['items'])):
            song = playlist['tracks']['items'][x]
            print(song['track']['name'])
            # get the song's name and artist
            term = f"{song['track']['name']} by {song['track']['artists'][0]['name']}"
            # get the url of the song from YouTube
            url = YoutubeDL(ytdlopts).extract_info(f"ytsearch:{term}", download=False)['entries'][0]['webpage_url']
            # add the song to the queue
            QUEUE.put(url)


async def process_audio(ctx, url):
    global QUEUE
    try:
        if ctx.voice_client is None:
            await ctx.author.voice.channel.join()
        # open youtube video and get the stream url
        with ytdl:
            song_info = ytdl.extract_info(url, download=False)
        await ctx.voice_client.play(discord.FFmpegPCMAudio(song_info['formats'][0]['url']), after=partial(_handle_error,
                                                                                                          ctx))
    except discord.ClientException:
        return False


async def __queueManager(ctx, song_data=None):
    global QUEUE
    if song_data is not None:
        with ytdl:
            song_info = ytdl.extract_info(song_data, download=False)
    print(ctx.voice_client.is_paused(), ctx.voice_client.is_playing())
    if not ctx.voice_client.is_paused():
        if QUEUE.empty() and song_data is not None:
            QUEUE.put(song_data)
            await ctx.respond(f"Added {song_info['title']} to the queue")
            await __queueManager(ctx, None)
        elif not QUEUE.empty() and song_data is not None:
            QUEUE.put(song_data)
            await ctx.respond(f"Added {song_info['title']} to the queue at position {QUEUE.qsize()}")
        elif not QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
            song = QUEUE.get()
            with ytdl:
                song = ytdl.extract_info(song, download=False)
            await ctx.respond(f"Now playing {song['title']}")
            await process_audio(ctx, song['formats'][0]['url'])
        elif QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
            await ctx.respond("The queue is empty")
            await leave(ctx)


def _handle_error(ctx, error):
    asyncio.run_coroutine_threadsafe(__queueManager(ctx, None), bot.loop)


bot.run(TOKEN)
