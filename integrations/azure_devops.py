"""
Phase 5 · Azure DevOps Adapter
Full REST integration: work items, comments, relations, WIQL, attachments.
"""

import base64
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

_SEVERITY_MAP = {
    "1 - Critical": BugSeverity.CRITICAL,
    "2 - High": BugSeverity.HIGH,
    "3 - Medium": BugSeverity.MEDIUM,
    "4 - Low": BugSeverity.LOW,
}
_SEVERITY_REVERSE = {v: k for k, v in _SEVERITY_MAP.items()}

_STATE_MAP = {
    "New": BugStatus.NEW,
    "Active": BugStatus.ACTIVE,
    "Resolved": BugStatus.RESOLVED,
    "Closed": BugStatus.CLOSED,
}


class AzureDevOpsAdapter(IBugTracker):
    """
    Connects to Azure DevOps REST API v7.0.
    Auth: PAT token via Basic header.
    """

    API_VERSION = "7.0"

    def __init__(
        self,
        organisation: str,
        project: str,
        pat: str,
        area_path: Optional[str] = None,
        iteration_path: Optional[str] = None,
        base_url: str = "https://dev.azure.com",
    ):
        self.org = organisation
        self.project = project
        self.area_path = area_path or project
        self.iteration_path = iteration_path or project
        self._base = f"{base_url}/{organisation}/{project}/_apis"
        self._auth = aiohttp.BasicAuth("", pat)
        self._session: Optional[aiohttp.ClientSession] = None

    # ── session management ─────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                auth=self._auth,
                headers={"Content-Type": "application/json-patch+json"},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── internal helpers ───────────────────────────────────────

    async def _request(
        self, method: str, path: str, *,
        json_body: Any = None, data: Any = None,
        content_type: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self._base}/{path}"
        params = {"api-version": api_version or self.API_VERSION}
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type

        async with session.request(
            method, url, params=params,
            json=json_body if not data else None,
            data=data,
            headers=headers if headers else None,
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise TrackerError(
                    f"ADO {method} {path} → {resp.status}: {body[:500]}",
                    status_code=resp.status,
                    raw=body,
                )
            return json.loads(body) if body.strip() else {}

    def _normalise(self, wi: Dict[str, Any]) -> BugRecord:
        fields = wi.get("fields", {})
        sev_str = fields.get("Microsoft.VSTS.Common.Severity", "3 - Medium")
        state_str = fields.get("System.State", "New")
        created_str = fields.get("System.CreatedDate")
        updated_str = fields.get("System.ChangedDate")
        return BugRecord(
            tracker_id=f"ADO-{wi['id']}",
            title=fields.get("System.Title", ""),
            description=fields.get("System.Description", ""),
            severity=_SEVERITY_MAP.get(sev_str, BugSeverity.MEDIUM),
            status=_STATE_MAP.get(state_str, BugStatus.NEW),
            assigned_to=fields.get("System.AssignedTo", {}).get("displayName")
            if isinstance(fields.get("System.AssignedTo"), dict) else
            fields.get("System.AssignedTo"),
            tags=[t.strip() for t in fields.get("System.Tags", "").split(";") if t.strip()],
            attachments=[
                r["url"] for r in wi.get("relations", [])
                if r.get("rel") == "AttachedFile"
            ],
            url=wi.get("_links", {}).get("html", {}).get("href"),
            created=datetime.fromisoformat(created_str.rstrip("Z")) if created_str else None,
            updated=datetime.fromisoformat(updated_str.rstrip("Z")) if updated_str else None,
            raw=wi,
        )

    # ── IBugTracker implementation ─────────────────────────────

    async def create_bug(
        self,
        title: str,
        description: str,
        severity: BugSeverity = BugSeverity.MEDIUM,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> BugRecord:
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description},
            {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Severity",
             "value": _SEVERITY_REVERSE.get(severity, "3 - Medium")},
            {"op": "add", "path": "/fields/System.AreaPath", "value": self.area_path},
            {"op": "add", "path": "/fields/System.IterationPath", "value": self.iteration_path},
        ]
        if extra_fields:
            for key, val in extra_fields.items():
                patch.append({"op": "add", "path": f"/fields/{key}", "value": val})

        wi = await self._request("POST", "wit/workitems/$Bug", json_body=patch)
        logger.info("Created ADO bug #%s: %s", wi["id"], title)
        return self._normalise(wi)

    async def update_bug(
        self, tracker_id: str, updates: Dict[str, Any],
    ) -> BugRecord:
        wi_id = tracker_id.replace("ADO-", "")
        patch: List[Dict] = []

        # handle special keys
        if "comment" in updates:
            await self._request(
                "POST", f"wit/workitems/{wi_id}/comments",
                json_body={"text": updates.pop("comment")},
                content_type="application/json",
            )

        if "add_link" in updates:
            link = updates.pop("add_link")
            target_id = link["target"].replace("ADO-", "")
            patch.append({
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "System.LinkTypes.Related",
                    "url": f"{self._base}/wit/workitems/{target_id}",
                    "attributes": {"comment": link.get("type", "Related")},
                },
            })

        # remaining flat field updates
        field_mapping = {
            "title": "System.Title",
            "description": "System.Description",
            "state": "System.State",
            "assigned_to": "System.AssignedTo",
            "tags": "System.Tags",
            "severity": "Microsoft.VSTS.Common.Severity",
        }
        for key, val in updates.items():
            ado_field = field_mapping.get(key, key)
            if key == "severity" and isinstance(val, BugSeverity):
                val = _SEVERITY_REVERSE.get(val, "3 - Medium")
            if key == "tags" and isinstance(val, list):
                val = "; ".join(val)
            patch.append({"op": "replace", "path": f"/fields/{ado_field}", "value": val})

        if patch:
            wi = await self._request("PATCH", f"wit/workitems/{wi_id}", json_body=patch)
        else:
            wi = await self._request("GET", f"wit/workitems/{wi_id}",
                                     content_type="application/json")
        return self._normalise(wi)

    async def search_bugs(
        self, query: str, max_results: int = 50,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[BugRecord]:
        wiql = self._build_wiql(query, max_results, filters)
        resp = await self._request(
            "POST", "wit/wiql",
            json_body={"query": wiql},
            content_type="application/json",
        )
        ids = [str(wi["id"]) for wi in resp.get("workItems", [])]
        if not ids:
            return []

        # batch fetch with fields + relations
        batch_url = f"wit/workitems?ids={','.join(ids[:200])}&$expand=relations"
        data = await self._request("GET", batch_url, content_type="application/json")
        return [self._normalise(wi) for wi in data.get("value", [])]

    async def get_fields(self, work_item_type: str = "Bug") -> List[BugField]:
        resp = await self._request(
            "GET", f"wit/workitemtypes/{work_item_type}/fields",
            content_type="application/json",
        )
        fields = []
        for f in resp.get("value", []):
            fields.append(BugField(
                name=f["referenceName"],
                field_type=f.get("type", "string"),
                required=f.get("alwaysRequired", False),
                allowed_values=f.get("allowedValues") or None,
            ))
        return fields

    async def attach_evidence(
        self, tracker_id: str, evidence: Evidence,
    ) -> str:
        wi_id = tracker_id.replace("ADO-", "")
        # 1. Upload attachment
        upload = await self._request(
            "POST",
            f"wit/attachments?fileName={evidence.filename}",
            data=evidence.content,
            content_type="application/octet-stream",
        )
        att_url = upload["url"]

        # 2. Link attachment to work item
        patch = [{
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "AttachedFile",
                "url": att_url,
                "attributes": {"comment": evidence.description or evidence.filename},
            },
        }]
        await self._request("PATCH", f"wit/workitems/{wi_id}", json_body=patch)
        logger.info("Attached %s to ADO-%s", evidence.filename, wi_id)
        return att_url

    # ── WIQL builder ───────────────────────────────────────────

    def _build_wiql(
        self, query: str, top: int, filters: Optional[Dict[str, Any]]
    ) -> str:
        clauses = [
            "[System.TeamProject] = @project",
            "[System.WorkItemType] = 'Bug'",
        ]
        if query:
            safe_q = query.replace("'", "''")
            clauses.append(
                f"[System.Title] CONTAINS '{safe_q}'"
            )
        if filters:
            if "state" in filters:
                clauses.append(f"[System.State] = '{filters['state']}'")
            if "severity" in filters:
                clauses.append(
                    f"[Microsoft.VSTS.Common.Severity] = '{filters['severity']}'"
                )
            if "assigned_to" in filters:
                clauses.append(
                    f"[System.AssignedTo] = '{filters['assigned_to']}'"
                )
            if "area_path" in filters:
                clauses.append(
                    f"[System.AreaPath] UNDER '{filters['area_path']}'"
                )
            if "tags" in filters:
                clauses.append(
                    f"[System.Tags] CONTAINS '{filters['tags']}'"
                )
        where = " AND ".join(clauses)
        return (
            f"SELECT [System.Id] FROM WorkItems "
            f"WHERE {where} "
            f"ORDER BY [System.ChangedDate] DESC "
        )