"""Web search tool backed by DuckDuckGo (via the `ddgs` package)."""
from typing import Dict, List


def web_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Return a list of {title, href, body} search results.

    Falls back to an empty list if the search backend is unavailable so
    the agents can keep running offline.
    """
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover - optional dependency
        return [{"title": "search unavailable", "href": "", "body": "ddgs not installed"}]

    results: List[Dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "href": r.get("href", "") or r.get("url", ""),
                        "body": r.get("body", "") or r.get("snippet", ""),
                    }
                )
    except Exception as exc:  # network / rate-limit errors should not crash agents
        return [{"title": "search error", "href": "", "body": str(exc)}]

    return results


def search_summary(query: str, max_results: int = 5) -> str:
    """Return search results as a compact text block for LLM context."""
    results = web_search(query, max_results=max_results)
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['body']}\n   {r['href']}")
    return "\n".join(lines)
