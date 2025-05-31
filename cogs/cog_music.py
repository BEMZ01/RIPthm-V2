import asyncio
import datetime
import io
import logging
import random
import re
import traceback
from pprint import pprint
import aiohttp
import discord
import lavalink
import lyricsgenius
import requests
import spotipy
from PIL import Image, ImageFilter
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
import time

load_dotenv()
SPOTIFY_CLIENT_ID = str(os.getenv('SPOTIFY_CLIENT_ID'))
SPOTIFY_CLIENT_SECRET = str(os.getenv('SPOTIFY_SECRET'))
url_rx = re.compile(r'https?://(?:www\.)?.+')
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
        print(f"Disconnect called with force={force}")
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


class DiscordDropDownSelect(discord.ui.Select):
    def __init__(self, options: list, **kwargs):
        super().__init__(**kwargs)
        # [{"label": "Option 1", "value": "option1"}, ...]
        for option in options:
            self.add_option(label=option['label'], value=option['value'])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(f'You selected {self.values[0]}', ephemeral=True)


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
        if line == "":
            break  # we have reached the end of the file
        line = line.split(",")
        if line[2] == str(userID):
            return {"name": line[0], "date": line[1], "id": line[2]}
    return {"name": None, "date": None, "id": None}


async def get_skip_segments(uri):
    async with aiohttp.ClientSession() as session:
        async with session.get(
                f"https://sponsor.ajay.app/api/skipSegments?videoID={uri}") as response:
            return await response.json()


class Music(commands.Cog):
    def __init__(self, bot, logger):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.handlers = logger.handlers
        self.logger.setLevel(logger.level)
        self.logger.propagate = False
        self.disconnect_timer = False
        self.bot = bot
        self.playing_message = None
        self.update_playing_message.start()
        self.test_vid.start()
        self.check_call.start()
        self.sponsorBlock = True
        self.continue_playing = True
        self.Effect = Effect(True, True)
        self.genius = lyricsgenius.Genius(os.getenv('GENIUS_TOKEN'))
        self.genius.verbose = True
        self.genius.remove_section_headers = True
        self.genius.skip_non_songs = False
        self.CP = None
        self.stop_import = False
        self.bot.lavalink = None
        self.last_status = None
        self.last_track = None
        bot.loop.create_task(self.connect())

    async def connect(self):
        await self.bot.wait_until_ready()
        if not hasattr(self.bot,
                       'lavalink') or self.bot.lavalink is None:  # This ensures the client isn't overwritten during
            # cog reloads.
            self.bot.lavalink = lavalink.Client(self.bot.user.id)
            addr = os.getenv("LAVA_ADDR")
            port = os.getenv("LAVA_PORT")
            token = os.getenv("LAVA_TOKEN")
            if " " in addr:
                addr = addr.split(" ")
                port = port.split(" ")
                token = token.split(" ")
                for i in range(len(addr)):
                    self.logger.info(f"({i + 1}/{len(addr)}) Adding node {addr[i]}:{port[i]}")
                    self.bot.lavalink.add_node(addr[i], int(port[i]), token[i], 'eu', f'node-{addr[i]}:{port[i]}')
            else:
                self.logger.info(f"Adding default-node {addr}:{port}")
                self.bot.lavalink.add_node(os.getenv("LAVA_ADDR"), int(os.getenv("LAVA_PORT")), os.getenv("LAVA_TOKEN"),
                                           'eu',
                                           'default-node')
            self.bot.lavalink.add_event_hook(self.track_hook)
            await asyncio.sleep(2.5)
            try:
                results = await self.bot.lavalink.get_tracks("ytmsearch:Test")
                if not results or not results['tracks']:
                    self.logger.error("Lavalink failed to connect.")
                    return
                elif results['tracks']:
                    self.logger.info(f"Connected to Lavalink. Test video: {results['tracks'][0]['info']['title']}")
            except IndexError as e:
                self.logger.error(f"Failed to connect to Lavalink: {e}")
                return
        else:
            self.logger.warning("Lavalink already connected.")

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info("onready event received")
        await self.wait_until_lavalink_ready()
        self.check_update_status.start()

    @tasks.loop(seconds=10)
    async def check_update_status(self):
        if (not hasattr(self.bot, 'lavalink') or not self.bot.lavalink.node_manager.nodes or
                not self.bot.lavalink.node_manager.nodes[0].stats.uptime):
            return
        listening = True
        if len(self.bot.voice_clients) == 0:
            status = "nothing :("
        else:
            player = self.bot.lavalink.player_manager.get(self.bot.voice_clients[0].guild.id)
            if player.is_playing:
                status = f"{player.current.title} by {player.current.author}."
            elif len(self.bot.voice_clients) > 1:
                status = f"in {len(self.bot.voice_clients)} voice channels 🎵"
                listening = False
            else:
                status = "music 🎵"
        if status != self.last_status:
            self.logger.info(f"Updating status to {status}")
            if len(self.bot.voice_clients) != 0:
                if self.last_track is None and player.current is not None:
                    self.logger.warning("last_track is None, setting to current track")
                    self.last_track = player.current
            if listening:
                await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening,
                                                                         name=status))
            else:
                await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing,
                                                                         name=status))
            self.last_status = status

    async def wait_until_lavalink_ready(self):
        while True:
            if (self.bot.lavalink.node_manager.nodes and len(self.bot.lavalink.node_manager.nodes) > 0 and
                    self.bot.lavalink.node_manager.nodes[0].stats.uptime > 0):
                break
            await asyncio.sleep(1)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        if isinstance(error, discord.errors.Forbidden):
            await self.handle_permission_error(ctx, "send_messages")
        elif isinstance(error, commands.MissingPermissions):
            await self.handle_permission_error(ctx, "manage_messages")
        if isinstance(error, commands.CommandInvokeError):

            embed = discord.Embed(title="Error", description=f"```{error.original}```", color=discord.Color.red())
            self.logger.error(f"Error in {ctx.command.name}: {error.original}\n{tb}")
            await ctx.respond(embed=embed, ephemeral=True, delete_after=10)
        self.logger.error(f"Error in {ctx.command.name}: {error}\n{tb}")
        user = self.bot.get_user(self.bot.owner_id)
        if len(f"Error in {ctx.command.name}: {error}\n```{tb}```") > 2000:
            await user.send(f"Error in {ctx.command.name}: {error}", file=discord.File(tb, "traceback.txt"))
        else:
            await user.send(f"Error in {ctx.command.name}: {error}\n```{tb}```")

    async def handle_permission_error(self, ctx, missing_permission):
        """Handle permission errors by finding an alternative channel or DMing the user."""
        guild = ctx.guild
        user = ctx.author
        # Search for a channel where the bot has permission to send messages
        async for channel in guild.text_channels:
            permissions = channel.permissions_for(guild.me)
            if permissions.send_messages:
                await channel.send(
                    f"{user.mention}, I am missing the `{missing_permission}` permission in the original channel. "
                    f"Please check my permissions in {ctx.channel.mention}."
                )
                return
        try:
            await user.send(
                f"I am missing the `{missing_permission}` permission in the original channel, "
                f"and I couldn't find any other channel to send a message in. Please check my permissions."
            )
        except discord.errors.Forbidden:
            logging.error(f"Could not DM {user} about missing permissions.")

    async def find_alternative_channel(self, guild):
        """Find an alternative channel where the bot can send messages."""
        for channel in guild.text_channels:
            permissions = channel.permissions_for(guild.me)
            if permissions.send_messages:
                return channel
        return None

    async def handle_missing_permissions(self, ctx, embed):
        """Handle cases where the bot cannot send messages in the current channel."""
        alternative_channel = await self.find_alternative_channel(ctx.guild)
        if alternative_channel:
            await alternative_channel.send(f"<@{ctx.author.id}> I am missing permissions in {ctx.channel.mention}.",
                                           embed=embed)
        else:
            try:
                await ctx.author.send(
                    "I am missing the `SEND_MESSAGES` permission in the original channel, "
                    "and I couldn't find any other channel to send a message in. Please check my permissions."
                )
            except discord.errors.Forbidden:
                logging.error(f"Could not DM {ctx.author} about missing permissions.")

    def get_playing_message(self, guild_id):
        # Logic to retrieve the current playing message for the guild
        return self.playing_message

    @tasks.loop(seconds=30)
    async def check_call(self):
        """Check the voice channel to see if the bot is the only one in the channel"""
        try:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            channel = self.bot.get_channel(player.fetch('VoiceChannel'))
            client = channel.guild.voice_client
        except AttributeError:
            self.logger.warning("AttributeError in check_call. Ignoring.")
            pass
        else:
            # if the player is connected and the bot is the only one in the channel (not counting other bots and itself)
            self.logger.debug(
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
                    # await player.reset_filters()
                    await self.playing_message.delete()
                    await self.playing_message.channel.send("`I have left the voice channel because I was alone.`\n"
                                                            "Unpause the music with `/pause`",
                                                            delete_after=10)
                else:
                    self.disconnect_timer = False

    @tasks.loop(seconds=5)
    async def update_playing_message(self, ctx=None):
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

        async def recommendations_callback(interaction):
            await interaction.response.defer(ephemeral=True)
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            if player.is_playing:
                self.continue_playing = not self.continue_playing
                if self.continue_playing:
                    await interaction.channel.send("`Recommendations are now enabled.`", delete_after=10)
                else:
                    await interaction.channel.send("`Recommendations are now disabled.`", delete_after=10)
            else:
                await interaction.channel.send("`Recommendations are only available while playing music.`",
                                               delete_after=10)

        try:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            channel = self.bot.get_channel(player.fetch('VoiceChannel'))
            client = channel.guild.voice_client
            if player.fetch('VoiceStatus') == "-1":  # We have been kicked from the channel
                self.stop_import = True
                try:
                    await self.playing_message.channel.send("`I have been kicked from the voice channel. :(`",
                                                            delete_after=10)
                    await self.playing_message.delete()
                except discord.errors.Forbidden as e:
                    self.logger.error(f"Error deleting playing message: {e}")
                    self.playing_message = None
                self.playing_message = None
                await player.stop()
                player.store("VoiceStatus", "0")
            title = re.sub(r'\([^)]*\)', '', player.current.title)
            title = re.sub(r'\[[^)]*\]', '', title)
            title = title.lower()
            title_blacklist = ["lyrics", "lyric", "official", "video", "audio", "music", "full", "hd", "hq", "remix",
                               "ost", "theme", "original", "version", " - Topic"]
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
            self.logger.debug(f"Raw: {player.current.title}\nTitle: {title}\nAuthor: {author}")
            song = self.genius.search_song(title, author).to_dict()
            if song is None:
                self.CP = (None, player.current.title)
            else:
                self.CP = (song, player.current.title)
        except AttributeError:
            pass
        if self.playing_message is None:
            self.logger.debug("Playing message is None, not updating.")
            return
        else:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            # check if the bot can send messages to the channel
            if not self.playing_message.channel.permissions_for(self.playing_message.guild.me).send_messages:
                self.logger.warning("Bot cannot send messages in the current channel, skipping update.")
                await self.handle_missing_permissions(self.playing_message, discord.Embed(
                    title="Error",
                    description="I cannot send messages in this channel. Please check my permissions.",
                    color=discord.Color.red()
                ))
                return
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
                                          description=f'**[{player.current.title}]({player.current.uri})**\n'
                                                      f'{player.current.author}',
                                          color=color)
                else:
                    try:
                        embed = discord.Embed(title="Now Playing " + loop + " " + shuffle,
                                              description=f'**[{player.current.title}]({player.current.uri})**\n'
                                                          f'{player.current.author}',
                                              color=color)
                    except AttributeError:
                        embed = discord.Embed(title="Now Playing " + loop + " " + shuffle,
                                              description=f'[UNABLE TO GET TITLE]',
                                              color=color)
                if self.CP is not None:
                    embed.set_thumbnail(url=self.CP[0]['song_art_image_url'])
                else:
                    if player.current is None:
                        return
                    else:
                        embed.set_thumbnail(url=f"https://img.youtube.com/vi/"
                                                f"{player.current.identifier if player is not None else 'ABCDEF'}"
                                                f"/hqdefault.jpg")
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
                if self.continue_playing:
                    buttons.append(
                        [Button(style=ButtonStyle.green, emoji="🎧", custom_id="recommendations"), "recommendations"])
                else:
                    buttons.append(
                        [Button(style=ButtonStyle.red, emoji="🎧", custom_id="recommendations"), "recommendations"])
                view = discord.ui.View()
                for button in buttons:
                    # add the callback to the button
                    button[0].callback = locals()[f"{button[1]}_callback"]
                    view.add_item(button[0])
                try:
                    await self.playing_message.edit("", embed=embed, view=view)
                except discord.errors.NotFound:
                    self.logger.warning("Message not found, creating new one")
                    self.playing_message = await self.playing_message.channel.send("", embed=embed, view=view)

    @update_playing_message.error
    async def update_playing_message_error(self, exception):
        self.logger.error(f"Error in update_playing_message: {exception}")
        user = self.bot.get_user(self.bot.owner_id)
        tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
        tb = ''.join(tb)
        await user.send(f"Error in update_playing_message: {exception}\n```{tb}```")
        self.update_playing_message.restart()

    @tasks.loop(seconds=1)
    async def test_vid(self):
        if self.playing_message is None:
            return
        else:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            if player.current and self.sponsorBlock:
                try:
                    segments = await get_skip_segments(player.current.uri)
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

    async def ensure_voice(self, ctx: discord.ApplicationContext):
        """ This check ensures that the bot and command author are in the same voice channel. """
        while True:
            if hasattr(self.bot, 'lavalink') and self.bot.lavalink is not None:
                await self.wait_until_lavalink_ready()
                break
            await asyncio.sleep(1)
        self.logger.info("Lavalink ready.")
        player = self.bot.lavalink.player_manager.create(ctx.guild.id)
        # if lavalink not in bot, return (for wavelink)

        # Create returns a player if one exists, otherwise creates.
        # This line is important because it ensures that a player always exists for a guild.

        # Most people might consider this a waste of resources for guilds that aren't playing, but this is
        # the easiest and simplest way of ensuring players are created.

        # These are commands that require the bot to join a voice channel (i.e. initiating playback).
        # Commands such as volume/skip etc. don't require the bot to be in a voice channel so don't need listing here.
        should_connect = ctx.command.name in ('play', 'pirate', 'quickplay', 'search')
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
        if isinstance(event, lavalink.events.TrackStartEvent):
            self.logger.debug("TrackStartEvent received")
            self.last_track = event.track
        elif isinstance(event, lavalink.events.QueueEndEvent):
            self.logger.debug("QueueEndEvent received")
            guild_id = event.player.guild_id
            guild = self.bot.get_guild(guild_id)
            player = self.bot.lavalink.player_manager.get(guild_id)

            if self.continue_playing and self.last_track is not None:
                self.logger.info("Track ended, getting recommendations...")
                tracks = self.get_similar_tracks(self.last_track)
                if tracks is None:
                    self.logger.info("No similar tracks found. Falling back to YouTube search")
                    tracks = await player.node.get_tracks(f"ytmsearch:{self.last_track.author}")
                    # if there are more than 10 results, take the first 10 and replace tracks['tracks'] with them
                    # TypeError: 'LoadResult' object does not support item assignment
                    if len(tracks['tracks']) > 10:
                        tracks = tracks['tracks'][:10]
                        tracks = [f"{track['info']['title']} {track['info']['author']}" for track in tracks]
                    elif tracks and tracks['tracks']:
                        tracks = [f"{track['info']['title']} {track['info']['author']}" for track in tracks['tracks']]
                    else:
                        self.logger.info("No similar tracks found.")

                if tracks:
                    for track in tracks:
                        results = await player.node.get_tracks(f"ytmsearch:{track}")
                        if results and results['tracks']:
                            track = results['tracks'][0]
                            player.add(track=track)
                    if not player.is_playing:
                        await player.play()
                else:
                    self.logger.info("No similar tracks found.")
            else:
                self.logger.info("Queue ended, disconnecting...")
                await guild.voice_client.disconnect(force=True)

    def get_similar_tracks(self, track):
        if os.getenv('LASTFM_API_KEY') is None:
            self.logger.warning("LASTFM_API_KEY not set, skipping similar tracks.")
            return None
        try:
            if " - Topic" in track.author:
                track.author = track.author.replace(" - Topic", "")
            track.title = re.sub(r'\([^)]*\)', '', track.title)
            track.title = re.sub(r'\[[^)]*\]', '', track.title)
            track.title = track.title.strip()
            response = requests.get(
                f"https://ws.audioscrobbler.com/2.0/?method=track.getsimilar&artist={track.author}&track={track.title}&api_key={os.getenv('LASTFM_API_KEY')}&format=json&limit=10&autocorrect=1"
            )
            if response.status_code == 200:
                data = response.json()
                if 'similartracks' in data and 'track' in data['similartracks']:
                    tracks = data['similartracks']['track']
                    if tracks:
                        tracks = {track['name']: track for track in tracks}.values()
                        tracks = sorted(tracks, key=lambda x: x['match'], reverse=True)
                        self.logger.info(f"Found {len(tracks)} similar tracks.")
                        return (f"{track['name']} {track['artist']['name']}" for track in tracks)
            self.logger.error(f"Error fetching similar tracks: {response.text}")
        except Exception as e:
            self.logger.error(f"Exception in get_similar_tracks: {e}")
        return None

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
    @option(name="source", description="The source of the song.", required=False, choices=["youtube", "spotify",
                                                                                           "soundcloud",
                                                                                           "youtube_music", "twitch"],
            default="youtube_music")
    async def play(self, ctx: discord.ApplicationContext, *, query: str, shuffle: bool = False,
                   source: str = "youtube_music"):
        """ Searches and plays a song from a given query. """
        await ctx.defer(ephemeral=True)
        # Get the player for this guild from cache.
        if not ctx.guild:
            return
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if os.path.exists('birthdays.csv'):
            # check the current channel for any birthday
            now = str(datetime.datetime.now().strftime("%d/%m"))
            voice = ctx.author.voice.channel.members
            for member in voice:
                if member.bot:
                    continue
                birthday = birthday_easteregg(member.id)
                if birthday["date"] is None:
                    continue
                elif str(now) == str(birthday["date"]):
                    try:
                        await ctx.channel.send(f"Happy birthday {birthday['name']}! 🎉🎂")
                    except discord.errors.Forbidden:
                        self.logger.error("Bot does not have permission to send messages in this channel.")
                        # inform the user that the bot cannot send messages in this channel
                        await self.handle_missing_permissions(ctx, discord.Embed(
                            title="Error",
                            description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                        f"check my permissions.",
                            color=discord.Color.red()
                        ))
                        return
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
            if source == "direct":
                # called from within the bot - use top result
                query = query if query.startswith("ytmsearch:") else f"ytmsearch:{query}"
            else:
                await self.search(ctx, query, "cmd" + source)
                return
        if query.startswith('https://open.spotify.com/playlist/') or query.startswith(
                "https://open.spotify.com/album/"):
            self.stop_import = False
            await ctx.respond(
                "👍 `Started import of Spotify to YouTube, please watch the next message for progress.`",
                delete_after=10, ephemeral=True)
            player = self.bot.lavalink.player_manager.get(ctx.guild.id)
            try:
                message = await ctx.send("🎶`Converting Spotify playlist to YouTube. For large playlists (2.5k+) this "
                                         "may take upto 30 seconds...`")
            except discord.errors.Forbidden:
                self.logger.error("Bot does not have permission to send messages in this channel.")
                # inform the user that the bot cannot send messages in this channel
                await self.handle_missing_permissions(ctx, discord.Embed(
                    title="Error",
                    description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                f"check my permissions.",
                    color=discord.Color.red()
                ))
                return
            t_before = time.time()
            tracks, name = await self.get_playlist_songs(query)
            t_after = time.time()
            self.logger.info(f"Time taken to get tracks: {t_after - t_before}")
            if shuffle:
                random.shuffle(tracks)
            try:
                if "/album/" in query:
                    playlist_info = sp.album(query)
                elif "/playlist/" in query:
                    playlist_info = sp.playlist(query)
            except spotipy.SpotifyException as e:
                await ctx.respond(f"👎 `Failed to import Spotify playlist. ({e})`\nIs the playlist private?",
                                  ephemeral=True, delete_after=10)
                await message.delete()
                return
            start_time = time.time()
            bar_length = 12
            batch_size = max(1, len(tracks) // bar_length)
            for i in range(0, len(tracks), batch_size):
                if self.stop_import:
                    await ctx.respond("👍 `Stopped importing Spotify playlist.`", ephemeral=True, delete_after=10)
                    await message.delete()
                    self.stop_import = False
                    return
                batch = tracks[i:i + batch_size]
                tasks = []
                for track in batch:
                    if "/album/" in query:
                        squery = f'ytmsearch:{track["name"]} {track["artists"][0]["name"]}'
                    elif "/playlist/" in query:
                        squery = f'ytmsearch:{track["track"]["name"]} {track["track"]["artists"][0]["name"]}'
                    tasks.append(player.node.get_tracks(squery))
                try:
                    results = await asyncio.gather(*tasks)
                except Exception as e:
                    self.logger.error(f"Error fetching tracks: {e}")
                    continue
                for result in results:
                    if not result or not result['tracks']:
                        self.logger.error(
                            f"Failed to get track for {track['name']} by {track['artists'][0]['name']}")
                        continue
                    track = result['tracks'][0]
                    player.add(requester=ctx.author.id, track=track)
                if not player.is_playing:
                    await player.play()
                    if shuffle:
                        player.shuffle = True
                    embed = discord.Embed(color=discord.Color.blurple())
                    embed.title = f'Awaiting song information...'
                    try:
                        self.playing_message = await ctx.channel.send(embed=embed)
                    except discord.errors.Forbidden:
                        self.logger.error("Bot does not have permission to send messages in this channel.")
                        # inform the user that the bot cannot send messages in this channel
                        await self.handle_missing_permissions(ctx, discord.Embed(
                            title="Error",
                            description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                        f"check my permissions.",
                            color=discord.Color.red()
                        ))
                        return
                elapsed_time = time.time() - start_time
                remaining_tracks = len(tracks) - (i + batch_size)
                estimated_time = (elapsed_time / (i + batch_size)) * remaining_tracks
                embed = discord.Embed(color=await Generate_color(str(playlist_info["images"][0]["url"])))
                if "/album/" in query:
                    embed.title = f'Importing Spotify album: {name}'
                    embed.description = (f'**Importing:** [{name}](<{query}>) by '
                                         f'[{playlist_info["artists"][0]["name"]}]'
                                         f'(<{playlist_info["artists"][0]["external_urls"]["spotify"]}>)')
                elif "/playlist/" in query:
                    embed.title = f'Importing Spotify playlist: {name}'
                    embed.description = (f'**Importing:** [{name}](<{query}>) by '
                                         f'[{playlist_info["owner"]["display_name"]}]'
                                         f'(<{playlist_info["owner"]["external_urls"]["spotify"]}>)')
                embed.set_thumbnail(url=playlist_info["images"][0]["url"])
                view = discord.ui.View()
                button = Button(style=ButtonStyle.red, label="Stop Import", custom_id="stop_import")
                button.callback = self.cb_stop_import
                view.add_item(button)
                progress = ((i + batch_size) / len(tracks)) * bar_length
                embed.add_field(
                    name=f"Progress: {round(((i + batch_size) / len(tracks)) * 100, 2)}% ({i + batch_size}/{len(tracks)}) | ETA: {round(estimated_time, 2)}s",
                    value=f"{'🟩' * int(progress)}{'⬜' * (bar_length - int(progress))}")
                await message.edit(embed=embed, content="", view=view)
            await message.edit(content="👍 `Finished import of Spotify to YouTube.`", embed=None)
            await asyncio.sleep(10)
            await message.delete()
            return True
        elif query.startswith("https://open.spotify.com/track/"):
            track = sp.track(query)
            self.logger.info(f"Spotify track: {track['name']} by {track['artists'][0]['name']}")
            query = f'ytmsearch:{track["name"]} {track["artists"][0]["name"]}'
        # Get the results for the query from Lavalink.
        results = await player.node.get_tracks(query)
        # Results could be None if Lavalink returns an invalid response (non-JSON/non-200 (OK)).
        # Alternatively, results.tracks could be an empty array if the query yielded no tracks.
        if not results or not results.tracks:
            return await ctx.respond('Nothing found!', delete_after=10, ephemeral=True)
        elif results.load_type == LoadType.EMPTY:
            return await ctx.respond(f'Nothing found for `{query}`!', delete_after=10, ephemeral=True)

        embed = discord.Embed(color=discord.Color.blurple(), title="Fetching song information...")

        if results.load_type == 'PLAYLIST':
            # If the query was a playlist, we add all the tracks to the queue.
            async for track in results.tracks:
                player.add(requester=ctx.author.id, track=track)
            self.logger.debug(f"Queue length: {len(player.queue)}")
        elif results.load_type == 'SEARCH':
            # If the query was a search query, we take the top item from the search results.
            track = results.tracks[0]
            player.add(requester=ctx.author.id, track=track)
            self.logger.debug(f"Queue length: {len(player.queue)}")
        elif results.load_type == 'TRACK':
            # If the query was a single video, we add it to the queue.
            track = results.tracks[0]
            player.add(requester=ctx.author.id, track=track)
        elif results.load_type == 'NO_MATCHES':
            return await ctx.respond('Nothing found!', delete_after=10, ephemeral=True)
        elif results.load_type == 'LOAD_FAILED':
            return await ctx.respond('Failed to load track.', delete_after=10, ephemeral=True)
        else:
            return await ctx.respond(f'Unknown results type: {results.load_type}', delete_after=10, ephemeral=True)
        # send thumbs up
        await ctx.respond("Enqueued song", delete_after=1, ephemeral=True)
        # We don't want to call .play() if the player is playing as that will effectively skip
        # the current track.
        if not player.is_playing or player.fetch('VoiceState') in ['-1', '0']:
            await player.play()
            self.playing_message = await ctx.channel.send(embed=embed)

    @commands.slash_command(name="quickplay", description="Play a song from your status.")
    async def quickplay(self, ctx: discord.ApplicationContext):
        status = ctx.author.activities
        for activity in status:
            if isinstance(activity, discord.Spotify):
                self.logger.debug(activity.to_dict())
                song = activity.title
                artist = activity.artist
                query = f'{song} {artist}'
                await self.play(ctx, query=query, source="direct")
                return await ctx.respond(f"Playing {song} by {artist} from your status!", ephemeral=True,
                                         delete_after=10)
        return await ctx.respond("I can't see the spotify status. Are you listening to spotify?", ephemeral=True,
                                 delete_after=10)

    async def get_playlist_songs(self, playlist):
        """
        Fetch all songs from a Spotify playlist or album efficiently.
        :param playlist: Spotify playlist or album URL.
        :return: A tuple containing the list of tracks and the playlist/album name.
        """
        try:
            # Determine if the input is a playlist or album
            if "/album/" in playlist:
                playlist_info = sp.album(playlist)
                tracks = playlist_info['tracks']
            elif "/playlist/" in playlist:
                playlist_info = sp.playlist(playlist)
                tracks = playlist_info['tracks']
            else:
                return False, "Not a playlist or album"
            total_tracks = tracks['total']
            self.logger.info(f"Fetching {total_tracks} tracks from {playlist_info['name']}...")
            # Semaphore to limit concurrent requests
            semaphore = asyncio.Semaphore(10)  # Adjust concurrency level as needed

            async def fetch_page(offset):
                async with semaphore:
                    try:
                        return sp.playlist_items(playlist, offset=offset, limit=100)['items']
                    except spotipy.SpotifyException as e:
                        self.logger.error(f"Error fetching page at offset {offset}: {e}")
                        return []

            # Create tasks for all pages
            tasks = [fetch_page(offset) for offset in range(0, total_tracks, 100)]
            all_tracks = await asyncio.gather(*tasks)
            # Flatten the list of tracks
            all_tracks = [track for page in all_tracks for track in page]
            self.logger.info(f"Successfully fetched {len(all_tracks)} tracks.")
            return all_tracks, playlist_info['name']
        except Exception as e:
            self.logger.error(f"Error fetching playlist songs: {e}")
            return False, str(e)

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
        await ctx.defer(ephemeral=True)
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
        now_playing = {"title": player.current.title, "thumb": player.current.uri, "author": player.current.author}
        name = ctx.author.display_name
        pages = paginator(items=player.queue, embed_data=embed_data, per_page=limit, current_info=now_playing,
                          author=name)
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

    async def cb_stop_import(self, ctx):
        # check to see if the user is the one who started the import
        if ctx.user.id != self.bot.user.id:
            self.stop_import = True

    @commands.slash_command(name="pirate", description="Add the pirate shanties playlist to the queue")
    @option(name="shuffle", description="Shuffle the playlist", required=False)
    async def pirate(self, ctx: discord.ApplicationContext, shuffle: bool = False):
        await self.play(ctx, query="https://open.spotify.com/playlist/098Oij7Ia2mktRbbTkBK0X?si=f457e15700534905", shuffle=shuffle)

    @commands.slash_command(name="clean", description="Cleanup spam in any channel")
    @commands.has_permissions(manage_messages=True)
    async def clean(self, ctx: discord.ApplicationContext, amount: int = 100):
        # check if the bot has manage messages permission
        if not ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            return await ctx.respond("⚠️ `I don't have the correct permissions for that. I need the MANAGE_MESSAGES "
                                     "permission!`", ephemeral=True)
        await ctx.respond(f"👍 `Cleaning {amount} messages, please wait.`", delete_after=10, ephemeral=True)
        self.stop_import = True
        await ctx.channel.purge(limit=amount, check=lambda m: m.author == ctx.bot.user and m.channel == ctx.channel)

    @commands.slash_command(name="display", description="Lost the display message? Use this command to get it back.")
    async def display(self, ctx: discord.ApplicationContext):
        if self.bot.lavalink.player_manager.get(ctx.guild.id).is_playing:
            await ctx.respond("👍 `Sending display message...`", delete_after=10, ephemeral=True)
            embed = discord.Embed(color=discord.Color.blurple())
            embed.title = f'Fetching song information...'
            if self.playing_message:
                try:
                    await self.playing_message.delete()
                except discord.NotFound:
                    pass
            if not ctx.channel.permissions_for(ctx.guild.me).send_messages:
                self.logger.warning("Bot cannot send messages in the current channel, skipping update.")
                await self.handle_missing_permissions(ctx.channel, discord.Embed(
                    title="Error",
                    description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                f"check my permissions.",
                    color=discord.Color.red()
                ))
                self.playing_message = None
                return
            else:
                try:
                    self.playing_message = await ctx.channel.send(embed=embed)
                except discord.errors.Forbidden:
                    self.logger.error("Bot does not have permission to send messages in this channel.")
                    self.playing_message = None
                    await self.handle_missing_permissions(ctx, discord.Embed(
                        title="Error",
                        description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                    f"check my permissions.",
                        color=discord.Color.red()
                    ))
            await self.update_playing_message(ctx)
        else:
            return await ctx.respond('Nothing playing.', delete_after=5, ephemeral=True)

    @commands.slash_command(name="search", description="Search for a song, and add it to the queue.")
    @option(name="query", description="The song to search for", required=True)
    @option(name="service", description="The music provider to search with", required=False,
            choices=["youtube", "spotify", "youtube_music"],
            default="youtube_music")
    async def search(self, ctx: discord.ApplicationContext, query: str, service: str = "youtube_music"):
        async def dropdown_callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            selected_value = interaction.data['values'][0]

            if not hasattr(self.bot,
                           'lavalink') or self.bot.lavalink is None or not self.bot.lavalink.node_manager.nodes:
                self.logger.error("Lavalink is not initialized/ready on the bot object in dropdown.")
                await interaction.edit_original_response(content="Music system error: Lavalink not ready.", view=None)
                return
            if not interaction.guild_id:
                self.logger.error("Interaction guild_id is None in dropdown_callback.")
                await interaction.edit_original_response(content="Error: Guild context not found.", view=None)
                return

            player = self.bot.lavalink.player_manager.get(interaction.guild_id)
            if player is None:
                player = self.bot.lavalink.player_manager.create(interaction.guild_id)

            actual_track_title = "Track"
            actual_track_author = ""
            results_lavalink = None

            if selected_value.startswith("spotify:track:"):
                try:
                    sp_track_info = sp.track(selected_value)
                    actual_track_title = sp_track_info['name']
                    actual_track_author = sp_track_info['artists'][0]['name']
                    query_for_lavalink = f'ytmsearch:{actual_track_title} {actual_track_author}'
                    results_lavalink = await player.node.get_tracks(query_for_lavalink)
                except Exception as e:
                    self.logger.error(f"Error processing Spotify track URI {selected_value}: {e}")
                    await interaction.edit_original_response(content=f"Error processing Spotify track: {e}", view=None)
                    return
            else:
                results_lavalink = await player.node.get_tracks(selected_value)
                if results_lavalink and results_lavalink.tracks:
                    actual_track_title = results_lavalink.tracks[0].title
                    actual_track_author = results_lavalink.tracks[0].author

            final_track_display_name = f"{actual_track_title} by {actual_track_author}" if actual_track_author else actual_track_title

            if not results_lavalink or not results_lavalink.tracks:
                await interaction.edit_original_response(
                    content=f"Could not find track information for: {final_track_display_name}.", view=None)
                return

            track_to_play = results_lavalink.tracks[0]
            player.add(requester=interaction.user.id, track=track_to_play)

            response_message_content = ""
            playback_started_successfully = False

            user_vc_channel = interaction.user.voice.channel if interaction.user.voice else None

            if not user_vc_channel:
                if (not player.is_playing and len(player.queue) == 1 and
                        player.queue[0].identifier == track_to_play.identifier):
                    player.queue.pop(0)
                    response_message_content = (f"🎶 Track **{final_track_display_name}** removed. Join a voice "
                                                f"channel to play music.")
                else:
                    response_message_content = (f"🎶 Track **{final_track_display_name}** added to queue. Join a voice "
                                                f"channel to play.")
                await interaction.edit_original_response(content=response_message_content, view=None)
                return

            bot_vc = interaction.guild.voice_client if interaction.guild else None

            if not player.is_playing:
                connected_or_moved = False
                if not bot_vc:
                    try:
                        await user_vc_channel.connect(cls=LavalinkVoiceClient)
                        player.store('VoiceChannel', user_vc_channel.id)
                        player.store('channel', interaction.channel_id)
                        connected_or_moved = True
                    except Exception as e:
                        self.logger.error(f"Dropdown: Failed to connect to {user_vc_channel.name}: {e}")
                        if player.queue and player.queue[0].identifier == track_to_play.identifier: player.queue.pop(0)
                        response_message_content = f"Error: Could not join {user_vc_channel.name}. Track removed."
                elif bot_vc.channel.id != user_vc_channel.id:
                    try:
                        await bot_vc.move_to(user_vc_channel)
                        player.store('VoiceChannel', user_vc_channel.id)
                        player.store('channel', interaction.channel_id)
                        connected_or_moved = True
                    except Exception as e:
                        self.logger.error(f"Dropdown: Failed to move to {user_vc_channel.name}: {e}")
                        if player.queue and player.queue[0].identifier == track_to_play.identifier: player.queue.pop(0)
                        response_message_content = f"Error: Could not move to {user_vc_channel.name}. Track removed."
                else:
                    connected_or_moved = True
                    player.store('channel', interaction.channel_id)

                if connected_or_moved:
                    await player.play()
                    playback_started_successfully = True
                    response_message_content = f"🎵 Now playing: **{final_track_display_name}**"
            else:
                if bot_vc and bot_vc.channel.id != user_vc_channel.id:
                    response_message_content = f"🎶 Track **{final_track_display_name}** added. I'm playing in {bot_vc.channel.mention}."
                else:
                    response_message_content = f"🎶 Track **{final_track_display_name}** added to queue."

            await interaction.edit_original_response(content=response_message_content, view=None)
            current_playing_msg = self.get_playing_message(interaction.guild_id)

            if playback_started_successfully and interaction.channel:
                if current_playing_msg and current_playing_msg.channel.id != interaction.channel_id:
                    try:
                        await current_playing_msg.delete()
                        self.delete_playing_message_ref(interaction.guild_id)
                    except discord.errors.NotFound:
                        self.delete_playing_message_ref(interaction.guild_id)
                    except Exception as e:
                        self.logger.error(f"Error deleting old playing_message for guild {interaction.guild_id}: {e}")
                        self.delete_playing_message_ref(interaction.guild_id)
                    current_playing_msg = None

                if current_playing_msg is None:
                    embed = discord.Embed(color=discord.Color.blurple(), title="Fetching track information...")
                    try:
                        self.playing_message = await interaction.channel.send(embed=embed)
                        self.store_playing_message_ref(interaction.guild_id, self.playing_message)
                        await self.update_playing_message(interaction)
                    except discord.Forbidden:
                        self.logger.error(
                            f"Failed to send playing_message in dropdown_callback for guild {interaction.guild_id}. "
                            f"Missing permissions.")
                    except discord.errors.HTTPException as e:
                        self.logger.error(
                            f"Failed to send playing_message in dropdown_callback for guild {interaction.guild_id}: {e}")
                    except AttributeError:
                        self.logger.error(
                            f"interaction.channel is None or interaction object is malformed for guild {interaction.guild_id}.")

        if not hasattr(self.bot, 'lavalink') or self.bot.lavalink is None or not self.bot.lavalink.node_manager.nodes:
            await ctx.respond("Music system is not ready. Please try again later.", ephemeral=True)
            return
        if not ctx.guild_id:
            await ctx.respond("This command can only be used in a server.", ephemeral=True)
            return

        player = self.bot.lavalink.player_manager.get(ctx.guild_id)
        if player is None:
            player = self.bot.lavalink.player_manager.create(ctx.guild_id)

        is_internal_call = "cmd" in service
        if not is_internal_call:
            await ctx.defer(ephemeral=True)
        else:
            service = service.replace("cmd", "")

        options = []
        if service == "spotify":
            results_spotify = sp.search(q=query, type="track", limit=20)
            if not results_spotify or not results_spotify['tracks']['items']:
                response_content = 'Nothing found on Spotify!'
                if is_internal_call:
                    await ctx.edit_original_response(content=response_content, view=None)
                else:
                    await ctx.respond(response_content, ephemeral=True, delete_after=10)
                return
            options = [{"value": track['uri'],
                        "label": limit(f"{track['name']} by {track['artists'][0]['name']}", 100)} for track in
                       results_spotify['tracks']['items']]
        else:
            search_query_for_lavalink = query
            if service == "youtube_music":
                search_query_for_lavalink = f'ytmsearch:{query}'
            elif service == "youtube":
                search_query_for_lavalink = f'ytsearch:{query}'

            results_lavalink_search = await player.node.get_tracks(search_query_for_lavalink)

            if not results_lavalink_search or not results_lavalink_search.tracks:
                response_content = 'Nothing found!'
                if is_internal_call:
                    await ctx.followup.send(content=response_content, ephemeral=True)
                else:
                    await ctx.respond(response_content, ephemeral=True, delete_after=10)
                return

            if (results_lavalink_search.load_type == LoadType.SEARCH or
                    results_lavalink_search.load_type == lavalink.LoadType.TRACK):
                options = [{"value": track.uri, "label": limit(f"{track.title} by {track.author}", 100)} for track in
                           results_lavalink_search.tracks[:25] if len(track.uri) < 100]
            else:
                response_content = 'Nothing found or unsupported link type for search!'
                if is_internal_call:
                    await ctx.followup.send(content=response_content, ephemeral=True)
                else:
                    await ctx.respond(response_content, ephemeral=True, delete_after=10)
                return
        if options:
            view = discord.ui.View(timeout=180)
            select_menu = DiscordDropDownSelect(
                options=options,
                placeholder=f"Found {len(options)} results for \"{query}\""
            )
            select_menu.callback = dropdown_callback
            view.add_item(select_menu)

            response_content = "Select a song to add to the queue:"
            if is_internal_call:
                await ctx.followup.send(content=response_content, view=view, ephemeral=True)
            else:
                await ctx.respond(response_content, view=view, ephemeral=True)
        else:
            response_content = 'Nothing found!'
            if is_internal_call:
                await ctx.followup.send(content=response_content, ephemeral=True)
            else:
                await ctx.respond(response_content, ephemeral=True, delete_after=10)


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
    if image.size[0] == image.size[1] and image.size[0] > 100:
        left_color = image.getpixel((int(image.size[0] * 0.05), int(image.size[1] / 2)))
        right_color = image.getpixel((int(image.size[0] * 0.95), int(image.size[1] / 2)))
        if left_color == right_color:
            return discord.Color.from_rgb(left_color[0], left_color[1], left_color[2])
    image = image.resize((int(image.size[0] * (100 / image.size[1])), 100), Image.Resampling.LANCZOS)
    colors = image.getcolors(image.size[0] * image.size[1])
    if not colors:
        return discord.Color.blurple()
    colors.sort(key=lambda x: x[0], reverse=True)
    while colors:
        color = colors[0][1]
        if color != (0, 0, 0) and color != (255, 255, 255):
            break
        colors.pop(0)
    else:
        return discord.Color.blurple()
    try:
        if len(color) < 3:
            return discord.Color.blurple()
    except TypeError:
        return discord.Color.blurple()
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
    # Split the list into chunks of per_page
    chunks = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    # Check if the amount of chunks is larger than the hard limit
    if len(chunks) > hard_limit:
        # If it is, then we will just return the first hard_limit pages
        chunks = chunks[:hard_limit]
    # Loop through the chunks
    index = 1
    for chunk in chunks:
        # Create a new embed
        embed = discord.Embed(**embed_data)
        embed.description = f"Currently playing: {current_info['title']}\nFor more info use /nowplaying"
        embed.set_footer(text=f"Requested by {author}")
        # Add the items to the embed
        for item in chunk:
            embed.add_field(name=f"{index}. {item.title}", value=f"{item.author} [Source Video]({item.uri})",
                            inline=False)
            index += 1
        # Add the embed to the pages
        pages.append(embed)
    return pages


def limit(string: str, limit: int):
    """
    Limit the length of a string

    :param string: The string to limit
    :param limit: The limit of the string
    :return: The limited string
    """
    if len(string) > limit:
        return string[:limit - 3] + "..."
    return string


def setup(bot):
    bot.add_cog(Music(bot, bot.logger))
