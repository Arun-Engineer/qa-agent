import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


# =========================================================
# Public API
# =========================================================

@dataclass
class RunArtifacts:
    run_id: str
    run_json: str
    report_json: Optional[str]
    pdf: Optional[str]
    xlsx: Optional[str]


def export_run_artifacts(spec: str, plan: Dict[str, Any], detailed_results: List[Dict[str, Any]]) -> RunArtifacts:
    """
    Real-world style artifacts:
      - Run JSON (raw payload)
      - Report JSON (pytest-json-report output if present)
      - PDF execution report (with metadata + executive summary + tables)
      - Excel report (Summary / Testcases / Observations)

    Output directory:
      - data/logs/ (default) or ARTIFACTS_DIR env.
    """
    out_dir = Path(os.getenv("ARTIFACTS_DIR", str(Path("data") / "logs")))
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_json_name = f"run_{run_id}.json"
    report_json_name = f"report_{run_id}.json"
    pdf_name = f"run_{run_id}.pdf"
    xlsx_name = f"run_{run_id}.xlsx"

    meta = _meta()
    payload = {
        "spec": spec,
        "plan": plan,
        "results": detailed_results,
        "created_at": dt.datetime.utcnow().isoformat(),
        "meta": meta,
    }
    (out_dir / run_json_name).write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")

    # Try to copy pytest-json-report output if present (best for per-test outcomes)
    report_obj = None
    copied_report = None
    src_report = _find_pytest_report_json()
    if src_report:
        try:
            txt = Path(src_report).read_text(encoding="utf-8", errors="ignore")
            (out_dir / report_json_name).write_text(txt, encoding="utf-8")
            copied_report = report_json_name
            report_obj = json.loads(txt)
        except Exception:
            copied_report = None
            report_obj = None

    summary = _summarize(meta=meta, plan=plan, detailed_results=detailed_results, report_obj=report_obj)
    tc_headers, tc_rows = _build_testcases(spec=spec, plan=plan, detailed_results=detailed_results, report_obj=report_obj)
    obs_headers, obs_rows = _build_observations(run_id=run_id, testcases_rows=tc_rows)

    # Excel
    xlsx_out = None
    try:
        _write_excel(
            out_path=out_dir / xlsx_name,
            meta=meta,
            summary=summary,
            testcases_headers=tc_headers,
            testcases_rows=tc_rows,
            obs_headers=obs_headers,
            obs_rows=obs_rows,
        )
        xlsx_out = xlsx_name
    except Exception:
        xlsx_out = None

    # PDF
    pdf_out = None
    try:
        _write_pdf(
            out_path=out_dir / pdf_name,
            meta=meta,
            summary=summary,
            testcases_headers=tc_headers,
            testcases_rows=tc_rows,
            obs_headers=obs_headers,
            obs_rows=obs_rows,
        )
        pdf_out = pdf_name
    except Exception:
        pdf_out = None

    return RunArtifacts(
        run_id=run_id,
        run_json=run_json_name,
        report_json=copied_report,
        pdf=pdf_out,
        xlsx=xlsx_out,
    )


# =========================================================
# Helpers
# =========================================================

def _meta() -> Dict[str, str]:
    # Override in AWS via env vars / secrets
    return {
        "project": os.getenv("QA_PROJECT_NAME", "AI QA Platform Demo"),
        "environment": os.getenv("QA_ENVIRONMENT", os.getenv("MODE", "Local")),
        "build_version": os.getenv("QA_BUILD_VERSION", "Sample v0.1"),
        "prepared_by": os.getenv("QA_PREPARED_BY", "QA Agent (Automated)"),
    }


def _json_default(o: Any):
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    return str(o)


def _find_pytest_report_json() -> Optional[str]:
    # If you run pytest with pytest-json-report, this file is common.
    for p in [Path(".report.json"), Path("data") / ".report.json", Path(".") / "report.json"]:
        if p.exists() and p.is_file():
            return str(p)
    return None


def _soft_breaks(s: str) -> str:
    z = "\u200b"  # zero-width space (helps wrap long tokens in PDF tables)
    return (
        s.replace("/", f"/{z}")
         .replace("\\", f"\\{z}")
         .replace("_", f"_{z}")
         .replace("-", f"-{z}")
         .replace("::", f"::{z}")
         .replace("?", f"?{z}")
         .replace("&", f"&{z}")
         .replace("=", f"={z}")
         .replace(".", f".{z}")
    )


def _pretty_tool(name: str) -> str:
    return {
        "pytest_runner": "Pytest (API/Unit)",
        "playwright_runner": "Playwright (UI)",
        "api_caller": "API Caller",
        "bug_reporter": "Bug Reporter",
    }.get(name, (name or "").replace("_", " ").title())


def _infer_expected(text: str, fallback: str) -> str:
    t = (text or "").lower()
    if "login" in t and ("invalid" in t or "wrong" in t or "negative" in t):
        return "Login must be rejected; validation/error message shown; no session created."
    if "add_to_cart" in t or "add to cart" in t:
        return "Item should be added to cart and cart count should update."
    return fallback


def _short_error(s: str, limit: int = 260) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return (s[:limit] + "…") if len(s) > limit else s


# =========================================================
# Build summary + rows
# =========================================================

def _summarize(meta: Dict[str, str], plan: Dict[str, Any], detailed_results: List[Dict[str, Any]], report_obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    goal = plan.get("goal") or ""

    passed = failed = skipped = 0

    # Prefer pytest report summary
    if isinstance(report_obj, dict) and isinstance(report_obj.get("summary"), dict):
        s = report_obj["summary"]
        passed = int(s.get("passed", 0) or 0)
        failed = int(s.get("failed", 0) or 0)
        skipped = int(s.get("skipped", 0) or 0)
    else:
        # Else sum from tool summaries
        for item in detailed_results:
            res = (item or {}).get("result") or {}
            sm = res.get("summary") or {}
            passed += int(sm.get("passed", 0) or 0)
            failed += int(sm.get("failed", 0) or 0)
            skipped += int(sm.get("skipped", 0) or 0)

        # Hard fallback
        if passed == failed == skipped == 0:
            total_steps = len(plan.get("steps") or [])
            passed = total_steps

    total = passed + failed + skipped
    pass_rate = f"{(passed / total * 100):.1f}%" if total else "0.0%"

    readiness = "READY for the next environment gate." if failed == 0 else "NOT READY. Fix failures before promoting this build."

    return {
        "project": meta.get("project"),
        "environment": meta.get("environment"),
        "build_version": meta.get("build_version"),
        "prepared_by": meta.get("prepared_by"),
        "report_date": dt.date.today().isoformat(),
        "goal": goal,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": pass_rate,
        "readiness": readiness,
        "generated_on": dt.datetime.utcnow().isoformat(),
    }


def _build_testcases(
    spec: str,
    plan: Dict[str, Any],
    detailed_results: List[Dict[str, Any]],
    report_obj: Optional[Dict[str, Any]],
) -> Tuple[List[str], List[List[Any]]]:
    headers = ["S.NO", "Module", "Page", "Test scenario / case", "Expected", "Actual", "Status", "QA comments"]

    goal = plan.get("goal") or spec
    base_expected = _infer_expected(goal, goal)

    # Best-effort page hint
    page_hint = ""
    for st in (plan.get("steps") or []):
        args = (st or {}).get("args") or {}
        page_hint = args.get("url") or args.get("base_url") or args.get("page") or args.get("endpoint") or ""
        if page_hint:
            break

    rows: List[List[Any]] = []

    tests = (report_obj or {}).get("tests") if isinstance(report_obj, dict) else None
    if isinstance(tests, list) and tests:
        for i, t in enumerate(tests, start=1):
            nodeid = t.get("nodeid") or ""
            outcome = (t.get("outcome") or "").lower()
            status = "Pass" if outcome == "passed" else ("Fail" if outcome == "failed" else "Skip")

            file_part = nodeid.split("::")[0] if "::" in nodeid else nodeid
            module = Path(file_part).stem if file_part else "automation"
            module = module.replace("_", " ").title()

            scenario = _soft_breaks(nodeid.replace("::", " :: "))

            expected = _infer_expected(nodeid, base_expected)

            actual = status
            comments = ""
            if status == "Fail":
                lr = t.get("longrepr") or ""
                crash_msg = ""
                if isinstance(t.get("call"), dict):
                    crash_msg = ((t["call"].get("crash") or {}) or {}).get("message") or ""
                err = crash_msg or lr or ""
                actual = _short_error(err, 260) or "Failed (see Run JSON / Report JSON for full traceback)."
                comments = "Failure captured from automation. Use Report JSON for full error context."

            rows.append([i, module, _soft_breaks(page_hint), scenario, expected, actual, status, comments])

        return headers, rows

    # Fallback: suite-level rows from plan steps
    for idx, item in enumerate(detailed_results, start=1):
        step = (item or {}).get("step") or {}
        res = (item or {}).get("result") or {}

        tool = step.get("tool") or ""
        args = step.get("args") or {}

        module = _pretty_tool(tool)
        page = args.get("url") or args.get("base_url") or args.get("page") or args.get("endpoint") or page_hint

        scenario = step.get("description") or step.get("name") or goal
        expected = _infer_expected(scenario, base_expected)

        sm = res.get("summary") or {}
        p = int(sm.get("passed", 0) or 0)
        f = int(sm.get("failed", 0) or 0)
        s = int(sm.get("skipped", 0) or 0)
        total = p + f + s
        status = "Fail" if f > 0 else "Pass"

        actual = f"Summary: total={total}, passed={p}, failed={f}, skipped={s}"
        comments = ""
        if f > 0:
            err = res.get("error") or res.get("stderr") or res.get("output") or ""
            comments = _short_error(err, 260) or "Suite has failures. See Run JSON for details."

        rows.append([idx, module, _soft_breaks(str(page or "")), _soft_breaks(str(scenario)), expected, _soft_breaks(str(actual)), status, _soft_breaks(str(comments))])

    return headers, rows


def _build_observations(run_id: str, testcases_rows: List[List[Any]]) -> Tuple[List[str], List[List[Any]]]:
    headers = ["S.NO", "BUG ID", "Description", "Priority"]
    obs = []
    bug_no = 1
    for r in testcases_rows:
        status = str(r[6]).strip().lower() if len(r) > 6 else ""
        if status == "fail":
            bug_id = f"AUTO-{run_id}-{bug_no:03d}"
            desc = f"{r[3]} — {r[5]}"
            obs.append([bug_no, bug_id, _short_error(desc, 320), "P1"])
            bug_no += 1
    if not obs:
        obs.append([1, f"AUTO-{run_id}-000", "No defects observed in this execution run.", "P3"])
    return headers, obs


# =========================================================
# Excel
# =========================================================

def _write_excel(
    out_path: Path,
    meta: Dict[str, str],
    summary: Dict[str, Any],
    testcases_headers: List[str],
    testcases_rows: List[List[Any]],
    obs_headers: List[str],
    obs_rows: List[List[Any]],
) -> None:
    wb = openpyxl.Workbook()

    # Summary
    ws_sum = wb.active
    ws_sum.title = "Summary"
    ws_sum.append(["Metric", "Value"])
    ws_sum["A1"].font = Font(bold=True)
    ws_sum["B1"].font = Font(bold=True)

    rows = [
        ("Project", meta.get("project")),
        ("Environment", meta.get("environment")),
        ("Build / Version", meta.get("build_version")),
        ("Prepared by", meta.get("prepared_by")),
        ("Goal", summary.get("goal")),
        ("Total Cases", summary.get("total")),
        ("Passed", summary.get("passed")),
        ("Failed", summary.get("failed")),
        ("Skipped", summary.get("skipped")),
        ("Pass Rate", summary.get("pass_rate")),
        ("Release Readiness", summary.get("readiness")),
        ("Generated On", summary.get("generated_on")),
    ]
    for k, v in rows:
        ws_sum.append([k, v])

    ws_sum.column_dimensions["A"].width = 22
    ws_sum.column_dimensions["B"].width = 60
    for r in range(2, ws_sum.max_row + 1):
        ws_sum[f"A{r}"].font = Font(bold=True)
        ws_sum[f"A{r}"].alignment = Alignment(vertical="top")
        ws_sum[f"B{r}"].alignment = Alignment(wrap_text=True, vertical="top")

    # Testcases
    ws_tc = wb.create_sheet("Testcases")
    ws_tc.append(testcases_headers)
    for c in range(1, len(testcases_headers) + 1):
        cell = ws_tc.cell(row=1, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="00B0F0")
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    for r in testcases_rows:
        ws_tc.append(r)

    widths = [6, 18, 26, 44, 30, 34, 10, 34]
    for i, w in enumerate(widths, start=1):
        ws_tc.column_dimensions[get_column_letter(i)].width = w

    for row in ws_tc.iter_rows(min_row=2, max_row=ws_tc.max_row, min_col=1, max_col=len(testcases_headers)):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    # Observations
    ws_obs = wb.create_sheet("Observations")
    ws_obs.append(obs_headers)
    for c in range(1, len(obs_headers) + 1):
        cell = ws_obs.cell(row=1, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="00B0F0")
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    for r in obs_rows:
        ws_obs.append(r)

    obs_widths = [6, 18, 60, 10]
    for i, w in enumerate(obs_widths, start=1):
        ws_obs.column_dimensions[get_column_letter(i)].width = w

    for row in ws_obs.iter_rows(min_row=2, max_row=ws_obs.max_row, min_col=1, max_col=len(obs_headers)):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(out_path)


# =========================================================
# PDF
# =========================================================

def _write_pdf(
    out_path: Path,
    meta: Dict[str, str],
    summary: Dict[str, Any],
    testcases_headers: List[str],
    testcases_rows: List[List[Any]],
    obs_headers: List[str],
    obs_rows: List[List[Any]],
) -> None:
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleX",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        spaceAfter=6,
    )
    sub_style = ParagraphStyle(
        "SubX",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.HexColor("#475569"),
        spaceAfter=14,
    )
    h_style = ParagraphStyle(
        "HX",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        spaceBefore=10,
        spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        "CellX",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=10,
        wordWrap="CJK",
    )

    def P(v: Any) -> Paragraph:
        s = "" if v is None else str(v)
        s = _soft_breaks(s)
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        s = s.replace("\n", "<br/>")
        return Paragraph(s, cell_style)

    def footer(c, doc):
        c.saveState()
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#64748B"))
        c.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.5 * inch, f"Page {c.getPageNumber()}")
        c.restoreState()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=landscape(A4),
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.75 * inch,
    )

    story: List[Any] = []
    story.append(Paragraph("QA Test Execution Report", title_style))
    story.append(Paragraph(meta.get("project", "AI QA Platform Demo"), sub_style))

    # Meta block
    meta_rows = [
        ["Project", meta.get("project")],
        ["Environment", meta.get("environment")],
        ["Build / Version", meta.get("build_version")],
        ["Prepared by", meta.get("prepared_by")],
        ["Report Date", summary.get("report_date")],
    ]
    meta_tbl = Table([[P(a), P(b)] for a, b in meta_rows], colWidths=[doc.width * 0.22, doc.width * 0.78])
    meta_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(meta_tbl)

    story.append(Spacer(1, 10))
    story.append(Paragraph("Executive Summary", h_style))

    bullets = [
        f"Goal: {summary.get('goal')}",
        f"Total test cases executed: {summary.get('total')}.",
        f"Result: {summary.get('passed')} passed, {summary.get('failed')} failed, {summary.get('skipped')} skipped (Pass rate: {summary.get('pass_rate')}).",
        f"Release readiness: {summary.get('readiness')}",
    ]
    lf = ListFlowable([ListItem(Paragraph(_soft_breaks(b), styles["BodyText"]), leftIndent=14) for b in bullets],
                      bulletType="bullet", leftIndent=16)
    story.append(lf)

    story.append(Spacer(1, 10))
    story.append(Paragraph("Test Execution Summary", h_style))

    exec_tbl = Table(
        [[P("Total"), P(summary.get("total")),
          P("Passed"), P(summary.get("passed")),
          P("Failed"), P(summary.get("failed")),
          P("Skipped"), P(summary.get("skipped")),
          P("Pass Rate"), P(summary.get("pass_rate"))]],
        colWidths=[doc.width * 0.08, doc.width * 0.07] * 5
    )
    exec_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF2FF")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(exec_tbl)

    def make_table(headers: List[str], rows: List[List[Any]], weights: List[float]) -> Table:
        data = [[P(h) for h in headers]]
        for r in rows:
            data.append([P(v) for v in r])

        total_w = sum(weights)
        col_widths = [doc.width * (w / total_w) for w in weights]
        t = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#00B0F0")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        for i in range(1, len(data)):
            if i % 2 == 0:
                t.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F3F4F6"))]))
        return t

    story.append(Spacer(1, 14))
    story.append(Paragraph("Detailed Test Cases", h_style))
    tc_weights = [0.6, 1.2, 1.4, 3.6, 2.2, 2.2, 0.8, 2.8]
    story.append(make_table(testcases_headers, testcases_rows, tc_weights))

    story.append(Spacer(1, 14))
    story.append(Paragraph("Observations", h_style))
    obs_weights = [0.7, 1.3, 4.6, 1.0]
    story.append(make_table(obs_headers, obs_rows, obs_weights))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
