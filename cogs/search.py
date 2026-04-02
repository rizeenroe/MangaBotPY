# cogs/search.py
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from utils.manga import search_by_title, build_embeds


class Search(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="search", description="Search for a manga or manhwa by name")
    @app_commands.describe(title="The manga or manhwa title to search for")
    async def search(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        async with aiohttp.ClientSession() as session:
            results = await search_by_title(session, title)

        if not results:
            await interaction.followup.send(
                f"No results found for **{title}**.", ephemeral=True
            )
            return

        embeds = build_embeds(results, interaction.user)
        await interaction.followup.send(embeds=embeds[:10])


async def setup(bot: commands.Bot):
    await bot.add_cog(Search(bot))