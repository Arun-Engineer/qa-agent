"""
Phase 5 · Jira Adapter
REST v3 + Atlassian MCP integration for Jira Cloud / Server.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

import aiohttp

from .bug_tracker_interface import (
    IBugTracker, BugRecord, BugField, BugSeverity, BugStatus,
    Evidence, TrackerError,
)

logger = logging.getLogger(__name__)

_PRIORITY_TO_SEVERITY = {
    "Highest": BugSeverity.CRITICAL,
    "High": BugSeverity.HIGH,
    "Medium": BugSeverity.MEDIUM,
    "Low": BugSeverity.LOW,
    "Lowest": BugSeverity.TRIVIAL,
}
_SEVERITY_TO_PRIORITY = {v: k for k, v in _PRIORITY_TO_SEVERITY.items()}

_STATUS_MAP = {
    "To Do": BugStatus.NEW,
    "Open": BugStatus.NEW,
    "In Progress": BugStatus.ACTIVE,
    "Done": BugStatus.RESOLVED,
    "Closed": BugStatus.CLOSED,
    "Reopened": BugStatus.REACTIVATED,
}


class JiraAdapter(IBugTracker):
    """
    Jira Cloud/Server adapter using REST API v3 with Basic/Bearer auth.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        project_key: str,
        issue_type: str = "Bug",
        auth_mode: str = "basic",  # "basic" | "bearer"
    ):
        self.base_url = base_url.rstrip("/")
        self.project_key = project_key
        self.issue_type = issue_type
        self._api = f"{self.base_url}/rest/api/3"
        self._email = email
        self._token = api_token
        self._auth_mode = auth_mode
        self._session: Optional[aiohttp.ClientSession] = None

    # ── session ────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            auth = None
            if self._auth_mode == "basic":
                auth = aiohttp.BasicAuth(self._email, self._token)
            else:
                headers["Authorization"] = f"Bearer {self._token}"
            self._session = aiohttp.ClientSession(auth=auth, headers=headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── helpers ────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, *,
        json_body: Any = None, data: Any = None,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = await self._get_session()
        url = path if path.startswith("http") else f"{self._api}/{path}"
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type

        kw: Dict[str, Any] = {}
        if json_body is not None:
            kw["json"] = json_body
        if data is not None:
            kw["data"] = data

        async with session.request(method, url, headers=headers or None, **kw) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise TrackerError(
                    f"Jira {method} {path} → {resp.status}: {body[:500]}",
                    status_code=resp.status, raw=body,
                )
            return json.loads(body) if body.strip() else {}

    def _normalise(self, issue: Dict[str, Any]) -> BugRecord:
        fields = issue.get("fields", {})
        priority = (fields.get("priority") or {}).get("name", "Medium")
        status_name = (fields.get("status") or {}).get("name", "Open")
        assignee = fields.get("assignee")
        created_str = fields.get("created")
        updated_str = fields.get("updated")

        attachments = [
            att["content"] for att in (fields.get("attachment") or [])
            if "content" in att
        ]

        # Build description from ADF or fallback
        desc = ""
        if fields.get("description"):
            desc = self._adf_to_text(fields["description"])

        return BugRecord(
            tracker_id=f"JIRA-{issue['key']}",
            title=fields.get("summary", ""),
            description=desc,
            severity=_PRIORITY_TO_SEVERITY.get(priority, BugSeverity.MEDIUM),
            status=_STATUS_MAP.get(status_name, BugStatus.NEW),
            assigned_to=assignee.get("displayName") if assignee else None,
            tags=[l["name"] for l in (fields.get("labels") or [])],
            attachments=attachments,
            url=f"{self.base_url}/browse/{issue['key']}",
            created=datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else None,
            updated=datetime.fromisoformat(updated_str.replace("Z", "+00:00")) if updated_str else None,
            raw=issue,
        )

    @staticmethod
    def _adf_to_text(adf: Any) -> str:
        """Recursively extract plain text from Atlassian Document Format."""
        if isinstance(adf, str):
            return adf
        if isinstance(adf, dict):
            if adf.get("type") == "text":
                return adf.get("text", "")
            children = adf.get("content", [])
            return "\n".join(
                JiraAdapter._adf_to_text(c) for c in children
            )
        if isinstance(adf, list):
            return "\n".join(JiraAdapter._adf_to_text(i) for i in adf)
        return str(adf)

    # ── IBugTracker implementation ─────────────────────────────

    async def create_bug(
        self, title: str, description: str,
        severity: BugSeverity = BugSeverity.MEDIUM,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> BugRecord:
        payload: Dict[str, Any] = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": title,
                "issuetype": {"name": self.issue_type},
                "description": {
                    "type": "doc", "version": 1,
                    "content": [
                        {"type": "paragraph", "content": [
                            {"type": "text", "text": description}
                        ]}
                    ],
                },
                "priority": {
                    "name": _SEVERITY_TO_PRIORITY.get(severity, "Medium")
                },
            }
        }
        if extra_fields:
            payload["fields"].update(extra_fields)

        resp = await self._request("POST", "issue", json_body=payload)
        issue = await self._request("GET", f"issue/{resp['key']}?expand=renderedFields")
        logger.info("Created Jira %s: %s", resp["key"], title)
        return self._normalise(issue)

    async def update_bug(
        self, tracker_id: str, updates: Dict[str, Any],
    ) -> BugRecord:
        key = tracker_id.replace("JIRA-", "")
        jira_fields: Dict[str, Any] = {}

        # comment
        if "comment" in updates:
            await self._request(
                "POST", f"issue/{key}/comment",
                json_body={
                    "body": {
                        "type": "doc", "version": 1,
                        "content": [{"type": "paragraph", "content": [
                            {"type": "text", "text": updates.pop("comment")}
                        ]}],
                    }
                },
            )

        # link
        if "add_link" in updates:
            link = updates.pop("add_link")
            target_key = link["target"].replace("JIRA-", "")
            await self._request(
                "POST", "issueLink",
                json_body={
                    "type": {"name": link.get("type", "Relates")},
                    "inwardIssue": {"key": key},
                    "outwardIssue": {"key": target_key},
                },
            )

        # transition (status change)
        if "state" in updates:
            await self._transition(key, updates.pop("state"))

        # flat fields
        mapping = {
            "title": "summary",
            "assigned_to": "assignee",
            "tags": "labels",
            "severity": "priority",
        }
        for k, v in updates.items():
            jira_key = mapping.get(k, k)
            if k == "severity" and isinstance(v, BugSeverity):
                v = {"name": _SEVERITY_TO_PRIORITY.get(v, "Medium")}
            elif k == "assigned_to":
                v = {"accountId": v} if v else None
            jira_fields[jira_key] = v

        if jira_fields:
            await self._request("PUT", f"issue/{key}", json_body={"fields": jira_fields})

        issue = await self._request("GET", f"issue/{key}")
        return self._normalise(issue)

    async def _transition(self, key: str, target_status: str):
        resp = await self._request("GET", f"issue/{key}/transitions")
        for t in resp.get("transitions", []):
            if t["name"].lower() == target_status.lower():
                await self._request(
                    "POST", f"issue/{key}/transitions",
                    json_body={"transition": {"id": t["id"]}},
                )
                return
        logger.warning("No transition found for status '%s' on %s", target_status, key)

    async def search_bugs(
        self, query: str, max_results: int = 50,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[BugRecord]:
        jql_parts = [
            f"project = {self.project_key}",
            f"issuetype = {self.issue_type}",
        ]
        if query:
            safe = query.replace('"', '\\"')
            jql_parts.append(f'summary ~ "{safe}"')
        if filters:
            if "status" in filters:
                jql_parts.append(f'status = "{filters["status"]}"')
            if "priority" in filters:
                jql_parts.append(f'priority = "{filters["priority"]}"')
            if "assignee" in filters:
                jql_parts.append(f'assignee = "{filters["assignee"]}"')
            if "labels" in filters:
                jql_parts.append(f'labels = "{filters["labels"]}"')

        jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"
        resp = await self._request(
            "POST", "search",
            json_body={"jql": jql, "maxResults": max_results},
        )
        return [self._normalise(i) for i in resp.get("issues", [])]

    async def get_fields(self, work_item_type: str = "Bug") -> List[BugField]:
        resp = await self._request("GET", "field")
        fields = []
        for f in resp if isinstance(resp, list) else resp.get("values", []):
            fields.append(BugField(
                name=f.get("id", f.get("key", "")),
                field_type=f.get("schema", {}).get("type", "string"),
                required=f.get("required", False),
                allowed_values=f.get("allowedValues"),
            ))
        return fields

    async def attach_evidence(
        self, tracker_id: str, evidence: Evidence,
    ) -> str:
        key = tracker_id.replace("JIRA-", "")
        session = await self._get_session()
        url = f"{self._api}/issue/{key}/attachments"

        form = aiohttp.FormData()
        form.add_field(
            "file", evidence.content,
            filename=evidence.filename,
            content_type=evidence.mime_type,
        )

        async with session.post(
            url,
            data=form,
            headers={"X-Atlassian-Token": "no-check"},
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise TrackerError(
                    f"Jira attach {key} → {resp.status}: {body[:500]}",
                    status_code=resp.status, raw=body,
                )
            result = json.loads(body)
            att_url = result[0]["content"] if result else ""

        logger.info("Attached %s to JIRA-%s", evidence.filename, key)
        return att_url