"""
Phase 6 · Slack Reporter
Smart Slack notifications for test runs, gate decisions, and bug alerts.
Supports Block Kit formatting, threading, and channel routing.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class SlackChannel:
    channel_id: str
    name: str
    purpose: str = ""  # "alerts", "reports", "bugs"


@dataclass
class SlackConfig:
    bot_token: str
    default_channel: str
    alert_channel: Optional[str] = None
    bug_channel: Optional[str] = None
    report_channel: Optional[str] = None
    mention_on_failure: List[str] = field(default_factory=list)  # user IDs
    thread_replies: bool = True
    include_details: bool = True


class SlackReporter:
    """
    Sends rich Slack notifications using Block Kit.
    Routes to different channels based on event type.
    """

    API_URL = "https://slack.com/api"

    def __init__(self, config: SlackConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "Authorization": f"Bearer {self.config.bot_token}",
                "Content-Type": "application/json",
            })
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── high-level notifications ───────────────────────────────

    async def notify_run_complete(
        self, run_data: Dict[str, Any], gate: Optional[Dict] = None,
    ) -> Optional[str]:
        """Post a run completion summary. Returns message timestamp for threading."""
        results = run_data.get("results", [])
        total = len(results)
        passed = sum(1 for r in results if r.get("status") == "passed")
        failed = sum(1 for r in results if r.get("status") == "failed")
        pass_rate = round(passed / total * 100, 1) if total else 0

        emoji = ":white_check_mark:" if pass_rate >= 90 else ":warning:" if pass_rate >= 70 else ":x:"
        gate_text = ""
        if gate:
            gv = gate.get("verdict", "N/A")
            gate_emoji = {
                "PASS": ":large_green_circle:", "WARN": ":large_yellow_circle:", "FAIL": ":red_circle:"
            }.get(gv, ":grey_question:")
            gate_text = f"\n{gate_emoji} *Release Gate:* {gv} (Score: {gate.get('score', 0)}%)"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} Test Run Complete"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Run:* {run_data.get('run_id', 'N/A')}"},
                {"type": "mrkdwn", "text": f"*Environment:* {run_data.get('environment', 'N/A')}"},
                {"type": "mrkdwn", "text": f"*Total:* {total} | *Passed:* {passed} | *Failed:* {failed}"},
                {"type": "mrkdwn", "text": f"*Pass Rate:* {pass_rate}%{gate_text}"},
            ]},
        ]

        # Mention on failure
        if failed > 0 and self.config.mention_on_failure:
            mentions = " ".join(f"<@{uid}>" for uid in self.config.mention_on_failure)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":rotating_light: {mentions} — {failed} test(s) failed"},
            })

        # Top failures detail
        if self.config.include_details and failed > 0:
            failures = [r for r in results if r.get("status") == "failed"][:5]
            failure_text = "\n".join(
                f"• `{f.get('name', '?')[:50]}` — {f.get('error', 'Unknown')[:60]}"
                for f in failures
            )
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Top Failures:*\n{failure_text}"}})

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"AI QA Agent | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"}],
        })

        channel = self.config.report_channel or self.config.default_channel
        return await self._post_message(channel, blocks=blocks)

    async def notify_bugs_filed(
        self, bugs: List[Dict[str, Any]], thread_ts: Optional[str] = None,
    ) -> Optional[str]:
        """Post bug filing notification."""
        if not bugs:
            return None

        critical = [b for b in bugs if b.get("severity") in ("critical", "high")]
        bug_lines = []
        for b in bugs[:10]:
            sev = b.get("severity", "medium").upper()
            sev_emoji = {"CRITICAL": ":red_circle:", "HIGH": ":orange_circle:", "MEDIUM": ":yellow_circle:"}.get(sev, ":white_circle:")
            bug_lines.append(f"{sev_emoji} *{b.get('id', '?')}* — {b.get('title', '')[:60]}")

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f":bug: {len(bugs)} Bug(s) Filed"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(bug_lines)}},
        ]

        if critical:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":rotating_light: *{len(critical)} Critical/High severity bug(s)*"},
            })

        channel = self.config.bug_channel or self.config.default_channel
        return await self._post_message(channel, blocks=blocks, thread_ts=thread_ts)

    async def notify_gate_decision(
        self, gate: Dict[str, Any], thread_ts: Optional[str] = None,
    ) -> Optional[str]:
        """Post release gate decision as a standalone or threaded message."""
        verdict = gate.get("verdict", "N/A")
        emoji = {"PASS": ":white_check_mark:", "WARN": ":warning:", "FAIL": ":no_entry:"}.get(verdict, ":grey_question:")

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} Release Gate: {verdict}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Score:* {gate.get('score', 0)}%"},
                {"type": "mrkdwn", "text": f"*Confidence:* {gate.get('confidence', 0):.0%}"},
                {"type": "mrkdwn", "text": f"*Blockers:* {gate.get('blocking_count', 0)}"},
                {"type": "mrkdwn", "text": f"*Warnings:* {gate.get('warning_count', 0)}"},
            ]},
        ]

        # Rule details
        if gate.get("rules"):
            rule_lines = []
            for r in gate["rules"]:
                rv = r.get("verdict", "")
                re = {"PASS": ":white_check_mark:", "WARN": ":warning:", "FAIL": ":x:"}.get(rv, "")
                rule_lines.append(f"{re} {r.get('name','')} — actual: {r.get('actual','')} (threshold: {r.get('fail_threshold','')})")
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(rule_lines)}})

        channel = self.config.alert_channel or self.config.default_channel
        return await self._post_message(channel, blocks=blocks, thread_ts=thread_ts)

    async def send_alert(
        self, title: str, message: str, severity: str = "info",
        channel: Optional[str] = None,
    ) -> Optional[str]:
        """Send a generic alert message."""
        emoji_map = {"critical": ":red_circle:", "high": ":orange_circle:", "warn": ":warning:", "info": ":information_source:"}
        emoji = emoji_map.get(severity, ":speech_balloon:")
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        ]
        ch = channel or self.config.alert_channel or self.config.default_channel
        return await self._post_message(ch, blocks=blocks)

    # ── low-level API ──────────────────────────────────────────

    async def _post_message(
        self, channel: str, blocks: List[Dict],
        text: str = "", thread_ts: Optional[str] = None,
    ) -> Optional[str]:
        session = await self._get_session()
        payload: Dict[str, Any] = {"channel": channel, "blocks": blocks}
        if text:
            payload["text"] = text
        if thread_ts and self.config.thread_replies:
            payload["thread_ts"] = thread_ts

        try:
            async with session.post(f"{self.API_URL}/chat.postMessage", json=payload) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.error("Slack error: %s", data.get("error"))
                    return None
                return data.get("ts")
        except Exception as e:
            logger.error("Slack post failed: %s", e)
            return None

    async def upload_file(
        self, channel: str, filepath: str, title: str = "",
        thread_ts: Optional[str] = None,
    ) -> bool:
        """Upload a file (PDF, Excel) to Slack."""
        session = await self._get_session()
        import aiohttp
        form = aiohttp.FormData()
        form.add_field("channels", channel)
        if title:
            form.add_field("title", title)
        if thread_ts:
            form.add_field("thread_ts", thread_ts)
        form.add_field("file", open(filepath, "rb"), filename=filepath.split("/")[-1])

        try:
            async with session.post(f"{self.API_URL}/files.upload", data=form) as resp:
                data = await resp.json()
                return data.get("ok", False)
        except Exception as e:
            logger.error("Slack file upload failed: %s", e)
            return False