"""Page Classifier — Classify discovered pages by type using heuristics + optional LLM.

Assigns page types: home, plp, pdp, cart, checkout, login, search, form,
dashboard, settings, error, about, faq, blog, other, unknown.
"""
from __future__ import annotations

import os
import re
import json
import structlog
from typing import Optional
from dataclasses import dataclass

from src.discovery.site_model import PageInfo

logger = structlog.get_logger()


@dataclass
class ClassificationResult:
    page_type: str
    confidence: float            # 0.0 → 1.0
    method: str                  # heuristic | llm | combined
    evidence: list[str]          # reasons for classification


# ── Heuristic Rules ──────────────────────────────────────────

# URL path patterns → page type (checked in order)
URL_PATTERNS: list[tuple[str, str, float]] = [
    # (regex_pattern, page_type, confidence)
    (r"^/?$", "home", 0.9),
    (r"/login|/signin|/sign-in|/auth/login", "login", 0.95),
    (r"/signup|/register|/sign-up|/create-account", "signup", 0.95),
    (r"/forgot[-_]?password|/reset[-_]?password", "password_reset", 0.95),
    (r"/cart|/basket|/bag", "cart", 0.9),
    (r"/checkout|/payment|/order/confirm", "checkout", 0.9),
    (r"/search|/results\?", "search", 0.85),
    (r"/product/|/item/|/p/|/dp/", "pdp", 0.85),
    (r"/category/|/collection/|/shop/|/products/?$|/c/", "plp", 0.8),
    (r"/account|/profile|/my[-_]?account|/settings", "settings", 0.8),
    (r"/dashboard|/admin|/console|/panel", "dashboard", 0.8),
    (r"/blog|/post/|/article/|/news/", "blog", 0.8),
    (r"/about|/company|/team", "about", 0.8),
    (r"/faq|/help|/support|/contact", "faq", 0.8),
    (r"/404|/not[-_]?found", "error", 0.9),
    (r"/500|/error|/oops", "error", 0.9),
    (r"/terms|/privacy|/legal|/tos", "legal", 0.85),
]

# DOM-based signals (presence of specific elements)
DOM_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "login": [
        ('input[type="password"]', 0.8),
        ('form[action*="login"]', 0.85),
    ],
    "search": [
        ('input[type="search"]', 0.4),   # low — many pages have search
        ('[class*="search-result"]', 0.8),
        ('[class*="result-item"]', 0.7),
    ],
    "plp": [
        ('[class*="product-list"]', 0.8),
        ('[class*="product-grid"]', 0.8),
        ('[class*="item-card"]', 0.7),
        ('a[href*="/product/"]', 0.3),  # many product links = listing
    ],
    "pdp": [
        ('[class*="add-to-cart"]', 0.85),
        ('button:has-text("Add to Cart")', 0.85),
        ('button:has-text("Add to Bag")', 0.85),
        ('[class*="product-detail"]', 0.8),
        ('[class*="product-image"]', 0.6),
        ('[class*="price"]', 0.4),
    ],
    "cart": [
        ('[class*="cart-item"]', 0.85),
        ('[class*="bag-item"]', 0.85),
        ('button:has-text("Checkout")', 0.7),
        ('button:has-text("Proceed")', 0.5),
    ],
    "checkout": [
        ('[class*="checkout"]', 0.7),
        ('input[autocomplete="cc-number"]', 0.9),
        ('[class*="payment"]', 0.8),
        ('[class*="shipping"]', 0.6),
    ],
    "form": [
        ("form", 0.3),  # generic forms are low signal
        ('input[type="text"]', 0.2),
    ],
    "error": [
        ('text="404"', 0.6),
        ('text="Page not found"', 0.8),
        ('text="Not Found"', 0.7),
    ],
}


def _classify_by_url(url: str) -> Optional[ClassificationResult]:
    """Classify page by URL pattern matching."""
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()

    for pattern, page_type, confidence in URL_PATTERNS:
        if re.search(pattern, path, re.IGNORECASE):
            return ClassificationResult(
                page_type=page_type,
                confidence=confidence,
                method="heuristic",
                evidence=[f"URL path matches pattern: {pattern}"],
            )
    return None


def _classify_by_dom(page, url: str) -> Optional[ClassificationResult]:
    """Classify page by DOM element presence (Playwright page object)."""
    best_type = "unknown"
    best_confidence = 0.0
    evidence: list[str] = []

    for page_type, selectors in DOM_SIGNALS.items():
        type_score = 0.0
        type_evidence: list[str] = []

        for selector, weight in selectors:
            try:
                # Handle text-based pseudo-selectors
                if selector.startswith("text="):
                    count = page.locator(selector).count()
                elif selector.startswith("button:has-text"):
                    count = page.locator(selector).count()
                else:
                    count = page.locator(selector).count()

                if count > 0:
                    # Multiple matches boost confidence for listing pages
                    if page_type in ("plp", "search") and count > 5:
                        boost = min(0.2, count * 0.02)
                        type_score += weight + boost
                    else:
                        type_score += weight
                    type_evidence.append(f"Found {count}x '{selector}' (+{weight:.2f})")
            except Exception:
                continue

        # Normalize: cap at 1.0
        type_score = min(1.0, type_score)

        if type_score > best_confidence:
            best_confidence = type_score
            best_type = page_type
            evidence = type_evidence

    if best_confidence < 0.3:
        return None

    return ClassificationResult(
        page_type=best_type,
        confidence=round(best_confidence, 3),
        method="heuristic",
        evidence=evidence,
    )


def _classify_by_title_meta(page_info: PageInfo) -> Optional[ClassificationResult]:
    """Classify by page title and meta tags."""
    title = (page_info.title or "").lower()
    meta_desc = (page_info.meta.get("description") or page_info.meta.get("og:type") or "").lower()
    combined = f"{title} {meta_desc}"

    patterns = [
        (r"shopping cart|my bag|your cart", "cart", 0.85),
        (r"checkout|payment|order summary", "checkout", 0.85),
        (r"log\s*in|sign\s*in", "login", 0.8),
        (r"sign\s*up|create.*account|register", "signup", 0.8),
        (r"search results|results for", "search", 0.8),
        (r"product", "pdp", 0.4),  # low — needs more context
        (r"dashboard", "dashboard", 0.8),
        (r"404|not found|page not found", "error", 0.9),
    ]

    for pattern, page_type, confidence in patterns:
        if re.search(pattern, combined, re.IGNORECASE):
            return ClassificationResult(
                page_type=page_type,
                confidence=confidence,
                method="heuristic",
                evidence=[f"Title/meta matches: '{pattern}'"],
            )
    return None


def classify_page(
    page_info: PageInfo,
    page=None,
    use_llm: bool = False,
    min_confidence: float = 0.4,
) -> ClassificationResult:
    """Classify a page using layered heuristics, optionally with LLM.

    Layers (in priority order):
    1. URL pattern matching (fastest, high confidence for known patterns)
    2. DOM element analysis (requires Playwright page, medium cost)
    3. Title/meta analysis (fast fallback)
    4. LLM classification (optional, most accurate, highest cost)

    Args:
        page_info: PageInfo with url, title, meta
        page: Playwright Page object (optional, enables DOM analysis)
        use_llm: Whether to use LLM for uncertain classifications
        min_confidence: Below this threshold, try next layer

    Returns:
        ClassificationResult with type, confidence, method, evidence
    """
    # Layer 1: URL patterns
    result = _classify_by_url(page_info.url)
    if result and result.confidence >= min_confidence:
        return result

    # Layer 2: DOM analysis (if Playwright page available)
    if page is not None:
        dom_result = _classify_by_dom(page, page_info.url)
        if dom_result and dom_result.confidence >= min_confidence:
            # Combine with URL result if both available
            if result:
                if dom_result.page_type == result.page_type:
                    combined_conf = min(1.0, (result.confidence + dom_result.confidence) / 1.5)
                    return ClassificationResult(
                        page_type=dom_result.page_type,
                        confidence=round(combined_conf, 3),
                        method="combined",
                        evidence=result.evidence + dom_result.evidence,
                    )
                # Conflicting: prefer higher confidence
                return dom_result if dom_result.confidence > result.confidence else result
            return dom_result

    # Layer 3: Title / meta
    meta_result = _classify_by_title_meta(page_info)
    if meta_result and meta_result.confidence >= min_confidence:
        return meta_result

    # Layer 4: LLM (optional)
    if use_llm and os.getenv("OPENAI_API_KEY"):
        try:
            llm_result = _classify_by_llm(page_info)
            if llm_result and llm_result.confidence >= min_confidence:
                return llm_result
        except Exception as e:
            logger.warning("classify_llm_failed", error=str(e))

    # Fallback: return best guess or unknown
    if result:
        return result
    if page is not None:
        dom_result = _classify_by_dom(page, page_info.url)
        if dom_result:
            return dom_result

    return ClassificationResult(
        page_type="unknown", confidence=0.0,
        method="heuristic", evidence=["No pattern matched"],
    )


def _classify_by_llm(page_info: PageInfo) -> Optional[ClassificationResult]:
    """Use LLM to classify a page (fallback for ambiguous pages)."""
    try:
        from agent.utils.openai_wrapper import chat_completion
    except ImportError:
        return None

    prompt = f"""Classify this web page into one of these types:
home, plp (product listing), pdp (product detail), cart, checkout, login, signup,
search, form, dashboard, settings, blog, about, faq, error, legal, other.

Page info:
- URL: {page_info.url}
- Title: {page_info.title}
- Meta: {json.dumps(page_info.meta, default=str)[:500]}
- Components: {len(page_info.components)} found
- Outgoing links: {len(page_info.outgoing_links)}

Respond with ONLY a JSON object: {{"page_type": "...", "confidence": 0.0-1.0, "reason": "..."}}"""

    resp = chat_completion(
        messages=[
            {"role": "system", "content": "You are a web page classifier. Respond only with JSON."},
            {"role": "user", "content": prompt},
        ],
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.1,
        response_format={"type": "json_object"},
        service_name="qa-agent-page-classifier",
    )

    content = (resp.choices[0].message.content or "").strip()
    data = json.loads(content)

    return ClassificationResult(
        page_type=data.get("page_type", "unknown"),
        confidence=float(data.get("confidence", 0.5)),
        method="llm",
        evidence=[data.get("reason", "LLM classification")],
    )
