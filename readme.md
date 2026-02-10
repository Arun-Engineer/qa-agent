# 🤖 QA Agent: Auto-Planning, Testing, Reporting Agent

This AI agent can:
- Generate test steps from natural language specs
- Run tests via `pytest`, `Playwright`, or `API`
- File bugs, generate HTML/PDF reports
- Auto-ingest from logs, GitHub, Slack, and Jira
- Run fully headless via FastAPI or ECS/Lambda

---

## 🚀 Features

- ✅ LLM-based test generation from spec
- ✅ Pytest + Playwright + API testing support
- ✅ Auto-ingests GitHub issues, failed builds, logs
- ✅ Slack `/qa-test` command integration
- ✅ PDF + JSON log archival per run
- ✅ ECS + GitHub Actions CI/CD ready

---

## 🏗️ Folder Structure

See full structure [here](#folder-structure-final-summary). Key modules:

| Path | Purpose |
|------|---------|
| `main.py` | CLI & FastAPI entry |
| `slack_trigger.py` | Slack webhook trigger |
| `agent/planner.py` | Converts spec → tool steps |
| `agent/tools/` | Testing tools (pytest, playwright, api) |
| `agent/ingestion/` | Auto-run via logs, GitHub, Jira |
| `agent/utils/llm_utils.py` | Convert error logs → specs |

---

## 🐳 Local Docker Run

```bash
docker build -t qa-agent .
docker run -p 8000:8000 qa-agent
