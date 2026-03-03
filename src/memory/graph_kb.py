"""
src/memory/graph_kb.py — Knowledge Graph for QA relationships.

Stores: Page → Component → API → DB relationships.
Used for: blast radius queries, impact analysis, test prioritization.

Supports:
  - Neo4j (if NEO4J_URI configured)
  - In-memory graph fallback
"""
from __future__ import annotations

import os, structlog
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

logger = structlog.get_logger()


@dataclass
class GraphNode:
    id: str
    type: str       # page | component | api | db_table | test | bug
    properties: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str   # contains | calls | reads | writes | tests | blocks
    properties: dict = field(default_factory=dict)


class GraphKB:
    """QA Knowledge Graph — Neo4j or in-memory."""

    def __init__(self):
        self._neo4j = None
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []
        self._adj: dict[str, list[str]] = defaultdict(list)  # adjacency list
        self._init_backend()

    def _init_backend(self):
        neo4j_uri = (os.getenv("NEO4J_URI") or "").strip()
        if neo4j_uri:
            try:
                from neo4j import GraphDatabase
                user = os.getenv("NEO4J_USER", "neo4j")
                pwd = os.getenv("NEO4J_PASSWORD", "password")
                self._neo4j = GraphDatabase.driver(neo4j_uri, auth=(user, pwd))
                self._neo4j.verify_connectivity()
                logger.info("graph_kb_init", backend="neo4j", uri=neo4j_uri)
                return
            except ImportError:
                logger.warning("neo4j driver not installed, using in-memory")
            except Exception as e:
                logger.warning("neo4j_connect_failed", error=str(e))

        logger.info("graph_kb_init", backend="in_memory")

    def add_node(self, node_id: str, node_type: str, **properties) -> GraphNode:
        node = GraphNode(id=node_id, type=node_type, properties=properties)

        if self._neo4j:
            try:
                with self._neo4j.session() as session:
                    props = {k: str(v) for k, v in properties.items()}
                    session.run(
                        f"MERGE (n:{node_type} {{id: $id}}) SET n += $props",
                        id=node_id, props=props,
                    )
            except Exception as e:
                logger.error("neo4j_add_node_failed", error=str(e))

        self._nodes[node_id] = node
        return node

    def add_edge(self, source: str, target: str, relation: str, **properties) -> GraphEdge:
        edge = GraphEdge(source=source, target=target, relation=relation, properties=properties)

        if self._neo4j:
            try:
                with self._neo4j.session() as session:
                    session.run(
                        f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) "
                        f"MERGE (a)-[r:{relation.upper()}]->(b) SET r += $props",
                        src=source, tgt=target, props={k: str(v) for k, v in properties.items()},
                    )
            except Exception as e:
                logger.error("neo4j_add_edge_failed", error=str(e))

        self._edges.append(edge)
        self._adj[source].append(target)
        return edge

    def get_node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def get_neighbors(self, node_id: str, relation: str | None = None) -> list[str]:
        """Get all connected node IDs, optionally filtered by relation."""
        if relation:
            return [e.target for e in self._edges
                    if e.source == node_id and e.relation == relation]
        return self._adj.get(node_id, [])

    def blast_radius(self, node_id: str, max_depth: int = 3) -> set[str]:
        """Find all nodes affected by a change to the given node (BFS)."""
        visited = set()
        queue = [(node_id, 0)]

        while queue:
            current, depth = queue.pop(0)
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            for neighbor in self._adj.get(current, []):
                if neighbor not in visited:
                    queue.append((neighbor, depth + 1))

        # Also check reverse edges (who depends on this node)
        for edge in self._edges:
            if edge.target == node_id and edge.source not in visited:
                visited.add(edge.source)

        return visited

    def build_from_site_model(self, site_model: dict) -> dict:
        """Build graph from Phase 2 discovery site model."""
        stats = {"pages": 0, "apis": 0, "forms": 0, "edges": 0}

        base_url = site_model.get("base_url", "")

        for page in site_model.get("pages", []):
            url = page.get("url", "")
            page_id = f"page:{url}"
            self.add_node(page_id, "page", url=url,
                          page_type=page.get("page_type", "unknown"),
                          title=page.get("title", ""))
            stats["pages"] += 1

            # Forms on page
            for form in page.get("forms", []):
                form_id = f"form:{url}:{form.get('action', 'unknown')}"
                self.add_node(form_id, "component", component_type="form",
                              action=form.get("action", ""))
                self.add_edge(page_id, form_id, "contains")
                stats["forms"] += 1
                stats["edges"] += 1

            # Links between pages
            for link in page.get("links", []):
                href = link.get("href", "")
                if href.startswith("/") or href.startswith(base_url):
                    target_id = f"page:{href}"
                    self.add_edge(page_id, target_id, "links_to")
                    stats["edges"] += 1

        # API endpoints
        for api in site_model.get("api_endpoints", []):
            api_url = api.get("url", "")
            api_id = f"api:{api.get('method', 'GET')}:{api_url}"
            self.add_node(api_id, "api", method=api.get("method", "GET"),
                          url=api_url, status=api.get("status", ""))
            stats["apis"] += 1

        logger.info("graph_built_from_site_model", **stats)
        return stats

    def get_stats(self) -> dict:
        type_counts = defaultdict(int)
        for node in self._nodes.values():
            type_counts[node.type] += 1
        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "node_types": dict(type_counts),
        }

    def close(self):
        if self._neo4j:
            self._neo4j.close()
