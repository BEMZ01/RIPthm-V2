import logging
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Iterable, Optional, cast

import aiohttp
from discord.ext import commands, tasks

"""
Example code:
import urllib.request
import time

push_url = "https://status.bemz.info/api/push/XXXXXXXXXXXXXXXXXXXX?status=up&msg=OK&ping="
interval = 60

while True:
    urllib.request.urlopen(push_url)
    print("Pushed!\n")
    time.sleep(interval)
"""

class Uptime(commands.Cog):
    DEFAULT_INTERVAL = 60

    def __init__(self, bot, logger):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.handlers = logger.handlers
        self.logger.setLevel(logger.level)
        self.logger.propagate = False
        self.bot = bot
        #-------------------------------
        self.uptime_url: Optional[str] = os.getenv("UPTIME_URL")
        self.every: int = self._parse_interval(os.getenv("UPTIME_INTERVAL", self.DEFAULT_INTERVAL))
        self.push_status.change_interval(seconds=float(self.every))

    @staticmethod
    def _parse_interval(raw_interval) -> int:
        try:
            interval = int(raw_interval)
        except (TypeError, ValueError):
            return Uptime.DEFAULT_INTERVAL
        return max(1, interval)

    def _get_server_stats(self):
        installed_servers = len(getattr(self.bot, "guilds", []))
        playing_servers = 0

        lavalink_client = getattr(self.bot, "lavalink", None)
        player_manager = getattr(lavalink_client, "player_manager", None) if lavalink_client is not None else None
        players = getattr(player_manager, "players", None) if player_manager is not None else None

        if players is not None:
            iterable = cast(Iterable, players.values() if hasattr(players, "values") else players)
            for player in iterable:
                if getattr(player, "is_playing", False):
                    playing_servers += 1

        return installed_servers, playing_servers

    @staticmethod
    def _build_request_url(base_url: str, params: dict) -> str:
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update(params)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    async def _push_uptime(self):
        installed_servers, playing_servers = self._get_server_stats()
        url = self.uptime_url
        if url is None:
            return

        params = {
            "status": "up",
            "msg": f"OK | installed={installed_servers} | playing={playing_servers}",
            "servers": str(installed_servers),
            "playing": str(playing_servers),
        }
        request_url = self._build_request_url(url, params)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(request_url) as response:
                    if response.status == 200:
                        self.logger.info(
                            "Pushed uptime status successfully (installed=%s, playing=%s)",
                            installed_servers,
                            playing_servers,
                        )
                    else:
                        self.logger.warning(
                            "Failed to push uptime status, HTTP %s (installed=%s, playing=%s)",
                            response.status,
                            installed_servers,
                            playing_servers,
                        )
            except Exception as e:
                self.logger.error(f"Error pushing uptime status: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info('Uptime cog is ready')
        if self.uptime_url:
            if not self.push_status.is_running():
                self.push_status.start()
        else:
            self.logger.warning("UPTIME_URL is not set, uptime pushing is disabled")

    @tasks.loop(seconds=60)
    async def push_status(self):
        await self._push_uptime()

def setup(bot):
    bot.add_cog(Uptime(bot, bot.logger))
