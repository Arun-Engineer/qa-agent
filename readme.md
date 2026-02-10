# 🧠 Autonomous QA Agent

An AI-powered Quality Assurance agent that reads specs (or bugs/logs), plans test steps using LLMs, runs them (via pytest/playwright/API), logs results, and files bugs — all autonomously.

---

## 🚀 Features

- ✅ LLM-based test planning + codegen
- ✅ Pytest + Playwright runner integration
- ✅ Slack alerts on failures
- ✅ Auto-ingestion from GitHub, Jira, logs
- ✅ Auto-bug filing (Jira/GitHub)
- ✅ PDF + HTML reports
- ✅ Prometheus + Grafana support
- ✅ ECS + Lambda deploy templates

---

## 📁 Folder Structure

See architecture summary [above](#final-project-structure-summary-post-ingestion--slack--llm-expansion).

---

## ⚙️ Run (Locally)

```bash
python main.py --spec "Test login with invalid password" --html --trace