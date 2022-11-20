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
import lavalink
import sponsorblock as sb

# Discord bot to play music in voice channels
logging.basicConfig(level=logging.INFO)
dotenv.load_dotenv()
# Load the token from the .env file
TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_SECRET')

guild_ids = [730859265249509386, ]
bot = commands.Bot(debug_guilds=[730859265249509386])
bot.load_extension("cogs.music")
sbClient = sb.Client()


@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="music"))


# @bot.event
# async def on_connect():
#    for cog in os.listdir("./cogs"):
#        if cog.endswith(".py"):
#            bot.load_extension(f"cogs.{cog[:-3]}")
#    print("Bot connected")


if __name__ == "__main__":
    # bot.load_extension("cogs.music")
    # this errors complaining about not being able to find bot.user.id
    bot.run(TOKEN)
