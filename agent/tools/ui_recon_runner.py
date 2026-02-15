import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser

from agent.tools.capability_probing import probe_capabilities


@dataclass
class PageInfo:
    url: str
    title: str
    links: List[str]
    forms: List[Dict]
    actions: List[Dict]


def _same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlparse(a), urlparse(b)
        return pa.scheme == pb.scheme and pa.netloc == pb.netloc
    except Exception:
        return False


def _clean_url(u: str) -> str:
    if not u:
        return u
    return u.split("#")[0].rstrip("/")


def _classify_url(u: str) -> str:
    lu = (u or "").lower()
    if any(k in lu for k in ["/login", "signin", "sign-in", "auth"]):
        return "login"
    if any(k in lu for k in ["/cart", "basket", "bag"]):
        return "cart"
    if any(k in lu for k in ["/checkout", "payment", "shipping"]):
        return "checkout"
    if any(k in lu for k in ["/product", "/p/", "item"]):
        return "product"
    if "search" in lu:
        return "search"
    if any(k in lu for k in ["/signup", "register", "sign-up"]):
        return "signup"
    return "generic"


def _probe_reasons_str(probe: Dict) -> str:
    reasons = probe.get("reasons")
    if isinstance(reasons, list) and reasons:
        return ", ".join(str(x) for x in reasons)
    # backward compat if any older probe returns "reason"
    if probe.get("reason"):
        return str(probe["reason"])
    return "unknown"


class _HTMLCollector(HTMLParser):
    """
    Requests-only degraded extraction:
    - links from <a href>
    - title from <title>
    - basic form inputs from <form> + input/select/textarea
    - basic actions from <button>, <input type=submit>, <a role=button>
    """
    def __init__(self):
        super().__init__()
        self.links: List[str] = []
        self._in_title = False
        self.title_parts: List[str] = []

        self._in_form = False
        self._current_form_inputs: List[Dict] = []
        self.forms: List[Dict] = []

        self._in_button = False
        self._button_text_parts: List[str] = []
        self.actions: List[Dict] = []

        self._label_for: str = ""
        self._in_label = False
        self._label_text_parts: List[str] = []
        self.label_map: Dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs or [])
        t = (tag or "").lower()

        if t == "title":
            self._in_title = True

        if t == "a":
            href = a.get("href")
            if href:
                self.links.append(href.strip())

            role = (a.get("role") or "").lower()
            if role == "button":
                txt = (a.get("aria-label") or a.get("title") or "").strip()
                if txt:
                    self.actions.append({"tag": "a", "text": txt[:60]})

        if t == "form":
            self._in_form = True
            self._current_form_inputs = []

        if t in ("input", "textarea", "select") and self._in_form:
            tag_name = t
            el_type = (a.get("type") or tag_name).lower()
            name = (a.get("name") or "").strip()
            el_id = (a.get("id") or "").strip()
            placeholder = (a.get("placeholder") or "").strip()
            aria = (a.get("aria-label") or "").strip()
            required = "required" in a
            minlength = a.get("minlength")
            maxlength = a.get("maxlength")
            pattern = (a.get("pattern") or "").strip()

            label = ""
            if el_id and el_id in self.label_map:
                label = self.label_map[el_id]

            self._current_form_inputs.append({
                "tag": tag_name,
                "type": el_type,
                "name": name,
                "id": el_id,
                "placeholder": placeholder,
                "aria_label": aria,
                "label": label,
                "required": required,
                "minlength": minlength,
                "maxlength": maxlength,
                "pattern": pattern,
            })

            # submit button via input
            if tag_name == "input" and el_type in ("submit", "button"):
                txt = (a.get("value") or a.get("aria-label") or "Submit").strip()
                self.actions.append({"tag": "input", "text": txt[:60]})

        if t == "button":
            self._in_button = True
            self._button_text_parts = []
            # sometimes buttons only have aria-label
            txt = (a.get("aria-label") or "").strip()
            if txt:
                self.actions.append({"tag": "button", "text": txt[:60]})

        if t == "label":
            self._in_label = True
            self._label_text_parts = []
            self._label_for = (a.get("for") or "").strip()

    def handle_endtag(self, tag):
        t = (tag or "").lower()

        if t == "title":
            self._in_title = False

        if t == "form":
            if self._in_form:
                self.forms.append({"index": len(self.forms), "inputs": self._current_form_inputs})
            self._in_form = False
            self._current_form_inputs = []

        if t == "button":
            if self._in_button:
                txt = " ".join(x.strip() for x in self._button_text_parts if x.strip()).strip()
                if txt:
                    self.actions.append({"tag": "button", "text": txt[:60]})
            self._in_button = False
            self._button_text_parts = []

        if t == "label":
            if self._in_label:
                txt = " ".join(x.strip() for x in self._label_text_parts if x.strip()).strip()
                if txt and self._label_for:
                    self.label_map[self._label_for] = txt[:80]
            self._in_label = False
            self._label_for = ""
            self._label_text_parts = []

    def handle_data(self, data):
        if not data:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._in_button:
            self._button_text_parts.append(data)
        if self._in_label:
            self._label_text_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(x.strip() for x in self.title_parts if x.strip()).strip()


def _requests_fetch(url: str, timeout: int = 12) -> str:
    try:
        import requests
    except Exception:
        return ""

    try:
        r = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        if r.status_code >= 400:
            return ""
        return r.text or ""
    except Exception:
        return ""


def run_recon(
    base_url: str,
    max_pages: int = 25,
    max_depth: int = 2,
    timeout_ms: int = 20000,
) -> Dict:
    """
    Crawls same-origin pages, extracts:
    - links
    - forms (inputs with constraints)
    - actions (buttons/links)
    Writes model to data/site_models/<domain>.json and returns compact summary.
    """
    probe = probe_capabilities(base_url)

    # If probing says "skip", don't waste time
    if not probe.get("ok"):
        reason = _probe_reasons_str(probe)
        return {
            "status": "skipped",
            "summary": f"Recon skipped: {reason}",
            "model_path": None,
            "model-path": None,  # backward compat
            "pages_crawled": 0,
            "probe": probe,
        }

    # optionally degrade limits if probe suggests
    if probe.get("mode") == "degraded":
        max_pages = min(max_pages, int(probe.get("max_pages", 5) or 5))
        max_depth = min(max_depth, int(probe.get("max_depth", 1) or 1))

    base_url = (base_url or "").strip().rstrip("/")

    out_dir = Path("data/site_models")
    out_dir.mkdir(parents=True, exist_ok=True)

    domain = urlparse(base_url).netloc.replace(":", "_") or "site"
    model_path = out_dir / f"{domain}.json"

    seen: Set[str] = set()
    queue: List[Tuple[str, int]] = [(base_url, 0)]
    pages: List[PageInfo] = []

    def _clean_url(u: str) -> str:
        return (u or "").strip().split("#")[0].rstrip("/")

    # ---------- FULL MODE: Playwright crawl ----------
    def _crawl_playwright():
        # import inside (critical) so module doesn't crash if playwright isn't available
        from playwright.sync_api import sync_playwright  # type: ignore

        def js_extract():
            return """
            () => {
              const norm = (s) => (s || "").toString().trim();
              const uniq = (arr) => Array.from(new Set(arr));

              const links = uniq(Array.from(document.querySelectorAll("a[href]"))
                .map(a => a.getAttribute("href"))
                .filter(Boolean)
                .map(h => h.trim()));

              const forms = Array.from(document.querySelectorAll("form")).map((f, idx) => {
                const inputs = Array.from(f.querySelectorAll("input, textarea, select")).map(el => {
                  const tag = el.tagName.toLowerCase();
                  const type = (el.getAttribute("type") || tag).toLowerCase();
                  const name = norm(el.getAttribute("name"));
                  const id = norm(el.getAttribute("id"));
                  const placeholder = norm(el.getAttribute("placeholder"));
                  const aria = norm(el.getAttribute("aria-label"));
                  const required = el.hasAttribute("required");
                  const minlength = el.getAttribute("minlength");
                  const maxlength = el.getAttribute("maxlength");
                  const pattern = norm(el.getAttribute("pattern"));

                  let label = "";
                  if (id) {
                    const l = document.querySelector(`label[for="${id}"]`);
                    if (l) label = norm(l.innerText);
                  }
                  if (!label) {
                    const parentLabel = el.closest("label");
                    if (parentLabel) label = norm(parentLabel.innerText);
                  }

                  return { tag, type, name, id, placeholder, aria_label: aria, label, required, minlength, maxlength, pattern };
                });

                return { index: idx, inputs };
              });

              const actions = uniq(Array.from(document.querySelectorAll("button, a[role='button'], input[type='submit']"))
                .map(el => {
                  const tag = el.tagName.toLowerCase();
                  const text = norm(el.innerText) || norm(el.getAttribute("value")) || norm(el.getAttribute("aria-label"));
                  return JSON.stringify({ tag, text });
                }))
                .map(s => { try { return JSON.parse(s) } catch { return null } })
                .filter(Boolean)
                .filter(a => a.text && a.text.length <= 60);

              return { links, forms, actions };
            }
            """

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            while queue and len(seen) < max_pages:
                url, depth = queue.pop(0)
                url = _clean_url(url)

                if url in seen:
                    continue
                if not _same_origin(base_url, url):
                    continue
                if depth > max_depth:
                    continue

                seen.add(url)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    title = page.title() or ""
                    data = page.evaluate(js_extract())
                except Exception:
                    continue

                raw_links = data.get("links") or []
                abs_links: List[str] = []
                for h in raw_links:
                    if re.match(r"^(mailto:|tel:|javascript:)", h, re.I):
                        continue
                    abs_links.append(_clean_url(urljoin(url, h)))

                forms = data.get("forms") or []
                actions = data.get("actions") or []

                pages.append(PageInfo(url=url, title=title, links=abs_links[:80], forms=forms[:10], actions=actions[:30]))

                for nxt in abs_links:
                    if nxt not in seen and _same_origin(base_url, nxt):
                        queue.append((nxt, depth + 1))

            context.close()
            browser.close()

    # ---------- DEGRADED MODE: requests-only crawl ----------
    def _crawl_requests():
        while queue and len(seen) < max_pages:
            url, depth = queue.pop(0)
            url = _clean_url(url)

            if url in seen:
                continue
            if not _same_origin(base_url, url):
                continue
            if depth > max_depth:
                continue

            seen.add(url)

            html = _requests_fetch(url)
            if not html:
                continue

            parser = _HTMLCollector()
            try:
                parser.feed(html)
            except Exception:
                continue

            raw_links = parser.links or []
            abs_links: List[str] = []
            for h in raw_links:
                if re.match(r"^(mailto:|tel:|javascript:)", h, re.I):
                    continue
                abs_links.append(_clean_url(urljoin(url, h)))

            title = parser.title or ""
            forms = (parser.forms or [])[:10]
            actions = (parser.actions or [])[:30]

            pages.append(PageInfo(url=url, title=title, links=abs_links[:80], forms=forms, actions=actions))

            for nxt in abs_links:
                if nxt not in seen and _same_origin(base_url, nxt):
                    queue.append((nxt, depth + 1))

    # Decide crawl mode
    mode = probe.get("mode") or "full"
    if mode == "full":
        try:
            _crawl_playwright()
        except Exception:
            # if playwright blows up anyway, degrade to requests
            _crawl_requests()
    else:
        _crawl_requests()

    # write model
    model = {
        "base_url": base_url,
        "domain": domain,
        "mode": mode,
        "probe": probe,
        "pages": [
            {
                "url": pg.url,
                "type": _classify_url(pg.url),
                "title": pg.title,
                "forms": pg.forms,
                "actions": pg.actions,
                "links": pg.links,
            }
            for pg in pages
        ],
    }
    model_path.write_text(json.dumps(model, indent=2), encoding="utf-8")

    # compact summary to feed planner (LLM-friendly)
    summary_lines = [f"DISCOVERY: {base_url}", f"PAGES_FOUND: {len(pages)}", f"MODE: {mode}"]
    for pg in pages[:12]:
        ptype = _classify_url(pg.url)
        summary_lines.append(f"- [{ptype}] {pg.url} | title={pg.title[:60]!r}")
        if pg.forms:
            inputs = pg.forms[0].get("inputs") or []
            fields = []
            for inp in inputs[:8]:
                label = inp.get("label") or inp.get("aria_label") or inp.get("placeholder") or inp.get("name") or inp.get("id") or ""
                ftype = inp.get("type") or "input"
                fields.append(f"{label}<{ftype}>")
            if fields:
                summary_lines.append(f"  form_fields: {', '.join(fields)}")
        if pg.actions:
            act = [a.get("text") for a in pg.actions[:6] if a.get("text")]
            if act:
                summary_lines.append(f"  actions: {', '.join(act)}")

    return {
        "status": "ok",
        "model_path": str(model_path),
        "model-path": str(model_path),  # backward compat (your older code used this)
        "summary": "\n".join(summary_lines),
        "pages_crawled": len(pages),
        "probe": probe,
        "mode": mode,
    }
