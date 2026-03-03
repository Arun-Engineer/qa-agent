"""Component Fingerprinter — DOM traversal to discover UI elements and build taxonomy.

Walks the DOM of each page and catalogs: buttons, inputs, forms, navigation,
product cards, media elements, etc. Feeds into the SiteModel for Phase 3 agents.
"""
from __future__ import annotations

import yaml
import structlog
from pathlib import Path
from typing import Optional

from src.discovery.site_model import ComponentInfo

logger = structlog.get_logger()


# ── Default selectors (overridden by config/discovery.yaml if present) ──

DEFAULT_SELECTORS: dict[str, str] = {
    "buttons": "button, [role='button'], input[type='submit'], input[type='button'], a.btn, [class*='btn']",
    "inputs": "input:not([type='hidden']), textarea, select, [contenteditable='true']",
    "forms": "form, [role='form']",
    "navigation": "nav, [role='navigation'], [class*='navbar'], [class*='sidebar'], [class*='menu']",
    "media": "img, video, audio, canvas, svg:not([class*='icon'])",
    "cards": "[class*='card'], [class*='product'], [class*='item'], [class*='tile'], [class*='listing']",
    "links": "a[href]",
    "modals": "[role='dialog'], [class*='modal'], [class*='popup'], [class*='overlay']",
    "tables": "table, [role='grid'], [role='table']",
    "tabs": "[role='tablist'], [class*='tab-']",
}


def _load_config_selectors() -> dict[str, str]:
    """Load custom selectors from config/discovery.yaml if available."""
    try:
        config_path = Path("config/discovery.yaml")
        if not config_path.exists():
            return DEFAULT_SELECTORS
        with open(config_path) as f:
            data = yaml.safe_load(f)
        custom = data.get("fingerprinting", {}).get("interactive_selectors", {})
        if custom:
            merged = dict(DEFAULT_SELECTORS)
            merged.update(custom)
            return merged
    except Exception as e:
        logger.debug("fingerprint_config_load_failed", error=str(e))
    return DEFAULT_SELECTORS


def _extract_element_info(page, element, component_type: str) -> Optional[ComponentInfo]:
    """Extract info from a single DOM element via Playwright."""
    try:
        info = page.evaluate("""(el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const isVisible = !!(rect.width && rect.height &&
                                  style.visibility !== 'hidden' &&
                                  style.display !== 'none' &&
                                  style.opacity !== '0');
            return {
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || el.value || el.placeholder || el.alt || el.title || '').substring(0, 200).trim(),
                id: el.id || '',
                name: el.getAttribute('name') || '',
                className: el.className || '',
                type: el.getAttribute('type') || '',
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                href: el.getAttribute('href') || '',
                isVisible: isVisible,
                isDisabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                boundingBox: isVisible ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null,
            };
        }""", element)

        if not info:
            return None

        # Build a usable CSS selector for this element
        selector_parts = [info["tag"]]
        if info["id"]:
            selector_parts = [f"#{info['id']}"]
        elif info["name"]:
            selector_parts.append(f"[name='{info['name']}']")
        elif info["role"]:
            selector_parts.append(f"[role='{info['role']}']")
        elif info["type"]:
            selector_parts.append(f"[type='{info['type']}']")
        elif info["ariaLabel"]:
            selector_parts.append(f"[aria-label='{info['ariaLabel'][:50]}']")

        selector = "".join(selector_parts)

        is_interactive = info["tag"] in ("button", "input", "select", "textarea", "a") or \
                         info["role"] in ("button", "link", "textbox", "combobox", "checkbox", "radio", "tab")

        return ComponentInfo(
            component_type=component_type,
            selector=selector,
            tag=info["tag"],
            text=info["text"],
            attributes={
                "id": info["id"],
                "name": info["name"],
                "class": info["className"][:200] if isinstance(info["className"], str) else "",
                "type": info["type"],
                "role": info["role"],
                "aria_label": info["ariaLabel"],
                "href": info["href"][:200] if info["href"] else "",
                "disabled": info["isDisabled"],
            },
            is_interactive=is_interactive,
            is_visible=info["isVisible"],
            bounding_box=info["boundingBox"],
        )

    except Exception as e:
        logger.debug("fingerprint_element_failed", type=component_type, error=str(e))
        return None


def fingerprint_page(page, url: str, max_per_type: int = 50) -> list[ComponentInfo]:
    """Discover and catalog all UI components on the current page.

    Args:
        page: Playwright Page object (already navigated to the target URL)
        url: Current page URL (for logging)
        max_per_type: Max elements to collect per component type

    Returns:
        List of ComponentInfo objects representing discovered components
    """
    selectors = _load_config_selectors()
    components: list[ComponentInfo] = []
    seen_selectors: set[str] = set()  # dedup

    for comp_type, css_selector in selectors.items():
        try:
            elements = page.query_selector_all(css_selector)
            count = 0

            for el in elements:
                if count >= max_per_type:
                    break

                comp = _extract_element_info(page, el, comp_type)
                if comp is None:
                    continue

                # Dedup: skip if same selector already captured
                if comp.selector in seen_selectors:
                    continue
                seen_selectors.add(comp.selector)

                components.append(comp)
                count += 1

        except Exception as e:
            logger.debug("fingerprint_type_failed", type=comp_type, url=url, error=str(e))

    logger.info("fingerprint_complete", url=url, total_components=len(components),
                 types={t: sum(1 for c in components if c.component_type == t) for t in selectors})

    return components


def get_component_summary(components: list[ComponentInfo]) -> dict:
    """Generate a human-readable summary of discovered components."""
    summary: dict[str, int] = {}
    interactive_count = 0
    visible_count = 0

    for c in components:
        summary[c.component_type] = summary.get(c.component_type, 0) + 1
        if c.is_interactive:
            interactive_count += 1
        if c.is_visible:
            visible_count += 1

    return {
        "total": len(components),
        "by_type": summary,
        "interactive": interactive_count,
        "visible": visible_count,
    }
