"""
Phase 6 · PDF Report Generator
Jinja2 → HTML → PDF with executive summary, detailed results,
evidence screenshots, and confidence scoring.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class PDFReportConfig:
    title: str = "QA Test Execution Report"
    logo_path: Optional[str] = None
    company_name: str = "AI QA Agent"
    include_screenshots: bool = True
    include_stack_traces: bool = False
    max_detail_rows: int = 200
    output_dir: str = "/tmp/reports"
    orientation: str = "portrait"  # "portrait" | "landscape"


class PDFReportGenerator:
    """
    Generates professional PDF reports from test run data.
    Uses Jinja2 for HTML templating + WeasyPrint for PDF conversion.
    """

    def __init__(self, config: Optional[PDFReportConfig] = None):
        self.config = config or PDFReportConfig()
        os.makedirs(self.config.output_dir, exist_ok=True)

    def generate(self, run_data: Dict[str, Any], gate_decision: Optional[Dict] = None) -> str:
        """Generate PDF report. Returns file path."""
        results = run_data.get("results", [])
        bugs = run_data.get("bugs", [])
        summary = self._build_summary(results, bugs, run_data, gate_decision)
        html = self._render_html(summary, results, bugs, gate_decision)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        pdf_path = os.path.join(self.config.output_dir, f"report_{timestamp}.pdf")

        try:
            from weasyprint import HTML as WeasyHTML
            WeasyHTML(string=html).write_pdf(pdf_path)
        except ImportError:
            # Fallback: save HTML
            html_path = pdf_path.replace(".pdf", ".html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.warning("WeasyPrint not installed, saved HTML: %s", html_path)
            return html_path

        logger.info("PDF report generated: %s", pdf_path)
        return pdf_path

    def _build_summary(
        self, results: List[Dict], bugs: List[Dict],
        run_data: Dict, gate: Optional[Dict],
    ) -> Dict[str, Any]:
        total = len(results)
        passed = sum(1 for r in results if r.get("status") == "passed")
        failed = sum(1 for r in results if r.get("status") == "failed")
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        duration = sum(r.get("duration_ms", 0) for r in results)
        crit_bugs = sum(1 for b in bugs if b.get("severity") in ("critical", "high"))

        return {
            "total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "pass_rate": round(passed / total * 100, 1) if total else 0,
            "duration_sec": round(duration / 1000, 1),
            "critical_bugs": crit_bugs, "total_bugs": len(bugs),
            "environment": run_data.get("environment", "Unknown"),
            "run_id": run_data.get("run_id", "N/A"),
            "gate_verdict": gate.get("verdict", "N/A") if gate else "N/A",
            "gate_score": gate.get("score", 0) if gate else 0,
        }

    def _render_html(
        self, summary: Dict, results: List[Dict],
        bugs: List[Dict], gate: Optional[Dict],
    ) -> str:
        pr = summary["pass_rate"]
        pr_color = "#2ECC71" if pr >= 90 else "#F39C12" if pr >= 70 else "#E74C3C"
        gate_color = {"PASS": "#2ECC71", "WARN": "#F39C12", "FAIL": "#E74C3C"}.get(
            summary.get("gate_verdict", ""), "#999"
        )
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # Build result rows
        result_rows = ""
        for i, r in enumerate(results[:self.config.max_detail_rows]):
            status = r.get("status", "unknown")
            s_color = {"passed": "#2ECC71", "failed": "#E74C3C", "skipped": "#F39C12"}.get(status, "#999")
            result_rows += f"""<tr>
              <td style="padding:6px 10px;">{i+1}</td>
              <td style="padding:6px 10px;">{r.get('name', 'N/A')[:60]}</td>
              <td style="padding:6px 10px;">{r.get('module', '')}</td>
              <td style="padding:6px 10px;color:{s_color};font-weight:600;">{status.upper()}</td>
              <td style="padding:6px 10px;">{r.get('duration_ms', 0)}ms</td>
              <td style="padding:6px 10px;font-size:11px;">{r.get('error', '')[:80]}</td>
            </tr>"""

        # Build bug rows
        bug_rows = ""
        for b in bugs[:50]:
            sev = b.get("severity", "medium")
            sev_color = {"critical": "#E74C3C", "high": "#E67E22", "medium": "#F39C12", "low": "#3498DB"}.get(sev, "#999")
            bug_rows += f"""<tr>
              <td style="padding:6px 10px;">{b.get('id', 'N/A')}</td>
              <td style="padding:6px 10px;">{b.get('title', '')[:60]}</td>
              <td style="padding:6px 10px;color:{sev_color};font-weight:600;">{sev.upper()}</td>
              <td style="padding:6px 10px;">{b.get('status', 'New')}</td>
              <td style="padding:6px 10px;">{b.get('assigned_to', 'Unassigned')}</td>
            </tr>"""

        # Gate rules section
        gate_section = ""
        if gate and gate.get("rules"):
            gate_rows = ""
            for gr in gate["rules"]:
                v = gr.get("verdict", "")
                vc = {"PASS": "#2ECC71", "WARN": "#F39C12", "FAIL": "#E74C3C"}.get(v, "#999")
                gate_rows += f"""<tr>
                  <td style="padding:6px 10px;">{gr.get('name','')}</td>
                  <td style="padding:6px 10px;">{gr.get('actual','')}</td>
                  <td style="padding:6px 10px;">{gr.get('fail_threshold','')}</td>
                  <td style="padding:6px 10px;color:{vc};font-weight:600;">{v}</td>
                </tr>"""
            gate_section = f"""
            <h2 style="margin-top:30px;">Release Gate Evaluation</h2>
            <table style="width:100%;border-collapse:collapse;margin-top:10px;">
              <tr style="background:#f0f0f0;"><th style="padding:8px;text-align:left;">Rule</th>
              <th style="padding:8px;text-align:left;">Actual</th>
              <th style="padding:8px;text-align:left;">Threshold</th>
              <th style="padding:8px;text-align:left;">Verdict</th></tr>
              {gate_rows}
            </table>"""

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4 {self.config.orientation}; margin: 20mm; }}
  body {{ font-family: -apple-system, 'Segoe UI', sans-serif; font-size: 13px; color: #333; }}
  h1 {{ color: #2E75B6; font-size: 22px; border-bottom: 2px solid #2E75B6; padding-bottom: 8px; }}
  h2 {{ color: #1a4a7a; font-size: 16px; margin-top: 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ background: #f0f0f0; padding: 8px 10px; text-align: left; font-size: 12px; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }}
  .summary-card {{ background: #f8f9fa; border-radius: 8px; padding: 16px; text-align: center; }}
  .summary-card .val {{ font-size: 28px; font-weight: 700; }}
  .summary-card .lbl {{ font-size: 11px; opacity: 0.7; }}
</style>
</head>
<body>
<h1>{self.config.title}</h1>
<p style="font-size:12px;opacity:0.6;">Run: {summary['run_id']} | Environment: {summary['environment']} | Generated: {now}</p>

<div class="summary-grid">
  <div class="summary-card"><div class="val">{summary['total']}</div><div class="lbl">Total Tests</div></div>
  <div class="summary-card"><div class="val" style="color:#2ECC71;">{summary['passed']}</div><div class="lbl">Passed</div></div>
  <div class="summary-card"><div class="val" style="color:#E74C3C;">{summary['failed']}</div><div class="lbl">Failed</div></div>
  <div class="summary-card"><div class="val" style="color:{pr_color};">{summary['pass_rate']}%</div><div class="lbl">Pass Rate</div></div>
</div>

<div class="summary-grid">
  <div class="summary-card"><div class="val">{summary['duration_sec']}s</div><div class="lbl">Duration</div></div>
  <div class="summary-card"><div class="val" style="color:#E74C3C;">{summary['critical_bugs']}</div><div class="lbl">Critical Bugs</div></div>
  <div class="summary-card"><div class="val">{summary['total_bugs']}</div><div class="lbl">Total Bugs</div></div>
  <div class="summary-card"><div class="val" style="color:{gate_color};">{summary['gate_verdict']}</div><div class="lbl">Gate Verdict</div></div>
</div>

{gate_section}

<h2>Test Results Detail</h2>
<table>
  <tr><th>#</th><th>Test Name</th><th>Module</th><th>Status</th><th>Duration</th><th>Error</th></tr>
  {result_rows}
</table>

{"<h2>Bugs Filed</h2><table><tr><th>ID</th><th>Title</th><th>Severity</th><th>Status</th><th>Assigned</th></tr>" + bug_rows + "</table>" if bugs else ""}

<div style="margin-top:40px;text-align:center;font-size:11px;opacity:0.4;">
  AI QA Agent v5 — Automated Test Report | {now}
</div>
</body>
</html>"""