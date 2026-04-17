"""prompts/registry.py — Prompt Template Registry"""
from __future__ import annotations
import copy, structlog
from collections import defaultdict
from src.rag.prompts.templates import BUILTIN_TEMPLATES, PromptTemplate, PromptType
logger = structlog.get_logger()

class PromptRegistry:
    def __init__(self, load_builtins: bool = True):
        self._templates: dict[str, dict[str, PromptTemplate]] = defaultdict(dict)
        self._latest: dict[str, str] = {}
        if load_builtins:
            for t in BUILTIN_TEMPLATES: self.register(t)

    def register(self, template: PromptTemplate) -> None:
        self._templates[template.name][template.version] = template
        cur = self._latest.get(template.name, "0.0.0")
        if self._version_gt(template.version, cur): self._latest[template.name] = template.version

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        if name not in self._templates: raise KeyError(f"Prompt '{name}' not found. Available: {list(self._templates.keys())}")
        versions = self._templates[name]
        if version:
            if version not in versions: raise KeyError(f"Version '{version}' not found for '{name}'")
            return copy.deepcopy(versions[version])
        latest = self._latest.get(name)
        if latest and latest in versions and versions[latest].is_active: return copy.deepcopy(versions[latest])
        for v in sorted(versions.keys(), reverse=True):
            if versions[v].is_active: return copy.deepcopy(versions[v])
        raise KeyError(f"No active version for '{name}'")

    def get_by_type(self, prompt_type: PromptType) -> list[PromptTemplate]:
        results = []
        for name in self._templates:
            latest = self._latest.get(name)
            if latest and latest in self._templates[name]:
                t = self._templates[name][latest]
                if t.type == prompt_type and t.is_active: results.append(copy.deepcopy(t))
        return results

    def list_templates(self) -> list[dict]:
        result = []
        for name, versions in self._templates.items():
            latest = self._latest.get(name, "")
            result.append({"name": name, "latest_version": latest, "all_versions": sorted(versions.keys()),
                           "type": versions[latest].type.value if latest in versions else "unknown",
                           "is_active": versions[latest].is_active if latest in versions else False})
        return result

    def deactivate(self, name: str, version: str) -> bool:
        if name in self._templates and version in self._templates[name]:
            self._templates[name][version].is_active = False; return True
        return False

    @staticmethod
    def _version_gt(a: str, b: str) -> bool:
        def p(v):
            try: return tuple(int(x) for x in v.split("."))
            except: return (0,)
        return p(a) > p(b)

    @property
    def count(self) -> int: return sum(len(v) for v in self._templates.values())
