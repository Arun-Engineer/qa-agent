"""
stress/locustfile.py — Load Testing for AI QA Platform

Simulates multiple concurrent users:
  - Dashboard viewers (high frequency, light load)
  - Test executors (low frequency, heavy load)
  - Admin users (medium frequency, medium load)
  - Chat users (medium frequency, medium load)

Usage:
    pip install locust
    locust -f stress/locustfile.py --host=http://localhost:8000

    # Headless (CI mode):
    locust -f stress/locustfile.py --host=http://localhost:8000 \
        --users 50 --spawn-rate 5 --run-time 5m --headless \
        --csv=reports/loadtest
"""
from locust import HttpUser, task, between, tag


class DashboardViewer(HttpUser):
    """
    Simulates users who mostly view the dashboard.
    High frequency, light endpoints.
    """
    weight = 5  # Most common user type
    wait_time = between(5, 15)

    def on_start(self):
        """Login to get session."""
        self.client.post("/login", data={
            "email": "loadtest@example.com",
            "password": "loadtest123",
        })

    @tag("dashboard")
    @task(10)
    def view_dashboard(self):
        self.client.get("/api/metrics")

    @tag("dashboard")
    @task(3)
    def view_runs(self):
        self.client.get("/api/runs")

    @tag("health")
    @task(2)
    def health_check(self):
        self.client.get("/health")

    @tag("dashboard")
    @task(1)
    def view_llm_info(self):
        self.client.get("/api/llm/info")


class TestExecutor(HttpUser):
    """
    Simulates users running test specs.
    Low frequency, heavy endpoints (LLM calls).
    """
    weight = 2
    wait_time = between(30, 120)  # Runs are slow, users wait

    def on_start(self):
        self.client.post("/login", data={
            "email": "loadtest@example.com",
            "password": "loadtest123",
        })

    @tag("execute")
    @task(1)
    def run_spec(self):
        """Submit a test spec for execution."""
        self.client.post("/api/run", json={
            "spec": "Test the login page validates empty email and password fields",
            "task_type": "generate_testcases",
            "workflow_name": "api_test",
            "options": {},
            "use_rag": False,  # Skip RAG for load test
            "html": False,
            "trace": False,
        }, timeout=180)  # 3 min timeout for LLM-heavy operations


class AdminUser(HttpUser):
    """
    Simulates admin users managing tenants.
    Medium frequency, medium load.
    """
    weight = 1
    wait_time = between(10, 30)

    def on_start(self):
        self.client.post("/login", data={
            "email": "loadtest@example.com",
            "password": "loadtest123",
        })

    @tag("admin")
    @task(5)
    def view_admin_me(self):
        self.client.get("/api/admin/me")

    @tag("admin")
    @task(3)
    def view_members(self):
        self.client.get("/api/admin/members")

    @tag("admin")
    @task(2)
    def view_audit(self):
        self.client.get("/api/admin/audit")

    @tag("admin")
    @task(1)
    def view_invites(self):
        self.client.get("/api/admin/invites")


class ChatUser(HttpUser):
    """
    Simulates users using Ask QA chat.
    Medium frequency, medium-heavy (LLM calls).
    """
    weight = 2
    wait_time = between(15, 60)

    def on_start(self):
        self.client.post("/login", data={
            "email": "loadtest@example.com",
            "password": "loadtest123",
        })
        # Start a chat session
        resp = self.client.post("/api/chat/start")
        if resp.status_code == 200:
            data = resp.json()
            self.conversation_id = data.get("conversation_id")
        else:
            self.conversation_id = None

    @tag("chat")
    @task(1)
    def send_chat_message(self):
        if not self.conversation_id:
            return
        self.client.post("/api/chat/send", json={
            "conversation_id": self.conversation_id,
            "message": "What are the best practices for API testing?",
            "use_rag": True,
        }, timeout=60)
