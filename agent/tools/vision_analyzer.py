"""
agent/tools/vision_analyzer.py — Vision AI Analysis Tool

Sends screenshots to GPT-4o (or any vision-capable LLM) for visual analysis.
Supports:
  - Single image analysis
  - Multi-image comparison (e.g., "compare PLP vs PDP for Quick Tags")
  - Element-level analysis (e.g., "which product cards have a badge?")

Usage:
    from agent.tools.vision_analyzer import analyze_image, compare_images

    result = analyze_image(
        base64_image="...",
        question="Which products have a 'Quick Tag' badge?",
    )
"""
from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VisionResult:
    status: str  # "ok" | "error"
    analysis: str = ""
    structured_data: Dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None


def analyze_image(
    base64_image: str,
    question: str,
    context: str = "",
    model: str = "",
    detail: str = "high",
) -> VisionResult:
    """
    Send a single image to GPT-4o vision for analysis.

    Args:
        base64_image: base64-encoded PNG/JPEG
        question: what to analyze
        context: optional extra context (page URL, element type, etc.)
        model: override model (default: gpt-4o)
        detail: "high" | "low" | "auto"
    """
    start = time.time()
    model = model or os.getenv("VISION_MODEL", "gpt-4o")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        system_prompt = _SINGLE_IMAGE_PROMPT
        if context:
            system_prompt += f"\n\nAdditional context:\n{context}"

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                                "detail": detail,
                            },
                        },
                    ],
                },
            ],
            max_tokens=2000,
            temperature=0.2,
        )

        text = (response.choices[0].message.content or "").strip()
        tokens = getattr(response.usage, "total_tokens", 0) if response.usage else 0

        # Try to extract JSON if present
        structured = _try_parse_json(text)

        elapsed = (time.time() - start) * 1000
        return VisionResult(
            status="ok", analysis=text,
            structured_data=structured,
            tokens_used=tokens,
            duration_ms=round(elapsed, 2),
        )

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return VisionResult(
            status="error", error=str(e),
            duration_ms=round(elapsed, 2),
        )


def compare_images(
    images: List[Dict[str, str]],
    question: str,
    context: str = "",
    model: str = "",
) -> VisionResult:
    """
    Send multiple images for comparison analysis.

    Args:
        images: [{"base64": "...", "label": "PLP"}, {"base64": "...", "label": "PDP"}]
        question: what to compare
    """
    start = time.time()
    model = model or os.getenv("VISION_MODEL", "gpt-4o")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Build content array with labels + images
        content = [{"type": "text", "text": question}]
        for img in images:
            label = img.get("label", "Image")
            content.append({"type": "text", "text": f"\n--- {label} ---"})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img['base64']}",
                    "detail": "high",
                },
            })

        system_prompt = _COMPARISON_PROMPT
        if context:
            system_prompt += f"\n\nAdditional context:\n{context}"

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            max_tokens=3000,
            temperature=0.2,
        )

        text = (response.choices[0].message.content or "").strip()
        tokens = getattr(response.usage, "total_tokens", 0) if response.usage else 0
        structured = _try_parse_json(text)

        elapsed = (time.time() - start) * 1000
        return VisionResult(
            status="ok", analysis=text,
            structured_data=structured,
            tokens_used=tokens,
            duration_ms=round(elapsed, 2),
        )

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return VisionResult(
            status="error", error=str(e),
            duration_ms=round(elapsed, 2),
        )


def analyze_elements(
    elements: List[Dict[str, str]],
    question: str,
    model: str = "",
) -> VisionResult:
    """
    Analyze individual element screenshots (e.g., each product card).
    Identifies which elements match a criteria.

    Args:
        elements: [{"base64": "...", "label": "product_0", "text_content": "..."}, ...]
        question: criteria to check (e.g., "Does this product have a Quick Tag badge?")
    """
    start = time.time()
    model = model or os.getenv("VISION_MODEL", "gpt-4o")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Build content: one message with all element images
        content = [{"type": "text", "text": f"Analyze each element below. For each, answer: {question}\n\nRespond with a JSON array where each item has: label, has_match (true/false), details (string)."}]

        for el in elements[:20]:  # Cap at 20 to avoid token limits
            label = el.get("label", "element")
            text = el.get("text_content", "")
            content.append({"type": "text", "text": f"\n--- {label} (text: {text[:100]}) ---"})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{el['base64']}",
                    "detail": "low",  # low detail for many images to save tokens
                },
            })

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _ELEMENT_ANALYSIS_PROMPT},
                {"role": "user", "content": content},
            ],
            max_tokens=3000,
            temperature=0.1,
        )

        text = (response.choices[0].message.content or "").strip()
        tokens = getattr(response.usage, "total_tokens", 0) if response.usage else 0
        structured = _try_parse_json(text)

        elapsed = (time.time() - start) * 1000
        return VisionResult(
            status="ok", analysis=text,
            structured_data=structured,
            tokens_used=tokens,
            duration_ms=round(elapsed, 2),
        )

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return VisionResult(
            status="error", error=str(e),
            duration_ms=round(elapsed, 2),
        )


def _try_parse_json(text: str) -> Dict:
    """Try to extract JSON from LLM response."""
    try:
        # Try direct parse
        return json.loads(text)
    except Exception:
        pass
    try:
        # Try extracting from markdown code block
        import re
        match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except Exception:
        pass
    return {}


# ─── Prompts ───

_SINGLE_IMAGE_PROMPT = """You are a Senior QA Engineer analyzing a screenshot of a web application.

Your job is to identify visual elements, UI components, badges, tags, labels, and any visual anomalies.

Be precise and specific. Reference elements by their position (top-left, center, etc.) and appearance (color, size, text).

When asked about specific elements (like badges, tags, labels), list each instance found with:
- Location on the page
- Text content (if readable)
- Visual appearance (color, shape, size)
- Associated product/item name

If you can provide structured data, include a JSON block with your findings."""

_COMPARISON_PROMPT = """You are a Senior QA Engineer comparing screenshots of different pages in a web application.

Your job is to identify:
1. Elements present on one page but missing from another
2. Visual differences in shared elements (different badges, tags, labels)
3. Inconsistencies in styling or layout
4. Missing or extra content

For each difference found, specify:
- Which page has it and which doesn't
- What the element looks like
- Whether this is likely a bug or intentional

Provide a JSON summary with: {"differences": [...], "consistent_elements": [...], "potential_bugs": [...]}"""

_ELEMENT_ANALYSIS_PROMPT = """You are a QA automation system analyzing individual UI elements from screenshots.

For each element image provided, determine if it matches the criteria specified by the user.

Respond ONLY with a JSON array. Each item:
{
  "label": "element label from input",
  "has_match": true or false,
  "confidence": "high" | "medium" | "low",
  "details": "what you see in this element"
}

Be precise. If unsure, set confidence to "low"."""
