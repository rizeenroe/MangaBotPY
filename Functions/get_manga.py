# cogs/manga.py
import discord
from discord.ext import commands
import aiohttp
import re

MANGADEX_REGEX = re.compile(r"https://mangadex\.org/title/([a-f0-9-]+)")
WATCHED_CHANNEL_ID = 1298441444951982080  # replace with your channel ID

class Manga(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.channel.id != WATCHED_CHANNEL_ID:
            return

        match = MANGADEX_REGEX.search(message.content)
        if not match:
            return

        manga_id = match.group(1)

        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.mangadex.org/manga/{manga_id}") as response:
                data = await response.json()

        manga = data["data"]["attributes"]
        title = manga["title"].get("en") or next(iter(manga["title"].values()))
        description = manga["description"].get("en", "No description available.")
        status = manga["status"]
        tags = [tag["attributes"]["name"]["en"] for tag in manga["tags"]]

        embed = discord.Embed(title=title, description=description, color=0xFF6740)
        embed.add_field(name="Status", value=status.capitalize())
        embed.add_field(name="Tags", value=", ".join(tags))
        embed.set_footer(text="MangaDex")

        await message.reply(embed=embed)

async def setup(bot):
    await bot.add_cog(Manga(bot))