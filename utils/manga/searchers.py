# utils/manga/searchers.py
import re
import aiohttp
from abc import ABC, abstractmethod
from .models import MangaResult


class MangaSearcher(ABC):
    """Base class for a manga metadata source."""

    @abstractmethod
    async def fetch(self, session: aiohttp.ClientSession, manga_id: str) -> MangaResult | None:
        ...

    @abstractmethod
    def extract_id(self, url: str) -> str | None:
        ...


class MangaDexSearcher(MangaSearcher):
    REGEX = re.compile(r"https://mangadex\.org/title/([a-f0-9-]+)")
    API = "https://api.mangadex.org"

    def extract_id(self, url: str) -> str | None:
        m = self.REGEX.search(url)
        return m.group(1) if m else None

    async def fetch(self, session: aiohttp.ClientSession, manga_id: str) -> MangaResult | None:
        try:
            async with session.get(
                f"{self.API}/manga/{manga_id}",
                params={"includes[]": "cover_art"},
            ) as resp:
                if resp.status != 200:
                    return None
                data = (await resp.json())["data"]

            attrs = data["attributes"]
            title = attrs["title"].get("en") or next(iter(attrs["title"].values()), "Unknown")
            description = attrs["description"].get("en", "No description available.")
            status = attrs["status"]
            tags = [t["attributes"]["name"]["en"] for t in attrs.get("tags", [])]

            cover_url = None
            cover = next((r for r in data.get("relationships", []) if r["type"] == "cover_art"), None)
            if cover and cover.get("attributes"):
                fn = cover["attributes"]["fileName"]
                cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{fn}"

            latest_chapter = None
            async with session.get(
                f"{self.API}/manga/{manga_id}/aggregate",
                params={"translatedLanguage[]": "en"},
            ) as resp:
                if resp.status == 200:
                    agg = await resp.json()
                    volumes = agg.get("volumes", {})
                    all_chapters: list[float] = []
                    for vol in volumes.values():
                        for ch in vol.get("chapters", {}).keys():
                            try:
                                all_chapters.append(float(ch))
                            except ValueError:
                                pass
                    if all_chapters:
                        latest_chapter = str(max(all_chapters))
                        if latest_chapter.endswith(".0"):
                            latest_chapter = latest_chapter[:-2]

            return MangaResult(
                source="MangaDex",
                manga_id=manga_id,
                title=title,
                description=description,
                status=status,
                tags=tags,
                cover_url=cover_url,
                latest_chapter=latest_chapter,
                url=f"https://mangadex.org/title/{manga_id}",
            )
        except Exception:
            return None


class AnilistSearcher(MangaSearcher):
    API = "https://graphql.anilist.co"

    def extract_id(self, url: str) -> str | None:
        m = re.search(r"https://anilist\.co/manga/(\d+)", url)
        return m.group(1) if m else None

    async def fetch_by_title(self, session: aiohttp.ClientSession, title: str) -> MangaResult | None:
        query = """
        query ($search: String) {
          Media(search: $search, type: MANGA) {
            id
            title { romaji english }
            description(asHtml: false)
            status
            genres
            coverImage { extraLarge }
            chapters
            siteUrl
          }
        }
        """
        try:
            async with session.post(
                self.API,
                json={"query": query, "variables": {"search": title}},
            ) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()

            media = payload.get("data", {}).get("Media")
            if not media:
                return None

            al_title = media["title"].get("english") or media["title"].get("romaji", "Unknown")
            return MangaResult(
                source="Anilist",
                manga_id=str(media["id"]),
                title=al_title,
                description=media.get("description") or "No description available.",
                status=(media.get("status") or "unknown").lower(),
                tags=media.get("genres") or [],
                cover_url=(media.get("coverImage") or {}).get("extraLarge"),
                latest_chapter=str(media["chapters"]) if media.get("chapters") else None,
                url=media.get("siteUrl"),
            )
        except Exception:
            return None

    async def fetch(self, session: aiohttp.ClientSession, manga_id: str) -> MangaResult | None:
        query = """
        query ($id: Int) {
          Media(id: $id, type: MANGA) {
            id title { romaji english } description(asHtml: false)
            status genres coverImage { extraLarge } chapters siteUrl
          }
        }
        """
        try:
            async with session.post(
                self.API,
                json={"query": query, "variables": {"id": int(manga_id)}},
            ) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
            media = payload.get("data", {}).get("Media")
            if not media:
                return None
            al_title = media["title"].get("english") or media["title"].get("romaji", "Unknown")
            return MangaResult(
                source="Anilist",
                manga_id=str(media["id"]),
                title=al_title,
                description=media.get("description") or "No description available.",
                status=(media.get("status") or "unknown").lower(),
                tags=media.get("genres") or [],
                cover_url=(media.get("coverImage") or {}).get("extraLarge"),
                latest_chapter=str(media["chapters"]) if media.get("chapters") else None,
                url=media.get("siteUrl"),
            )
        except Exception as e:
            print(f"MangaDex fetch error: {e}")
            return None