"""
src/deep_access/log_aggregator.py — Time-correlated log search.

Supports: CloudWatch, Datadog, Elasticsearch, and local file logs.
Correlates logs with test execution timestamps for failure analysis.
"""
from __future__ import annotations

import os, json, time, re, structlog
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta

logger = structlog.get_logger()


@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    message: str
    source: str
    metadata: dict = field(default_factory=dict)


@dataclass
class LogSearchResult:
    entries: list[LogEntry]
    total_count: int
    source: str
    query: str
    time_range_ms: float = 0


class LogAggregator:
    """Multi-source log search with time correlation."""

    def __init__(self):
        self._backends: dict[str, dict] = {}
        self._init_backends()

    def _init_backends(self):
        """Auto-detect available log backends."""
        if os.getenv("AWS_REGION") and os.getenv("CLOUDWATCH_LOG_GROUP"):
            self._backends["cloudwatch"] = {
                "region": os.getenv("AWS_REGION"),
                "log_group": os.getenv("CLOUDWATCH_LOG_GROUP"),
            }

        if os.getenv("DATADOG_API_KEY"):
            self._backends["datadog"] = {
                "api_key": os.getenv("DATADOG_API_KEY"),
                "app_key": os.getenv("DATADOG_APP_KEY", ""),
            }

        if os.getenv("ELASTICSEARCH_URL"):
            self._backends["elasticsearch"] = {
                "url": os.getenv("ELASTICSEARCH_URL"),
                "index": os.getenv("ELASTICSEARCH_INDEX", "logs-*"),
            }

        # Always available: local file logs
        self._backends["local"] = {"log_dir": os.getenv("LOG_DIR", "data/logs")}

        logger.info("log_aggregator_init", backends=list(self._backends.keys()))

    def search(self, query: str, start_time: datetime | None = None,
               end_time: datetime | None = None, source: str = "auto",
               max_results: int = 100) -> LogSearchResult:
        """Search logs across configured backends."""
        if not start_time:
            start_time = datetime.utcnow() - timedelta(hours=1)
        if not end_time:
            end_time = datetime.utcnow()

        if source == "auto":
            # Try each backend in priority order
            for backend_name in ["elasticsearch", "cloudwatch", "datadog", "local"]:
                if backend_name in self._backends:
                    source = backend_name
                    break

        if source == "local":
            return self._search_local(query, start_time, end_time, max_results)
        elif source == "elasticsearch":
            return self._search_elasticsearch(query, start_time, end_time, max_results)
        elif source == "cloudwatch":
            return self._search_cloudwatch(query, start_time, end_time, max_results)
        elif source == "datadog":
            return self._search_datadog(query, start_time, end_time, max_results)
        else:
            return LogSearchResult(entries=[], total_count=0, source=source, query=query)

    def correlate_with_test(self, test_start: datetime, test_end: datetime,
                            keywords: list[str] | None = None) -> list[LogEntry]:
        """Find logs during a test's execution window."""
        # Add buffer: 5s before and after
        start = test_start - timedelta(seconds=5)
        end = test_end + timedelta(seconds=5)

        query = " OR ".join(keywords) if keywords else "error OR exception OR fail"
        result = self.search(query, start_time=start, end_time=end, max_results=200)

        # Filter for errors/warnings
        return [e for e in result.entries if e.level in ("ERROR", "WARN", "CRITICAL")]

    def _search_local(self, query, start_time, end_time, max_results) -> LogSearchResult:
        """Search local log files."""
        log_dir = Path(self._backends.get("local", {}).get("log_dir", "data/logs"))
        entries = []

        if not log_dir.exists():
            return LogSearchResult(entries=[], total_count=0, source="local", query=query)

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        ts_pattern = re.compile(r"(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})")

        for log_file in sorted(log_dir.glob("*.log"), reverse=True)[:10]:
            try:
                for line in log_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if not pattern.search(line):
                        continue

                    # Extract timestamp
                    ts_match = ts_pattern.search(line)
                    if ts_match:
                        try:
                            ts = datetime.fromisoformat(ts_match.group(1).replace(" ", "T"))
                            if ts < start_time or ts > end_time:
                                continue
                        except ValueError:
                            ts = datetime.utcnow()
                    else:
                        ts = datetime.utcnow()

                    # Detect log level
                    level = "INFO"
                    for lvl in ("ERROR", "WARN", "CRITICAL", "DEBUG"):
                        if lvl in line.upper():
                            level = lvl
                            break

                    entries.append(LogEntry(
                        timestamp=ts, level=level, message=line.strip(),
                        source=str(log_file.name),
                    ))

                    if len(entries) >= max_results:
                        break
            except Exception:
                continue

            if len(entries) >= max_results:
                break

        return LogSearchResult(
            entries=entries, total_count=len(entries),
            source="local", query=query,
        )

    def _search_elasticsearch(self, query, start_time, end_time, max_results) -> LogSearchResult:
        """Search Elasticsearch."""
        cfg = self._backends.get("elasticsearch", {})
        try:
            from elasticsearch import Elasticsearch
            es = Elasticsearch(cfg["url"])
            body = {
                "query": {
                    "bool": {
                        "must": [{"query_string": {"query": query}}],
                        "filter": [{"range": {"@timestamp": {
                            "gte": start_time.isoformat(),
                            "lte": end_time.isoformat(),
                        }}}],
                    }
                },
                "size": max_results,
                "sort": [{"@timestamp": "desc"}],
            }
            result = es.search(index=cfg.get("index", "logs-*"), body=body)
            entries = [
                LogEntry(
                    timestamp=datetime.fromisoformat(
                        hit["_source"].get("@timestamp", "").replace("Z", "")),
                    level=hit["_source"].get("level", "INFO"),
                    message=hit["_source"].get("message", ""),
                    source="elasticsearch",
                    metadata=hit["_source"],
                )
                for hit in result.get("hits", {}).get("hits", [])
            ]
            return LogSearchResult(
                entries=entries, total_count=result.get("hits", {}).get("total", {}).get("value", 0),
                source="elasticsearch", query=query,
            )
        except Exception as e:
            logger.error("elasticsearch_search_failed", error=str(e))
            return LogSearchResult(entries=[], total_count=0, source="elasticsearch", query=query)

    def _search_cloudwatch(self, query, start_time, end_time, max_results) -> LogSearchResult:
        """Search AWS CloudWatch Logs."""
        cfg = self._backends.get("cloudwatch", {})
        try:
            import boto3
            client = boto3.client("logs", region_name=cfg.get("region"))
            response = client.filter_log_events(
                logGroupName=cfg["log_group"],
                startTime=int(start_time.timestamp() * 1000),
                endTime=int(end_time.timestamp() * 1000),
                filterPattern=query,
                limit=max_results,
            )
            entries = [
                LogEntry(
                    timestamp=datetime.utcfromtimestamp(e["timestamp"] / 1000),
                    level="INFO", message=e.get("message", ""),
                    source=e.get("logStreamName", "cloudwatch"),
                )
                for e in response.get("events", [])
            ]
            return LogSearchResult(
                entries=entries, total_count=len(entries),
                source="cloudwatch", query=query,
            )
        except Exception as e:
            logger.error("cloudwatch_search_failed", error=str(e))
            return LogSearchResult(entries=[], total_count=0, source="cloudwatch", query=query)

    def _search_datadog(self, query, start_time, end_time, max_results) -> LogSearchResult:
        """Search Datadog Logs."""
        cfg = self._backends.get("datadog", {})
        try:
            import requests as req_lib
            resp = req_lib.post(
                "https://api.datadoghq.com/api/v2/logs/events/search",
                headers={
                    "DD-API-KEY": cfg["api_key"],
                    "DD-APPLICATION-KEY": cfg.get("app_key", ""),
                },
                json={
                    "filter": {
                        "query": query,
                        "from": start_time.isoformat() + "Z",
                        "to": end_time.isoformat() + "Z",
                    },
                    "page": {"limit": max_results},
                },
                timeout=30,
            )
            data = resp.json()
            entries = [
                LogEntry(
                    timestamp=datetime.fromisoformat(
                        e.get("attributes", {}).get("timestamp", "").replace("Z", "")),
                    level=e.get("attributes", {}).get("status", "INFO").upper(),
                    message=e.get("attributes", {}).get("message", ""),
                    source="datadog",
                )
                for e in data.get("data", [])
            ]
            return LogSearchResult(
                entries=entries, total_count=len(entries),
                source="datadog", query=query,
            )
        except Exception as e:
            logger.error("datadog_search_failed", error=str(e))
            return LogSearchResult(entries=[], total_count=0, source="datadog", query=query)

    def available_backends(self) -> list[str]:
        return list(self._backends.keys())
