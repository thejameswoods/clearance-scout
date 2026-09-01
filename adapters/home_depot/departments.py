"""Phase 1: department discovery via Home Depot's public XML sitemaps.

Confirmed approach (from reading HDScanner's source, see api_client.py's
module docstring): sitemaps, not a live browse/search API. This is safer
by construction -- sitemaps are meant for search-engine crawlers, so
they're unauthenticated, cheap, and not behind the same bot-management as
interactive endpoints.

A department is any sitemap `<loc>` URL matching `/b/<label>/N-<navParam>`
-- `navParam` is Home Depot's own department/category ID (used as
`retailer_department_id` throughout), `label` is a human-readable slug
pulled straight from the URL. Category-page sitemaps are themselves
crawled from a couple of top-level index files; this is a bounded 2-level
crawl, not the exhaustive tree-crawl-plus-brute-force-fallback HDScanner
does to find its full ~5000-category catalog -- WATCHED_DEPARTMENTS
already narrows what anyone using this actually needs, so completeness
here matters less than it would for a general-purpose scanner.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Iterator

from ..base import Department

logger = logging.getLogger("clearance_scout.adapters.home_depot")

SITEMAP_ENTRY_POINTS = [
    "https://www.homedepot.com/sitemap.xml",
    "https://www.homedepot.com/sitemap/B/PLPs.xml",
]

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_CATEGORY_URL_RE = re.compile(r"/b/([^/]+)/N-([a-zA-Z0-9]+)")
_NOISE_NAV_PARAM_RE = re.compile(r"Z1z[a-z]")  # matches HDScanner's own noise filter

MAX_CRAWL_DEPTH = 2


def _fetch_locs(browser_ctx: Any, url: str, retries: int = 2, retry_delay_seconds: float = 2.0) -> list[str]:
    # HD's sitemap endpoints occasionally hold the socket open without
    # responding at all (HDScanner's own source retries for exactly this
    # reason). Confirmed live 2026-09-01: a single unretried timeout here
    # aborted an entire scheduled scan over what was a normal, recoverable
    # network hiccup, not a real failure. A genuine 404 is different --
    # retrying that can't help, so it returns immediately without using up
    # a retry.
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = browser_ctx.request.get(url, timeout=15000)
        except Exception as exc:
            last_exc = exc
            logger.warning("Sitemap fetch failed (attempt %d/%d) for %s: %s", attempt + 1, retries + 1, url, exc)
            if attempt < retries:
                time.sleep(retry_delay_seconds)
            continue

        if response.ok:
            return _LOC_RE.findall(response.text())
        if response.status == 404:
            return []
        last_exc = RuntimeError(f"HTTP {response.status} for {url}")
        logger.warning("Sitemap fetch got HTTP %s (attempt %d/%d) for %s", response.status, attempt + 1, retries + 1, url)
        if attempt < retries:
            time.sleep(retry_delay_seconds)

    raise last_exc


def discover_departments(browser_ctx: Any) -> Iterator[Department]:
    seen_navparams: set[str] = set()
    visited_sitemaps: set[str] = set(SITEMAP_ENTRY_POINTS)
    frontier = list(SITEMAP_ENTRY_POINTS)

    for _depth in range(MAX_CRAWL_DEPTH):
        next_frontier: list[str] = []
        for sitemap_url in frontier:
            for loc in _fetch_locs(browser_ctx, sitemap_url):
                category_match = _CATEGORY_URL_RE.search(loc)
                if category_match:
                    label_slug, nav_param = category_match.groups()
                    if _NOISE_NAV_PARAM_RE.search(nav_param) or nav_param in seen_navparams:
                        continue
                    seen_navparams.add(nav_param)
                    yield Department(
                        retailer_department_id=nav_param,
                        name=label_slug.replace("-", " "),
                    )
                elif loc.endswith(".xml") and loc not in visited_sitemaps:
                    visited_sitemaps.add(loc)
                    next_frontier.append(loc)
        frontier = next_frontier
        if not frontier:
            break
