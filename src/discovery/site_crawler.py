"""Site Crawler — Playwright-based BFS/DFS crawler with SPA and cycle detection.

Crawls a target website, discovers all reachable pages, captures screenshots,
and collects links for the page classifier and component fingerprinter.
"""
from __future__ import annotations

import os
import re
import time
import hashlib
import structlog
from collections import deque
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

from src.discovery.site_model import PageInfo

logger = structlog.get_logger()

# Patterns to skip (binary files, anchors, mailto, etc.)
SKIP_PATTERNS = re.compile(
    r"\.(pdf|zip|tar|gz|exe|dmg|apk|ipa|mp4|mp3|avi|mov|jpg|jpeg|png|gif|svg|ico|woff2?|ttf|eot|css|js)$",
    re.IGNORECASE,
)
SKIP_SCHEMES = {"mailto", "tel", "javascript", "data", "blob", "ftp"}


def _normalize_url(url: str, base_url: str) -> Optional[str]:
    """Normalize a URL: resolve relative, strip fragments, enforce same-origin."""
    try:
        if not url or not url.strip():
            return None

        resolved = urljoin(base_url, url.strip())
        parsed = urlparse(resolved)

        if parsed.scheme in SKIP_SCHEMES:
            return None

        # Strip fragment
        cleaned = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))

        # Same-origin check
        base_parsed = urlparse(base_url)
        if parsed.netloc and parsed.netloc != base_parsed.netloc:
            return None

        # Skip binary files
        if SKIP_PATTERNS.search(parsed.path):
            return None

        return cleaned.rstrip("/") or cleaned

    except Exception:
        return None


def _content_hash(page) -> str:
    """Hash visible text content for SPA dedup (same URL, different content)."""
    try:
        text = page.evaluate("() => document.body?.innerText?.substring(0, 5000) || ''")
        return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    except Exception:
        return ""


def crawl_site(
    page,
    base_url: str,
    max_pages: int = 50,
    max_depth: int = 3,
    strategy: str = "bfs",
    screenshot_dir: Optional[str] = None,
    page_timeout_ms: int = 30000,
    wait_after_load_ms: int = 2000,
) -> list[PageInfo]:
    """Crawl a website starting from base_url.

    Args:
        page: Playwright Page object (already logged in if needed)
        base_url: Starting URL
        max_pages: Maximum pages to visit
        max_depth: Maximum link-follow depth from start
        strategy: 'bfs' (breadth-first) or 'dfs' (depth-first)
        screenshot_dir: Directory for screenshots (None to skip)
        page_timeout_ms: Navigation timeout per page
        wait_after_load_ms: Post-load wait for SPA rendering

    Returns:
        List of PageInfo objects (one per discovered page)
    """
    if screenshot_dir:
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    visited_urls: set[str] = set()
    visited_hashes: set[str] = set()  # SPA dedup: URL + content hash
    discovered_pages: list[PageInfo] = []

    # Queue: (url, depth, parent_url)
    frontier: deque = deque()
    start_url = base_url.rstrip("/")
    frontier.append((start_url, 0, None))

    def _pop():
        if strategy == "dfs":
            return frontier.pop()
        return frontier.popleft()  # BFS

    while frontier and len(discovered_pages) < max_pages:
        current_url, depth, parent_url = _pop()

        normalized = _normalize_url(current_url, base_url)
        if not normalized:
            continue
        if normalized in visited_urls:
            continue
        if depth > max_depth:
            continue

        visited_urls.add(normalized)

        logger.info("crawl_visiting", url=normalized, depth=depth,
                     visited=len(discovered_pages), remaining=len(frontier))

        # Navigate
        try:
            response = page.goto(normalized, wait_until="domcontentloaded", timeout=page_timeout_ms)
            status_code = response.status if response else 0
        except Exception as e:
            logger.warning("crawl_nav_error", url=normalized, error=str(e))
            discovered_pages.append(PageInfo(
                url=normalized, title="", page_type="error",
                status_code=0, depth=depth, parent_url=parent_url,
                meta={"error": str(e)},
            ))
            continue

        # Wait for SPA rendering
        try:
            page.wait_for_timeout(wait_after_load_ms)
        except Exception:
            pass

        # SPA dedup: same URL might render different content (tabs, filters)
        c_hash = _content_hash(page)
        url_hash_key = f"{normalized}|{c_hash}"
        if url_hash_key in visited_hashes and c_hash:
            logger.debug("crawl_spa_dedup", url=normalized)
            continue
        if c_hash:
            visited_hashes.add(url_hash_key)

        # Extract page title
        try:
            title = page.title() or ""
        except Exception:
            title = ""

        # Extract meta tags
        meta = {}
        try:
            meta_tags = page.evaluate("""() => {
                const metas = {};
                document.querySelectorAll('meta[name], meta[property]').forEach(m => {
                    const key = m.getAttribute('name') || m.getAttribute('property');
                    if (key) metas[key] = (m.getAttribute('content') || '').substring(0, 300);
                });
                return metas;
            }""")
            meta = meta_tags or {}
        except Exception:
            pass

        # Screenshot
        screenshot_path = None
        if screenshot_dir:
            try:
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", normalized.split("//")[-1])[:100]
                screenshot_path = str(Path(screenshot_dir) / f"{safe_name}.png")
                page.screenshot(path=screenshot_path, full_page=True, timeout=15000)
            except Exception as e:
                logger.debug("crawl_screenshot_failed", url=normalized, error=str(e))
                screenshot_path = None

        # Extract links for frontier
        outgoing_links: list[str] = []
        try:
            raw_links = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h && !h.startsWith('javascript:'))
                    .slice(0, 500);
            }""")
            for link in (raw_links or []):
                norm_link = _normalize_url(link, base_url)
                if norm_link and norm_link not in visited_urls:
                    outgoing_links.append(norm_link)
                    frontier.append((norm_link, depth + 1, normalized))
        except Exception as e:
            logger.debug("crawl_link_extract_failed", url=normalized, error=str(e))

        page_info = PageInfo(
            url=normalized,
            title=title,
            page_type="unknown",  # classified later by page_classifier
            status_code=status_code,
            depth=depth,
            parent_url=parent_url,
            screenshot_path=screenshot_path,
            outgoing_links=outgoing_links,
            meta=meta,
        )
        discovered_pages.append(page_info)

    logger.info("crawl_complete", total_pages=len(discovered_pages),
                 visited_urls=len(visited_urls))
    return discovered_pages
