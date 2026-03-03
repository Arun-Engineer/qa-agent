"""Discovery Engine — Orchestrates the full zero-knowledge site discovery pipeline.

Pipeline:
  1. Auth Handler → Login if credentials provided
  2. API Surface Mapper → Start network interception
  3. Site Crawler → BFS/DFS page discovery
  4. Component Fingerprinter → DOM analysis per page
  5. Page Classifier → Heuristic + LLM classification
  6. Site Model → Assemble and serialize results

This is a SYNCHRONOUS pipeline (runs in threadpool from async API).
Playwright requires sync API for reliable page interaction.
"""
from __future__ import annotations

import os
import time
import yaml
import structlog
from pathlib import Path
from typing import Optional
from datetime import datetime

from src.discovery.site_model import SiteModel, PageInfo
from src.discovery.site_crawler import crawl_site
from src.discovery.page_classifier import classify_page
from src.discovery.component_fingerprinter import fingerprint_page
from src.discovery.api_surface_mapper import ApiSurfaceMapper
from src.discovery.auth_handler import perform_login, LoginResult

logger = structlog.get_logger()


def _load_config() -> dict:
    """Load discovery config from YAML."""
    try:
        config_path = Path("config/discovery.yaml")
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


class DiscoveryEngine:
    """Full zero-knowledge site discovery pipeline.

    Usage:
        engine = DiscoveryEngine(
            base_url="https://example.com",
            max_pages=50,
            login_config={"login_url": "...", "username": "...", "password": "..."},
        )
        site_model = engine.run()
        site_model.save("output/site_model.json")
    """

    def __init__(
        self,
        base_url: str,
        max_pages: int = 50,
        max_depth: int = 3,
        strategy: str = "bfs",
        login_config: Optional[dict] = None,
        screenshot: bool = True,
        headless: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.strategy = strategy
        self.login_config = login_config
        self.screenshot = screenshot
        self.headless = headless

        self._config = _load_config()
        self._crawl_config = self._config.get("crawl", {})
        self._classify_config = self._config.get("classification", {})

    def run(self) -> SiteModel:
        """Execute the full discovery pipeline. Returns a populated SiteModel."""
        start_time = time.time()
        crawl_start = datetime.utcnow().isoformat()
        errors: list[dict] = []

        logger.info("discovery_engine_start", base_url=self.base_url,
                     max_pages=self.max_pages, strategy=self.strategy)

        # ── 0. Launch Playwright ─────────────────────────────
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=self._crawl_config.get(
                    "user_agent", "QA-Agent-Discovery/5.0"
                ),
                ignore_https_errors=True,
            )
            page = context.new_page()

            # ── 1. Auth Handler ──────────────────────────────
            login_result: Optional[LoginResult] = None
            if self.login_config:
                logger.info("discovery_login_start", login_url=self.login_config.get("login_url"))
                login_result = perform_login(
                    page,
                    login_url=self.login_config["login_url"],
                    username=self.login_config["username"],
                    password=self.login_config["password"],
                )
                if not login_result.success:
                    logger.warning("discovery_login_failed", error=login_result.error)
                    errors.append({
                        "phase": "auth",
                        "error": login_result.error,
                        "method": login_result.method,
                    })
                else:
                    logger.info("discovery_login_success",
                                 cookies=login_result.cookies_count,
                                 url_after=login_result.url_after_login)

            # ── 2. API Surface Mapper — start listening ──────
            api_mapper = ApiSurfaceMapper(page, base_url=self.base_url)
            if self._config.get("api_capture", {}).get("enabled", True):
                api_mapper.start()

            # ── 3. Site Crawler ──────────────────────────────
            screenshot_dir = None
            if self.screenshot:
                screenshot_dir = self._config.get("screenshots", {}).get(
                    "output_dir", "data/discovery/screenshots"
                )

            page_timeout = self._crawl_config.get("page_timeout_ms", 30000)
            wait_after = self._crawl_config.get("wait_after_load_ms", 2000)

            try:
                raw_pages = crawl_site(
                    page=page,
                    base_url=self.base_url,
                    max_pages=self.max_pages,
                    max_depth=self.max_depth,
                    strategy=self.strategy,
                    screenshot_dir=screenshot_dir,
                    page_timeout_ms=page_timeout,
                    wait_after_load_ms=wait_after,
                )
            except Exception as e:
                logger.error("discovery_crawl_failed", error=str(e))
                errors.append({"phase": "crawl", "error": str(e)})
                raw_pages = []

            # ── 4 & 5. Classify + Fingerprint each page ─────
            classified_pages: list[PageInfo] = []

            use_llm = self._classify_config.get("llm_enabled", False) and bool(os.getenv("OPENAI_API_KEY"))
            min_confidence = self._classify_config.get("min_confidence", 0.4)

            for pg in raw_pages:
                # Navigate back to this page for DOM analysis
                try:
                    current_url = page.url
                    if current_url != pg.url:
                        page.goto(pg.url, wait_until="domcontentloaded",
                                  timeout=page_timeout)
                        page.wait_for_timeout(1000)

                    api_mapper.set_current_page(pg.url)

                    # Fingerprint: discover DOM components
                    try:
                        components = fingerprint_page(page, pg.url)
                        pg.components = components
                    except Exception as e:
                        logger.debug("fingerprint_failed", url=pg.url, error=str(e))
                        errors.append({"phase": "fingerprint", "url": pg.url, "error": str(e)})

                    # Classify: determine page type
                    try:
                        result = classify_page(
                            page_info=pg,
                            page=page,
                            use_llm=use_llm,
                            min_confidence=min_confidence,
                        )
                        pg.page_type = result.page_type
                        pg.classification_confidence = result.confidence
                        pg.classification_method = result.method
                    except Exception as e:
                        logger.debug("classify_failed", url=pg.url, error=str(e))
                        errors.append({"phase": "classify", "url": pg.url, "error": str(e)})

                except Exception as e:
                    logger.debug("discovery_page_analysis_failed", url=pg.url, error=str(e))
                    errors.append({"phase": "analysis", "url": pg.url, "error": str(e)})

                classified_pages.append(pg)

            # ── 6. Stop API capture ──────────────────────────
            api_endpoints = api_mapper.stop()

            # ── 7. Assemble SiteModel ────────────────────────
            duration = round(time.time() - start_time, 2)

            site_model = SiteModel(
                base_url=self.base_url,
                pages=classified_pages,
                api_endpoints=api_endpoints,
                crawl_strategy=self.strategy,
                crawl_start=crawl_start,
                crawl_end=datetime.utcnow().isoformat(),
                total_duration_seconds=duration,
                errors=errors,
            )

            logger.info("discovery_engine_complete",
                         pages=len(classified_pages),
                         api_endpoints=len(api_endpoints),
                         errors=len(errors),
                         duration_s=duration)

            # Cleanup
            try:
                context.close()
                browser.close()
            except Exception:
                pass

        return site_model
