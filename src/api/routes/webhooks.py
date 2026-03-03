"""
Phase 6B - Webhook API Routes
Receives webhooks from ADO, Jira, GitHub, Slack, and CI/CD pipelines.
Validates signatures, parses payloads, and dispatches to handlers.
"""

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# -- Models -------------------------------------------------------------------

class WebhookResponse(BaseModel):
    status: str = "accepted"
    event_type: str
    message: str = ""


class SlackEventPayload(BaseModel):
    type: str
    challenge: Optional[str] = None
    event: Optional[Dict[str, Any]] = None
    token: Optional[str] = None


# -- Azure DevOps Webhook ----------------------------------------------------

@router.post("/ado", response_model=WebhookResponse)
async def ado_webhook(request: Request):
    """
    Receive Azure DevOps service hook notifications.
    Events: workitem.created, workitem.updated, workitem.commented,
            build.complete, release.deployment.completed
    """
    body = await request.json()
    event_type = body.get("eventType", "unknown")
    resource = body.get("resource", {})

    logger.info("ADO webhook: %s", event_type)

    try:
        if event_type == "workitem.updated":
            await _handle_ado_workitem_update(resource)
        elif event_type == "workitem.commented":
            await _handle_ado_comment(resource)
        elif event_type == "build.complete":
            await _handle_ado_build_complete(resource)
        elif event_type == "release.deployment.completed":
            await _handle_ado_deployment(resource)
        else:
            logger.debug("Unhandled ADO event: %s", event_type)

        return WebhookResponse(event_type=event_type, message=f"Processed {event_type}")
    except Exception as e:
        logger.error("ADO webhook error: %s", e)
        raise HTTPException(500, f"Webhook processing failed: {str(e)}")


# -- Jira Webhook ------------------------------------------------------------

@router.post("/jira", response_model=WebhookResponse)
async def jira_webhook(request: Request):
    """
    Receive Jira webhook events.
    Events: jira:issue_created, jira:issue_updated, comment_created
    """
    body = await request.json()
    event_type = body.get("webhookEvent", body.get("issue_event_type_name", "unknown"))
    issue = body.get("issue", {})

    logger.info("Jira webhook: %s (issue: %s)", event_type, issue.get("key", "N/A"))

    try:
        if "created" in event_type:
            await _handle_jira_issue_created(issue, body)
        elif "updated" in event_type:
            await _handle_jira_issue_updated(issue, body)
        elif "comment" in event_type:
            await _handle_jira_comment(issue, body.get("comment", {}))
        else:
            logger.debug("Unhandled Jira event: %s", event_type)

        return WebhookResponse(event_type=event_type, message=f"Processed {event_type}")
    except Exception as e:
        logger.error("Jira webhook error: %s", e)
        raise HTTPException(500, str(e))


# -- GitHub Webhook -----------------------------------------------------------

@router.post("/github", response_model=WebhookResponse)
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
):
    """
    Receive GitHub webhook events.
    Events: push, pull_request, issues, workflow_run, check_run
    """
    raw_body = await request.body()

    # Validate signature if secret is configured
    import os
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if secret and x_hub_signature_256:
        expected = "sha256=" + hmac.new(
            secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(401, "Invalid signature")

    body = json.loads(raw_body)
    event_type = x_github_event or "unknown"

    logger.info("GitHub webhook: %s", event_type)

    try:
        if event_type == "push":
            await _handle_github_push(body)
        elif event_type == "pull_request":
            await _handle_github_pr(body)
        elif event_type == "workflow_run":
            await _handle_github_workflow(body)
        elif event_type == "issues":
            await _handle_github_issue(body)
        else:
            logger.debug("Unhandled GitHub event: %s", event_type)

        return WebhookResponse(event_type=event_type, message=f"Processed {event_type}")
    except Exception as e:
        logger.error("GitHub webhook error: %s", e)
        raise HTTPException(500, str(e))


# -- Slack Events -------------------------------------------------------------

@router.post("/slack")
async def slack_webhook(request: Request):
    """
    Receive Slack Events API callbacks.
    Handles URL verification challenge and event dispatch.
    """
    body = await request.json()

    # URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    event = body.get("event", {})
    event_type = event.get("type", "unknown")

    logger.info("Slack event: %s", event_type)

    try:
        if event_type == "message" and not event.get("bot_id"):
            await _handle_slack_message(event, body)
        elif event_type == "app_mention":
            await _handle_slack_mention(event, body)
        elif event_type == "reaction_added":
            await _handle_slack_reaction(event, body)
        else:
            logger.debug("Unhandled Slack event: %s", event_type)

        return {"ok": True}
    except Exception as e:
        logger.error("Slack webhook error: %s", e)
        return {"ok": False, "error": str(e)}


# -- CI/CD Pipeline Webhook ---------------------------------------------------

@router.post("/ci-pipeline", response_model=WebhookResponse)
async def ci_pipeline_webhook(request: Request):
    """
    Generic CI/CD pipeline webhook receiver.
    Supports Jenkins, GitHub Actions, GitLab CI, Azure Pipelines.
    """
    body = await request.json()
    source = body.get("source", "unknown")
    event_type = body.get("event", body.get("action", "unknown"))
    status = body.get("status", body.get("conclusion", "unknown"))

    logger.info("CI webhook from %s: %s (status: %s)", source, event_type, status)

    try:
        if status in ("completed", "success", "succeeded"):
            await _handle_pipeline_success(body)
        elif status in ("failure", "failed"):
            await _handle_pipeline_failure(body)
        else:
            logger.debug("CI pipeline status: %s", status)

        return WebhookResponse(event_type=f"{source}/{event_type}", message=f"Pipeline {status}")
    except Exception as e:
        logger.error("CI webhook error: %s", e)
        raise HTTPException(500, str(e))


# -- Health endpoint for webhook monitoring -----------------------------------

@router.get("/health")
async def webhook_health():
    return {"status": "healthy", "endpoints": [
        "/webhooks/ado", "/webhooks/jira", "/webhooks/github",
        "/webhooks/slack", "/webhooks/ci-pipeline",
    ]}


# -- Handler Stubs (to be wired to agents/services) --------------------------

async def _handle_ado_workitem_update(resource: Dict):
    """Route ADO work item updates to scope change detector."""
    wi_id = resource.get("workItemId", resource.get("id"))
    fields = resource.get("fields", {})
    logger.info("ADO work item updated: %s, fields changed: %s", wi_id, list(fields.keys()))
    # TODO: Wire to perception/ado_discussion.py for scope change detection
    # from src.perception.ado_discussion import ADODiscussionAnalyser
    # analyser = ADODiscussionAnalyser(...)
    # summary = await analyser.analyse_discussion(wi_id)


async def _handle_ado_comment(resource: Dict):
    """Route ADO comments to discussion analyser."""
    wi_id = resource.get("workItemId")
    comment_text = resource.get("text", "")
    logger.info("ADO comment on WI-%s: %s...", wi_id, comment_text[:80])


async def _handle_ado_build_complete(resource: Dict):
    """Trigger test run on build completion."""
    build_id = resource.get("id")
    result = resource.get("result", "unknown")
    logger.info("ADO build #%s completed: %s", build_id, result)
    # TODO: Trigger automatic test run if build succeeded


async def _handle_ado_deployment(resource: Dict):
    """Trigger smoke tests on deployment completion."""
    env_name = resource.get("environment", {}).get("name", "unknown")
    status = resource.get("deploymentStatus", "unknown")
    logger.info("ADO deployment to %s: %s", env_name, status)


async def _handle_jira_issue_created(issue: Dict, payload: Dict):
    """Track new Jira issues for test coverage."""
    key = issue.get("key", "")
    summary = issue.get("fields", {}).get("summary", "")
    logger.info("Jira issue created: %s - %s", key, summary)


async def _handle_jira_issue_updated(issue: Dict, payload: Dict):
    """Detect requirement changes from Jira updates."""
    key = issue.get("key", "")
    changelog = payload.get("changelog", {}).get("items", [])
    changed_fields = [c.get("field") for c in changelog]
    logger.info("Jira issue updated: %s, changed: %s", key, changed_fields)


async def _handle_jira_comment(issue: Dict, comment: Dict):
    """Analyse Jira comments for scope signals."""
    key = issue.get("key", "")
    body = comment.get("body", "")
    logger.info("Jira comment on %s: %s...", key, str(body)[:80])


async def _handle_github_push(payload: Dict):
    """Detect code changes that may affect tests."""
    ref = payload.get("ref", "")
    commits = payload.get("commits", [])
    files_changed = set()
    for c in commits:
        files_changed.update(c.get("added", []))
        files_changed.update(c.get("modified", []))
    logger.info("GitHub push to %s: %d commits, %d files", ref, len(commits), len(files_changed))


async def _handle_github_pr(payload: Dict):
    """Trigger test generation for new PRs."""
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    pr_num = pr.get("number")
    logger.info("GitHub PR #%s: %s", pr_num, action)


async def _handle_github_workflow(payload: Dict):
    """Track GitHub Actions workflow results."""
    workflow = payload.get("workflow_run", {})
    name = workflow.get("name", "")
    conclusion = workflow.get("conclusion", "")
    logger.info("GitHub workflow '%s': %s", name, conclusion)


async def _handle_github_issue(payload: Dict):
    """Sync GitHub issues with bug tracker."""
    action = payload.get("action", "")
    issue = payload.get("issue", {})
    logger.info("GitHub issue #%s: %s", issue.get("number"), action)


async def _handle_slack_message(event: Dict, payload: Dict):
    """Process Slack messages for test commands."""
    text = event.get("text", "")
    channel = event.get("channel", "")
    user = event.get("user", "")
    logger.info("Slack message from %s in %s: %s...", user, channel, text[:80])
    # TODO: Wire to NL command parser for slash-command-like triggers


async def _handle_slack_mention(event: Dict, payload: Dict):
    """Respond to @qa-agent mentions."""
    text = event.get("text", "")
    logger.info("Slack mention: %s...", text[:80])


async def _handle_slack_reaction(event: Dict, payload: Dict):
    """Use reactions as feedback signals."""
    reaction = event.get("reaction", "")
    item = event.get("item", {})
    logger.info("Slack reaction '%s' on %s", reaction, item.get("ts"))
    # TODO: Map thumbsup/thumbsdown to feedback_handler approve/reject


async def _handle_pipeline_success(payload: Dict):
    """Auto-trigger test suite on successful pipeline."""
    source = payload.get("source", "unknown")
    branch = payload.get("branch", payload.get("ref", ""))
    logger.info("Pipeline success from %s on %s", source, branch)


async def _handle_pipeline_failure(payload: Dict):
    """Alert on pipeline failure, gather logs for triage."""
    source = payload.get("source", "unknown")
    error = payload.get("error", payload.get("message", ""))
    logger.info("Pipeline failure from %s: %s", source, error[:200])
