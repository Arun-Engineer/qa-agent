"""
Phase 5 · Figma Parser
Figma MCP integration: parse screen flows, extract component specs,
design tokens, and screen hierarchy for test generation.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class DesignToken:
    """Extracted design token from Figma styles."""
    name: str
    token_type: str  # "color", "typography", "spacing", "shadow", "border-radius"
    value: Any
    raw_style: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComponentSpec:
    """Parsed Figma component with properties and variants."""
    component_id: str
    name: str
    description: str = ""
    width: float = 0.0
    height: float = 0.0
    properties: Dict[str, Any] = field(default_factory=dict)
    variants: List[Dict[str, str]] = field(default_factory=list)
    children: List["ComponentSpec"] = field(default_factory=list)
    styles: Dict[str, Any] = field(default_factory=dict)
    bounding_box: Dict[str, float] = field(default_factory=dict)
    interactions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ScreenNode:
    """A single screen/frame in a Figma file."""
    node_id: str
    name: str
    screen_type: str = "frame"  # "frame", "component", "section"
    width: float = 0.0
    height: float = 0.0
    components: List[ComponentSpec] = field(default_factory=list)
    transitions: List["ScreenTransition"] = field(default_factory=list)
    thumbnail_url: Optional[str] = None


@dataclass
class ScreenTransition:
    """Navigation flow between screens."""
    source_screen: str
    target_screen: str
    trigger: str = "click"  # "click", "swipe", "hover", "timer"
    trigger_element: Optional[str] = None
    animation: str = "instant"  # "instant", "slide_left", "dissolve", etc.


@dataclass
class FigmaFileData:
    """Complete parsed Figma file with screens, flows, and tokens."""
    file_key: str
    file_name: str
    screens: List[ScreenNode] = field(default_factory=list)
    flows: List[ScreenTransition] = field(default_factory=list)
    tokens: List[DesignToken] = field(default_factory=list)
    components_map: Dict[str, ComponentSpec] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


class FigmaParser:
    """
    Figma REST API parser. Extracts screen flows, component specs,
    and design tokens for automated test generation.
    """

    API_BASE = "https://api.figma.com/v1"

    def __init__(self, access_token: str):
        self._token = access_token
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-Figma-Token": self._token},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── core API calls ─────────────────────────────────────────

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.API_BASE}/{path}"
        async with session.get(url, params=params) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise FigmaParserError(f"Figma API {resp.status}: {body[:300]}")
            return await resp.json()

    async def get_file(self, file_key: str, depth: int = 3) -> Dict[str, Any]:
        return await self._get(f"files/{file_key}", {"depth": depth})

    async def get_file_nodes(self, file_key: str, node_ids: List[str]) -> Dict[str, Any]:
        ids = ",".join(node_ids)
        return await self._get(f"files/{file_key}/nodes", {"ids": ids})

    async def get_images(
        self, file_key: str, node_ids: List[str],
        format: str = "png", scale: float = 2.0,
    ) -> Dict[str, str]:
        ids = ",".join(node_ids)
        data = await self._get(
            f"images/{file_key}",
            {"ids": ids, "format": format, "scale": scale},
        )
        return data.get("images", {})

    async def get_styles(self, file_key: str) -> Dict[str, Any]:
        return await self._get(f"files/{file_key}/styles")

    async def get_components(self, file_key: str) -> Dict[str, Any]:
        return await self._get(f"files/{file_key}/components")

    # ── high-level parsing ─────────────────────────────────────

    async def parse_file(self, file_key: str) -> FigmaFileData:
        """Full parse: screens, flows, components, tokens."""
        raw = await self.get_file(file_key, depth=4)
        file_name = raw.get("name", file_key)
        document = raw.get("document", {})

        screens: List[ScreenNode] = []
        components_map: Dict[str, ComponentSpec] = {}
        flows: List[ScreenTransition] = []

        # Walk top-level pages
        for page in document.get("children", []):
            for node in page.get("children", []):
                if node.get("type") in ("FRAME", "COMPONENT", "SECTION"):
                    screen = self._parse_screen(node)
                    screens.append(screen)
                    # Extract components within
                    self._extract_components(node, components_map)
                    # Extract prototype transitions
                    self._extract_transitions(node, flows)

        # Parse design tokens from styles
        tokens = await self._extract_tokens(file_key, raw)

        # Fetch thumbnails for screens
        if screens:
            try:
                images = await self.get_images(
                    file_key, [s.node_id for s in screens],
                )
                for screen in screens:
                    screen.thumbnail_url = images.get(screen.node_id)
            except Exception as e:
                logger.warning("Could not fetch thumbnails: %s", e)

        return FigmaFileData(
            file_key=file_key,
            file_name=file_name,
            screens=screens,
            flows=flows,
            tokens=tokens,
            components_map=components_map,
            raw=raw,
        )

    async def parse_screen_flows(self, file_key: str) -> List[ScreenTransition]:
        """Extract only the navigation flows from a Figma file."""
        data = await self.parse_file(file_key)
        return data.flows

    async def parse_component_specs(self, file_key: str) -> Dict[str, ComponentSpec]:
        """Extract only component specifications."""
        data = await self.parse_file(file_key)
        return data.components_map

    # ── screen parsing ─────────────────────────────────────────

    def _parse_screen(self, node: Dict[str, Any]) -> ScreenNode:
        abs_box = node.get("absoluteBoundingBox", {})
        return ScreenNode(
            node_id=node.get("id", ""),
            name=node.get("name", "Unnamed"),
            screen_type=node.get("type", "FRAME").lower(),
            width=abs_box.get("width", 0),
            height=abs_box.get("height", 0),
        )

    # ── component extraction ───────────────────────────────────

    def _extract_components(
        self, node: Dict[str, Any], out: Dict[str, ComponentSpec],
    ) -> None:
        if node.get("type") in ("COMPONENT", "COMPONENT_SET", "INSTANCE"):
            spec = self._parse_component(node)
            out[spec.component_id] = spec

        for child in node.get("children", []):
            self._extract_components(child, out)

    def _parse_component(self, node: Dict[str, Any]) -> ComponentSpec:
        abs_box = node.get("absoluteBoundingBox", {})
        # Extract variant properties from component set
        variants = []
        if node.get("type") == "COMPONENT_SET":
            for child in node.get("children", []):
                props = self._parse_variant_name(child.get("name", ""))
                if props:
                    variants.append(props)

        # Extract interactions / prototype connections
        interactions = []
        for reaction in node.get("reactions", []):
            trigger = reaction.get("trigger", {})
            action = reaction.get("action", {})
            interactions.append({
                "trigger_type": trigger.get("type", "ON_CLICK"),
                "action_type": action.get("type", "NAVIGATE"),
                "destination": action.get("destinationId"),
                "transition": action.get("transition", {}).get("type", "INSTANT"),
            })

        # Extract inline styles
        styles = {}
        fills = node.get("fills", [])
        if fills:
            for fill in fills:
                if fill.get("type") == "SOLID" and fill.get("visible", True):
                    c = fill.get("color", {})
                    styles["background_color"] = self._rgba_to_hex(c)
        if node.get("cornerRadius"):
            styles["border_radius"] = node["cornerRadius"]
        if node.get("strokes"):
            styles["has_border"] = True

        # Parse children recursively
        child_specs = []
        for child in node.get("children", []):
            if child.get("type") in ("COMPONENT", "INSTANCE", "FRAME", "GROUP"):
                child_specs.append(self._parse_component(child))

        return ComponentSpec(
            component_id=node.get("id", ""),
            name=node.get("name", ""),
            description=node.get("description", ""),
            width=abs_box.get("width", 0),
            height=abs_box.get("height", 0),
            properties=node.get("componentProperties", {}),
            variants=variants,
            children=child_specs,
            styles=styles,
            bounding_box=abs_box,
            interactions=interactions,
        )

    @staticmethod
    def _parse_variant_name(name: str) -> Dict[str, str]:
        """Parse 'Property=Value, Property2=Value2' format."""
        props = {}
        for part in name.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                props[k.strip()] = v.strip()
        return props

    # ── transition / prototype flow extraction ─────────────────

    def _extract_transitions(
        self, node: Dict[str, Any], out: List[ScreenTransition],
    ) -> None:
        for reaction in node.get("reactions", []):
            action = reaction.get("action", {})
            trigger = reaction.get("trigger", {})
            dest = action.get("destinationId")
            if dest and action.get("type") == "NAVIGATE":
                out.append(ScreenTransition(
                    source_screen=node.get("id", ""),
                    target_screen=dest,
                    trigger=self._map_trigger(trigger.get("type", "")),
                    trigger_element=node.get("name"),
                    animation=self._map_transition(
                        action.get("transition", {}).get("type", "")
                    ),
                ))

        for child in node.get("children", []):
            self._extract_transitions(child, out)

    @staticmethod
    def _map_trigger(figma_trigger: str) -> str:
        mapping = {
            "ON_CLICK": "click",
            "ON_HOVER": "hover",
            "ON_PRESS": "press",
            "ON_DRAG": "swipe",
            "AFTER_TIMEOUT": "timer",
            "MOUSE_ENTER": "hover",
            "MOUSE_LEAVE": "hover_exit",
        }
        return mapping.get(figma_trigger, figma_trigger.lower())

    @staticmethod
    def _map_transition(figma_transition: str) -> str:
        mapping = {
            "INSTANT_TRANSITION": "instant",
            "DISSOLVE": "dissolve",
            "SLIDE_IN": "slide_left",
            "SLIDE_OUT": "slide_right",
            "PUSH": "push",
            "MOVE_IN": "slide_up",
            "MOVE_OUT": "slide_down",
            "SMART_ANIMATE": "smart_animate",
        }
        return mapping.get(figma_transition, figma_transition.lower())

    # ── design token extraction ────────────────────────────────

    async def _extract_tokens(
        self, file_key: str, raw: Dict[str, Any],
    ) -> List[DesignToken]:
        tokens: List[DesignToken] = []

        # Inline style references
        styles_meta = raw.get("styles", {})
        for style_id, meta in styles_meta.items():
            style_type = meta.get("styleType", "")
            name = meta.get("name", style_id)

            if style_type == "FILL":
                tokens.append(DesignToken(
                    name=name, token_type="color",
                    value=meta.get("description", ""), raw_style=meta,
                ))
            elif style_type == "TEXT":
                tokens.append(DesignToken(
                    name=name, token_type="typography",
                    value=meta.get("description", ""), raw_style=meta,
                ))
            elif style_type == "EFFECT":
                tokens.append(DesignToken(
                    name=name, token_type="shadow",
                    value=meta.get("description", ""), raw_style=meta,
                ))
            elif style_type == "GRID":
                tokens.append(DesignToken(
                    name=name, token_type="spacing",
                    value=meta.get("description", ""), raw_style=meta,
                ))

        # Walk document for typography details
        self._walk_for_text_tokens(raw.get("document", {}), tokens)

        return tokens

    def _walk_for_text_tokens(self, node: Dict, tokens: List[DesignToken]) -> None:
        if node.get("type") == "TEXT":
            style = node.get("style", {})
            if style:
                tokens.append(DesignToken(
                    name=node.get("name", "text"),
                    token_type="typography",
                    value={
                        "fontFamily": style.get("fontFamily"),
                        "fontSize": style.get("fontSize"),
                        "fontWeight": style.get("fontWeight"),
                        "lineHeight": style.get("lineHeightPx"),
                        "letterSpacing": style.get("letterSpacing"),
                    },
                    raw_style=style,
                ))
        for child in node.get("children", []):
            self._walk_for_text_tokens(child, tokens)

    # ── helpers ────────────────────────────────────────────────

    @staticmethod
    def _rgba_to_hex(color: Dict[str, float]) -> str:
        r = int(color.get("r", 0) * 255)
        g = int(color.get("g", 0) * 255)
        b = int(color.get("b", 0) * 255)
        a = color.get("a", 1.0)
        if a < 1.0:
            return f"rgba({r},{g},{b},{a:.2f})"
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── test helper: generate selectors from components ────────

    def generate_test_selectors(
        self, components: Dict[str, ComponentSpec],
    ) -> Dict[str, str]:
        """
        Heuristic: generate CSS/accessibility selectors from Figma component names.
        Useful for bridging design → test automation.
        """
        selectors = {}
        for comp_id, spec in components.items():
            # Convert Figma name to selector-friendly format
            name = spec.name.lower()
            name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")

            if "button" in name or "btn" in name:
                selectors[spec.name] = f"button[data-testid='{name}'], [role='button'][aria-label*='{spec.name}']"
            elif "input" in name or "field" in name or "text" in name:
                selectors[spec.name] = f"input[data-testid='{name}'], [aria-label*='{spec.name}']"
            elif "nav" in name or "menu" in name:
                selectors[spec.name] = f"nav[data-testid='{name}'], [role='navigation']"
            elif "card" in name:
                selectors[spec.name] = f"[data-testid='{name}'], .{name}"
            elif "modal" in name or "dialog" in name:
                selectors[spec.name] = f"[role='dialog'][data-testid='{name}']"
            else:
                selectors[spec.name] = f"[data-testid='{name}']"

        return selectors


class FigmaParserError(Exception):
    pass