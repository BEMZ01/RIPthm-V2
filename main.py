import asyncio
from pprint import pprint
from queue import Queue
import random
import re
import discord
import dotenv
import os
import logging
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from yt_dlp import YoutubeDL
from threading import Thread
from colorthief import ColorThief
from discord.commands import Option
from discord.ext import commands
import urllib.request

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
# https://www.youtube.com/playlist?list=PLrAJUJfWhRegflDEnh5xUvV4OXlDSy2fI
##########################
######### GLOBAL #########

url_rx = re.compile('https?:\\/\\/(?:www\\.)?.+')  # https://regex101.com/r/4Q5Z5C/1
HEX = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "B", "C", "D", "E", "F"]
ytdlopts = {
    'format': 'bestaudio[ext!=webm]',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': True,
    'quiet': False,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # ipv6 addresses cause issues sometimes
    'reconnect': 1,
    'reconnect_streamed': 1,
    'reconnect_delay_max': 5,
    'extract_flat': True,
    'skip_download': True
}
ytdlopts_music = {
    'format': 'bestaudio[ext!=webm]',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': True,
    'quiet': False,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # ipv6 addresses cause issues sometimes
    'reconnect': 1,
    'reconnect_streamed': 1,
    'reconnect_delay_max': 5
}
ytdlopts_slim = {
    'extract_flat': True,
    'skip_download': True
}

ffmpegopts = {
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = YoutubeDL(ytdlopts)
ytdl_music = YoutubeDL(ytdlopts_music)
ytdl_slim = YoutubeDL(ytdlopts_slim)


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
    await ctx.respond(f"Pong! {round(bot.latency * 1000)}ms", delete_after=10)


def __ThreadedDownload(thumbnail_url, loc):
    urllib.request.urlretrieve(thumbnail_url, loc)


def parse_date(date: str):
    """Takes a date in YYYYMMDD format and inserts slashes to make DD/MM/YYYY"""
    return f"{date[6:]}/{date[4:6]}/{date[:4]}"


def setup(bot):
    bot.add_cog(Music(bot))


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


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.LOOP = 0
        self.QUEUE = Queue(maxsize=0)
        self.CP = {"Playing": None}
        self.Played_songs = []

    @discord.slash_command(name="join", description="Joins the voice channel you are currently in.")
    async def join(self, ctx):
        """Joins the voice channel you are currently in."""
        if ctx.voice_client is not None:
            await ctx.respond("I am already in a voice channel", delete_after=5)
        if ctx.author.voice is None:
            await ctx.respond("You are not in a voice channel", delete_after=5)
        await ctx.author.voice.channel.connect()
        await ctx.respond(f"Joined {ctx.author.voice.channel}", delete_after=5)

    @discord.slash_command(name="play", description="Plays a song from a youtube link or a search query.")
    async def play(self, ctx, url: str):
        await ctx.defer()
        await ctx.respond(":white_check_mark:", delete_after=5)
        with ctx.typing():
            if url_rx.match(url):
                if "spotify" in url:
                    song_data = sp.track(url)
                    # search for song on YouTube
                    song_data = ytdl_music.extract_info(
                        f"ytsearch:{song_data['name']} by {song_data['artists'][0]['name']}. YouTube Music",
                        download=False)
                    url = song_data['entries'][0]['webpage_url']
                elif "youtube" in url:
                    with ytdl_music:
                        song_data = ytdl_music.extract_info(url, download=False)
                        url = song_data['webpage_url']
                else:
                    await ctx.respond("Invalid URL", delete_after=5)
                    pass
                print("Attempt to play: " + url)
                await self.__queueManager(ctx, url)
            else:
                results = sp.search(q=url, limit=1)
                try:
                    song_data = results['tracks']['items'][0]
                except IndexError:
                    await ctx.respond("No results found", delete_after=5)
                    pass
                # search Youtubedl for the song
                song_data = ytdl.extract_info(
                    f"ytsearch:{song_data['name']} by {song_data['artists'][0]['name']}. YouTube Music",
                    download=False)
                print("Found: " + song_data['entries'][0]['url'])
                await self.__queueManager(ctx, song_data['entries'][0]['url'])

    @discord.slash_command()
    async def leave(self, ctx):
        try:
            await ctx.guild.voice_client.disconnect()
            await ctx.respond("Disconnected", delete_after=5)
        except AttributeError:
            await ctx.respond("I am not connected to a voice channel", delete_after=5)

    @discord.slash_command(name="pause", description="Pauses the current song.", aliases=["continue", "p"])
    async def pause(self, ctx):
        try:
            if ctx.voice_client.is_playing():
                ctx.voice_client.pause()
                await ctx.respond("Paused", delete_after=5)
            elif ctx.voice_client.is_paused():
                try:
                    ctx.voice_client.resume()
                    await ctx.respond("Resumed", delete_after=5)
                except discord.ClientException:
                    await ctx.respond("I am not connected to a voice channel", delete_after=5)
            else:
                await ctx.respond("I am not playing anything", delete_after=5)
        except AttributeError:
            await ctx.respond("I am not connected to a voice channel", delete_after=5)

    @discord.slash_command(name="queue", description="Show the queue")
    async def viewQueue(self, ctx):
        await ctx.defer()
        try:
            embed = discord.Embed(title="Queue",
                                  description=f"List of the next 10 songs in the queue. ({len(self.QUEUE.queue)}"
                                              f" total)", color=0xeee657)
            try:
                embed.add_field(name=f"CP. {self.CP['title']}",
                                value=f"Duration: {parse_duration(self.CP['duration'])}",
                                inline=False)
            except KeyError:
                await ctx.respond("The queue is empty", delete_after=5)
            for index, song in zip(range(10), self.QUEUE.queue):
                with ytdl_slim:
                    song_info = ytdl_slim.extract_info(song, download=False)
                embed.add_field(name=f"{index + 1}. {song_info['title']}",
                                value=f"Duration: {parse_duration(song_info['duration'])}",
                                inline=False)
            await ctx.respond(embed=embed)
        except RuntimeError:
            await ctx.respond("The queue is currently being modified, please try again later.", delete_after=5)

    @discord.slash_command(name="skip", description="Skip the current song")
    async def skip(self, ctx):
        await ctx.defer()
        try:
            if self.QUEUE.empty():
                await ctx.respond("The queue is empty", delete_after=5)
            else:
                # add the song to the history
                if self.LOOP == 2:
                    self.Played_songs.append(f"{self.CP['title']} by {self.CP['uploader']}. YouTube Music")
                ctx.voice_client.stop()
                await ctx.respond("Skipped", delete_after=5)
        except RuntimeError:
            await ctx.respond("The queue is currently being modified, please try again later.", delete_after=5)

    @discord.slash_command(name="clear", description="Clear the queue")
    async def clear(self, ctx):
        self.QUEUE.queue.clear()
        Played_songs = []
        await ctx.respond("Cleared the queue", delete_after=5)

    @discord.slash_command(name="stop", description="Stop the music and clear the queue.")
    async def stop(self, ctx):
        self.QUEUE.queue.clear()
        Played_songs = []
        try:
            ctx.voice_client.stop()
            await ctx.respond("Stopped", delete_after=5)
        except AttributeError:
            await ctx.respond("I am not connected to a voice channel", delete_after=5)

    @discord.slash_command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, ctx):
        queue_list = list(self.QUEUE.queue)
        random.shuffle(queue_list)
        self.QUEUE.queue.clear()
        for song in queue_list:
            self.QUEUE.put(song)
        await ctx.respond("Shuffled the queue", delete_after=5)

    @discord.slash_command(name="nowplaying", description="Query the song playing", aliases=["np", "info", "song"])
    async def nowplaying(self, ctx):
        embed = discord.Embed(title="Now Playing", description=f"Currently playing {self.CP['title']}",
                              color=self.GetEmbedColor(self.CP['thumbnail']))
        try:
            embed.add_field(name="Album", value=f"{self.CP['album']}", inline=False)
        except KeyError:
            embed.add_field(name="Album", value="Unknown", inline=False)
        try:
            embed.add_field(name="Artists", value=f"{self.CP['artists']}", inline=False)
        except KeyError:
            embed.add_field(name="Artists", value="Unknown", inline=False)
        embed.add_field(name="Duration", value=f"{parse_duration(self.CP['duration'])}", inline=False)
        embed.add_field(name="Views", value=f"{self.CP['view_count']}", inline=False)
        embed.add_field(name="URL", value=f"{self.CP['webpage_url']}", inline=False)
        embed.add_field(name="Upload Date", value=f"{parse_date(self.CP['upload_date'])}", inline=False)
        embed.set_thumbnail(url=self.CP['thumbnail'])
        await ctx.respond(embed=embed)

    @discord.slash_command(name="add", description="Recursively add songs to the queue from a playlist")
    async def recursiveAdd(self, ctx, url: str):
        await ctx.defer()
        try:
            with ctx.typing():
                if url_rx.match(url):
                    if "youtube" in url:
                        t = Thread(target=self.__PlaylistThread, args=(ctx, url, True))
                    elif "spotify" in url:
                        t = Thread(target=self.__PlaylistThread, args=(ctx, url, False))
                    t.start()
                    await asyncio.sleep(5)
                    await self.__queueManager(ctx)
                else:
                    await ctx.respond("Invalid URL", delete_after=5)
        except RuntimeError:
            await ctx.respond("The queue is currently being modified, please try again later.", delete_after=5)

    @discord.slash_command(name="loop", description="Toggle the loop state")
    async def loop(self, ctx, state: Option(str, "Optional. Either off, song or queue.", required=False, default=None)):
        """Toggle loop state between off, song and queue"""
        if state:
            if state.lower() == "off":
                self.LOOP = 0
                await ctx.respond("Looping disabled", delete_after=5)
            elif state.lower() == "song":
                self.LOOP = 1
                await ctx.respond("Looping song", delete_after=5)
            elif state.lower() == "queue":
                self.LOOP = 2
                self.Played_songs = []
                await ctx.respond("Looping queue", delete_after=5)
            else:
                await ctx.respond("Invalid state", delete_after=5)
        else:
            if self.LOOP == 0:
                self.LOOP = 1
                await ctx.respond("Looping song", delete_after=5)
            elif self.LOOP == 1:
                self.LOOP = 2
                self.Played_songs = []
                await ctx.respond("Looping queue", delete_after=5)
            elif self.LOOP == 2:
                self.LOOP = 0
                await ctx.respond("Looping disabled", delete_after=5)
        print("LOOP STATE: ", self.LOOP)

    async def process_audio(self, ctx, url):
        """Raw audio wrapper
        ctx: context
        url: url of the song (Direct link to the song)"""
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
                                      after=lambda o: asyncio.run_coroutine_threadsafe(self.__queueManager(ctx, None),
                                                                                       self.bot.loop))
            except discord.errors.ApplicationCommandInvokeError as e:
                await ctx.channel.send("Something went wrong " + str(e))
        except discord.ClientException:
            pass

    async def __queueManager(self, ctx, song_data=None):
        pprint(self.QUEUE.queue)
        pprint(self.Played_songs)
        print("__________")
        if ctx.voice_client is None:
            await ctx.author.voice.channel.connect()
        if self.LOOP == 2:
            for i in self.Played_songs:
                self.QUEUE.put(i)
            self.Played_songs = []
        if song_data is not None:  # Chill it's for song name
            with ytdl:
                song_info = ytdl.extract_info(song_data, download=False)
        await asyncio.sleep(1)
        if not ctx.voice_client.is_paused():
            if self.QUEUE.empty() and song_data is not None:
                self.QUEUE.put(song_data)
                await ctx.channel.send(f"Added {song_info['title']} to the queue", delete_after=5)
                if not self.QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
                    if self.LOOP == 1:
                        # no loop 2 here
                        await self.process_audio(ctx, self.CP['url'])
                    else:
                        song = self.QUEUE.get()
                        with ytdl_music:
                            CP = ytdl.extract_info(song, download=False)
                        await ctx.channel.send(f":musical_note:  {CP['title']}")
                        if self.LOOP == 2:
                            self.Played_songs.append(CP['webpage_url'])
                        await self.process_audio(ctx, CP['url'])
            elif not self.QUEUE.empty() and song_data is not None:
                self.QUEUE.put(song_data)
                await ctx.channel.send(f"Added {song_info['title']} to the queue at position {self.QUEUE.qsize()}",
                                       delete_after=5)
            elif not self.QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
                # OMG it's the actual bot requesting more music better give it some
                if self.LOOP == 1:
                    await self.process_audio(ctx, self.CP['url'])
                else:
                    pprint(self.QUEUE.queue)
                    pprint(self.Played_songs)
                    print("__________")
                    song = self.QUEUE.get()
                    with ytdl_music:
                        CP = ytdl.extract_info(song, download=False)
                    if not ctx.voice_client.is_playing():
                        await ctx.channel.send(f":musical_note:  {CP['title']}")
                    if self.LOOP == 2:
                        self.Played_songs.append(CP['webpage_url'])
                    await self.process_audio(ctx, CP['url'])
            elif self.QUEUE.empty() and ctx.voice_client is not None and not ctx.voice_client.is_playing():
                self.Played_songs = []
                await self.leave(ctx)

    def __PlaylistThread(self, ctx, playlist_url, YT):
        """Inner thread subroutine to add songs to playlist"""
        if YT:
            # playlist is YT
            with ytdl_slim:
                playlist = ytdl_slim.extract_info(playlist_url, download=False)
                msg = asyncio.run_coroutine_threadsafe(
                    ctx.respond(f"Adding {len(playlist['entries'])} songs to the queue")
                    , self.bot.loop).result(10)
                for i, song in enumerate(playlist['entries']):
                    self.QUEUE.put("https://www.youtube.com/watch?v=" + str(song['id']))
                    if i % 4 == 0:
                        asyncio.run_coroutine_threadsafe(msg.edit(content=f"Adding {len(playlist['entries'])} songs to "
                                                                          f"the queue. ("
                                                                          f"{(int(i) / int(len(playlist['entries']))) * 100}"
                                                                          f"%)"), self.bot.loop)
            asyncio.run_coroutine_threadsafe(msg.edit(content=f"Adding {len(playlist['entries'])} songs to "
                                                              f"the queue. (:white_check_mark:)"), self.bot.loop)
        else:
            if "playlist" in playlist_url:
                # playlist is Spotify
                playlist = sp.playlist(playlist_url)['tracks']
                # get all songs
                msg = asyncio.run_coroutine_threadsafe(ctx.respond(f"Adding {int(playlist['total'])} "
                                                                   f"songs to the queue. (0%)", ephemeral=True),
                                                       self.bot.loop) \
                    .result(10)
                out = []
                for i in playlist['items']:
                    out.append(f"{i['track']['name']} by {i['track']['artists'][0]['name']}. YouTube Music")
                    # Add the first 100 songs
                while playlist['next']:  # If there are more
                    print("Getting nxt 100 songs")
                    playlist = sp.next(playlist)
                    for i in playlist['items']:
                        out.append(f"{i['track']['name']} by {i['track']['artists'][0]['name']}. YouTube Music")
                for i, song in enumerate(out):
                    if i % 4 == 0:
                        asyncio.run_coroutine_threadsafe(msg.edit(content=f"Adding {int(playlist['total'])} songs to "
                                                                          f"the queue. ("
                                                                          f"{(int(i) / int(playlist['total'])) * 100}"
                                                                          f"%)"), self.bot.loop)
                    song_data = ytdl_slim.extract_info(f"ytsearch:{song}", download=False)
                    if ctx.voice_client is None:
                        asyncio.run_coroutine_threadsafe(ctx.author.voice.channel.connect(), self.bot.loop)
                    print("Found: " + song_data['entries'][0]['url'])
                    self.QUEUE.put(song_data['entries'][0]['url'])
                asyncio.run_coroutine_threadsafe(msg.edit(content=f"Adding {int(playlist['total'])} songs to "
                                                                  f"the queue. (:white_check_mark:)"), self.bot.loop)
            if "album" in playlist_url:
                # playlist is Spotify album
                playlist = sp.album(playlist_url)
                # get all songs
                msg = asyncio.run_coroutine_threadsafe(ctx.respond(f"Adding {int(playlist['total_tracks'])} "
                                                                   f"songs to the queue. (0%)", ephemeral=True),
                                                       self.bot.loop) \
                    .result(10)
                out = []
                for i in playlist['tracks']['items']:
                    out.append(f"{i['name']} by {i['artists'][0]['name']}. YouTube Music")
                    # Add the first 100 songs
                while playlist['tracks']['next']:
                    print("Getting nxt 100 songs")
                    playlist = sp.next(playlist['tracks'])
                    for i in playlist['items']:
                        out.append(f"{i['name']} by {i['artists'][0]['name']}. YouTube Music")
                for i, song in enumerate(out):
                    if i % 4 == 0:
                        asyncio.run_coroutine_threadsafe(
                            msg.edit(content=f"Adding {int(playlist['total_tracks'])} songs to "
                                             f"the queue. ("
                                             f"{(int(i) / int(playlist['total_tracks'])) * 100}"
                                             f"%)"), self.bot.loop)
                    song_data = ytdl_slim.extract_info(f"ytsearch:{song}", download=False)
                    if ctx.voice_client is None:
                        asyncio.run_coroutine_threadsafe(ctx.author.voice.channel.connect(), self.bot.loop)
                    print("Found: " + song_data['entries'][0]['url'])
                    self.QUEUE.put(song_data['entries'][0]['url'])
                    asyncio.run_coroutine_threadsafe(msg.edit(content=f"Adding {int(playlist['total'])} songs to "
                                                                      f"the queue. (:white_check_mark:)"),
                                                     self.bot.loop)

    def GetEmbedColor(self, thumbnail_url):
        # make sure the format matches remote image
        print(thumbnail_url.split(".")[-1])
        t = Thread(target=self.__ThreadedDownload,
                   args=(thumbnail_url, "temp/thumb." + str(thumbnail_url.split(".")[-1])))
        t.start()
        t.join()
        color_thief = ColorThief("temp/thumb." + str(thumbnail_url.split(".")[-1]))
        dominant_color = color_thief.get_color(quality=1)
        os.remove("temp/thumb." + str(thumbnail_url.split(".")[-1]))
        return int('0x{:X}{:X}{:X}'.format(dominant_color[0], dominant_color[1], dominant_color[2]), 16)


if __name__ == "__main__":
    bot.add_cog(Music(bot))
    bot.run(TOKEN)
