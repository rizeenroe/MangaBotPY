# cogs/manga.py
import discord
from discord.ext import commands
import aiohttp
import re
import asyncpg
import os

MANGADEX_REGEX = re.compile(r"https://mangadex\.org/title/([a-f0-9-]+)")
WATCHED_CHANNEL_ID = 1298441444951982080

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
        author = message.author

        await message.delete()

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.mangadex.org/manga/{manga_id}",
                params={"includes[]": "cover_art"}
            ) as response:
                data = await response.json()

        manga = data["data"]["attributes"]
        title = manga["title"].get("en") or next(iter(manga["title"].values()))
        description = manga["description"].get("en", "No description available.")
        status = manga["status"]
        tags = [tag["attributes"]["name"]["en"] for tag in manga["tags"]]

        cover_url = None
        relationships = data["data"].get("relationships", [])
        cover = next((r for r in relationships if r["type"] == "cover_art"), None)
        if cover:
            filename = cover["attributes"]["fileName"]
            cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{filename}"

        # check duplicate and save
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute("""
                INSERT INTO manga (manga_id, title)
                VALUES ($1, $2)
                ON CONFLICT (manga_id) DO NOTHING
            """, manga_id, title)
        finally:
            await conn.close()

        embed = discord.Embed(title=title, description=description, color=0xFF6740)
        embed.add_field(name="Status", value=status.capitalize())
        embed.add_field(name="Tags", value=", ".join(tags))
        embed.set_footer(text="MangaDex")
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
        if cover_url:
            embed.set_thumbnail(url=cover_url)

        await message.channel.send(f"{author.mention}", embed=embed)

async def setup(bot):
    await bot.add_cog(Manga(bot))