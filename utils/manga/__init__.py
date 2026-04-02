from .models import MangaResult
from .searchers import MangaSearcher, MangaDexSearcher, AnilistSearcher
from .aggregator import SEARCHERS, search_by_url, search_by_title, build_embeds