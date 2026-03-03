"""
Phase 6 · Dashboard Generator
NL → React+Recharts dashboard generation with stakeholder presets.
Produces self-contained HTML dashboards from test run data.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class StakeholderPreset(Enum):
    """Pre-built dashboard views for different audiences."""
    EXECUTIVE = "executive"        # KPIs, pass rate, trend, risk
    QA_LEAD = "qa_lead"            # Coverage map, failure clusters, flaky tests
    DEVELOPER = "developer"        # Failure details, stack traces, repro steps
    RELEASE_MANAGER = "release"    # Gate status, blockers, sign-off checklist
    PRODUCT_OWNER = "product"      # Feature coverage, user story mapping


@dataclass
class ChartSpec:
    """Specification for a single chart in the dashboard."""
    chart_id: str
    chart_type: str  # "line", "bar", "pie", "area", "scatter", "heatmap", "gauge"
    title: str
    data_key: str
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    color_scheme: List[str] = field(default_factory=lambda: [
        "#2E75B6", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6", "#1ABC9C"
    ])
    width: str = "100%"
    height: int = 300
    stacked: bool = False


@dataclass
class DashboardConfig:
    """Full dashboard configuration."""
    title: str = "QA Agent Dashboard"
    preset: StakeholderPreset = StakeholderPreset.QA_LEAD
    charts: List[ChartSpec] = field(default_factory=list)
    refresh_interval_sec: int = 0  # 0 = no auto-refresh
    theme: str = "light"  # "light" | "dark"
    filters_enabled: bool = True
    export_enabled: bool = True


# ── preset chart definitions ───────────────────────────────────

_PRESET_CHARTS: Dict[StakeholderPreset, List[ChartSpec]] = {
    StakeholderPreset.EXECUTIVE: [
        ChartSpec("pass_rate_gauge", "gauge", "Overall Pass Rate", "pass_rate", height=250),
        ChartSpec("trend_line", "line", "Pass Rate Trend (Last 30 Runs)", "trend", x_axis="run", y_axis="rate"),
        ChartSpec("severity_pie", "pie", "Open Bugs by Severity", "bug_severity"),
        ChartSpec("risk_bar", "bar", "Risk Score by Module", "risk_by_module", x_axis="module", y_axis="risk"),
    ],
    StakeholderPreset.QA_LEAD: [
        ChartSpec("coverage_heatmap", "heatmap", "Test Coverage Heatmap", "coverage_matrix"),
        ChartSpec("failure_clusters", "bar", "Top Failure Clusters", "failure_clusters", x_axis="cluster", y_axis="count"),
        ChartSpec("flaky_tests", "bar", "Flaky Test Frequency", "flaky_tests", x_axis="test", y_axis="flaky_count",
                  color_scheme=["#F39C12"]),
        ChartSpec("execution_time", "area", "Execution Time Trend", "exec_time", x_axis="run", y_axis="seconds"),
        ChartSpec("env_comparison", "bar", "Pass Rate by Environment", "env_pass_rate", x_axis="env", y_axis="rate", stacked=True),
    ],
    StakeholderPreset.DEVELOPER: [
        ChartSpec("failure_detail", "bar", "Failures by Error Type", "error_types", x_axis="type", y_axis="count"),
        ChartSpec("test_duration", "scatter", "Test Duration Distribution", "test_durations", x_axis="test", y_axis="ms"),
        ChartSpec("recent_failures", "bar", "Recent Failures", "recent_fails", x_axis="test", y_axis="count",
                  color_scheme=["#E74C3C"]),
    ],
    StakeholderPreset.RELEASE_MANAGER: [
        ChartSpec("gate_status", "gauge", "Release Gate Score", "gate_score", height=250),
        ChartSpec("blocker_count", "bar", "Open Blockers", "blockers", x_axis="category", y_axis="count",
                  color_scheme=["#E74C3C", "#F39C12"]),
        ChartSpec("signoff_progress", "bar", "Sign-off Progress", "signoff", x_axis="area", y_axis="percent", stacked=True),
    ],
    StakeholderPreset.PRODUCT_OWNER: [
        ChartSpec("feature_coverage", "bar", "Feature Coverage %", "feature_coverage", x_axis="feature", y_axis="percent"),
        ChartSpec("story_map", "heatmap", "User Story Test Map", "story_map"),
        ChartSpec("defect_trend", "line", "Defect Discovery Trend", "defect_trend", x_axis="sprint", y_axis="count"),
    ],
}


class DashboardGenerator:
    """
    Generates self-contained React+Recharts HTML dashboards
    from test run data with stakeholder presets.
    """

    def __init__(self, output_dir: str = "/tmp/dashboards"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def generate(
        self,
        run_data: Dict[str, Any],
        config: Optional[DashboardConfig] = None,
    ) -> str:
        """
        Generate a dashboard HTML file from run data.
        Returns the file path.
        """
        config = config or DashboardConfig()

        # Use preset charts if none custom
        if not config.charts:
            config.charts = _PRESET_CHARTS.get(config.preset, _PRESET_CHARTS[StakeholderPreset.QA_LEAD])

        # Extract and transform data for charts
        chart_data = self._prepare_chart_data(run_data, config.charts)

        # Render HTML
        html = self._render_html(config, chart_data, run_data)

        # Write file
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"dashboard_{config.preset.value}_{timestamp}.html"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info("Dashboard generated: %s (%d charts)", filepath, len(config.charts))
        return filepath

    def generate_all_presets(self, run_data: Dict[str, Any]) -> Dict[str, str]:
        """Generate dashboards for all stakeholder presets."""
        results = {}
        for preset in StakeholderPreset:
            config = DashboardConfig(
                title=f"QA Dashboard — {preset.value.replace('_', ' ').title()}",
                preset=preset,
            )
            path = self.generate(run_data, config)
            results[preset.value] = path
        return results

    # ── data preparation ───────────────────────────────────────

    def _prepare_chart_data(
        self, run_data: Dict[str, Any], charts: List[ChartSpec],
    ) -> Dict[str, Any]:
        """Transform run data into chart-specific data structures."""
        results = run_data.get("results", [])
        chart_data: Dict[str, Any] = {}

        # Pass rate
        total = len(results)
        passed = sum(1 for r in results if r.get("status") == "passed")
        chart_data["pass_rate"] = round((passed / total * 100) if total else 0, 1)

        # Trend (from history if available)
        history = run_data.get("history", [])
        chart_data["trend"] = [
            {"run": h.get("run_id", f"Run {i+1}"), "rate": h.get("pass_rate", 0)}
            for i, h in enumerate(history[-30:])
        ]

        # Bug severity distribution
        bugs = run_data.get("bugs", [])
        sev_counts = {}
        for b in bugs:
            sev = b.get("severity", "medium")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
        chart_data["bug_severity"] = [
            {"name": k.title(), "value": v} for k, v in sev_counts.items()
        ]

        # Risk by module
        modules = {}
        for r in results:
            mod = r.get("module", "Unknown")
            if mod not in modules:
                modules[mod] = {"total": 0, "failed": 0}
            modules[mod]["total"] += 1
            if r.get("status") != "passed":
                modules[mod]["failed"] += 1
        chart_data["risk_by_module"] = [
            {"module": m, "risk": round(d["failed"] / d["total"] * 100, 1) if d["total"] else 0}
            for m, d in modules.items()
        ]

        # Failure clusters
        error_clusters = {}
        for r in results:
            if r.get("status") == "failed":
                err = r.get("error_type", r.get("error", "Unknown")[:50])
                error_clusters[err] = error_clusters.get(err, 0) + 1
        chart_data["failure_clusters"] = sorted(
            [{"cluster": k, "count": v} for k, v in error_clusters.items()],
            key=lambda x: x["count"], reverse=True,
        )[:15]

        # Flaky tests
        chart_data["flaky_tests"] = [
            {"test": r.get("name", "?")[:40], "flaky_count": r.get("flaky_count", 0)}
            for r in results if r.get("flaky_count", 0) > 0
        ][:10]

        # Execution time
        chart_data["exec_time"] = [
            {"run": h.get("run_id", f"Run {i+1}"), "seconds": h.get("duration_sec", 0)}
            for i, h in enumerate(history[-20:])
        ]

        # Error types
        chart_data["error_types"] = chart_data["failure_clusters"]

        # Test durations
        chart_data["test_durations"] = [
            {"test": r.get("name", "?")[:30], "ms": r.get("duration_ms", 0)}
            for r in results[:50]
        ]

        # Recent failures
        chart_data["recent_fails"] = [
            {"test": r.get("name", "?")[:40], "count": 1}
            for r in results if r.get("status") == "failed"
        ][:10]

        # Gate score
        chart_data["gate_score"] = run_data.get("gate_score", chart_data["pass_rate"])

        # Blockers
        chart_data["blockers"] = [
            {"category": b.get("category", "General"), "count": 1}
            for b in bugs if b.get("severity") in ("critical", "high")
        ]

        # Feature coverage
        features = run_data.get("features", {})
        chart_data["feature_coverage"] = [
            {"feature": f, "percent": d.get("coverage", 0)}
            for f, d in features.items()
        ]

        # Coverage matrix / heatmap
        chart_data["coverage_matrix"] = self._build_coverage_matrix(results)

        # Env pass rate
        envs = {}
        for r in results:
            e = r.get("environment", "default")
            if e not in envs:
                envs[e] = {"total": 0, "passed": 0}
            envs[e]["total"] += 1
            if r.get("status") == "passed":
                envs[e]["passed"] += 1
        chart_data["env_pass_rate"] = [
            {"env": e, "rate": round(d["passed"]/d["total"]*100, 1) if d["total"] else 0}
            for e, d in envs.items()
        ]

        # Sign-off and story map (stubbed)
        chart_data["signoff"] = run_data.get("signoff", [])
        chart_data["story_map"] = run_data.get("story_map", [])
        chart_data["defect_trend"] = run_data.get("defect_trend", [])

        return chart_data

    def _build_coverage_matrix(self, results: List[Dict]) -> List[Dict]:
        matrix = {}
        for r in results:
            mod = r.get("module", "Unknown")
            cat = r.get("category", "General")
            key = f"{mod}|{cat}"
            if key not in matrix:
                matrix[key] = {"module": mod, "category": cat, "total": 0, "passed": 0}
            matrix[key]["total"] += 1
            if r.get("status") == "passed":
                matrix[key]["passed"] += 1
        return [
            {**v, "coverage": round(v["passed"]/v["total"]*100) if v["total"] else 0}
            for v in matrix.values()
        ]

    # ── HTML rendering ─────────────────────────────────────────

    def _render_html(
        self, config: DashboardConfig, chart_data: Dict[str, Any],
        run_data: Dict[str, Any],
    ) -> str:
        bg = "#ffffff" if config.theme == "light" else "#1a1a2e"
        text_color = "#333333" if config.theme == "light" else "#e0e0e0"
        card_bg = "#f8f9fa" if config.theme == "light" else "#16213e"

        charts_json = json.dumps(chart_data, default=str)
        specs_json = json.dumps([
            {
                "id": c.chart_id, "type": c.chart_type, "title": c.title,
                "dataKey": c.data_key, "xAxis": c.x_axis, "yAxis": c.y_axis,
                "colors": c.color_scheme, "height": c.height, "stacked": c.stacked,
            } for c in config.charts
        ])

        summary = {
            "total_tests": len(run_data.get("results", [])),
            "passed": sum(1 for r in run_data.get("results", []) if r.get("status") == "passed"),
            "failed": sum(1 for r in run_data.get("results", []) if r.get("status") == "failed"),
            "skipped": sum(1 for r in run_data.get("results", []) if r.get("status") == "skipped"),
            "bugs_filed": len(run_data.get("bugs", [])),
            "pass_rate": chart_data.get("pass_rate", 0),
            "generated": datetime.utcnow().isoformat(),
        }
        summary_json = json.dumps(summary)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{config.title}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.production.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.production.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/recharts/2.8.0/Recharts.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: {bg}; color: {text_color}; }}
  .header {{ background: linear-gradient(135deg, #2E75B6, #1a4a7a); color: white;
            padding: 24px 32px; display: flex; justify-content: space-between; align-items: center; }}
  .header h1 {{ font-size: 22px; font-weight: 600; }}
  .header .meta {{ font-size: 13px; opacity: 0.85; }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
             gap: 16px; padding: 24px 32px; }}
  .kpi-card {{ background: {card_bg}; border-radius: 10px; padding: 20px;
              text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
  .kpi-card .value {{ font-size: 32px; font-weight: 700; }}
  .kpi-card .label {{ font-size: 13px; opacity: 0.7; margin-top: 4px; }}
  .kpi-pass {{ color: #2ECC71; }}
  .kpi-fail {{ color: #E74C3C; }}
  .kpi-skip {{ color: #F39C12; }}
  .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
                 gap: 20px; padding: 0 32px 32px; }}
  .chart-card {{ background: {card_bg}; border-radius: 10px; padding: 20px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
  .chart-card h3 {{ font-size: 15px; margin-bottom: 12px; font-weight: 600; }}
  .footer {{ text-align: center; padding: 16px; font-size: 12px; opacity: 0.5; }}
</style>
</head>
<body>
<div class="header">
  <div><h1>{config.title}</h1>
  <div class="meta">Preset: {config.preset.value} | Generated: <span id="gen-time"></span></div></div>
</div>
<div id="kpi-row" class="kpi-row"></div>
<div id="charts" class="charts-grid"></div>
<div class="footer">AI QA Agent v5 — Dashboard Generator</div>
<script>
const CHART_DATA = {charts_json};
const CHART_SPECS = {specs_json};
const SUMMARY = {summary_json};
document.getElementById('gen-time').textContent = new Date(SUMMARY.generated).toLocaleString();

// KPI cards
const kpiRow = document.getElementById('kpi-row');
const kpis = [
  {{label:'Total Tests', value:SUMMARY.total_tests, cls:''}},
  {{label:'Passed', value:SUMMARY.passed, cls:'kpi-pass'}},
  {{label:'Failed', value:SUMMARY.failed, cls:'kpi-fail'}},
  {{label:'Skipped', value:SUMMARY.skipped, cls:'kpi-skip'}},
  {{label:'Pass Rate', value:SUMMARY.pass_rate+'%', cls:SUMMARY.pass_rate>=80?'kpi-pass':'kpi-fail'}},
  {{label:'Bugs Filed', value:SUMMARY.bugs_filed, cls:'kpi-fail'}},
];
kpis.forEach(k => {{
  const card = document.createElement('div');
  card.className = 'kpi-card';
  card.innerHTML = '<div class="value '+k.cls+'">'+k.value+'</div><div class="label">'+k.label+'</div>';
  kpiRow.appendChild(card);
}});

// Chart rendering (simplified — renders data tables as fallback)
const chartsDiv = document.getElementById('charts');
CHART_SPECS.forEach(spec => {{
  const card = document.createElement('div');
  card.className = 'chart-card';
  const data = CHART_DATA[spec.dataKey];
  let content = '<h3>'+spec.title+'</h3>';
  if (spec.type === 'gauge') {{
    const val = typeof data === 'number' ? data : 0;
    const color = val >= 80 ? '#2ECC71' : val >= 60 ? '#F39C12' : '#E74C3C';
    content += '<div style="text-align:center"><div style="font-size:64px;font-weight:700;color:'+color+'">'+val+'%</div></div>';
  }} else if (Array.isArray(data) && data.length > 0) {{
    content += '<div id="chart-'+spec.id+'" style="height:'+spec.height+'px;overflow:auto;">';
    content += '<table style="width:100%;border-collapse:collapse;font-size:13px;">';
    const keys = Object.keys(data[0]);
    content += '<tr>' + keys.map(k => '<th style="text-align:left;padding:6px;border-bottom:2px solid #ddd;">'+k+'</th>').join('') + '</tr>';
    data.slice(0, 20).forEach(row => {{
      content += '<tr>' + keys.map(k => '<td style="padding:6px;border-bottom:1px solid #eee;">'+(row[k]??'')+'</td>').join('') + '</tr>';
    }});
    content += '</table></div>';
  }} else {{
    content += '<div style="padding:20px;opacity:0.5;text-align:center;">No data available</div>';
  }}
  card.innerHTML = content;
  chartsDiv.appendChild(card);
}});
</script>
</body>
</html>"""