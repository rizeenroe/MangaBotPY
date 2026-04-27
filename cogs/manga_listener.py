# cogs/manga_listener.py
import discord
from discord.ext import commands
import aiohttp
import asyncpg
import os
from utils.manga import SEARCHERS, search_by_url, build_embeds

WATCHED_CHANNEL_IDS: set[int] = {
    1298440749305561188,
    1298560730240385046,
    1306714353701224528,
    1486811154079547433,
    1298441444951982080
}


class MangaListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool | None = None

    async def cog_load(self):
        self.pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])

    async def cog_unload(self):
        if self.pool:
            await self.pool.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id not in WATCHED_CHANNEL_IDS:
            return

        has_known_url = any(
            searcher.extract_id(message.content) is not None
            for searcher in SEARCHERS
        )
        if not has_known_url:
            return

        await message.delete()

        async with aiohttp.ClientSession() as session:
            results, _ = await search_by_url(session, message.content)

        if not results:
            await message.channel.send(
                "Could not find manga info from any source.", delete_after=10
            )
            return

        primary = results[0]
        await self.pool.execute(
            """
            INSERT INTO manga (manga_id, title)
            VALUES ($1, $2)
            ON CONFLICT (manga_id) DO NOTHING
            """,
            primary.manga_id,
            primary.title,
        )

        embeds = build_embeds(results, message.author)
        await message.channel.send(
            content=f"## {primary.title}",
            embeds=embeds[:10]
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MangaListener(bot))