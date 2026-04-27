import re
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

from Functions.media_pipeline import (
    fetch_from_mangadex,
    fetch_from_anilist,
    find_existing,
    insert_media,
    search_db,
    get_random_from_db,
    get_all_tags,
)

WATCHED_CHANNEL_ID = 1298440749305561188
MANGADEX_RE = re.compile(r"https://mangadex\.org/title/([a-f0-9\-]{36})")
ANILIST_RE  = re.compile(r"https://anilist\.co/(manga|anime)/(\d+)")
MANGOKU_BASE = "https://mangoku.org/manga"


# ── Buttons ───────────────────────────────────────────────────────────────────

class MangaView(discord.ui.View):
    def __init__(self, title: str, mangoku_id: str | None = None):
        super().__init__(timeout=None)
        self.title = title
        if mangoku_id:
            self.add_item(discord.ui.Button(
                label="View on Mangoku",
                url=f"{MANGOKU_BASE}/{mangoku_id}",
                style=discord.ButtonStyle.link,
                emoji="🌐",
            ))

    @discord.ui.button(label="Copy title", style=discord.ButtonStyle.secondary, emoji="📋")
    async def copy_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"```{self.title}```",
            ephemeral=True,
        )


# ── Embed builder ─────────────────────────────────────────────────────────────

def _build_embed(
    row: dict,
    author: discord.Member | discord.User,
    source: str,
    *,
    existing_id: str | None = None,
    new_id: str | None = None,
) -> discord.Embed:
    title      = row['title']
    synopsis   = (row.get('synopsis') or '').strip()
    media_type = row['type'].capitalize()
    status     = row['status'].capitalize()

    try:
        hex_color = int(row['color'].lstrip('#'), 16)
    except Exception:
        hex_color = 0x9333EA  # purple fallback

    embed = discord.Embed(
        title=title,
        description=synopsis[:350] or "No description.",
        color=hex_color,
    )

    # Core info row
    info_parts = [f"**{media_type}**", f"**{status}**"]
    if row.get('year'):
        info_parts.append(str(row['year']))
    embed.add_field(name="Info", value="  ·  ".join(info_parts), inline=False)

    # Credits
    if row.get('author'):
        credit = row['author']
        if row.get('artist') and row['artist'] != row['author']:
            credit += f"\n✏️ {row['artist']}"
        embed.add_field(name="Author", value=credit, inline=True)
    if row.get('studio'):
        embed.add_field(name="Studio", value=row['studio'], inline=True)

    # Length
    length_parts = []
    if row.get('chapters'): length_parts.append(f"{row['chapters']} ch")
    if row.get('volumes'):  length_parts.append(f"{row['volumes']} vol")
    if row.get('episodes'): length_parts.append(f"{row['episodes']} ep")
    if length_parts:
        embed.add_field(name="Length", value="  ·  ".join(length_parts), inline=True)

    # Tags
    genres = row.get('genres') or []
    if genres:
        embed.add_field(name="Tags", value="  ·  ".join(genres[:10]), inline=False)

    # Alt titles
    alts = row.get('alt_titles') or []
    if alts:
        embed.add_field(name="Also known as", value="\n".join(alts[:3]), inline=False)

    # Read / Watch links — one per line for clarity
    links = row.get('translation_links') or []
    if links:
        lines = "\n".join(f"[{l['name']}]({l['url']})" for l in links)
        embed.add_field(name="Read / Watch", value=lines, inline=False)

    # Footer
    mangoku_id = existing_id or new_id
    if existing_id:
        footer = f"Already in Mangoku library  ·  via {source}"
    elif new_id:
        footer = f"✅ Added to Mangoku  ·  via {source}"
    else:
        footer = f"⚠️ Fetched but not saved  ·  via {source}"

    if mangoku_id:
        footer += f"  ·  ID: {mangoku_id[:8]}…"

    embed.set_footer(text=footer)
    embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)

    if row.get('cover_url'):
        embed.set_thumbnail(url=row['cover_url'])

    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class get_manga(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Link listener ──────────────────────────────────────────────────────────

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

        async with aiohttp.ClientSession() as session:
            if md_match:
                source = 'MangaDex'
                row = await fetch_from_mangadex(md_match.group(1), session)
            else:
                source = 'AniList'
                row = await fetch_from_anilist(int(al_match.group(2)), session)

        if not row:
            await message.channel.send(f"❌ Couldn't fetch data from {source}.", delete_after=12)
            return

        existing_id = new_id = None
        try:
            async with aiohttp.ClientSession() as session:
                existing_id = await find_existing(row['title'], row.get('alt_titles') or [], session)
                if not existing_id:
                    new_id, err = await insert_media(row, session)
                    if not new_id:
                        await message.channel.send(f"⚠️ Saved failed: `{err}`", delete_after=30)
        except Exception as e:
            await message.channel.send(f"⚠️ Database error: `{e}`", delete_after=30)

        embed = _build_embed(row, message.author, source, existing_id=existing_id, new_id=new_id)
        view  = MangaView(row['title'], mangoku_id=existing_id or new_id)
        await message.channel.send(embed=embed, view=view)

    # ── /search ────────────────────────────────────────────────────────────────

    @app_commands.command(name="search", description="Search Mangoku for a manga, anime, or light novel")
    @app_commands.describe(title="Title to search for")
    async def search(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer()
        async with aiohttp.ClientSession() as session:
            results = await search_db(title, session, limit=5)

        if not results:
            await interaction.followup.send(f"❌ No results found for **{title}**.")
            return

        if len(results) == 1:
            row = results[0]
            embed = _build_embed(row, interaction.user, 'Mangoku', existing_id=row['id'])
            view  = MangaView(row['title'], mangoku_id=row['id'])
            await interaction.followup.send(embed=embed, view=view)
        else:
            # Multiple results — list them
            embed = discord.Embed(
                title=f"Search results for \"{title}\"",
                color=0x9333EA,
            )
            for r in results:
                meta = f"{r['type'].capitalize()}  ·  {r['status'].capitalize()}"
                if r.get('year'): meta += f"  ·  {r['year']}"
                embed.add_field(
                    name=r['title'],
                    value=f"{meta}\n[View on Mangoku]({MANGOKU_BASE}/{r['id']})",
                    inline=False,
                )
            await interaction.followup.send(embed=embed)

    # ── /random ────────────────────────────────────────────────────────────────

    @app_commands.command(name="random", description="Get a random title from Mangoku")
    @app_commands.describe(tags="Optional comma-separated tags to filter by (e.g. Action,Romance)")
    async def random(self, interaction: discord.Interaction, tags: str | None = None):
        await interaction.response.defer()
        tag_list = [t.strip() for t in tags.split(',')] if tags else None
        async with aiohttp.ClientSession() as session:
            row = await get_random_from_db(tag_list, session)

        if not row:
            msg = f"❌ No titles found"
            if tag_list:
                msg += f" with tags: **{', '.join(tag_list)}**"
            await interaction.followup.send(msg)
            return

        embed = _build_embed(row, interaction.user, 'Mangoku', existing_id=row['id'])
        if tag_list:
            embed.set_footer(text=f"🎲 Random pick  ·  filtered by: {', '.join(tag_list)}")
        else:
            embed.set_footer(text="🎲 Random pick from Mangoku")
        view = MangaView(row['title'], mangoku_id=row['id'])
        await interaction.followup.send(embed=embed, view=view)

    # ── /tags ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="tags", description="Show all available genre tags on Mangoku")
    async def tags(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with aiohttp.ClientSession() as session:
            all_tags = await get_all_tags(session)

        if not all_tags:
            await interaction.followup.send("❌ Couldn't fetch tags.", ephemeral=True)
            return

        chunks = [all_tags[i:i+20] for i in range(0, len(all_tags), 20)]
        embed = discord.Embed(title="Available tags on Mangoku", color=0x9333EA)
        for i, chunk in enumerate(chunks[:4]):
            embed.add_field(name=f"Tags {i*20+1}–{i*20+len(chunk)}", value="  ·  ".join(chunk), inline=False)
        embed.set_footer(text=f"{len(all_tags)} total tags  ·  Use /random tags:Action,Romance to filter")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(get_manga(bot))
