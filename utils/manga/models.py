# utils/manga/models.py
from dataclasses import dataclass, field


@dataclass
class MangaResult:
    """Normalised manga info returned by any MangaSearcher implementation."""
    source: str
    manga_id: str
    title: str
    description: str = "No description available."
    status: str = "unknown"
    tags: list[str] = field(default_factory=list)
    cover_url: str | None = None
    latest_chapter: str | None = None
    url: str | None = None