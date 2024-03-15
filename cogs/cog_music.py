import asyncio
import datetime
import io
import json
import logging
import random
import re
from pprint import pprint
from threading import Thread
import aiohttp
import discord
import lavalink
import lyricsgenius
import spotipy
from PIL import Image
from discord import option, ButtonStyle
from discord.ext.pages import Paginator
from discord.ui import Button, View
from lavalink import LoadType
from spotipy.oauth2 import SpotifyClientCredentials
import sponsorblock as sb
from dotenv import load_dotenv
from discord.ext import commands, tasks
from lavalink.filters import *
import os
from copy import deepcopy

load_dotenv()
SPOTIFY_CLIENT_ID = str(os.getenv('SPOTIFY_CLIENT_ID'))
SPOTIFY_CLIENT_SECRET = str(os.getenv('SPOTIFY_SECRET'))
url_rx = re.compile(r'https?://(?:www\.)?.+')
sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID,
                                                                         client_secret=SPOTIFY_CLIENT_SECRET))
sbClient = sb.Client()

logger = logging.getLogger(__name__)


class Effect:
    def __init__(self, nightcore: bool = False, vaporwave: bool = False):
        self.nightcore = nightcore
        self.vaporwave = vaporwave

    def set(self, tag: str, state: bool):
        if tag == "nightcore":
            self.nightcore = state
        elif tag == "vaporwave":
            self.vaporwave = state


class LavalinkVoiceClient(discord.VoiceClient):
    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        self.client = client
        self.channel = channel
        # ensure a client already exists
        if hasattr(self.client, 'lavalink'):
            self.lavalink = self.client.lavalink
        else:
            self.client.lavalink = lavalink.Client(client.user.id)
            self.client.lavalink.add_node(
                'localhost',
                2333,
                'youshallnotpass',
                'us',
                'default-node'
            )
            self.lavalink = self.client.lavalink

    async def on_voice_server_update(self, data):
        # the data needs to be transformed before being handed down to
        # voice_update_handler
        lavalink_data = {
            't': 'VOICE_SERVER_UPDATE',
            'd': data
        }
        try:
            player = self.lavalink.player_manager.get(self.client.voice_clients[0].channel.guild.id)
            if player.fetch('VoiceChannel') is not None:
                player.store('VoiceChannel', self.client.voice_clients[0].channel.id)
                logger.info("I've been moved to a new channel. Updating variables...")
        except KeyError:
            return
        await self.lavalink.voice_update_handler(lavalink_data)

    async def on_voice_state_update(self, data):
        """ Called first """
        player = self.lavalink.player_manager.get(self.client.voice_clients[0].channel.guild.id)
        # the data needs to be transformed before being handed down to
        # voice_update_handler
        lavalink_data = {
            't': 'VOICE_STATE_UPDATE',
            'd': data
        }
        # Test if bot has been kicked
        if int(data['user_id']) == int(self.client.user.id) and player.is_playing:
            if data['channel_id'] is None:
                logger.info("I've been kicked from a channel.")
                player.store("VoiceStatus", "-1")
                player.channel_id = None
                self.cleanup()
                player.queue.clear()
        elif player.fetch('VoiceState') == "0":
            player.store("VoiceState", None)
        await self.lavalink.voice_update_handler(lavalink_data)

    async def connect(self, *, timeout: float, reconnect: bool, self_deaf: bool = True,
                      self_mute: bool = False) -> None:
        """
        Connect the bot to the voice channel and create a player_manager
        if it doesn't exist yet.
        """
        # ensure there is a player_manager when creating a new voice_client
        self.lavalink.player_manager.create(guild_id=self.channel.guild.id)
        await self.channel.guild.change_voice_state(channel=self.channel, self_mute=self_mute, self_deaf=self_deaf)

    async def disconnect(self, *, force: bool = False) -> None:
        """
        Handles the disconnect.
        Cleans up running player and leaves the voice client.
        """
        player = self.lavalink.player_manager.get(self.channel.guild.id)

        # no need to disconnect if we are not connected
        if not force and not player.is_connected:
            return

        # None means disconnect
        await self.channel.guild.change_voice_state(channel=None)

        # update the channel_id of the player to None
        # this must be done because the on_voice_state_update that would set channel_id
        # to None doesn't get dispatched after the disconnect
        player.channel_id = None
        self.cleanup()


def progress_bar(player):
    # This is a helper function that generates a progress bar for the currently playing track.
    # It's not necessary for the cog to function, but it's a nice touch.
    bar_length = 12
    progress = (player.position / player.current.duration) * bar_length
    return f"[{'🟩' * int(progress)}{'⬜' * (bar_length - int(progress))}]"


def birthday_easteregg(userID):
    # read from a csv file
    with open("birthdays.csv", "r") as file:
        data = file.read()
    data = data.split("\n")
    for line in data:
        line = line.split(",")
        if line[2] == str(userID):
            return {"name": line[0], "date": line[1], "id": line[2]}
    return {"name": None, "date": None, "id": None}


class Music(commands.Cog):
    def __init__(self, bot):
        self.disconnect_timer = False
        self.bot = bot
        self.playing_message = None
        self.update_playing_message.start()
        self.test_vid.start()
        self.check_call.start()
        self.sponsorBlock = True
        self.Effect = Effect(True, True)
        self.genius = lyricsgenius.Genius(os.getenv('GENIUS_TOKEN'))
        self.genius.verbose = True
        self.genius.remove_section_headers = True
        self.genius.skip_non_songs = False
        self.CP = None
        self.stop_import = False
        bot.loop.create_task(self.connect())

    async def connect(self):
        await self.bot.wait_until_ready()
        if not hasattr(self.bot, 'lavalink'):  # This ensures the client isn't overwritten during cog reloads.
            self.bot.lavalink = lavalink.Client(self.bot.user.id)
            #self.bot.lavalink.add_node('192.168.1.235', 2333, os.getenv("LAVA_TOKEN"), 'eu',
            #                           'default-node')
            self.bot.lavalink.add_node(os.getenv("LAVA_ADDR"), int(os.getenv("LAVA_PORT")), os.getenv("LAVA_TOKEN"), 'eu',
                                       'default-node')
            self.bot.lavalink.add_event_hook(self.track_hook)
            # try to complete a test search
            results = await self.bot.lavalink.get_tracks("ytsearch:Test")
            if not results or not results['tracks']:
                print("Lavalink failed to connect.")
                return
            else:
                print("Lavalink connected.")
        else:
            print("Lavalink already connected.")

    @commands.Cog.listener()
    async def on_ready(self):
        # attempt to find old playing message
        for guild in self.bot.guilds:
            # search for a message with the embed title "Now Playing" that was sent by the bot and delete it
            for channel in guild.text_channels:
                try:
                    async for message in channel.history(limit=200):
                        if message.author == self.bot.user and message.embeds:
                            if message.embeds[0].title == "Now Playing":
                                await message.delete()
                                logger.info(f"Found old playing message in {guild.name}")
                except discord.errors.Forbidden:
                    pass
            print(f"Connected to {guild.name}")
        print(f"Connected to Discord as {self.bot.user}")

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandInvokeError):
            embed = discord.Embed(title="Error", description=f"```{error.original}```", color=discord.Color.red())
            logger.error(f"Error in {ctx.command.name}: {error.original}")
            await ctx.respond(embed=embed, ephemeral=True, delete_after=10)

    @tasks.loop(seconds=30)
    async def check_call(self):
        """Check the voice channel to see if the bot is the only one in the channel"""
        try:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            channel = self.bot.get_channel(player.fetch('VoiceChannel'))
            client = channel.guild.voice_client
        except AttributeError:
            logger.warning("check_call AttributeError")
            pass
        else:
            # if the player is connected and the bot is the only one in the channel (not counting other bots and itself)
            logger.debug(
                f'There are {len([member for member in channel.members if not member.bot])} members in the vc.')
            if player.is_connected and len(
                    [member for member in channel.members if not member.bot]) == 0 and not self.disconnect_timer:
                self.disconnect_timer = True
                await self.playing_message.channel.send(
                    "`I will leave the voice channel in 30 seconds if no one joins.`",
                    delete_after=60)
                await asyncio.sleep(30)
                if player.is_connected and len(
                        [member for member in channel.members if not member.bot]) == 0 and self.disconnect_timer:
                    self.disconnect_timer = False
                    self.stop_import = True
                    await player.set_pause(True)
                    await client.disconnect(force=True)
                    await player.reset_filters()
                    await self.playing_message.delete()
                    await self.playing_message.channel.send("`I have left the voice channel because I was alone.`\n"
                                                            "Unpause the music with `/pause`",
                                                            delete_after=10)
                else:
                    self.disconnect_timer = False

    @tasks.loop(seconds=5)
    async def update_playing_message(self):
        async def play_callback(interaction):
            await interaction.response.defer()
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            if player.paused:
                await player.set_pause(False)
            else:
                await player.set_pause(True)
            await self.update_playing_message()

        async def skip_callback(interaction):
            await interaction.response.defer()
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            await player.skip()
            await self.update_playing_message()

        async def stop_callback(interaction):
            await interaction.response.defer()
            player = self.bot.lavalink.player_manager.get(interaction.guild_id)
            if not self.bot.get_channel(player.channel_id):
                return await interaction.channel.send('Not connected.', delete_after=10)
            if interaction.user.voice.channel != self.bot.get_channel(player.channel_id):
                return await interaction.channel.send("You are not in the same voice channel as me.",
                                                      delete_after=10)
            self.stop_import = True
            self.bot.lavalink.player_manager.get(interaction.guild_id).queue.clear()
            await self.bot.lavalink.player_manager.get(interaction.guild_id).stop()
            for vc in self.bot.voice_clients:
                if vc.guild == interaction.guild:
                    await vc.disconnect()
            try:
                await self.playing_message.delete()
                self.playing_message = None
            except AttributeError:
                pass
            await interaction.channel.send('*⃣ | Disconnected.', delete_after=10)

        async def shuffle_callback(interaction):
            await interaction.response.defer()
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            player.shuffle = not player.shuffle
            await self.update_playing_message()

        async def loop_callback(interaction):
            await interaction.response.defer()
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            if player.loop == player.LOOP_NONE:
                player.loop = player.LOOP_QUEUE
            elif player.loop == player.LOOP_QUEUE:
                player.loop = player.LOOP_SINGLE
            elif player.loop == player.LOOP_SINGLE:
                player.loop = player.LOOP_NONE
            await self.update_playing_message()

        async def sponsorBlock_callback(interaction):
            await interaction.response.defer()
            self.sponsorBlock = not self.sponsorBlock
            await self.update_playing_message()

        try:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            channel = self.bot.get_channel(player.fetch('VoiceChannel'))
            client = channel.guild.voice_client
            if player.fetch('VoiceStatus') == "-1":  # We have been kicked from the channel
                self.stop_import = True
                await self.playing_message.channel.send("`I have been kicked from the voice channel.`\n||Something "
                                                        "something use the disconnect command and stop admin abuse :(||",
                                                        delete_after=10)
                await self.playing_message.delete()
                self.playing_message = None
                await player.stop()
                player.store("VoiceStatus", "0")
            title = re.sub(r'\([^)]*\)', '', player.current.title)
            title = re.sub(r'\[[^)]*\]', '', title)
            title = title.lower()
            title_blacklist = ["lyrics", "lyric", "official", "video", "audio", "music", "full", "hd", "hq", "remix",
                               "ost", "theme", "original", "version"]
            for word in title_blacklist:
                title = title.replace(word, "")
            title = title.strip()
            if " - " in title:
                title = title.split(" - ")
                author = title[0]
                title = title[1]
            elif "by" in title:
                title = title.split("by")
                author = title[1]
                title = title[0]
            elif " | " in title:
                title = title.split(" | ")
                author = title[0]
                title = title[1]
            else:
                author = None
            if player.current.author.contains("VEVO"):
                author = player.current.author.replace("VEVO", "")
            logger.debug(f"Raw: {player.current.title}\nTitle: {title}\nAuthor: {author}")
            song = self.genius.search_song(title, author).to_dict()
            if song is None:
                self.CP = (None, player.current.title)
            else:
                self.CP = (song, player.current.title)
        except AttributeError:
            pass
        if self.playing_message is None:
            return
        else:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            if player.current:
                # check loop status
                loop = ""
                if player.loop == player.LOOP_SINGLE:
                    loop = "🔂"
                elif player.loop == player.LOOP_QUEUE:
                    loop = "🔁"
                # check shuffle status
                if player.shuffle:
                    shuffle = "🔀"
                else:
                    shuffle = ""
                if self.CP is not None:
                    color = await Generate_color(self.CP[0]['song_art_image_url'])
                else:
                    color = await Generate_color(
                        f"https://img.youtube.com/vi/{player.current.identifier}/hqdefault.jpg")
                if player.paused:
                    embed = discord.Embed(title="Paused " + loop + " " + shuffle,
                                          description=f'[{player.current.title}]({player.current.uri})',
                                          color=color)
                else:
                    try:
                        embed = discord.Embed(title="Now Playing " + loop + " " + shuffle,
                                              description=f'[{player.current.title}]({player.current.uri})',
                                              color=color)
                    except AttributeError:
                        embed = discord.Embed(title="Now Playing " + loop + " " + shuffle,
                                              description=f'[UNABLE TO GET TITLE]',
                                              color=color)
                if self.CP is not None:
                    embed.set_thumbnail(url=self.CP[0]['song_art_image_url'])
                else:
                    embed.set_thumbnail(url=f"https://img.youtube.com/vi/{player.current.identifier}/hqdefault.jpg")
                if player.current is None:
                    return
                else:
                    embed.add_field(name='Duration', value=f'{lavalink.utils.format_time(player.position)}/'
                                                           f'{lavalink.utils.format_time(player.current.duration)} '
                                                           f'({int((player.position / player.current.duration) * 100)}'
                                                           f'%)',
                                    inline=False)
                    embed.add_field(name='Progress', value=progress_bar(player), inline=False)
                buttons = []
                if self.playing_message:
                    player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
                else:
                    return []
                buttons = []
                if player.paused:
                    buttons.append([Button(style=ButtonStyle.red, emoji="▶️", custom_id="play"), "play"])
                else:
                    buttons.append([Button(style=ButtonStyle.green, emoji="⏸️", custom_id="play"), "play"])
                buttons.append([Button(style=ButtonStyle.grey, emoji="⏭️", custom_id="skip"), "skip"])
                buttons.append([Button(style=ButtonStyle.grey, emoji="⏹️", custom_id="stop"), "stop"])
                if player.shuffle:
                    buttons.append([Button(style=ButtonStyle.green, emoji="🔀", custom_id="shuffle"), "shuffle"])
                else:
                    buttons.append([Button(style=ButtonStyle.red, emoji="🔀", custom_id="shuffle"), "shuffle"])
                if player.loop == player.LOOP_NONE:
                    buttons.append([Button(style=ButtonStyle.red, emoji="🔂", custom_id="loop"), "loop"])
                elif player.loop == player.LOOP_SINGLE:
                    buttons.append([Button(style=ButtonStyle.green, emoji="🔂", custom_id="loop"), "loop"])
                elif player.loop == player.LOOP_QUEUE:
                    buttons.append([Button(style=ButtonStyle.green, emoji="🔁", custom_id="loop"), "loop"])
                if self.sponsorBlock:
                    buttons.append(
                        [Button(style=ButtonStyle.green, emoji="🚫", custom_id="sponsorBlock"), "sponsorBlock"])
                else:
                    buttons.append([Button(style=ButtonStyle.red, emoji="🚫", custom_id="sponsorBlock"), "sponsorBlock"])
                view = discord.ui.View()
                for button in buttons:
                    # add the callback to the button
                    button[0].callback = locals()[f"{button[1]}_callback"]
                    view.add_item(button[0])
                try:
                    await self.playing_message.edit("", embed=embed, view=view)
                except discord.errors.NotFound:
                    logger.warning("Message not found, creating new one")
                    self.playing_message = await self.playing_message.channel.send("", embed=embed, view=view)

    @tasks.loop(seconds=1)
    async def test_vid(self):
        if self.playing_message is None:
            return
        else:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            if player.current and self.sponsorBlock:
                try:
                    segments = sbClient.get_skip_segments(player.current.uri)
                except Exception as e:
                    segments = None
                if segments:
                    # seek past any segments that are in segments
                    for segment in segments:
                        if float(segment.start * 1000) < player.position < float(segment.end * 1000):
                            await player.seek(int(segment.end * 1000))
                            embed = discord.Embed(title="SponsorBlock",
                                                  description=f'Skipped segment because it was: `{segment.category}`',
                                                  color=discord.Color.brand_red())
                            embed.set_footer(text=f'`Use /sponsorblock to toggle the SponsorBlock integration.`')

                            await self.playing_message.channel.send(embed=embed, delete_after=30)

    def cog_unload(self):
        """ Cog unload handler. This removes any event hooks that were registered. """
        self.bot.lavalink._event_hooks.clear()

    async def cog_before_invoke(self, ctx: discord.ApplicationContext):
        """ Command before-invoke handler. """
        guild_check = ctx.guild is not None
        #  This is essentially the same as `@commands.guild_only()`
        #  except it saves us repeating ourselves (and also a few lines).
        if guild_check:
            await self.ensure_voice(ctx)
            #  Ensure that the bot and command author share a mutual voice channel.
        return guild_check

    async def cog_command_error(self, ctx: discord.ApplicationContext, error):
        if isinstance(error, commands.CommandInvokeError):
            await ctx.respond(error.original, delete_after=10, ephemeral=True)
            # The above handles errors thrown in this cog and shows them to the user.
            # This shouldn't be a problem as the only errors thrown in this cog are from `ensure_voice`
            # which contain a reason string, such as "Join a voice channel" etc. You can modify the above
            # if you want to do things differently.

    async def ensure_voice(self, ctx: discord.ApplicationContext):
        """ This check ensures that the bot and command author are in the same voice channel. """
        player = self.bot.lavalink.player_manager.create(ctx.guild.id)
        # Create returns a player if one exists, otherwise creates.
        # This line is important because it ensures that a player always exists for a guild.

        # Most people might consider this a waste of resources for guilds that aren't playing, but this is
        # the easiest and simplest way of ensuring players are created.

        # These are commands that require the bot to join a voice channel (i.e. initiating playback).
        # Commands such as volume/skip etc. don't require the bot to be in a voice channel so don't need listing here.
        should_connect = ctx.command.name in ('play', 'pirate')
        if should_connect:
            if not ctx.author.voice or not ctx.author.voice.channel:
                # Our cog_command_error handler catches this and sends it to the voice channel.
                # Exceptions allow us to "short-circuit" command invocation via checks so the
                # execution state of the command goes no further.
                raise commands.CommandInvokeError(Exception('Join a voice channel first.'))

            v_client = ctx.voice_client
            if not v_client:
                if not should_connect:
                    raise commands.CommandInvokeError(Exception('Not connected.'))

                permissions = ctx.author.voice.channel.permissions_for(ctx.me)

                if not permissions.connect or not permissions.speak:  # Check user limit too?
                    raise commands.CommandInvokeError(Exception('I need the `CONNECT` and `SPEAK` permissions.'))

                player.store('channel', ctx.channel.id)
                player.store('VoiceChannel', ctx.author.voice.channel.id)
                await ctx.author.voice.channel.connect(cls=LavalinkVoiceClient)
            else:
                if type(player.fetch('VoiceChannel')) == int:
                    if player.fetch('VoiceChannel') != ctx.author.voice.channel.id:
                        raise commands.CommandInvokeError(Exception('You need to be in my voice channel.'))
                else:
                    if v_client.channel.id != ctx.author.voice.channel.id:
                        raise commands.CommandInvokeError(Exception('You need to be in my voice channel.'))

    async def track_hook(self, event):
        if isinstance(event, lavalink.events.QueueEndEvent):
            # When this track_hook receives a "QueueEndEvent" from lavalink.py
            # it indicates that there are no tracks left in the player's queue.
            # To save on resources, we can tell the bot to disconnect from the voice channel.
            guild_id = event.player.guild_id
            guild = self.bot.get_guild(guild_id)
            # wait for a second
            await asyncio.sleep(2.5)
            try:
                await self.playing_message.delete()
                await event.player.reset_filters()
            except AttributeError:
                pass
            finally:
                self.playing_message = None
            try:
                await guild.voice_client.disconnect(force=True)
            except AttributeError:
                # We are already disconnected.
                pass

    @commands.slash_command(name="join", description="Joins the voice channel you are in.")
    @option(name="channel", description="The voice channel to join.", required=False)
    async def join(self, ctx: discord.ApplicationContext, channel: discord.VoiceChannel = None):
        """ Joins a voice channel. """
        if ctx.voice_client:
            return await ctx.respond('Already connected to a voice channel.')
        if not channel:
            if not ctx.author.voice or not ctx.author.voice.channel:
                raise commands.CommandInvokeError(Exception('Join a voice channel first.'))

            channel = ctx.author.voice.channel

        permissions = channel.permissions_for(ctx.me)

        if not permissions.connect or not permissions.speak:
            raise commands.CommandInvokeError(Exception('I need the `CONNECT` and `SPEAK` permissions.'))
        await channel.connect(cls=LavalinkVoiceClient)
        await ctx.respond(f'Joined {channel.name}', ephemeral=True)

    @commands.slash_command(name="play", description="Play a song")
    @option(name="query", description="The song to play.", required=True)
    @option(name="shuffle", description="Shuffle the queue.", required=False)
    async def play(self, ctx: discord.ApplicationContext, *, query: str, shuffle: bool = False):
        """ Searches and plays a song from a given query. """
        await ctx.defer()
        # Get the player for this guild from cache.
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        # check the current channel for any birthday
        for member in ctx.author.voice.channel.members:
            if member.bot:
                continue
            birthday = birthday_easteregg(member.id)
            if birthday["date"] is not None:
                print(datetime.datetime.now().strftime("%d/%m"))
                if datetime.datetime.now().strftime("%d/%m") == birthday["date"]:
                    await ctx.channel.send(f"Happy birthday {birthday['name']}! 🎉🎂")
                    bquery = f'ytsearch:happy birthday {birthday["name"].lower()} EpicHappyBirthdays'
                    results = await player.node.get_tracks(bquery)
                    # if there is a result, add it to the queue
                    if results and results['tracks']:
                        track = results['tracks'][0]
                        player.add(requester=ctx.author.id, track=track)
                        if not player.is_playing:
                            await player.play()
                            embed = discord.Embed(color=discord.Color.blurple())
                            embed.title = f'Awaiting song information...'
                            self.playing_message = await ctx.channel.send(embed=embed)
        # Check if the user input might be a URL. If it isn't, we can Lavalink do a YouTube search for it instead.
        # SoundCloud searching is possible by prefixing "scsearch:" instead.
        if not url_rx.match(query):
            query = f'ytsearch:{query}'
        elif query.startswith('https://open.spotify.com/playlist/') or query.startswith(
                "https://open.spotify.com/album/"):
            await ctx.respond(
                "👍 `Started import of Spotify to YouTube, please watch the next message for progress.`",
                delete_after=10, ephemeral=True)
            player = self.bot.lavalink.player_manager.get(ctx.guild.id)
            message = await ctx.send("🔁")
            tracks, name = await self.get_playlist_songs(query)
            if shuffle:
                random.shuffle(tracks)
            try:
                if "/album/" in query:
                    playlist_info = sp.album(query)
                elif "/playlist/" in query:
                    playlist_info = sp.playlist(query)
            except spotipy.SpotifyException as e:
                await ctx.respond(f"👎 `Failed to import Spotify playlist. ({e})`\n"
                                  "The playlist might be private.", ephemeral=True, delete_after=10)
                await message.delete()
                return
            for track in tracks:
                if self.stop_import:
                    await ctx.respond("👍 `Stopped importing Spotify playlist.`", ephemeral=True, delete_after=10)
                    await message.delete()
                    self.stop_import = False
                    return
                if "/album/" in query:
                    squery = f'ytsearch:{track["name"]} {track["artists"][0]["name"]}'
                elif "/playlist/" in query:
                    squery = f'ytsearch:{track["track"]["name"]} {track["track"]["artists"][0]["name"]}'
                if int(tracks.index(track)) % 5 == 0:
                    embed = discord.Embed(color=await Generate_color(str(playlist_info["images"][0]["url"])))
                    if "/album/" in query:
                        embed.title = f'Importing Spotify album: {name}'
                        embed.description = f'**Importing:** {name} by {playlist_info["artists"][0]["name"]}'
                    elif "/playlist/" in query:
                        embed.title = f'Importing Spotify playlist: {name}'
                        embed.description = f'**Importing:** {name} by {playlist_info["owner"]["display_name"]}'
                    embed.set_thumbnail(url=playlist_info["images"][0]["url"])
                    bar_length = 12
                    progress = (tracks.index(track) / len(tracks)) * bar_length
                    # progress bar using green and white square emojis
                    embed.add_field(
                        name=f"Progress: {round((tracks.index(track) / len(tracks)) * 100, 2)}% ({tracks.index(track)}/{len(tracks)})",
                        # 🟩⬜
                        value=f"{'🟩' * int(progress)}{'⬜' * (bar_length - int(progress))}")
                    await message.edit(embed=embed, content="")
                results = await player.node.get_tracks(squery)
                if not results or not results['tracks']:
                    continue
                track = results['tracks'][0]
                player.add(requester=ctx.author.id, track=track)

                if not player.is_playing:
                    await player.play()
                    embed = discord.Embed(color=discord.Color.blurple())
                    embed.title = f'Awaiting song information...'
                    self.playing_message = await ctx.channel.send(embed=embed)
                pass
            await message.edit(content="👍 `Finished import of Spotify to YouTube.`", embed=None)
            await asyncio.sleep(10)
            await message.delete()
            return True

        # Get the results for the query from Lavalink.
        results = await player.node.get_tracks(query)
        pprint(results)
        # Results could be None if Lavalink returns an invalid response (non-JSON/non-200 (OK)).
        # Alternatively, results.tracks could be an empty array if the query yielded no tracks.
        if not results or not results.tracks:
            return await ctx.respond('Nothing found!', delete_after=10, ephemeral=True)
        elif results.load_type == LoadType.EMPTY:
            return await ctx.respond(f'Nothing found for `{query}`!', delete_after=10, ephemeral=True)

        embed = discord.Embed(color=discord.Color.blurple(), title="Fetching song information...")

        # Valid loadTypes are:
        #   TRACK_LOADED    - single video/direct URL
        #   PLAYLIST_LOADED - direct URL to playlist
        #   SEARCH_RESULT   - query prefixed with either ytsearch: or scsearch:.
        #   NO_MATCHES      - query yielded no results
        #   LOAD_FAILED     - most likely, the video encountered an exception during loading.
        if results.load_type == 'PLAYLIST_LOADED':
            tracks = results.tracks
            for track in tracks:
                # Add all the tracks from the playlist to the queue.
                player.add(requester=ctx.author.id, track=track)
        else:
            track = results.tracks[0]
            player.add(requester=ctx.author.id, track=track)
        # send thumbs up
        await ctx.respond("Enqueued song", delete_after=1, ephemeral=True)
        # We don't want to call .play() if the player is playing as that will effectively skip
        # the current track.
        if not player.is_playing or player.fetch('VoiceState') in ['-1', '0']:
            await player.play()
            self.playing_message = await ctx.channel.send(embed=embed)

    async def get_playlist_songs(self, playlist):
        try:
            if "/album/" in playlist:
                playlist_info = sp.album(playlist)
            elif "/playlist/" in playlist:
                playlist_info = sp.playlist(playlist)
            else:
                return False, "Not a playlist or album"
        except Exception as e:
            logger.error(f"Failed to get playlist info: {e.with_traceback(None)}")
            return False, e
        songs = deepcopy(playlist_info['tracks']['items'])
        if not playlist_info['tracks']['next']:
            return songs, str(playlist_info['name'])
        else:
            while playlist_info['tracks']['next']:
                playlist_info['tracks'] = sp.next(playlist_info['tracks'])
                songs.extend(playlist_info['tracks']['items'])
                if self.stop_import:
                    return False, "Stopped"
            return songs, playlist_info['name']

    @commands.slash_command(name="lowpass", description="Set the lowpass filter strength")
    async def lowpass(self, ctx: discord.ApplicationContext, strength: float):
        """ Sets the strength of the low pass filter. """
        # Get the player for this guild from cache.
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        # This enforces that strength should be a minimum of 0.
        # There's no upper limit on this filter.
        strength = max(0.0, strength)

        # Even though there's no upper limit, we will enforce one anyway to prevent
        # extreme values from being entered. This will enforce a maximum of 100.
        strength = min(100, strength)

        embed = discord.Embed(color=discord.Color.blurple(), title='Low Pass Filter')

        # A strength of 0 effectively means this filter won't function, so we can disable it.
        if strength == 0.0:
            await player.remove_filter('lowpass')
            embed.description = 'Disabled **Low Pass Filter**'
            return await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

        # Let's create our filter.
        low_pass = LowPass()
        low_pass.update(smoothing=strength)  # Set the filter strength to the user's desired level.

        # This applies our filter. If the filter is already enabled on the player, then this will
        # just overwrite the filter with the new values.
        await player.set_filter(low_pass)

        embed.description = f'Set **Low Pass Filter** strength to {strength}.'
        await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

    @commands.slash_command(name="karaoke", description="Set the karaoke filter")
    async def karaoke(self, ctx: discord.ApplicationContext, level: float, mono_level: float, filter_band: float,
                      filter_width: float):
        """ Sets the karaoke filter. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        # We will enforce a minimum of 0.0 and a maximum of 1.0 for each of these values.
        level = max(0.0, min(1.0, level))
        mono_level = max(0.0, min(1.0, mono_level))
        filter_band = max(0.0, min(1.0, filter_band))
        filter_width = max(0.0, min(1.0, filter_width))

        embed = discord.Embed(color=discord.Color.blurple(), title='Karaoke Filter')

        # If the level is 0.0, then we can just disable the filter.
        if level == 0.0:
            await player.remove_filter('karaoke')
            embed.description = 'Disabled **Karaoke Filter**'
            return await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

        # Create the filter.
        karaoke = Karaoke()
        karaoke.update(level=level, mono_level=mono_level, filter_band=filter_band, filter_width=filter_width)

        # Apply the filter.
        await player.set_filter(karaoke)

        embed.description = f'Set **Karaoke Filter** level to {level}.'
        await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

    @commands.slash_command(name="timescale", description="Set the timescale filter")
    async def timescale(self, ctx: discord.ApplicationContext, speed: float, pitch: float, rate: float):
        """ Sets the timescale filter. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        # We will enforce a minimum of 0.1 and a maximum of 2.0 for each of these values.
        speed = max(0.1, min(2.0, speed))
        pitch = max(0.1, min(2.0, pitch))
        rate = max(0.1, min(2.0, rate))

        embed = discord.Embed(color=discord.Color.blurple(), title='Timescale Filter')

        # If the speed is 1.0, then we can just disable the filter.
        if speed == 1.0 and pitch == 1.0 and rate == 1.0:
            await player.remove_filter('timescale')
            embed.description = 'Disabled **Timescale Filter**'
            return await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

        # Create the filter.
        timescale = Timescale()
        timescale.update(speed=speed, pitch=pitch, rate=rate)

        # Apply the filter.
        await player.set_filter(timescale)

        embed.description = f'Set **Timescale Filter** speed to {speed}.'
        await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

    @commands.slash_command(name="nightcore", description="Toggle the nightcore filter")
    async def nightcore(self, ctx: discord.ApplicationContext):
        self.Effect.nightcore = not self.Effect.nightcore
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if self.Effect.nightcore:
            embed = discord.Embed(color=discord.Color.blurple(), title='Nightcore Filter')
            embed.description = 'Disabled **Nightcore Filter**'
            await player.remove_filter('timescale')
            await ctx.respond(embed=embed, delete_after=10, ephemeral=True)
        else:
            embed = discord.Embed(color=discord.Color.blurple(), title='Nightcore Filter')
            embed.description = 'Enabled **Nightcore Filter**'
            timescale = Timescale()
            timescale.update(speed=1.3, pitch=1.3, rate=1.3)
            await player.set_filter(timescale)
            await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

    @commands.slash_command(name="vaporwave", description="Toggle the vaporwave filter")
    async def vaporwave(self, ctx: discord.ApplicationContext):
        self.Effect.vaporwave = not self.Effect.vaporwave
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if self.Effect.vaporwave:
            embed = discord.Embed(color=discord.Color.blurple(), title='Vaporwave Filter')
            embed.description = 'Disabled **Vaporwave Filter**'
            await player.remove_filter('timescale')
            await ctx.respond(embed=embed, delete_after=10, ephemeral=True)
        else:
            embed = discord.Embed(color=discord.Color.blurple(), title='Vaporwave Filter')
            embed.description = 'Enabled **Vaporwave Filter**'
            timescale = Timescale()
            timescale.update(speed=0.8, pitch=0.8, rate=0.8)
            await player.set_filter(timescale)
            await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

    @commands.slash_command(name="reset", description="Clear all filters")
    async def reset(self, ctx: discord.ApplicationContext):
        """ Resets all filters. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        # Remove all filters.
        await player.reset_filters()
        self.Effect.nightcore = True
        self.Effect.vaporwave = True

        embed = discord.Embed(color=discord.Color.blurple(), title='Reset Filters')
        embed.description = 'Removed all filters.'
        await ctx.respond(embed=embed, delete_after=10, ephemeral=True)

    @commands.slash_command(name="disconnect", description="Disconnect the bot from the voice channel", )
    async def disconnect(self, ctx: discord.ApplicationContext):
        """ Disconnects the player from the voice channel and clears its queue. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not ctx.voice_client:
            # We can't disconnect, if we're not connected.
            return await ctx.respond('Not connected.', delete_after=10, ephemeral=True)

        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            # Abuse prevention. Users not in voice channels, or not in the same voice channel as the bot
            # may not disconnect the bot.
            return await ctx.respond('You\'re not in my voice channel!', delete_after=10, ephemeral=True)

        self.stop_import = True
        # Clear the queue to ensure old tracks don't start playing
        # when someone else queues something.
        player.queue.clear()
        # Stop the current track so Lavalink consumes less resources.
        await player.stop()
        # Disconnect from the voice channel.
        await ctx.voice_client.disconnect(force=True)
        try:
            await self.playing_message.delete()
            self.playing_message = None
        except AttributeError:
            pass
        await ctx.respond('*⃣ | Disconnected.', delete_after=10, ephemeral=True)

    @commands.slash_command(name="pause", description="Pause/resume the current song", aliases=['resume'])
    async def pause(self, ctx: discord.ApplicationContext):
        """ Pauses the current track. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if player.paused:
            await player.set_pause(False)
            await ctx.respond('▶ | Resumed.', delete_after=5)
            return
        if not ctx.voice_client:
            return await ctx.respond('Not connected.', delete_after=5, ephemeral=True)

        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)

        if not player.is_playing:
            return await ctx.respond('Nothing playing.', delete_after=5, ephemeral=True)

        await player.set_pause(True)
        await ctx.respond('⏸ | Paused the song.', delete_after=5)

    @commands.slash_command(name="loop", description="Cycle loop")
    async def loop(self, ctx: discord.ApplicationContext, type: str = None):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)
        if type is None:
            if player.loop == player.LOOP_NONE:
                player.loop = player.LOOP_QUEUE
                await ctx.respond("Looping the queue.", delete_after=5)
            elif player.loop == player.LOOP_QUEUE:
                player.loop = player.LOOP_SINGLE
                await ctx.respond(f"Looping {player.current.title}.", delete_after=5)
            elif player.loop == player.LOOP_SINGLE:
                player.loop = player.LOOP_NONE
                await ctx.respond('No longer looping.', delete_after=5)
        elif type in ["single", "song", "track"]:
            player.loop = player.LOOP_SINGLE
            await ctx.respond(f"Looping {player.current.title}.", delete_after=5)
        elif type in ["queue", "all"]:
            player.loop = player.LOOP_QUEUE
            await ctx.respond("Looping the queue.", delete_after=5)
        elif type in ["off", "none"]:
            player.loop = player.LOOP_NONE
            await ctx.respond('No longer looping.', delete_after=5)
        else:
            await ctx.respond("Invalid loop type.", delete_after=5)

    @commands.slash_command(name="volume", description="Change the volume")
    async def volume(self, ctx: discord.ApplicationContext, volume: int):
        """ Changes the player's volume. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)

        volume = max(min(volume, 1000), 0)

        await player.set_volume(volume)
        await ctx.respond(f'Volume set to **{volume}**', delete_after=5)

    @commands.slash_command(name="queue", description="Show the queue")
    @option(name="limit", description="The amount of songs to show", required=False)
    async def queue(self, ctx: discord.ApplicationContext, limit: int = 10):
        await ctx.defer()
        """ Shows the player's queue. in a paginator response"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=10, ephemeral=True)
        if not player.queue:
            return await ctx.respond('Nothing queued.', delete_after=10, ephemeral=True)
        embed_data = {
            "title": f"Queue for {ctx.guild.name}",
            "description": f"Showing {limit} songs."
        }
        logger.info("Generating pages")
        now_playing = {"title": player.current.title, "thumb": player.current.uri, "author": player.current.author}
        name = ctx.author.display_name
        pages = paginator(items=player.queue, embed_data=embed_data, per_page=limit, current_info=now_playing,
                          author=name)
        logger.info("Sending pages")
        page_iterator = Paginator(pages=pages, loop_pages=True)
        await page_iterator.respond(ctx.interaction)

    @commands.slash_command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, ctx: discord.ApplicationContext):
        """ Shuffles the player's queue. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)
        if not player.queue:
            return await ctx.respond('Nothing queued.', delete_after=5, ephemeral=True)
        player.set_shuffle(not player.shuffle)
        await ctx.respond(f'Shuffle {"enabled" if player.shuffle else "disabled"}', delete_after=5)

    @commands.slash_command(name="skip", description="Skip the current song")
    async def skip(self, ctx: discord.ApplicationContext):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)
        if not player.is_playing:
            return await ctx.respond('Nothing playing.', delete_after=5, ephemeral=True)
        await player.skip()
        await ctx.respond('⏭ | Skipped the song.', delete_after=5, ephemeral=True)

    @commands.slash_command(name="sponsorblock", description="Toggle the sponsorblock integration.")
    async def sponsorblock(self, ctx: discord.ApplicationContext):
        self.sponsorBlock = not self.sponsorBlock
        if self.sponsorBlock:
            await ctx.respond("SponsorBlock has been enabled!", delete_after=5, ephemeral=True)
        else:
            await ctx.respond("SponsorBlock has been disabled!", delete_after=5, ephemeral=True)

    @commands.slash_command(name="nowplaying", description="Show the current song")
    async def nowplaying(self, ctx: discord.ApplicationContext):
        embed = discord.Embed(color=discord.Color.blurple())
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)
        if not player.current:
            return await ctx.respond('Nothing playing.', delete_after=5, ephemeral=True)
        embed.title = f'Now Playing for {ctx.guild.name}'
        embed.description = f'**Now Playing:** {player.current.title}'
        embed.add_field(name=f"({player.current.title})[{player.current.uri}]", value=f"{player.current.author}",
                        inline=False)
        await ctx.respond(embed=embed, delete_after=15, ephemeral=True)

    @commands.slash_command(name="clear", description="Clear the queue")
    async def clear(self, ctx: discord.ApplicationContext):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        self.stop_import = True
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)
        if not player.queue:
            return await ctx.respond('Nothing queued.', delete_after=5, ephemeral=True)
        player.queue.clear()
        await ctx.respond(f'Queue cleared.', delete_after=5)

    @commands.slash_command(name="remove", description="Remove a song from the queue")
    async def remove(self, ctx: discord.ApplicationContext, index: int = None):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)
        if not player.queue:
            return await ctx.respond('Nothing queued.', delete_after=5, ephemeral=True)
        if index is None:
            return await ctx.respond('No index provided.', delete_after=5, ephemeral=True)
        if index > len(player.queue):
            return await ctx.respond('Index out of range.', delete_after=5, ephemeral=True)
        track = player.queue.pop(index)
        await ctx.respond(f'Removed {track.title} from the queue.', delete_after=5)

    @commands.slash_command(name="lyrics", description="Get the lyrics of the current song")
    @option(name="song", description="The song to get the lyrics of", required=False)
    async def lyrics(self, ctx: discord.ApplicationContext, song: str = None):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if player.is_playing or song is not None:
            if song is None:
                song = player.current.title
            song = re.sub(r'\([^)]*\)', '', song)
            song = re.sub(r'\[[^)]*\]', '', song)
            author = re.sub(r'\([^)]*\)', '', player.current.author)
            author = re.sub(r'\[[^)]*\]', '', author)
            if " - " in author:
                author = author.split(" - ")[0]
            if " - " in song:
                author = song.split(" - ")[0]
            song = self.genius.search_song(song, author).to_dict()

            slyrics = re.sub(r'\[.*\]', '', re.sub(r'^.*\n', '', song['lyrics'])).replace('\n\n\n', '\n\n')
            embed = discord.Embed(color=await Generate_color(song['song_art_image_url']), url=song['url'],
                                  title=song['title'])
            embed.description = f'**Lyrics:**\n {slyrics[0:2048]}'
            embed.set_thumbnail(url=song['song_art_image_url'])
            embed.set_author(name=song['primary_artist']['name'], url=song['primary_artist']['url'],
                             icon_url=song['primary_artist']['image_url'])
            embed.set_footer(text=f"Requested by {ctx.author.display_name}",
                             icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        else:
            return await ctx.respond('Nothing playing.', delete_after=5, ephemeral=True)

    @commands.slash_command(name="pirate", description="Add the pirate shanties playlist to the queue")
    @option(name="shuffle", description="Shuffle the playlist", required=False)
    async def pirate(self, ctx: discord.ApplicationContext, shuffle: bool = False):
        await ctx.respond(
            "👍 `Started import of pirate shanties, please watch the next message for progress.`",
            delete_after=10, ephemeral=True)
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        message = await ctx.send("Initializing Spotify wrapper... (0%)")
        tracks, name = await self.get_playlist_songs(
            "https://open.spotify.com/playlist/098Oij7Ia2mktRbbTkBK0X?si=e10a2ba7062e487e")
        if shuffle:
            random.shuffle(tracks)
        playlist_info = sp.playlist("https://open.spotify.com/playlist/098Oij7Ia2mktRbbTkBK0X?si=e10a2ba7062e487e")
        for track in tracks:
            if self.stop_import:
                await ctx.respond("👍 `Stopped importing Spotify playlist.`", ephemeral=True, delete_after=10)
                await message.delete()
                self.stop_import = False
                return
            query = f'ytsearch:{track["track"]["name"]} {track["track"]["artists"][0]["name"]}'
            if int(tracks.index(track)) % 5 == 0:
                embed = discord.Embed(color=discord.Color.blurple())
                embed.title = f'Importing Spotify playlist'
                embed.description = f'**Importing:** {name}'
                bar_length = 12
                progress = (tracks.index(track) / len(tracks)) * bar_length
                # progress bar using green and white square emojis
                embed.add_field(
                    name=f"Progress: {round((tracks.index(track) / len(tracks)) * 100, 2)}% ({tracks.index(track)}/{len(tracks)})",
                    value=f"{'🟩' * int(progress)}{'⬜' * (bar_length - int(progress))}")
                # Add the image of the playlist cover to the embed
                embed.set_thumbnail(url=playlist_info["images"][0]["url"])
                await message.edit(embed=embed, content="")
            results = await player.node.get_tracks(query)
            if not results or not results['tracks']:
                continue
            track = results['tracks'][0]
            player.add(requester=ctx.author.id, track=track)
            if not player.is_playing:
                await player.play()
                embed = discord.Embed(color=discord.Color.blurple())
                embed.title = f'Awaiting song information...'
                self.playing_message = await ctx.channel.send(embed=embed)
                player.set_shuffle(True)
        await message.edit(content=f"👍 `Finished import of {name}`", embed=None)
        await asyncio.sleep(10)
        await message.delete()
        return True

    @commands.slash_command(name="clean", description="Cleanup spam in any channel")
    @commands.has_permissions(manage_messages=True)
    async def clean(self, ctx: discord.ApplicationContext, amount: int = 100):
        await ctx.respond(f"👍 `Cleaning {amount} messages, please wait.`", delete_after=10, ephemeral=True)
        self.stop_import = True
        await ctx.channel.purge(limit=amount, check=lambda m: m.author == ctx.bot.user)

    @commands.slash_command(name="display", description="Lost the display message? Use this command to get it back.")
    async def display(self, ctx: discord.ApplicationContext):
        if self.bot.lavalink.player_manager.get(ctx.guild.id).is_playing:
            await ctx.respond("👍 `Sending display message...`", delete_after=10, ephemeral=True)
            embed = discord.Embed(color=discord.Color.blurple())
            embed.title = f'Fetching song information...'
            self.playing_message = await ctx.channel.send(embed=embed)
            await self.update_playing_message(ctx)
        else:
            return await ctx.respond('Nothing playing.', delete_after=5, ephemeral=True)


async def Generate_color(image_url):
    """Generate a similar color to the album cover of the song.
    :param image_url: The url of the album cover.
    :return: The color of the album cover."""

    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as resp:
            if resp.status != 200:
                return discord.Color.blurple()
            f = io.BytesIO(await resp.read())
    image = Image.open(f)
    # Get adverage color of the image
    colors = image.getcolors(image.size[0] * image.size[1])
    # Sort the colors by the amount of pixels and get the most common color
    colors.sort(key=lambda x: x[0], reverse=True)
    # Get the color of the most common color
    color = colors[0][1]
    try:
        if len(color) < 3:
            return discord.Color.blurple()
    except TypeError:
        return discord.Color.blurple()
    # Convert the color to a discord color
    return discord.Color.from_rgb(color[0], color[1], color[2])


def paginator(items, embed_data, author: str, current_info: dict, per_page=10, hard_limit=100):
    """This function builds a complete list of embeds for the paginator.
        :param per_page: The amount of items per page.
        :param embed_data: The data for the embeds.
        :param items: The list to insert for the embeds.
        :param hard_limit: The hard limit of items to paginate.
        :param author: The username of the user who requested the queue.
        :param current_info: The current song info dict.
        :return: A list of embeds."""
    pages = []
    # Split the list into chunks of 10
    chunks = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    # Check if the amount of chunks is larger than the hard limit
    if len(chunks) > hard_limit:
        # If it is, then we will just return the first 100 pages
        chunks = chunks[:hard_limit]
    # Loop through the chunks
    index = 1
    for chunk in chunks:
        logger.info(f"Generating page {index}/{len(chunks)}")
        # Create a new embed
        embed = discord.Embed(**embed_data)
        embed.description = f"Currently playing: {current_info['title']}\nFor more info use /nowplaying"
        embed.set_footer(text=f"Requested by {author}")
        # Add the items to the embed
        for item in chunk:
            logger.info(f"\tAdding item {chunk.index(item)}")
            embed.add_field(name=f"{index}. {item.title}", value=f"{item.author} [Source Video]({item.uri})",
                            inline=False)
            index += 1
        # Add the embed to the pages
        pages.append(embed)
    return pages


def setup(bot):
    bot.add_cog(Music(bot))
