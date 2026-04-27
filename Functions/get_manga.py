import re
import discord
from discord.ext import commands
import aiohttp

from Functions.media_pipeline import (
    fetch_from_mangadex,
    fetch_from_anilist,
    find_existing,
    insert_media,
)

WATCHED_CHANNEL_ID = 1298440749305561188

MANGADEX_RE = re.compile(r"https://mangadex\.org/title/([a-f0-9\-]{36})")
ANILIST_RE  = re.compile(r"https://anilist\.co/(manga|anime)/(\d+)")


class get_manga(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id != WATCHED_CHANNEL_ID:
            return
        try:
            await self._handle(message)
        except Exception:
            import traceback
            traceback.print_exc()

    async def _handle(self, message: discord.Message):
        md_match = MANGADEX_RE.search(message.content)
        al_match = ANILIST_RE.search(message.content)

        if not md_match and not al_match:
            return

        await message.delete()

        # ── 1. Fetch metadata ────────────────────────────────────────────────
        async with aiohttp.ClientSession() as session:
            if md_match:
                source = 'MangaDex'
                row = await fetch_from_mangadex(md_match.group(1), session)
            else:
                source = 'AniList'
                row = await fetch_from_anilist(int(al_match.group(2)), session)

        if not row:
            await message.channel.send(
                f"Couldn't fetch data from {source}.", delete_after=12
            )
            return

        # ── 2. DB: check then insert ─────────────────────────────────────────
        db_status = 'error'
        existing_id = None
        new_id = None

        try:
            async with aiohttp.ClientSession() as session:
                existing_id = await find_existing(
                    row['title'], row.get('alt_titles') or [], session
                )
                print(f"[bot] '{row['title']}' existing={existing_id}")

                if existing_id:
                    db_status = 'exists'
                else:
                    new_id, err = await insert_media(row, session)
                    if new_id:
                        db_status = 'added'
                        print(f"[bot] inserted id={new_id}")
                    else:
                        db_status = 'error'
                        print(f"[bot] insert failed: {err}")
                        await message.channel.send(
                            f"⚠ Couldn't save to database: `{err}`", delete_after=30
                        )
        except Exception as e:
            print(f"[bot] DB error: {e}")
            await message.channel.send(
                f"⚠ Database error: `{e}`", delete_after=30
            )

        # ── 3. Always post the embed ─────────────────────────────────────────
        embed = _build_embed(
            row, message.author, source,
            existing_id=existing_id, new_id=new_id,
        )
        await message.channel.send(embed=embed)


def _build_embed(
    row: dict,
    author: discord.Member,
    source: str,
    *,
    existing_id: str | None = None,
    new_id: str | None = None,
) -> discord.Embed:
    title    = row['title']
    synopsis = (row.get('synopsis') or '').strip()
    media_type = row['type'].capitalize()
    status   = row['status'].capitalize()

    # Colour: use genre colour if we have one, else discord orange
    try:
        hex_color = int(row['color'].lstrip('#'), 16)
    except Exception:
        hex_color = 0xFF6740

    embed = discord.Embed(
        title=title,
        description=synopsis[:300] or "No description.",
        color=hex_color,
    )

    embed.add_field(name="Type",   value=media_type, inline=True)
    embed.add_field(name="Status", value=status,     inline=True)
    if row.get('year'):
        embed.add_field(name="Year", value=str(row['year']), inline=True)

    if row.get('author'):
        credit = row['author']
        if row.get('artist') and row['artist'] != row['author']:
            credit += f" (art: {row['artist']})"
        embed.add_field(name="Author", value=credit, inline=True)

    if row.get('studio'):
        embed.add_field(name="Studio", value=row['studio'], inline=True)

    genres = row.get('genres') or []
    if genres:
        embed.add_field(name="Tags", value=", ".join(genres[:8]), inline=False)

    if row.get('chapters'):
        embed.add_field(name="Chapters", value=str(row['chapters']), inline=True)
    if row.get('volumes'):
        embed.add_field(name="Volumes",  value=str(row['volumes']),  inline=True)
    if row.get('episodes'):
        embed.add_field(name="Episodes", value=str(row['episodes']), inline=True)

    # Reading links
    links = row.get('translation_links') or []
    if links:
        link_str = '  ·  '.join(f"[{l['name']}]({l['url']})" for l in links)
        embed.add_field(name="Read / Watch", value=link_str, inline=False)

    # Alt titles
    alts = row.get('alt_titles') or []
    if alts:
        embed.add_field(name="Also known as", value="  ·  ".join(alts[:4]), inline=False)

    if existing_id:
        embed.set_footer(text=f"Already in MangaZ library · from {source}")
    elif new_id:
        embed.set_footer(text=f"Added to MangaZ · from {source}")
    else:
        embed.set_footer(text=f"⚠ Fetched but not saved · from {source}")

    embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)

    if row.get('cover_url'):
        embed.set_thumbnail(url=row['cover_url'])

    return embed


async def setup(bot):
    await bot.add_cog(get_manga(bot))
