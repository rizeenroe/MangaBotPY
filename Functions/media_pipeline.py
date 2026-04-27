"""
media_pipeline.py
Shared fetch + normalize + upsert pipeline for MangaDex and AniList.
Mirrors the logic in scripts/seed-from-anilist.mjs and scripts/backfill-*.mjs.
"""

import re
import random
from urllib.parse import quote
import os

import aiohttp

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ANILIST_URL  = "https://graphql.anilist.co"
MANGADEX_API = "https://api.mangadex.org"

# ── Color palette (same as seed scripts) ─────────────────────────────────────

GENRE_COLOR = {
    'Action': '#dc2626', 'Adventure': '#d97706', 'Comedy': '#16a34a',
    'Drama': '#7c3aed', 'Fantasy': '#2563eb', 'Horror': '#1c1c2e',
    'Mystery': '#4f46e5', 'Romance': '#db2777', 'Sci-Fi': '#0891b2',
    'Slice of Life': '#059669', 'Sports': '#ea580c', 'Supernatural': '#6d28d9',
    'Thriller': '#9f1239', 'Psychological': '#5b21b6', 'Mecha': '#0e7490',
}
FALLBACK_COLORS = ['#7c3aed', '#0891b2', '#be185d', '#059669', '#b45309', '#dc2626', '#4f46e5']

def pick_color(genres: list[str]) -> str:
    for g in genres:
        if g in GENRE_COLOR:
            return GENRE_COLOR[g]
    return random.choice(FALLBACK_COLORS)

# ── Field mappers ─────────────────────────────────────────────────────────────

def map_status_md(raw: str) -> str:
    """MangaDex status string → our schema."""
    if raw in ('completed', 'cancelled'):
        return 'completed'
    if raw == 'hiatus':
        return 'hiatus'
    return 'ongoing'

def map_status_al(raw: str) -> str:
    """AniList status enum → our schema."""
    if raw in ('FINISHED', 'CANCELLED'):
        return 'completed'
    if raw == 'HIATUS':
        return 'hiatus'
    return 'ongoing'

def map_type_md(original_language: str) -> str:
    if original_language == 'ko':
        return 'manhwa'
    if original_language in ('zh', 'zh-hk'):
        return 'manhua'
    return 'manga'

def map_type_al(fmt: str | None, country: str) -> str:
    if country == 'KR' or fmt == 'MANHWA':
        return 'manhwa'
    if country in ('CN', 'TW') or fmt == 'MANHUA':
        return 'manhua'
    return 'manga'

def parse_int(val) -> int | None:
    try:
        return int(float(val)) if val else None
    except (ValueError, TypeError):
        return None

def strip_html(s: str) -> str:
    s = re.sub(r'<[^>]+>', '', s)
    for ent, char in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&quot;','"'),('&#39;',"'"),('&nbsp;',' ')]:
        s = s.replace(ent, char)
    return s.strip()

def build_alt_titles(primary: str, *title_sources: str | None) -> list[str]:
    seen, result = {primary.lower()}, []
    for t in title_sources:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            result.append(t)
    return result

# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_headers() -> dict:
    return {
        'apikey':        SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type':  'application/json',
    }

async def find_existing(title: str, alt_titles: list[str], session: aiohttp.ClientSession) -> str | None:
    """Return the Supabase media id if a title (or alt) already exists."""
    headers = _sb_headers()
    for t in [title] + (alt_titles or []):
        url = f"{SUPABASE_URL}/rest/v1/media?select=id&title=ilike.{quote(t, safe='')}&limit=1"
        async with session.get(url, headers=headers) as r:
            data = await r.json()
            if isinstance(data, list) and data:
                return data[0]['id']
    return None

async def insert_media(row: dict, session: aiohttp.ClientSession) -> tuple[str | None, str | None]:
    """
    Insert a new media row.
    Returns (supabase_id, None) on success or (None, error_message) on failure.
    """
    async with session.post(
        f"{SUPABASE_URL}/rest/v1/media",
        json=row,
        headers={**_sb_headers(), 'Prefer': 'return=representation'},
    ) as r:
        body = await r.text()
        if r.status in (200, 201):
            import json as _json
            data = _json.loads(body)
            inserted_id = data[0]['id'] if data else None
            return inserted_id, None
        err = f"HTTP {r.status}: {body[:300]}"
        print(f"[pipeline] Supabase insert error — {err}")
        return None, err

# ── AniList cross-reference (get AniList link for a title) ───────────────────

_AL_SEARCH_QUERY = """
query ($search: String, $type: MediaType) {
  Page(page: 1, perPage: 3) {
    media(search: $search, type: $type, isAdult: false) {
      id type title { english romaji }
    }
  }
}
"""

async def find_mangadex_link(title: str, session: aiohttp.ClientSession) -> str | None:
    """Search MangaDex by title and return its page URL, or None."""
    try:
        async with session.get(
            f"{MANGADEX_API}/manga",
            params={'title': title, 'limit': 1},
        ) as r:
            if not r.ok:
                return None
            data = await r.json()
            results = data.get('data', [])
            if not results:
                return None
            return f"https://mangadex.org/title/{results[0]['id']}"
    except Exception:
        return None

async def find_anilist_link(title: str, media_type: str, session: aiohttp.ClientSession) -> str | None:
    """Search AniList by title and return its page URL, or None."""
    al_type = 'ANIME' if media_type in ('anime', 'movie') else 'MANGA'
    try:
        async with session.post(
            ANILIST_URL,
            json={'query': _AL_SEARCH_QUERY, 'variables': {'search': title, 'type': al_type}},
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        ) as r:
            if r.status == 429:
                return None  # skip quietly — not worth blocking for
            data = await r.json()
            results = data.get('data', {}).get('Page', {}).get('media', [])
            if not results:
                return None
            norm = title.lower().strip()
            for m in results:
                en = (m['title'].get('english') or '').lower().strip()
                ro = (m['title'].get('romaji')  or '').lower().strip()
                if en == norm or ro == norm:
                    t = 'anime' if m['type'] == 'ANIME' else 'manga'
                    return f"https://anilist.co/{t}/{m['id']}"
            # Fallback to first result
            m = results[0]
            t = 'anime' if m['type'] == 'ANIME' else 'manga'
            return f"https://anilist.co/{t}/{m['id']}"
    except Exception:
        return None

# ── MangaDex pipeline ─────────────────────────────────────────────────────────

async def fetch_from_mangadex(manga_id: str, session: aiohttp.ClientSession) -> dict | None:
    """
    Fetch full metadata from MangaDex and return a normalized media row dict.
    Returns None if the request fails.
    """
    params = [
        ('includes[]', 'cover_art'),
        ('includes[]', 'author'),
        ('includes[]', 'artist'),
    ]
    async with session.get(f"{MANGADEX_API}/manga/{manga_id}", params=params) as r:
        data = await r.json()

    if data.get('result') != 'ok':
        return None

    attrs = data['data']['attributes']
    rels  = data['data'].get('relationships', [])

    # Titles
    primary   = attrs['title'].get('en') or next(iter(attrs['title'].values()), 'Unknown')
    alt_map   = attrs.get('altTitles', [])
    alt_raw   = [v for d in alt_map for v in d.values() if v]
    alt_titles = build_alt_titles(primary, *alt_raw)[:10]

    # Metadata
    status    = map_status_md(attrs.get('status', 'ongoing'))
    md_type   = map_type_md(attrs.get('originalLanguage', 'ja'))
    year      = attrs.get('year')
    synopsis  = strip_html(attrs.get('description', {}).get('en', ''))
    tags      = [t['attributes']['name']['en'] for t in attrs.get('tags', [])
                 if t['attributes']['name'].get('en')]

    # Chapters / volumes
    chapters = parse_int(attrs.get('lastChapter'))
    volumes  = parse_int(attrs.get('lastVolume'))

    # Cover
    cover_url = None
    cover_rel = next((r for r in rels if r['type'] == 'cover_art'), None)
    if cover_rel and cover_rel.get('attributes', {}).get('fileName'):
        cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{cover_rel['attributes']['fileName']}"

    # Author + Artist (MangaDex has separate relationship types)
    author_rel = next((r for r in rels if r['type'] == 'author'), None)
    artist_rel = next((r for r in rels if r['type'] == 'artist'), None)
    author_name = author_rel['attributes']['name'] if author_rel and author_rel.get('attributes') else None
    artist_raw  = artist_rel['attributes']['name'] if artist_rel and artist_rel.get('attributes') else None
    artist_name = artist_raw if artist_raw and artist_raw != author_name else None

    # Translation links: MangaDex self + AniList (looked up by title)
    md_link = {'name': 'MangaDex', 'url': f'https://mangadex.org/title/{manga_id}'}
    al_url  = await find_anilist_link(primary, md_type, session)
    links   = [md_link] + ([{'name': 'AniList', 'url': al_url}] if al_url else [])

    return {
        'title':      primary[:200],
        'type':       md_type,
        'score':      0,
        'color':      pick_color(tags),
        'status':     status,
        'year':       year,
        'synopsis':   synopsis[:2000],
        'genres':     tags,
        'author':     author_name,
        'artist':     artist_name,
        'author_url': None,
        'artist_url': None,
        'studio':     None,
        'chapters':   chapters,
        'volumes':    volumes,
        'episodes':   None,
        'related':    [],
        'cover_url':  cover_url,
        'alt_titles': alt_titles or None,
        'translation_links': links,
    }

# ── AniList pipeline ──────────────────────────────────────────────────────────

_AL_FETCH_QUERY = """
query ($id: Int) {
  Media(id: $id) {
    id type format status countryOfOrigin averageScore
    title { english romaji native }
    startDate { year }
    coverImage { extraLarge large }
    genres
    description(asHtml: false)
    chapters volumes episodes
    staff(perPage: 6) { edges { role node { name { full } } } }
    studios { nodes { name isAnimationStudio } }
    externalLinks { url site type }
  }
}
"""

def _extract_credits_al(edges: list) -> tuple[str | None, str | None]:
    story = next((e for e in edges if re.search(r'\bstory\b|\boriginal\b', e['role'], re.I)
                  and not re.search(r'\bart\b', e['role'], re.I)), None)
    art   = next((e for e in edges if re.search(r'\bart\b|\billustrat', e['role'], re.I)
                  and not re.search(r'\bstory\b', e['role'], re.I)), None)
    both  = next((e for e in edges if re.search(r'story.{0,5}art|art.{0,5}story', e['role'], re.I)), None)
    author_name = (story or both or (edges[0] if edges else None))
    author_name = author_name['node']['name']['full'] if author_name else None
    artist_raw  = art['node']['name']['full'] if art else None
    artist_name = artist_raw if artist_raw and artist_raw != author_name else None
    return author_name, artist_name

async def fetch_from_anilist(anilist_id: int, session: aiohttp.ClientSession) -> dict | None:
    """
    Fetch full metadata from AniList by numeric ID and return a normalized media row dict.
    Returns None if the request fails.
    """
    async with session.post(
        ANILIST_URL,
        json={'query': _AL_FETCH_QUERY, 'variables': {'id': anilist_id}},
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
    ) as r:
        if not r.ok:
            return None
        data = await r.json()

    m = data.get('data', {}).get('Media')
    if not m:
        return None

    is_anime = m['type'] == 'ANIME'
    primary  = (m['title'].get('english') or m['title'].get('romaji') or '').strip()
    alt_titles = build_alt_titles(
        primary,
        m['title'].get('english'),
        m['title'].get('romaji'),
        m['title'].get('native'),
    )

    status   = map_status_al(m.get('status', ''))
    al_type  = 'anime' if is_anime else map_type_al(m.get('format'), m.get('countryOfOrigin', 'JP'))
    year     = m.get('startDate', {}).get('year')
    score    = (m.get('averageScore') or 0) / 10
    synopsis = strip_html(m.get('description') or '')
    genres   = m.get('genres') or []

    cover_url = (m.get('coverImage') or {}).get('extraLarge') or (m.get('coverImage') or {}).get('large')

    # Credits
    if is_anime:
        author_name, artist_name = None, None
        studio = next(
            (s['name'] for s in (m.get('studios') or {}).get('nodes', []) if s.get('isAnimationStudio')),
            next((s['name'] for s in (m.get('studios') or {}).get('nodes', [])), None)
        )
    else:
        author_name, artist_name = _extract_credits_al((m.get('staff') or {}).get('edges', []))
        studio = None

    # Translation links: AniList self + MangaDex
    al_url  = f"https://anilist.co/{'anime' if is_anime else 'manga'}/{m['id']}"
    links   = [{'name': 'AniList', 'url': al_url}]
    ext     = m.get('externalLinks') or []
    md_ext  = next((l for l in ext if 'mangadex' in (l.get('url') or '').lower()), None)
    if md_ext:
        links.append({'name': 'MangaDex', 'url': md_ext['url']})
    elif not is_anime:
        # AniList rarely lists MangaDex in external links — search directly
        md_url = await find_mangadex_link(primary, session)
        if md_url:
            links.append({'name': 'MangaDex', 'url': md_url})

    return {
        'title':      primary[:200],
        'type':       al_type,
        'score':      score,
        'color':      pick_color(genres),
        'status':     status,
        'year':       year,
        'synopsis':   synopsis[:2000],
        'genres':     genres,
        'author':     author_name,
        'artist':     artist_name,
        'author_url': None,
        'artist_url': None,
        'studio':     studio,
        'chapters':   m.get('chapters'),
        'volumes':    m.get('volumes'),
        'episodes':   m.get('episodes'),
        'related':    [],
        'cover_url':  cover_url,
        'alt_titles': alt_titles or None,
        'translation_links': links,
    }
