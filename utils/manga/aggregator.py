# utils/manga/aggregator.py
import aiohttp
import discord
from .models import MangaResult
from .searchers import MangaSearcher, AnilistSearcher, MangaDexSearcher

SEARCHERS: list[MangaSearcher] = [
    MangaDexSearcher(),
    AnilistSearcher(),
]

SOURCE_COLORS = {
    "MangaDex": 0xFF6740,
    "Anilist":  0x02A9FF,
}
SOURCE_EMOJIS = {
    "MangaDex": "🔶",
    "Anilist":  "🔷",
}


async def search_by_url(
    session: aiohttp.ClientSession,
    url: str,
) -> tuple[list[MangaResult], str | None]:
    primary_id: str | None = None

    for searcher in SEARCHERS:
        manga_id = searcher.extract_id(url)
        if manga_id:
            primary_id = manga_id
            result = await searcher.fetch(session, manga_id)
            return ([result] if result else []), primary_id

    return [], primary_id


async def search_by_title(
    session: aiohttp.ClientSession,
    title: str,
) -> list[MangaResult]:
    result = await _mangadex_search_by_title(session, title)
    return [result] if result else []


async def _mangadex_search_by_title(
    session: aiohttp.ClientSession,
    title: str,
) -> MangaResult | None:
    """Search MangaDex by title string."""
    try:
        async with session.get(
            "https://api.mangadex.org/manga",
            params={"title": title, "includes[]": "cover_art", "limit": 1},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        items = data.get("data", [])
        if not items:
            return None

        item = items[0]
        manga_id = item["id"]
        searcher = MangaDexSearcher()
        return await searcher.fetch(session, manga_id)
    except Exception:
        return None


def build_embeds(
    results: list[MangaResult],
    author: discord.Member | discord.User,
) -> list[discord.Embed]:
    embeds: list[discord.Embed] = []

    for i, result in enumerate(results):
        color = SOURCE_COLORS.get(result.source, 0x888888)
        emoji = SOURCE_EMOJIS.get(result.source, "📖")

        desc = result.description[:300].rstrip()
        if len(result.description) > 300:
            desc += "…"

        embed = discord.Embed(
            title=result.title,
            url=result.url,
            description=desc,
            color=color,
        )

        embed.set_author(
            name=f"{author.display_name}  •  via {emoji} {result.source}",
            icon_url=author.display_avatar.url,
        )

        embed.add_field(name="Status", value=result.status.capitalize(), inline=True)

        if result.latest_chapter:
            embed.add_field(name="Latest Chapter", value=f"Ch. {result.latest_chapter}", inline=True)

        if result.tags:
            embed.add_field(name="Tags", value=", ".join(result.tags[:8]), inline=False)

        if result.cover_url:
            if i == 0:
                embed.set_image(url=result.cover_url)
            else:
                embed.set_thumbnail(url=result.cover_url)

        embed.set_footer(text=result.source)
        embeds.append(embed)

    return embeds