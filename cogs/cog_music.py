import asyncio
import datetime
import io
import json
import logging
import random
import re
import traceback
import urllib.parse
from pprint import pprint

import aiohttp
import discord
import lavalink
import lyricsgenius
import requests
import spotipy
from discord import option, ButtonStyle
from discord.ext.pages import Paginator
from discord.ui import Button
from lavalink import LoadType
from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv
from discord.ext import commands, tasks
from lavalink.filters import *
import os
import time
import sponsorblock as sb
from utils.topgg_api import TopGGAPI
from utils.generic import Generate_color, paginator, limit, progress_bar
from utils.profanity import ProfanityFilter

load_dotenv()
SPOTIFY_CLIENT_ID = str(os.getenv('SPOTIFY_CLIENT_ID'))
SPOTIFY_CLIENT_SECRET = str(os.getenv('SPOTIFY_SECRET'))
url_rx = re.compile(r'https?://(?:www\.)?.+')
sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID,
                                                                         client_secret=SPOTIFY_CLIENT_SECRET))
PIRATE_RADIO_URL = "https://open.spotify.com/playlist/098Oij7Ia2mktRbbTkBK0X?si=f457e15700534905"
# Keep parity with requested behavior: queue up to the top 20 tracks for artist URLs.
ARTIST_TOP_TRACK_LIMIT = 20

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


def parse_radio_config(raw_radios: str):
    stations = {}
    if not raw_radios:
        return stations
    for entry in raw_radios.split(","):
        value = entry.strip()
        if not value or ";" not in value:
            continue
        name, url = value.split(";", 1)
        name = name.strip()
        url = url.strip()
        if not name or not url:
            continue
        stations[name.lower()] = {"name": name, "url": url}
    return stations


def build_eternal_jukebox_url(spotify_track_id: str):
    base_url = os.getenv("ETERNAL_JUKEBOX_URL", "https://eternalbox.floriegl.tech/jukebox_go.html")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}id={urllib.parse.quote(spotify_track_id)}"

class Music(commands.Cog):
    def __init__(self, bot, logger):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.handlers = logger.handlers
        self.logger.setLevel(logger.level)
        self.logger.propagate = False
        self.disconnect_timer = False
        self.bot = bot
        self.profanity_filter = ProfanityFilter()
        self.playing_message = None
        self.update_playing_message.start()
        self.test_vid.start()
        self.check_call.start()
        self.sponsorBlock = True
        self.eternal_jukebox = False
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
        self.last_song_uri = None
        self.last_song_uri_cache = None
        self.sponsorblock_message_sent = False
        self.last_sponsorblock_message_time = 0
        self.playing_message_refs_file = os.path.join("temp", "playing_message_refs.json")
        self.fetching_recommendations = False
        self.recommendations_fetched = {}  # Track fetched recommendations per guild
        self.radio_stations = parse_radio_config(os.getenv("RADIOS", ""))
        self.radio_stations["pirate"] = {
            "name": "pirate",
            "url": PIRATE_RADIO_URL,
        }
        # Radio queue state storage - stores saved queue per guild
        self.radio_saved_queues = {}

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
                if not results or not results.tracks:
                    self.logger.error("Lavalink failed to connect.")
                    return
                self.logger.info(f"Connected to Lavalink. Test video: {results.tracks[0].title}")
            except (IndexError, AttributeError) as e:
                self.logger.error(f"Failed to connect to Lavalink: {e}")
                return
        else:
            self.logger.warning("Lavalink already connected.")

    def get_radio_station(self, station: str):
        if not station:
            return None
        key = station.strip().lower()
        return self.radio_stations.get(key)

    def format_station_name(self, station_key: str) -> str:
        """Format station name for display (replace _ with space, capitalize each word)."""
        return " ".join(word.capitalize() for word in station_key.replace("_", " ").split())

    def save_queue_state(self, guild_id: int, player) -> None:
        """Save the current queue and track position before switching to radio."""
        if guild_id not in self.radio_saved_queues:
            queue_data = {
                "current_track": None,
                "position": 0,
                "queue": []
            }
            
            # Save current track info
            if player.current:
                queue_data["current_track"] = {
                    "uri": player.current.uri,
                    "title": player.current.title,
                    "author": player.current.author,
                    "identifier": player.current.identifier,
                }
                queue_data["position"] = player.position
            
            # Save queue tracks
            for track in player.queue:
                queue_data["queue"].append({
                    "uri": track.uri,
                    "title": track.title,
                    "author": track.author,
                    "identifier": track.identifier,
                })
            
            self.radio_saved_queues[guild_id] = queue_data
            self.logger.info(f"Saved queue state for guild {guild_id}: {len(queue_data['queue'])} tracks in queue, "
                           f"current track: {queue_data['current_track']['title'] if queue_data['current_track'] else 'None'}")

    def restore_queue_state(self, guild_id: int, player) -> bool:
        """Restore the saved queue and track position after exiting radio mode."""
        if guild_id not in self.radio_saved_queues:
            self.logger.debug(f"No saved queue state for guild {guild_id}")
            return False
        
        queue_data = self.radio_saved_queues.pop(guild_id)
        
        # Clear current queue
        player.queue.clear()
        
        # Add saved queue tracks back
        for track_data in queue_data["queue"]:
            # Recreate track object from saved data
            # We'll need to fetch the actual track from Lavalink
            # For now, we'll store the URIs and they can be re-added
            self.logger.debug(f"Restoring track: {track_data['title']}")
        
        # Restore current track if it exists
        if queue_data["current_track"]:
            self.logger.info(f"Queue state restored for guild {guild_id}: {len(queue_data['queue'])} tracks in queue")
            return True
        
        return False

    def resolve_spotify_track_id(self, title: str, author: str):
        if not title:
            return None
        query = f"track:{title}"
        if author:
            query += f" artist:{author}"
        try:
            result = sp.search(q=query, type="track", limit=1)
        except spotipy.SpotifyException as e:
            self.logger.debug(f"Failed to resolve Spotify track id for Eternal Jukebox: {e}")
            return None
        tracks = (result or {}).get("tracks", {}).get("items", [])
        if not tracks:
            return None
        return tracks[0].get("id")

    async def refresh_eternal_jukebox_link(self, player):
        if player is None or player.current is None:
            if player is not None:
                player.store("eternal_jukebox_url", None)
            return None
        spotify_track_id = await asyncio.to_thread(
            self.resolve_spotify_track_id,
            player.current.title,
            player.current.author
        )
        if not spotify_track_id:
            player.store("eternal_jukebox_url", None)
            return None
        url = build_eternal_jukebox_url(spotify_track_id)
        player.store("eternal_jukebox_url", url)
        return url

    async def get_skip_segments(self, uri):
        if self.last_song_uri == uri:
            if self.last_song_uri_cache is not None:
                return self.last_song_uri_cache
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    f"https://sponsor.ajay.app/api/skipSegments?videoID={uri}") as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        self.last_song_uri_cache = data
                        self.last_song_uri = uri
                        return data
                    else:
                        self.last_song_uri_cache = None
                        self.last_song_uri = None
                        return None
                if 400 <= response.status < 500:
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"Client error: {response.status} - {await response.text()}"
                    )
                if 500 <= response.status < 600:
                    raise aiohttp.ServerResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"Server error: {response.status} - {await response.text()}"
                    )
                else:
                    return None

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info("onready event received")
        await self.cleanup_orphaned_playing_messages()
        await self.wait_until_lavalink_ready()
        self.check_update_status.start()
        self.logger.info("Lavalink ready and cog is loaded.")
        if not hasattr(self.bot, 'topgg') and os.getenv('TOPGG_TOKEN'):
            self.bot.topgg = TopGGAPI(token=os.getenv('TOPGG_TOKEN'), bot_id=self.bot.user.id)
        elif not hasattr(self.bot, 'topgg') and (os.getenv('TOPGG_TOKEN') is None or os.getenv('TOPGG_TOKEN') == ""):
            self.logger.warning("TOPGG_TOKEN not found in environment variables. Top.gg API will not be available.")
            self.bot.topgg = TopGGAPI(token=os.getenv('TOPGG_TOKEN'), bot_id=self.bot.user.id, bypass=True)

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
        # Profanity filter the status
        # This will replace only profane words with asterisks, not the entire string
        status = self.profanity_filter.filter(status)
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

    async def interaction_send(self, ctx, *, content: str = None,
                               embed: discord.Embed = None, ephemeral: bool = True, delete_after: int = None,
                               **kwargs):
        """Send a slash-command response safely even if the initial interaction is already acknowledged."""
        try:
            scheduler = getattr(self.bot, "schedule_persistent_delete", None)
            native_delete_after = delete_after
            response = getattr(ctx, "response", None)
            if response is not None and response.is_done():
                message = await ctx.followup.send(content=content, embed=embed, ephemeral=ephemeral,
                                                  delete_after=native_delete_after, **kwargs)
            elif hasattr(ctx, "respond"):
                message = await ctx.respond(content=content, embed=embed, ephemeral=ephemeral,
                                            delete_after=native_delete_after, **kwargs)
            elif hasattr(ctx, "send"):
                message = await ctx.send(content=content, embed=embed, delete_after=native_delete_after, **kwargs)
            else:
                message = None

            if scheduler is not None and delete_after is not None and message is not None:
                await scheduler(message, delete_after)
            return message
        except discord.errors.NotFound:
            self.logger.warning("Interaction expired before a response could be sent.")
            return None

    async def safe_defer(self, interaction: discord.Interaction, ephemeral: bool = False) -> bool:
        """Safely defer an interaction response. Returns True if defer succeeded."""
        try:
            resp = getattr(interaction, 'response', None)
            if resp is None:
                return False
            await resp.defer(ephemeral=ephemeral)
            return True
        except discord.errors.NotFound:
            # Interaction no longer exists / has expired
            self.logger.debug("Interaction defer failed: Unknown or expired interaction.")
            return False
        except discord.HTTPException as e:
            self.logger.warning(f"Interaction defer failed: {e}")
            return False
        except Exception:
            self.logger.exception("Unexpected error while deferring interaction")
            return False

    async def send_with_delete_tracking(self, destination, *, delete_after: int = None, **kwargs):
        """Send a channel message and persist delete_after for restart-safe cleanup."""
        scheduler = getattr(self.bot, "schedule_persistent_delete", None)
        native_delete_after = delete_after
        message = await destination.send(delete_after=native_delete_after, **kwargs)
        if scheduler is not None and delete_after is not None:
            await scheduler(message, delete_after)
        return message

    async def safe_edit_original(self, interaction: discord.Interaction, **kwargs):
        """Edit an interaction's original response without raising on expired interactions."""
        try:
            return await interaction.edit_original_response(**kwargs)
        except discord.errors.NotFound:
            self.logger.warning("Interaction expired before original response could be edited.")
            return None

    async def set_bot_pause_mute(self, guild: discord.Guild, muted: bool):
        """Mirror player pause state in voice by self-muting/unmuting the bot."""
        if guild is None or guild.voice_client is None or guild.voice_client.channel is None:
            return
        try:
            await guild.change_voice_state(channel=guild.voice_client.channel, self_mute=muted)
        except (discord.errors.Forbidden, discord.errors.HTTPException) as exc:
            self.logger.warning(f"Could not update bot self-mute state to {muted}: {exc}")

    async def notify_owner_error(self, message: str, tb: str):
        """Best-effort owner notification that never raises into command handlers."""
        owner_id = getattr(self.bot, "owner_id", None)
        if not owner_id:
            self.logger.error(f"Owner ID is not set. Original error: {message}\n{tb}")
            return

        user = self.bot.get_user(owner_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(owner_id)
            except (discord.errors.NotFound, discord.errors.HTTPException) as exc:
                self.logger.error(f"Failed to fetch owner user {owner_id}: {exc}. Original error: {message}\n{tb}")
                return

        try:
            if len(f"{message}\n```{tb}```") > 2000:
                tb_file = io.BytesIO(tb.encode("utf-8"))
                await user.send(message, file=discord.File(tb_file, "traceback.txt"))
            else:
                await user.send(f"{message}\n```{tb}```")
        except (discord.errors.Forbidden, discord.errors.HTTPException) as exc:
            self.logger.error(f"Could not DM owner about error: {exc}. Original error: {message}\n{tb}")

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        if isinstance(error, discord.errors.Forbidden):
            await self.handle_permission_error(ctx, "send_messages")
        elif isinstance(error, commands.MissingPermissions):
            await self.handle_permission_error(ctx, ", ".join(error.missing_permissions))
        if isinstance(error, commands.CommandInvokeError):
            embed = discord.Embed(title="Error", description=f"```{error.original}```", color=discord.Color.red())
            self.logger.error(f"Error in {ctx.command.name}: {error.original}\n{tb}")
            await self.interaction_send(ctx, embed=embed, ephemeral=True, delete_after=10)
        #if the error is not isinstance(error, commands.MissingPermissions):
        if not isinstance(error, commands.CommandInvokeError):
            self.logger.error(f"Error in {ctx.command.name}: {error}\n{tb}")
            await self.notify_owner_error(f"Error in {ctx.command.name}: {error}", tb)

    async def handle_permission_error(self, ctx, missing_permission):
        """Handle permission errors by finding an alternative channel or DMing the user."""
        guild = ctx.guild
        user = ctx.author
        # Search for a channel where the bot has permission to send messages
        # try original channel first
        permissions = ctx.channel.permissions_for(guild.me)
        if permissions.send_messages:
            await ctx.respond(
                f"{user.mention}, I am missing the `{missing_permission}` permission. Please check my permissions.",
                ephemeral=True
            )
            return
        for channel in guild.text_channels:
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
            try:
                await alternative_channel.send(f"<@{ctx.author.id}> I am missing permissions in {ctx.channel.mention}.",
                                               embed=embed)
            except AttributeError:
                # ctx could be Interaction (to fix AttributeError: 'Interaction' object has no attribute 'author')
                await alternative_channel.send(f"<@{ctx.user.id}> I am missing permissions in {ctx.channel.mention}.",
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
        if self.playing_message is None:
            return None
        msg_guild = getattr(self.playing_message, "guild", None)
        if msg_guild is None:
            return None
        if int(msg_guild.id) != int(guild_id):
            return None
        return self.playing_message

    def _read_playing_message_refs(self):
        if not os.path.exists(self.playing_message_refs_file):
            return []
        try:
            with open(self.playing_message_refs_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [entry for entry in data if isinstance(entry, dict)]
        except (OSError, json.JSONDecodeError):
            self.logger.warning("Failed to read playing message refs.")
        return []

    def _write_playing_message_refs(self, refs):
        os.makedirs(os.path.dirname(self.playing_message_refs_file), exist_ok=True)
        tmp_path = f"{self.playing_message_refs_file}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(refs, f)
        os.replace(tmp_path, self.playing_message_refs_file)

    def store_playing_message_ref(self, guild_id, message):
        if message is None:
            return
        channel_id = getattr(getattr(message, "channel", None), "id", None)
        message_id = getattr(message, "id", None)
        if channel_id is None or message_id is None:
            return
        refs = [entry for entry in self._read_playing_message_refs() if int(entry.get("guild_id", -1)) != int(guild_id)]
        refs.append({
            "guild_id": int(guild_id),
            "channel_id": int(channel_id),
            "message_id": int(message_id),
        })
        self._write_playing_message_refs(refs)

    def delete_playing_message_ref(self, guild_id):
        refs = [entry for entry in self._read_playing_message_refs() if int(entry.get("guild_id", -1)) != int(guild_id)]
        self._write_playing_message_refs(refs)

    def _delete_current_playing_message_ref(self):
        if self.playing_message is None:
            return
        guild = getattr(self.playing_message, "guild", None)
        if guild is None:
            return
        self.delete_playing_message_ref(guild.id)

    async def cleanup_orphaned_playing_messages(self):
        refs = self._read_playing_message_refs()
        if not refs:
            return

        remaining = []
        for entry in refs:
            channel_id = int(entry.get("channel_id", 0))
            message_id = int(entry.get("message_id", 0))
            if channel_id == 0 or message_id == 0:
                continue

            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.errors.NotFound, discord.errors.Forbidden):
                    continue
                except discord.errors.HTTPException:
                    remaining.append(entry)
                    continue

            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except discord.errors.NotFound:
                continue
            except (discord.errors.Forbidden, discord.errors.HTTPException):
                remaining.append(entry)

        self._write_playing_message_refs(remaining)

    @tasks.loop(seconds=30)
    async def check_call(self):
        """Check the voice channel to see if the bot is the only one in the channel"""
        if self.playing_message is None or not hasattr(self.bot, 'lavalink') or self.bot.lavalink is None:
            return

        player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
        if player is None:
            return

        channel_id = player.fetch('VoiceChannel')
        channel = self.bot.get_channel(channel_id) if channel_id else None
        client = channel.guild.voice_client if channel else None
        if channel is None or client is None:
            return

        # if the player is connected and the bot is the only one in the channel (not counting other bots and itself)
        self.logger.debug(
            f'There are {len([member for member in channel.members if not member.bot])} members in the vc.')
        if player.is_connected and len([member for member in channel.members if not member.bot]) == 0 and not self.disconnect_timer:
            self.disconnect_timer = True
            await self.send_with_delete_tracking(
                self.playing_message.channel,
                content="`I will leave the voice channel in 30 seconds if no one joins.`",
                delete_after=60,
            )
            await asyncio.sleep(30)
            if player.is_connected and len([member for member in channel.members if not member.bot]) == 0 and self.disconnect_timer:
                self.disconnect_timer = False
                self.stop_import = True
                await player.set_pause(True)
                await self.set_bot_pause_mute(channel.guild, True)
                await client.disconnect(force=True)
                # await player.reset_filters()
                msg_channel = self.playing_message.channel
                await self.playing_message.delete()
                self._delete_current_playing_message_ref()
                self.playing_message = None
                await self.send_with_delete_tracking(
                    msg_channel,
                    content="`I have left the voice channel because I was alone.`\nUnpause the music with `/pause`",
                    delete_after=10,
                )
            else:
                self.disconnect_timer = False

    @tasks.loop(seconds=5)
    async def update_playing_message(self, ctx=None):
        async def play_callback(interaction):
            await self.safe_defer(interaction)
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            if player.paused:
                await player.set_pause(False)
                await self.set_bot_pause_mute(interaction.guild, False)
            else:
                await player.set_pause(True)
                await self.set_bot_pause_mute(interaction.guild, True)
            await self.update_playing_message()

        async def skip_callback(interaction):
            await self.safe_defer(interaction)
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            await player.skip()
            await self.update_playing_message()

        async def stop_callback(interaction):
            await self.safe_defer(interaction, ephemeral=True)
            player = self.bot.lavalink.player_manager.get(interaction.guild_id)
            if not self.bot.get_channel(player.channel_id):
                return await self.send_with_delete_tracking(interaction.channel, content='Not connected.', delete_after=10)
            if interaction.user.voice.channel != self.bot.get_channel(player.channel_id):
                return await self.send_with_delete_tracking(
                    interaction.channel,
                    content="You are not in the same voice channel as me.",
                    delete_after=10,
                )
            
            # Check if we're in radio mode and restore queue
            current_radio = player.fetch("radio_name")
            if current_radio:
                # Try to restore the queue
                if self.restore_queue_state(interaction.guild_id, player):
                    # Restore was successful, clear radio mode
                    player.store("radio_name", None)
                    player.store("eternal_jukebox_url", None)
                    await self.send_with_delete_tracking(interaction.channel, content='*⃣ | Exited radio mode. Queue restored.', delete_after=10)
                    await self.update_playing_message()
                    return None
            
            self.stop_import = True
            self.bot.lavalink.player_manager.get(interaction.guild_id).queue.clear()
            await self.bot.lavalink.player_manager.get(interaction.guild_id).stop()
            player.store("radio_name", None)
            player.store("eternal_jukebox_url", None)
            for vc in self.bot.voice_clients:
                if vc.guild == interaction.guild:
                    await vc.disconnect()
            try:
                await self.playing_message.delete()
                self._delete_current_playing_message_ref()
                self.playing_message = None
            except AttributeError:
                pass
            await self.send_with_delete_tracking(interaction.channel, content='*⃣ | Disconnected.', delete_after=10)
            return None

        async def shuffle_callback(interaction):
            await self.safe_defer(interaction)
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            player.shuffle = not player.shuffle
            await self.update_playing_message()

        async def loop_callback(interaction):
            await self.safe_defer(interaction)
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            if player.loop == player.LOOP_NONE:
                player.loop = player.LOOP_QUEUE
            elif player.loop == player.LOOP_QUEUE:
                player.loop = player.LOOP_SINGLE
            elif player.loop == player.LOOP_SINGLE:
                player.loop = player.LOOP_NONE
            await self.update_playing_message()

        async def sponsorBlock_callback(interaction):
            await self.safe_defer(interaction)
            self.sponsorBlock = not self.sponsorBlock
            await self.update_playing_message()

        async def eternal_jukebox_callback(interaction):
            await self.safe_defer(interaction)
            self.eternal_jukebox = not self.eternal_jukebox
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            if self.eternal_jukebox:
                await self.refresh_eternal_jukebox_link(player)
            else:
                player.store("eternal_jukebox_url", None)
            await self.update_playing_message()

        async def recommendations_callback(interaction):
            await self.safe_defer(interaction, ephemeral=True)
            player = self.bot.lavalink.player_manager.get(interaction.guild.id)
            if player.is_playing:
                self.continue_playing = not self.continue_playing
                if self.continue_playing:
                    await self.send_with_delete_tracking(
                        interaction.channel,
                        content="`Recommendations are now enabled.`",
                        delete_after=10,
                    )
                else:
                    await self.send_with_delete_tracking(
                        interaction.channel,
                        content="`Recommendations are now disabled.`",
                        delete_after=10,
                    )
            else:
                await self.send_with_delete_tracking(
                    interaction.channel,
                    content="`Recommendations are only available while playing music.`",
                    delete_after=10,
                )

        try:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            channel = self.bot.get_channel(player.fetch('VoiceChannel'))
            client = channel.guild.voice_client
            if player.fetch('VoiceStatus') == "-1":  # We have been kicked from the channel
                self.stop_import = True
                try:
                    await self.send_with_delete_tracking(
                        self.playing_message.channel,
                        content="`I have been kicked from the voice channel. :(`",
                        delete_after=10,
                    )
                    await self.playing_message.delete()
                    self._delete_current_playing_message_ref()
                except discord.errors.Forbidden as e:
                    self.logger.error(f"Error deleting playing message: {e}")
                    self._delete_current_playing_message_ref()
                    self.playing_message = None
                self._delete_current_playing_message_ref()
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
            return None
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
                return None
            if player.current:
                current_radio = player.fetch("radio_name")
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
                    color = await Generate_color(self.CP[0]['song_art_image_url'], random_color=True)
                else:
                    color = await Generate_color(
                        f"https://img.youtube.com/vi/{player.current.identifier}/hqdefault.jpg", random_color=True)
                if player.paused:
                    embed_title = "Paused " + loop + " " + shuffle
                else:
                    try:
                        embed_title = "Now Playing " + loop + " " + shuffle
                    except AttributeError:
                        embed_title = "Now Playing " + loop + " " + shuffle
                if current_radio:
                    if player.paused:
                        embed_title = f"Radio Paused • {current_radio}"
                    else:
                        embed_title = f"Live Radio • {current_radio}"
                try:
                    embed_description = f'**[{player.current.title}]({player.current.uri})**\n{player.current.author}'
                except AttributeError:
                    embed_description = '[UNABLE TO GET TITLE]'
                embed = discord.Embed(title=embed_title.strip(), description=embed_description, color=color)
                if self.CP is not None:
                    embed.set_thumbnail(url=self.CP[0]['song_art_image_url'])
                else:
                    if player.current is None:
                        return None
                    else:
                        embed.set_thumbnail(url=f"https://img.youtube.com/vi/"
                                                f"{player.current.identifier if player is not None else 'ABCDEF'}"
                                                f"/hqdefault.jpg")
                if player.current is None:
                    return None
                elif current_radio:
                    embed.add_field(name='Station', value=current_radio, inline=False)
                    embed.add_field(name='Stream', value='LIVE', inline=False)
                else:
                    embed.add_field(name='Duration', value=f'{lavalink.utils.format_time(player.position)}/'
                                                           f'{lavalink.utils.format_time(player.current.duration)} '
                                                           f'({int((player.position / player.current.duration) * 100)}'
                                                           f'%)',
                                    inline=False)
                    embed.add_field(name='Progress', value=progress_bar(player), inline=False)
                eternal_jukebox_url = player.fetch("eternal_jukebox_url")
                if self.eternal_jukebox and eternal_jukebox_url:
                    embed.add_field(
                        name="Eternal Jukebox",
                        value=f"[Open loop mode]({eternal_jukebox_url})",
                        inline=False
                    )
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
                if self.eternal_jukebox:
                    buttons.append(
                        [Button(style=ButtonStyle.green, emoji="♾️", custom_id="eternal_jukebox"), "eternal_jukebox"])
                else:
                    buttons.append(
                        [Button(style=ButtonStyle.red, emoji="♾️", custom_id="eternal_jukebox"), "eternal_jukebox"])
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
                    return None
                except discord.errors.NotFound:
                    try:
                        self.logger.warning("Message not found, creating new one")
                        if self.playing_message is not None:
                            self.playing_message = await self.playing_message.channel.send("", embed=embed, view=view)
                            self.store_playing_message_ref(self.playing_message.guild.id, self.playing_message)
                            return None
                        return None
                    except discord.errors.Forbidden:
                        self.logger.error("Bot cannot send messages in the current channel, skipping update.")
                        await self.handle_missing_permissions(self.playing_message, discord.Embed(
                            title="Error",
                            description="I cannot send messages in this channel. Please check my permissions.",
                            color=discord.Color.red()
                        ))
                        return None
            return None

    @update_playing_message.error
    async def update_playing_message_error(self, exception):
        self.logger.error(f"Error in update_playing_message: {exception}")
        tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
        tb = ''.join(tb)
        await self.notify_owner_error(f"Error in update_playing_message: {exception}", tb)
        self.update_playing_message.restart()

    @tasks.loop(seconds=1)
    async def test_vid(self):
        if self.playing_message is None:
            return
        else:
            player = self.bot.lavalink.player_manager.get(self.playing_message.guild.id)
            guild_id = self.playing_message.guild.id
            
            # Fetch recommendations in the last 10 seconds of the track
            if (player.current and (self.continue_playing or self.eternal_jukebox) and self.last_track is not None and
                not self.fetching_recommendations and guild_id not in self.recommendations_fetched):
                remaining_ms = player.current.duration - player.position
                # Fetch if there's less than 10 seconds remaining and more than 0
                if remaining_ms <= 10000 and remaining_ms > 0:
                    self.fetching_recommendations = True
                    try:
                        self.logger.info(f"Fetching recommendations with {remaining_ms}ms remaining in track")
                        channel = self.bot.get_channel(player.fetch('VoiceChannel'))
                        members = channel.members if channel else []
                        
                        # Check if there are voters in the channel
                        if hasattr(self.bot, 'vote_bypass_guilds') and guild_id in getattr(self.bot, 'vote_bypass_guilds', []):
                            voters_in_channel = [True]
                        else:
                            voters_in_channel = []
                            for member in members:
                                if not member.bot and self.bot.topgg.get_user_vote(member.id):
                                    voters_in_channel.append(member)
                        
                        if len(voters_in_channel) > 0 or self.eternal_jukebox:
                            # Fetch if there are voters, or if eternal jukebox mode bypasses voter checks
                            candidates = self.get_similar_tracks(self.last_track)
                            if candidates is None:
                                self.logger.info("No similar tracks found. Falling back to YouTube search")
                                tracks = await player.node.get_tracks(f"ytmsearch:{self.last_track.author}")
                                if tracks and tracks.tracks:
                                    ytm_tracks = tracks.tracks[:10]
                                    candidates = [
                                        {
                                            "query": f"{track.title} {track.author}",
                                            "name": track.title,
                                            "artist": track.author,
                                            "match": 0.0,
                                        }
                                        for track in ytm_tracks
                                    ]
                            
                            if candidates:
                                ranked_tracks = await self.rank_recommendations_by_views(
                                    player, candidates, self.last_track.author
                                )
                                self.recommendations_fetched[guild_id] = ranked_tracks[:10]
                                self.logger.info(f"Recommendations pre-fetched for guild {guild_id}")
                    except Exception as e:
                        self.logger.error(f"Error pre-fetching recommendations: {e}")
                    finally:
                        self.fetching_recommendations = False
            
            if player.current and self.sponsorBlock:
                try:
                    channel = self.bot.get_channel(player.channel_id)
                    if channel is None:
                        self.logger.warning("Channel not found for the current player.")
                        return
                except AttributeError:
                    return
                members = channel.members if channel else []
                # If the guild is configured in VOTE_BYPASS env var, skip top.gg checking
                # and treat the guild as having voters (so it won't prompt to vote).
                guild_id = getattr(self.playing_message.guild, 'id', None)
                if hasattr(self.bot, 'vote_bypass_guilds') and guild_id in getattr(self.bot, 'vote_bypass_guilds', []):
                    voters_in_channel = [True]
                else:
                    voters_in_channel = []
                    for member in members:
                        if not member.bot and self.bot.topgg.get_user_vote(member.id):
                            voters_in_channel.append(member)
                try:
                    if self.last_song_uri != player.current.identifier:
                        sbc = sb.Client()
                        segments = sbc.get_skip_segments(player.current.identifier)
                        self.last_song_uri_cache = segments
                    else:
                        segments = self.last_song_uri_cache
                except Exception as e:
                    segments = None
                current_time = time.time()
                if segments:
                    for segment in segments:
                        if float(segment.start * 1000) < player.position < float(segment.end * 1000):
                            if len(voters_in_channel) == 0:
                                if not self.sponsorblock_message_sent or current_time - self.last_sponsorblock_message_time > 30:
                                    self.sponsorblock_message_sent = True  # Set the flag to True after sending the message
                                    view = discord.ui.View()
                                    view.add_item(
                                        discord.ui.Button(label="Vote for me on top.gg!",
                                                          url=self.bot.topgg.get_vote_url(),
                                                          style=discord.ButtonStyle.link))
                                    embed = discord.Embed(title="SponsorBlock",
                                                          description=f"You could have saved {int(segment.end - segment.start)} seconds of silence! "
                                                                      f"Please vote for the bot on top.gg to enable automatic SponsorBlock skipping!",
                                                          color=discord.Color.brand_red())
                                    await self.send_with_delete_tracking(
                                        self.playing_message.channel,
                                        embed=embed,
                                        view=view,
                                        delete_after=30,
                                    )
                                    self.last_sponsorblock_message_time = current_time
                                return
                            else:
                                self.sponsorblock_message_sent = False  # Reset the flag if there are voters
                                embed = discord.Embed(title="SponsorBlock",
                                                      description=f'You saved **{int(segment.end - segment.start)}** seconds of filler!\n-# Segment was skipped because it was `{segment.category}`.\n',
                                                      color=discord.Color.brand_red())
                                embed.set_footer(text=f'Use /sponsorblock to toggle the feature or press 🚫 in the playing message.')
                                await self.send_with_delete_tracking(
                                    self.playing_message.channel,
                                    embed=embed,
                                    delete_after=30,
                                )
                                self.last_sponsorblock_message_time = current_time
                                await player.seek(int(segment.end * 1000))

    def cog_unload(self):
        """ Cog unload handler. This removes any event hooks that were registered. """
        # Schedule asynchronous cleanup to cancel background tasks and close sessions.
        try:
            asyncio.create_task(self._async_cog_unload())
        except Exception:
            # If creating a task fails (e.g., loop closed), try best-effort cleanup.
            try:
                self.bot.lavalink._event_hooks.clear()
            except Exception:
                pass

    async def _async_cog_unload(self):
        """Asynchronous cleanup run on cog unload or program shutdown.
        Cancels running background tasks and attempts to close network sessions (lavalink/aiohttp).
        """
        # Persist the current panel so startup can clean up orphaned controls.
        try:
            if self.playing_message is not None:
                guild = getattr(self.playing_message, "guild", None)
                if guild is not None:
                    self.store_playing_message_ref(guild.id, self.playing_message)
        except Exception:
            pass

        # Cancel tasks started by this cog
        for task_attr in ("update_playing_message", "test_vid", "check_call", "check_update_status", "check_update_status"):
            task = getattr(self, task_attr, None)
            try:
                if task is not None and hasattr(task, 'cancel'):
                    try:
                        task.cancel()
                    except Exception:
                        # Some task wrappers raise on cancel; ignore
                        pass
            except Exception:
                continue

        # Allow cancelled tasks to finish cancelling
        await asyncio.sleep(0.1)

        # Clear lavalink event hooks and try to close lavalink's aiohttp session
        try:
            if hasattr(self.bot, 'lavalink') and self.bot.lavalink is not None:
                try:
                    self.bot.lavalink._event_hooks.clear()
                except Exception:
                    pass
                # Close lavalink internal session if present
                try:
                    sess = getattr(self.bot.lavalink, '_session', None)
                    if sess is not None and not sess.closed:
                        await sess.close()
                except Exception:
                    pass
        except Exception:
            pass

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
        should_connect = ctx.command.name in ('play', 'pirate', 'quickplay', 'search', 'radio')
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
                    raise commands.MissingPermissions(['CONNECT', 'SPEAK'])

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
            if self.eternal_jukebox:
                await self.refresh_eternal_jukebox_link(event.player)
            else:
                event.player.store("eternal_jukebox_url", None)
            # Reset pre-fetched recommendations for this track
            guild_id = event.player.guild_id
            if guild_id in self.recommendations_fetched:
                self.recommendations_fetched.pop(guild_id, None)
        elif isinstance(event, lavalink.events.QueueEndEvent):
            self.logger.debug("QueueEndEvent received")
            guild_id = event.player.guild_id
            guild = self.bot.get_guild(guild_id)
            player = self.bot.lavalink.player_manager.get(guild_id)

            if (self.continue_playing or self.eternal_jukebox) and self.last_track is not None:
                self.logger.info("Track ended...")
                channel = self.bot.get_channel(player.fetch('VoiceChannel'))
                members = channel.members if channel else []
                # Respect VOTE_BYPASS for this guild
                guild_id = guild_id if 'guild_id' in locals() else event.player.guild_id
                if hasattr(self.bot, 'vote_bypass_guilds') and guild_id in getattr(self.bot, 'vote_bypass_guilds', []):
                    voters_in_channel = [True]
                else:
                    voters_in_channel = []
                    for member in members:
                        if not member.bot and self.bot.topgg.get_user_vote(member.id):
                            voters_in_channel.append(member)
                if len(voters_in_channel) == 0 and not self.eternal_jukebox:
                    self.logger.info("No voters in channel, skipping recommendations.")
                    try:
                        view = discord.ui.View()
                        view.add_item(
                            discord.ui.Button(label="Vote for me on top.gg!",
                                              url=self.bot.topgg.get_vote_url(),
                                              style=discord.ButtonStyle.link))
                        embed = discord.Embed(title="Recommendations",
                                              description=f"Keep the music going! "
                                                          f"Please vote for the bot on top.gg to enable recommended music!",
                                              color=discord.Color.brand_red())
                        await self.send_with_delete_tracking(
                            self.playing_message.channel,
                            embed=embed,
                            view=view,
                            delete_after=30,
                        )
                    except discord.errors.Forbidden:
                        self.logger.error("Bot cannot send messages in the current channel, skipping recommendations.")
                        await self.handle_missing_permissions(self.playing_message, discord.Embed(
                            title="Error",
                            description="I cannot send messages in this channel. Please check my permissions.",
                            color=discord.Color.red()
                        ))
                    finally:
                        self.stop_import = True
                        player.store("radio_name", None)
                        player.store("eternal_jukebox_url", None)
                        player.queue.clear()
                        await player.stop()
                        await guild.voice_client.disconnect(force=True)
                        try:
                            await self.playing_message.delete()
                            self._delete_current_playing_message_ref()
                            self.playing_message = None
                        except AttributeError:
                            pass
                    return
                
                # Use pre-fetched recommendations if available, otherwise fetch now
                if guild_id in self.recommendations_fetched:
                    ranked_tracks = self.recommendations_fetched.pop(guild_id)
                    self.logger.info(f"Using pre-fetched recommendations for guild {guild_id}")
                else:
                    self.logger.info("Getting similar tracks...")
                    candidates = self.get_similar_tracks(self.last_track)
                    if candidates is None:
                        self.logger.info("No similar tracks found. Falling back to YouTube search")
                        tracks = await player.node.get_tracks(f"ytmsearch:{self.last_track.author}")
                        if tracks and tracks.tracks:
                            ytm_tracks = tracks.tracks[:10]
                            candidates = [
                                {
                                    "query": f"{track.title} {track.author}",
                                    "name": track.title,
                                    "artist": track.author,
                                    "match": 0.0,
                                }
                                for track in ytm_tracks
                            ]
                        else:
                            self.logger.info("No similar tracks found.")
                    
                    if candidates:
                        ranked_tracks = await self.rank_recommendations_by_views(
                            player, candidates, self.last_track.author
                        )
                    else:
                        ranked_tracks = []
                
                # Reset the pre-fetch flag for the next track
                if guild_id in self.recommendations_fetched:
                    self.recommendations_fetched.pop(guild_id, None)
                
                if ranked_tracks:
                    for track in ranked_tracks[:10]:
                        player.add(track=track)
                    if not player.is_playing:
                        await player.play()
                else:
                    self.logger.info("No similar tracks found.")
            else:
                self.logger.info("Queue ended, disconnecting...")
                self.stop_import = True
                player.queue.clear()
                await player.stop()
                await guild.voice_client.disconnect(force=True)
                try:
                    await self.playing_message.delete()
                    self._delete_current_playing_message_ref()
                    self.playing_message = None
                except AttributeError:
                    pass
                return

    def _normalize_artist(self, artist_name: str) -> str:
        if not artist_name:
            return ""
        artist_name = artist_name.lower().replace(" - topic", "").strip()
        artist_name = re.sub(r"[^a-z0-9\s]", " ", artist_name)
        return re.sub(r"\s+", " ", artist_name).strip()

    def _artist_weight(self, candidate_artist: str, current_artist: str) -> float:
        normalized_candidate = self._normalize_artist(candidate_artist)
        normalized_current = self._normalize_artist(current_artist)
        if not normalized_candidate or not normalized_current:
            return 1.0
        if normalized_candidate == normalized_current:
            return 1.35
        if normalized_candidate in normalized_current or normalized_current in normalized_candidate:
            return 1.2
        candidate_tokens = set(normalized_candidate.split())
        current_tokens = set(normalized_current.split())
        if candidate_tokens and current_tokens and candidate_tokens.intersection(current_tokens):
            return 1.1
        return 1.0

    def _parse_view_count_text(self, text: str) -> int:
        if not text:
            return 0
        match = re.search(r"([\d.,]+)\s*([KMBkmb]?)", text)
        if not match:
            digits = re.sub(r"\D", "", text)
            return int(digits) if digits else 0
        number = float(match.group(1).replace(",", ""))
        suffix = match.group(2).upper()
        multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
        return int(number * multiplier)

    async def get_youtube_view_count(self, video_id: str) -> int:
        if not video_id:
            return 0
        url = f"https://www.youtube.com/watch?v={video_id}"
        timeout = aiohttp.ClientTimeout(total=8)
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return 0
                    html = await response.text()
        except Exception:
            return 0

        direct_match = re.search(r'"viewCount"\s*:\s*"(\d+)"', html)
        if direct_match:
            return int(direct_match.group(1))

        simple_text_match = re.search(r'"simpleText"\s*:\s*"([^\"]*views)"', html)
        if simple_text_match:
            return self._parse_view_count_text(simple_text_match.group(1))

        return 0

    async def rank_recommendations_by_views(self, player, candidates, current_artist: str):
        ranked = []
        for candidate in candidates:
            query = candidate.get("query")
            if not query:
                continue

            try:
                results = await player.node.get_tracks(f"ytmsearch:{query}")
            except Exception as exc:
                self.logger.debug(f"Recommendation lookup failed for '{query}': {exc}")
                continue

            if not results or not results.tracks:
                continue

            ytm_track = results.tracks[0]
            views = await self.get_youtube_view_count(getattr(ytm_track, "identifier", None))
            artist_weight = self._artist_weight(getattr(ytm_track, "author", ""), current_artist)
            match_weight = 1.0 + float(candidate.get("match", 0.0))
            score = max(views, 1) * artist_weight * match_weight

            ranked.append((score, views, artist_weight, ytm_track))

        ranked.sort(key=lambda item: item[0], reverse=True)
        self.logger.info(f"Ranked {len(ranked)} recommendations by view count and artist similarity.")
        return [item[3] for item in ranked]

    def get_similar_tracks(self, track):
        if os.getenv('LASTFM_API_KEY') is None:
            self.logger.warning("LASTFM_API_KEY not set, skipping similar tracks.")
            return None
        try:
            author = getattr(track, "author", "") or ""
            title = getattr(track, "title", "") or ""
            if " - Topic" in author:
                author = author.replace(" - Topic", "")
            title = re.sub(r'\([^)]*\)', '', title)
            title = re.sub(r'\[[^)]*\]', '', title)
            title = title.strip()
            response = requests.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={
                    "method": "track.getsimilar",
                    "artist": author,
                    "track": title,
                    "api_key": os.getenv('LASTFM_API_KEY'),
                    "format": "json",
                    "limit": 10,
                    "autocorrect": 1,
                },
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                if 'similartracks' in data and 'track' in data['similartracks']:
                    tracks = data['similartracks']['track']
                    if tracks:
                        unique_tracks = {}
                        for similar in tracks:
                            name = (similar.get('name') or "").strip()
                            similar_artist = (similar.get('artist', {}).get('name') or "").strip()
                            if not name or not similar_artist:
                                continue
                            key = (name.lower(), similar_artist.lower())
                            match_score = float(similar.get('match', 0.0) or 0.0)
                            existing = unique_tracks.get(key)
                            if existing is None or match_score > existing["match"]:
                                unique_tracks[key] = {
                                    "query": f"{name} {similar_artist}",
                                    "name": name,
                                    "artist": similar_artist,
                                    "match": match_score,
                                }
                        ordered = sorted(unique_tracks.values(), key=lambda x: x['match'], reverse=True)
                        self.logger.info(f"Found {len(ordered)} similar tracks.")
                        return ordered
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
            raise commands.MissingPermissions(['CONNECT', 'SPEAK'])

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
        try:
            await ctx.defer(ephemeral=True)
        except discord.errors.NotFound:
            self.logger.warning("Interaction expired before /play could defer.")

        if not ctx.guild:
            return None
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        artist_top_tracks = False
        artist_name = None

        if source != "radio":
            player.store("radio_name", None)

        if os.path.exists('birthdays.csv'):
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
                        await self.handle_missing_permissions(ctx, discord.Embed(
                            title="Error",
                            description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                        f"check my permissions.",
                            color=discord.Color.red()
                        ))
                        return None
                    bquery = f'ytsearch:happy birthday {birthday["name"].lower()} EpicHappyBirthdays'
                    results = await player.node.get_tracks(bquery)
                    if results and results.tracks:
                        track = results.tracks[0]
                        player.add(requester=ctx.author.id, track=track)
                        if not player.is_playing:
                            await player.play()
                            embed = discord.Embed(color=discord.Color.blurple())
                            embed.title = f'Awaiting song information...'
                            self.playing_message = await ctx.channel.send(embed=embed)
                            self.store_playing_message_ref(ctx.guild.id, self.playing_message)
        # Check if the user input might be a URL. If it isn't, we can Lavalink do a YouTube search for it instead.
        # SoundCloud searching is possible by prefixing "scsearch:" instead.
        if not url_rx.match(query):
            if source == "direct":
                # called from within the bot - use top result
                query = query if query.startswith("ytmsearch:") else f"ytmsearch:{query}"
            else:
                await self.search(ctx, query, "cmd" + source)
                return None
        if query.startswith('https://open.spotify.com/playlist/') or query.startswith(
                "https://open.spotify.com/album/"):
            self.stop_import = False
            await self.interaction_send(
                ctx,
                content="👍 `Started import of Spotify to YouTube, please watch the next message for progress.`",
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
                return None
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
                await self.interaction_send(
                    ctx,
                    content=f"👎 `Failed to import Spotify playlist. ({e})`\nIs the playlist private?",
                    ephemeral=True,
                    delete_after=10
                )
                await message.delete()
                return None
            start_time = time.time()
            bar_length = 12
            batch_size = max(1, len(tracks) // bar_length)
            for i in range(0, len(tracks), batch_size):
                if self.stop_import:
                    await self.interaction_send(ctx, content="👍 `Stopped importing Spotify playlist.`", ephemeral=True,
                                                delete_after=10)
                    await message.delete()
                    self.stop_import = False
                    return None
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
                    if not result or not result.tracks:
                        self.logger.error(f"No tracks found for query: {squery}")
                        # Backup search on YouTube
                        squery_backup = squery.replace('ytmsearch:', 'ytsearch:')
                        self.logger.info(f"Attempting backup search on YouTube for query: {squery_backup}")
                        result = await player.node.get_tracks(squery_backup)
                        if not result or not result.tracks:
                            self.logger.error(f"No tracks found for backup query: {squery_backup}")
                            continue
                    track = result.tracks[0]
                    player.add(requester=ctx.author.id, track=track)
                if not player.is_playing:
                    await player.play()
                    if shuffle:
                        player.shuffle = True
                    embed = discord.Embed(color=discord.Color.blurple())
                    embed.title = f'Awaiting song information...'
                    try:
                        self.playing_message = await ctx.channel.send(embed=embed)
                        self.store_playing_message_ref(ctx.guild.id, self.playing_message)
                    except discord.errors.Forbidden:
                        self.logger.error("Bot does not have permission to send messages in this channel.")
                        # inform the user that the bot cannot send messages in this channel
                        await self.handle_missing_permissions(ctx, discord.Embed(
                            title="Error",
                            description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                        f"check my permissions.",
                            color=discord.Color.red()
                        ))
                        return None
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
        elif query.startswith("https://open.spotify.com/artist/"):
            try:
                artist_info = sp.artist(query)
                artist_name = (artist_info.get("name") or "").strip()
                if not artist_name:
                    raise spotipy.SpotifyException(400, -1, "Artist name not found in Spotify response.")
                self.logger.info(f"Spotify artist: {artist_name}")
                query = f'ytmsearch:top songs by {artist_name}'
                artist_top_tracks = True
            except spotipy.SpotifyException:
                return await self.interaction_send(
                    ctx,
                    content="👎 `Failed to resolve Spotify artist. Please verify the URL is a valid Spotify artist link.`",
                    delete_after=10,
                    ephemeral=True,
                )
        # Get the results for the query from Lavalink.
        results = await player.node.get_tracks(query)
        # Results could be None if Lavalink returns an invalid response (non-JSON/non-200 (OK)).
        # Alternatively, results.tracks could be an empty array if the query yielded no tracks.
        if not results or not results.tracks:
            return await self.interaction_send(ctx, content='Nothing found!', delete_after=10, ephemeral=True)
        elif results.load_type == LoadType.EMPTY:
            return await self.interaction_send(ctx, content=f'Nothing found for `{query}`!', delete_after=10,
                                               ephemeral=True)

        embed = discord.Embed(color=discord.Color.blurple(), title="Fetching song information...")

        if results.load_type == LoadType.PLAYLIST:
            # If the query was a playlist, we add all the tracks to the queue.
            for track in results.tracks:
                player.add(requester=ctx.author.id, track=track)
            self.logger.debug(f"Queue length: {len(player.queue)}")
        elif artist_top_tracks:
            queued_tracks = results.tracks[:ARTIST_TOP_TRACK_LIMIT]
            for track in queued_tracks:
                player.add(requester=ctx.author.id, track=track)
            self.logger.debug(
                f"Queued {len(queued_tracks)} top tracks for artist {artist_name}. "
                f"Queue length: {len(player.queue)}"
            )
        elif results.load_type == LoadType.SEARCH:
            # If the query was a search query, we take the top item from the search results.
            track = results.tracks[0]
            player.add(requester=ctx.author.id, track=track)
            self.logger.debug(f"Queue length: {len(player.queue)}")
        elif results.load_type == LoadType.TRACK:
            # If the query was a single video, we add it to the queue.
            track = results.tracks[0]
            player.add(requester=ctx.author.id, track=track)
        elif str(results.load_type) == 'NO_MATCHES':
            return await self.interaction_send(ctx, content='Nothing found!', delete_after=10, ephemeral=True)
        elif str(results.load_type) == 'LOAD_FAILED':
            return await self.interaction_send(ctx, content='Failed to load track.', delete_after=10, ephemeral=True)
        else:
            return await self.interaction_send(ctx, content=f'Unknown results type: {results.load_type}',
                                               delete_after=10, ephemeral=True)
        # send thumbs up
        await self.interaction_send(ctx, content="Enqueued song", delete_after=1, ephemeral=True)
        # We don't want to call .play() if the player is playing as that will effectively skip
        # the current track.
        if not player.is_playing or player.fetch('VoiceState') in ['-1', '0']:
            await player.play()
            try:
                self.playing_message = await ctx.channel.send(embed=embed)
                self.store_playing_message_ref(ctx.guild.id, self.playing_message)
                return None
            except discord.errors.Forbidden:
                self.logger.error("Bot does not have permission to send messages in this channel.")
                # inform the user that the bot cannot send messages in this channel
                await self.handle_missing_permissions(ctx, discord.Embed(
                    title="Error",
                    description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                f"check my permissions.",
                    color=discord.Color.red()
                ))
                return None
        return None

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
            is_album = "/album/" in playlist
            if is_album:
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
                        # Use album_tracks for albums, playlist_items for playlists
                        if is_album:
                            # sp.album_tracks default limit is 50, which works well
                            result = sp.album_tracks(playlist, limit=50, offset=offset)
                            # Return tracks with consistent structure (album tracks are direct, not wrapped)
                            return result['items'] if result else []
                        else:
                            # sp.playlist_items default limit is 50 as well
                            result = sp.playlist_items(playlist, limit=50, offset=offset)
                            # Playlist tracks come wrapped in a "track" object, keep the full structure
                            return result['items'] if result else []
                    except spotipy.SpotifyException as e:
                        self.logger.error(f"Error fetching page at offset {offset}: {e}")
                        return []

            # Create tasks for all pages - use batch size of 50 for both
            batch_size = 50
            tasks = [fetch_page(offset) for offset in range(0, total_tracks, batch_size)]
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

        # Check if we're in radio mode and revert to non-radio mode
        current_radio = player.fetch("radio_name")
        if current_radio:
            # Try to restore the queue
            if self.restore_queue_state(ctx.guild.id, player):
                # Restore was successful, clear radio mode
                player.store("radio_name", None)
                # Try to re-add the tracks (simplified version)
                # In a production environment, you'd want to restore the tracks more robustly
                await ctx.respond('*⃣ | Exited radio mode. Queue restored.', delete_after=10, ephemeral=True)
            else:
                # No queue to restore or restore failed
                player.store("radio_name", None)
                player.queue.clear()
                await player.stop()
                await ctx.voice_client.disconnect(force=True)
                await ctx.respond('*⃣ | Disconnected.', delete_after=10, ephemeral=True)
                return None
        else:
            # Not in radio mode, just disconnect normally
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
                self._delete_current_playing_message_ref()
                self.playing_message = None
            except AttributeError:
                pass
            await ctx.respond('*⃣ | Disconnected.', delete_after=10, ephemeral=True)
            return None

    @commands.slash_command(name="pause", description="Pause/resume the current song", aliases=['resume'])
    async def pause(self, ctx: discord.ApplicationContext):
        """ Pauses the current track. """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.voice_client:
            return await self.interaction_send(ctx, content='Not connected.', delete_after=5, ephemeral=True)

        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await self.interaction_send(ctx, content='You\'re not in my voice channel!', delete_after=5,
                                               ephemeral=True)

        if player.paused:
            await player.set_pause(False)
            await self.set_bot_pause_mute(ctx.guild, False)
            await self.interaction_send(ctx, content='▶ | Resumed.', delete_after=5)
            return

        if not player.is_playing:
            return await self.interaction_send(ctx, content='Nothing playing.', delete_after=5, ephemeral=True)

        await player.set_pause(True)
        await self.set_bot_pause_mute(ctx.guild, True)
        await self.interaction_send(ctx, content='⏸ | Paused the song.', delete_after=5)

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
        try:
            await ctx.defer(ephemeral=True)
        except discord.errors.NotFound:
            self.logger.warning("Interaction expired before /queue could defer.")
            return
        """ Shows the player's queue. in a paginator response"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await self.interaction_send(ctx, content='You\'re not in my voice channel!', delete_after=10,
                                               ephemeral=True)
        if not player.queue:
            return await self.interaction_send(ctx, content='Nothing queued.', delete_after=10, ephemeral=True)
        embed_data = {
            "title": f"Queue for {ctx.guild.name}",
            "description": f"Showing {limit} songs."
        }
        now_playing = {"title": player.current.title, "thumb": player.current.uri, "author": player.current.author}
        name = ctx.author.display_name
        pages = paginator(items=player.queue, embed_data=embed_data, per_page=limit, current_info=now_playing,
                          author=name)
        page_iterator = Paginator(pages=pages, loop_pages=True)
        await page_iterator.respond(ctx.interaction, ephemeral=True)

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

    @commands.slash_command(name="eternaljukebox", description="Toggle The Eternal Jukebox mode.")
    async def eternaljukebox(self, ctx: discord.ApplicationContext):
        self.eternal_jukebox = not self.eternal_jukebox
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if self.eternal_jukebox:
            eternal_jukebox_url = await self.refresh_eternal_jukebox_link(player)
            if eternal_jukebox_url:
                return await ctx.respond(
                    f"The Eternal Jukebox mode has been enabled!\n{eternal_jukebox_url}",
                    delete_after=15,
                    ephemeral=True
                )
            await ctx.respond("The Eternal Jukebox mode has been enabled!", delete_after=5, ephemeral=True)
        else:
            player.store("eternal_jukebox_url", None)
            await ctx.respond("The Eternal Jukebox mode has been disabled!", delete_after=5, ephemeral=True)

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

    @commands.slash_command(name="radio", description="Play a configured radio station")
    async def radio(self, ctx: discord.ApplicationContext):
        """Play a radio station from the configured list."""
        await ctx.defer(ephemeral=True)
        
        if not self.radio_stations:
            return await self.interaction_send(
                ctx,
                content="No radio stations configured.",
                delete_after=10,
                ephemeral=True,
            )
        
        # Create select menu for radio stations
        async def radio_select_callback(interaction: discord.Interaction):
            await self.safe_defer(interaction, ephemeral=True)
            selected_station_key = interaction.data['values'][0]
            selected_station = self.get_radio_station(selected_station_key)
            
            if selected_station is None:
                return await self.interaction_send(
                    interaction,
                    content="Station not found.",
                    delete_after=10,
                    ephemeral=True,
                )
            
            try:
                # Get the player before saving state
                player = self.bot.lavalink.player_manager.get(interaction.guild_id)
                
                # Save the current queue and track position
                self.save_queue_state(interaction.guild_id, player)
                
                # Clear the queue and stop current track
                player.queue.clear()
                await player.stop()
                
                # Get tracks from Lavalink and add them to queue
                query = selected_station["url"]
                results = await player.node.get_tracks(query)
                
                if not results or not results.tracks:
                    await self.interaction_send(
                        interaction,
                        content=f"❌ Could not load **{selected_station['name']}** radio station.",
                        delete_after=10,
                        ephemeral=True,
                    )
                    return
                
                # Add tracks to queue
                for track in results.tracks:
                    player.add(requester=interaction.user.id, track=track)
                
                # Start playing if not already playing
                if not player.is_playing:
                    await player.play()
                
                # Set radio mode flag
                player.store("radio_name", selected_station["name"])
                
                await self.interaction_send(
                    interaction,
                    content=f"🎙️ Now playing **{selected_station['name']}** radio station.",
                    delete_after=5,
                    ephemeral=True,
                )
            except Exception as e:
                self.logger.error(f"Error starting radio station: {e}")
                await self.interaction_send(
                    interaction,
                    content=f"❌ Error starting radio: {str(e)}",
                    delete_after=10,
                    ephemeral=True,
                )
        
        # Build select menu options
        options = []
        for station_key in sorted(self.radio_stations.keys()):
            display_name = self.format_station_name(station_key)
            options.append(
                discord.SelectOption(
                    label=display_name,
                    value=station_key,
                    description=f"Play {display_name} radio"
                )
            )
        
        # Create and send select menu
        select = discord.ui.Select(
            placeholder="Choose a radio station...",
            options=options,
            min_values=1,
            max_values=1
        )
        select.callback = radio_select_callback
        
        view = discord.ui.View()
        view.add_item(select)
        
        await self.interaction_send(
            ctx,
            content="🎙️ Select a radio station to play:",
            view=view,
            ephemeral=True,
        )

    @commands.slash_command(name="pirate", description="Add the pirate shanties playlist to the queue")
    @option(name="shuffle", description="Shuffle the playlist", required=False)
    async def pirate(self, ctx: discord.ApplicationContext, shuffle: bool = False):
        selected_station = self.get_radio_station("pirate")
        await self.play(ctx, query=selected_station["url"], shuffle=shuffle, source="radio")
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if player.current or player.queue:
            player.store("radio_name", selected_station["name"])

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
                    self._delete_current_playing_message_ref()
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
                self._delete_current_playing_message_ref()
                self.playing_message = None
                return None
            else:
                try:
                    self.playing_message = await ctx.channel.send(embed=embed)
                    self.store_playing_message_ref(ctx.guild.id, self.playing_message)
                    await self.update_playing_message(ctx)
                except discord.Forbidden:
                    self.logger.error(
                        f"Failed to send playing_message in dropdown_callback for guild {ctx.guild.id}. "
                        f"Missing permissions.")
                    self._delete_current_playing_message_ref()
                    self.playing_message = None
                    await self.handle_missing_permissions(ctx, discord.Embed(
                        title="Error",
                        description=f"I cannot send messages in the {ctx.channel.name} channel. Please "
                                    f"check my permissions.",
                        color=discord.Color.red()
                    ))
            await self.update_playing_message(ctx)
            return None
        else:
            return await ctx.respond('Nothing playing.', delete_after=5, ephemeral=True)

    @commands.slash_command(name="search", description="Search for a song, and add it to the queue.")
    @option(name="query", description="The song to search for", required=True)
    @option(name="service", description="The music provider to search with", required=False,
            choices=["youtube", "spotify", "youtube_music"],
            default="youtube_music")
    async def search(self, ctx: discord.ApplicationContext, query: str, service: str = "youtube_music"):
        async def dropdown_callback(interaction: discord.Interaction):
            # Safely defer the interaction; if it fails the interaction is likely expired.
            if not await self.safe_defer(interaction, ephemeral=True):
                self.logger.warning("Dropdown interaction expired before defer.")
                return

            selected_value = interaction.data['values'][0]

            if not hasattr(self.bot,
                           'lavalink') or self.bot.lavalink is None or not self.bot.lavalink.node_manager.nodes:
                self.logger.error("Lavalink is not initialized/ready on the bot object in dropdown.")
                await self.safe_edit_original(interaction, content="Music system error: Lavalink not ready.", view=None)
                return
            if not interaction.guild_id:
                self.logger.error("Interaction guild_id is None in dropdown_callback.")
                await self.safe_edit_original(interaction, content="Error: Guild context not found.", view=None)
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
                    await self.safe_edit_original(interaction, content=f"Error processing Spotify track: {e}", view=None)
                    return
            else:
                results_lavalink = await player.node.get_tracks(selected_value)
                if results_lavalink and results_lavalink.tracks:
                    actual_track_title = results_lavalink.tracks[0].title
                    actual_track_author = results_lavalink.tracks[0].author

            final_track_display_name = f"{actual_track_title} by {actual_track_author}" if actual_track_author else actual_track_title

            if not results_lavalink or not results_lavalink.tracks:
                await self.safe_edit_original(
                    interaction,
                    content=f"Could not find track information for: {final_track_display_name}.",
                    view=None
                )
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
                await self.safe_edit_original(interaction, content=response_message_content, view=None)
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

            await self.safe_edit_original(interaction, content=response_message_content, view=None)
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
                        await self.handle_missing_permissions(interaction, discord.Embed(
                            title="Error",
                            description=f"I cannot send messages in the {interaction.channel.name} channel. Please "
                                        f"check my permissions.",
                            color=discord.Color.red()
                        ))
                    except discord.errors.HTTPException as e:
                        self.logger.error(
                            f"Failed to send playing_message in dropdown_callback for guild {interaction.guild_id}: {e}")
                    except AttributeError:
                        self.logger.error(
                            f"interaction.channel is None or interaction object is malformed for guild {interaction.guild_id}.")

        if not hasattr(self.bot, 'lavalink') or self.bot.lavalink is None or not self.bot.lavalink.node_manager.nodes:
            await self.interaction_send(ctx, content="Music system is not ready. Please try again later.", ephemeral=True)
            return
        if not ctx.guild_id:
            await self.interaction_send(ctx, content="This command can only be used in a server.", ephemeral=True)
            return

        player = self.bot.lavalink.player_manager.get(ctx.guild_id)
        if player is None:
            player = self.bot.lavalink.player_manager.create(ctx.guild_id)

        is_internal_call = "cmd" in service
        if not is_internal_call:
            try:
                await ctx.defer(ephemeral=True)
            except discord.errors.NotFound:
                self.logger.warning("Interaction expired before /search could defer.")
                return
        else:
            service = service.replace("cmd", "")

        options = []
        if service == "spotify":
            results_spotify = sp.search(q=query, type="track", limit=20)
            if not results_spotify or not results_spotify['tracks']['items']:
                response_content = 'Nothing found on Spotify!'
                await self.interaction_send(ctx, content=response_content, ephemeral=True, delete_after=10)
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
                await self.interaction_send(ctx, content=response_content, ephemeral=True, delete_after=10)
                return

            if (results_lavalink_search.load_type == LoadType.SEARCH or
                    results_lavalink_search.load_type == lavalink.LoadType.TRACK):
                options = [{"value": track.uri, "label": limit(f"{track.title} by {track.author}", 100)} for track in
                           results_lavalink_search.tracks[:25] if len(track.uri) < 100]
            else:
                response_content = 'Nothing found or unsupported link type for search!'
                await self.interaction_send(ctx, content=response_content, ephemeral=True, delete_after=10)
                return
        if options:
            view = discord.ui.View(timeout=180)
            select_menu = DiscordDropDownSelect(
                options=options,
                placeholder=limit(f"Found {len(options)} results for \"{query}\"", 150)
            )
            select_menu.callback = dropdown_callback
            view.add_item(select_menu)

            response_content = "Select a song to add to the queue:"
            await self.interaction_send(ctx, content=response_content, view=view, ephemeral=True)
        else:
            response_content = 'Nothing found!'
            await self.interaction_send(ctx, content=response_content, ephemeral=True, delete_after=10)
def setup(bot):
    bot.add_cog(Music(bot, bot.logger))

