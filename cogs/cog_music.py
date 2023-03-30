import asyncio
import datetime
import io
import json
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
from discord.ui import Button, View
from spotipy.oauth2 import SpotifyClientCredentials
import sponsorblock as sb
from dotenv import load_dotenv
from discord.ext import commands, tasks
from lavalink.filters import *
import os

load_dotenv()
SPOTIFY_CLIENT_ID = str(os.getenv('SPOTIFY_CLIENT_ID'))
SPOTIFY_CLIENT_SECRET = str(os.getenv('SPOTIFY_SECRET'))
url_rx = re.compile(r'https?://(?:www\.)?.+')
guild_ids = [730859265249509386, ]
sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID,
                                                                         client_secret=SPOTIFY_CLIENT_SECRET))
sbClient = sb.Client()


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
                print("I've been moved to a new channel. Updating variables...")
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
                print("I've been kicked from a channel.")
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
        lavalink.add_event_hook(self.track_hook)
        bot.loop.create_task(self.connect())

    async def connect(self):
        await self.bot.wait_until_ready()
        if not hasattr(self.bot, 'lavalink'):  # This ensures the client isn't overwritten during cog reloads.
            self.bot.lavalink = lavalink.Client(self.bot.user.id)
            #self.bot.lavalink.add_node('84.66.67.45', 2333, os.getenv("LAVA_TOKEN"), 'eu',
            #                           'default-node')
            self.bot.lavalink.add_node('raspberrypi.local', 2333, os.getenv("LAVA_TOKEN"), 'eu',
                                       'default-node')

    @tasks.loop(minutes=1)
    async def check_call(self):
        """Check the voice channel to see if the bot is the only one in the channel"""
        try:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            channel = self.bot.get_channel(player.fetch('VoiceChannel'))
            client = channel.guild.voice_client
        except AttributeError:
            return
        else:
            # if the player is connected and the bot is the only one in the channel
            if player.is_connected and len(channel.members) == 1:
                self.disconnect_timer = True
                await self.playing_message.channel.send("`I will leave the voice channel in 1 minute if no one joins.`",
                                                        delete_after=60)
                await asyncio.sleep(60)
                if len(channel.members) == 1 and player.is_connected:
                    self.disconnect_timer = False
                    await player.stop()
                    await client.disconnect(force=True)
                    await player.reset_filters()
                    await self.playing_message.delete()
                    await self.playing_message.channel.send("`I have left the voice channel because I was alone.`",
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
                player.loop = player.LOOP_SINGLE
            elif player.loop == player.LOOP_SINGLE:
                player.loop = player.LOOP_QUEUE
            elif player.loop == player.LOOP_QUEUE:
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
            if player.fetch('VoiceStatus') == "-1": # We have been kicked from the channel
                await self.playing_message.channel.send("`I have been kicked from the voice channel.`\n||Something "
                                                        "something use the disconnect command and stop admin abuse :(||",
                                                        delete_after=10)
                await self.playing_message.delete()
                self.playing_message = None
                await player.stop()
                player.store("VoiceStatus", "0")
            title = re.sub(r'\([^)]*\)', '', player.current.title)
            title = re.sub(r'\[[^)]*\]', '', title)
            author = re.sub(r'\([^)]*\)', '', player.current.author)
            author = re.sub(r'\[[^)]*\]', '', author)
            if " - " in author:
                author = author.split(" - ")[0]
            if self.CP is None and player.is_playing:
                print(f"Title: {title} Author: {author}")
                if " - " in title:
                    author = title.split(" - ")[0].split("&")[0]
                    song = self.genius.search_song(title, author).to_dict()
                else:
                    song = self.genius.search_song(title, author).to_dict()
                if song is None:
                    self.CP = (None, player.current.title)
                else:
                    self.CP = (song, player.current.title)
            if self.CP is not None and player.is_playing:
                if self.CP[1] != player.current.title:
                    if " - " in title:
                        author = title.split(" - ")[0]
                        song = self.genius.search_song(title, author).to_dict()
                    else:
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
                    print(f"https://img.youtube.com/vi/{player.current.identifier}/hqdefault.jpg")
                    color = await Generate_color(f"https://img.youtube.com/vi/{player.current.identifier}/hqdefault.jpg")
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
                #await self.playing_message.channel.send("Test", view=view)
                await self.playing_message.edit("", embed=embed, view=view)

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
            await guild.voice_client.disconnect(force=True)

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
    async def play(self, ctx: discord.ApplicationContext, *, query: str):
        """ Searches and plays a song from a given query. """
        await ctx.defer()
        # Get the player for this guild from cache.
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        # Remove leading and trailing <>. <> may be used to suppress embedding links in Discord.
        query = query.strip('<>')

        # Check if the user input might be a URL. If it isn't, we can Lavalink do a YouTube search for it instead.
        # SoundCloud searching is possible by prefixing "scsearch:" instead.
        if not url_rx.match(query):
            query = f'ytsearch:{query}'
        elif query.startswith('https://open.spotify.com/playlist/'):
            await ctx.respond(
                "👍 `Started import of Spotify to YouTube, please watch the next message for progress.`",
                delete_after=10, ephemeral=True)
            player = self.bot.lavalink.player_manager.get(ctx.guild.id)
            message = await ctx.send("Initializing Spotify wrapper... (0%)")
            tracks, name = self.get_playlist_songs(query)
            try:
                playlist_info = sp.playlist(query)
            except spotipy.SpotifyException:
                await ctx.respond("👎 `Failed to import Spotify playlist.`\n"
                                  "The playlist might be private.", ephemeral=True, delete_after=10)
                await message.delete()
                return
            for track in tracks:
                query = f'ytsearch:{track["track"]["name"]} {track["track"]["artists"][0]["name"]}'
                if int(tracks.index(track)) % 10 == 0:
                    embed = discord.Embed(color=await Generate_color(playlist_info["images"][0]["url"]))
                    embed.title = f'Importing Spotify playlist'
                    embed.description = f'**Importing:** {name} by {playlist_info["owner"]["display_name"]}'
                    embed.set_thumbnail(url=playlist_info["images"][0]["url"])
                    bar_length = 12
                    progress = (tracks.index(track) / len(tracks)) * bar_length
                    # progress bar using green and white square emojis
                    embed.add_field(
                        name=f"Progress: {round((tracks.index(track) / len(tracks)) * 100, 2)}% ({tracks.index(track)}/{len(tracks)})",
                        #🟩⬜
                        value=f"{'🟩' * int(progress)}{'⬜' * (bar_length - int(progress))}")
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
                pass
            await message.edit(content="👍 `Finished import of Spotify to YouTube.`", embed=None)
            await asyncio.sleep(10)
            await message.delete()
            return True

        # Get the results for the query from Lavalink.
        results = await player.node.get_tracks(query)

        # Results could be None if Lavalink returns an invalid response (non-JSON/non-200 (OK)).
        # Alternatively, results.tracks could be an empty array if the query yielded no tracks.
        if not results or not results.tracks:
            return await ctx.respond('Nothing found!', delete_after=10, ephemeral=True)

        embed = discord.Embed(color=discord.Color.blurple(), title="Awaiting song information...")

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

    def get_playlist_songs(self, playlist):
        try:
            playlist = sp.playlist(playlist)
        except Exception as e:
            print(e)
            return False, e
        tracks = playlist['tracks']
        songs = tracks['items']
        while tracks['next']:
            tracks = sp.next(tracks)
            for item in tracks['items']:
                songs.append(item)
        return songs, playlist['name']

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

    @commands.slash_command(name="disconnect", description="Disconnect the bot from the voice channel",
                            guild_ids=guild_ids)
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

    @commands.slash_command(name="pause", description="Pause/resume the current song", aliases=['resume'],
                            guild_ids=guild_ids)
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
                player.loop = player.LOOP_SINGLE
                await ctx.respond(f"Looping {player.current.title}.", delete_after=5)
            elif player.loop == player.LOOP_SINGLE:
                player.loop = player.LOOP_QUEUE
                await ctx.respond("Looping the queue.", delete_after=5)
            elif player.loop == player.LOOP_QUEUE:
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
    async def queue(self, ctx: discord.ApplicationContext):
        """ Shows the player's queue. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.respond('You\'re not in my voice channel!', delete_after=5, ephemeral=True)
        if not player.queue:
            return await ctx.respond('Nothing queued.', delete_after=5, ephemeral=True)
        embed = discord.Embed(color=discord.Color.blurple())
        embed.title = f'Queue for {ctx.guild.name}'
        embed.description = f'**Now Playing:** {player.current.title}'
        # top 10 songs in queue
        for track in player.queue[:10]:
            embed.add_field(name=f"[{track.title}]({track.uri})", value=f"{track.author}", inline=False)
        await ctx.respond(embed=embed, delete_after=15, ephemeral=True)

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
        embed.add_field(name=f"({player.current.title})[{player.current.uri}]", value=f"{player.current.author}", inline=False)
        await ctx.respond(embed=embed, delete_after=15, ephemeral=True)

    @commands.slash_command(name="clear", description="Clear the queue")
    async def clear(self, ctx: discord.ApplicationContext):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
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
    async def pirate(self, ctx: discord.ApplicationContext):
        await ctx.respond(
            "👍 `Started import of pirate shanties, please watch the next message for progress.`",
            delete_after=10, ephemeral=True)
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        message = await ctx.send("Initializing Spotify wrapper... (0%)")
        tracks, name = self.get_playlist_songs(
            "https://open.spotify.com/playlist/098Oij7Ia2mktRbbTkBK0X?si=e10a2ba7062e487e")
        playlist_info = sp.playlist("https://open.spotify.com/playlist/098Oij7Ia2mktRbbTkBK0X?si=e10a2ba7062e487e")
        for track in tracks:
            query = f'ytsearch:{track["track"]["name"]} {track["track"]["artists"][0]["name"]}'
            if int(tracks.index(track)) % 10 == 0:
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
        await message.edit(content="👍 `Finished import of Spotify to YouTube.`", embed=None)
        await asyncio.sleep(10)
        await message.delete()
        return True

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
        if len(color) != 3:
            return discord.Color.blurple()
    except TypeError:
        return discord.Color.blurple()
    # Convert the color to a discord color
    return discord.Color.from_rgb(color[0], color[1], color[2])

def setup(bot):
    bot.add_cog(Music(bot))
