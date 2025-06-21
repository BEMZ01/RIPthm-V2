import aiohttp
import discord
import io
from PIL import Image


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


def progress_bar(player):
    """Generate a progress bar for the current song.
    :param player: The player object containing the current song's position and duration.
    :return: A string representing the progress bar."""
    bar_length = 12
    progress = (player.position / player.current.duration) * bar_length
    return f"[{'🟩' * int(progress)}{'⬜' * (bar_length - int(progress))}]"
