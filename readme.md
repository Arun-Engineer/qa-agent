# 🤖 QA Agent: Self-Driving Test Engineer

AI-powered Quality Assurance Agent that plans, writes, executes, and verifies tests — with zero manual effort.

---

## 💡 Features

- 🧠 Auto-generates tests from natural language spec
- 🧪 Runs Pytest, Playwright, and API validations
- 🧾 Verifies bugs and files structured bug reports
- 🕵️‍♀️ Ingests logs, Jira/GitHub issues, Prometheus alerts
- ⚙️ FastAPI wrapper + CLI
- 📤 Slack-triggerable: `/qa-test` → runs a plan
- 🧩 Optional LLM: auto-spec from logs (`llm_utils.py`)
- 🚀 ECS/Lambda auto-deployment via GitHub Actions

---

## 📦 Installation

```bash
git clone https://github.com/Arun-Engineer/qa-agent.git
cd qa-agent
pip install -r requirements.txt
