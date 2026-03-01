"""Integration tests for Runs API."""


class TestRunsAPI:
    def _create_session(self, client, env="sit"):
        r = client.post("/api/v1/sessions/", json={
            "user_id": "u1", "environment": env, "task": "test"
        })
        return r.json()["session_id"]

    def test_create_run(self, client):
        sid = self._create_session(client)
        r = client.post("/api/v1/runs/", json={
            "session_id": sid, "test_type": "smoke"
        })
        assert r.status_code == 201
        assert r.json()["status"] == "queued"
        assert r.json()["session_id"] == sid

    def test_create_run_with_url(self, client):
        sid = self._create_session(client)
        r = client.post("/api/v1/runs/", json={
            "session_id": sid, "test_type": "discovery",
            "target_url": "https://example.com"
        })
        assert r.status_code == 201
        assert r.json()["target_url"] == "https://example.com"

    def test_create_run_invalid_session(self, client):
        r = client.post("/api/v1/runs/", json={
            "session_id": "nonexistent", "test_type": "smoke"
        })
        assert r.status_code == 404

    def test_list_runs(self, client):
        sid = self._create_session(client)
        client.post("/api/v1/runs/", json={"session_id": sid, "test_type": "smoke"})
        client.post("/api/v1/runs/", json={"session_id": sid, "test_type": "regression"})
        r = client.get("/api/v1/runs/")
        assert r.json()["total"] == 2

    def test_list_runs_by_session(self, client):
        s1 = self._create_session(client)
        s2 = self._create_session(client)
        client.post("/api/v1/runs/", json={"session_id": s1, "test_type": "smoke"})
        client.post("/api/v1/runs/", json={"session_id": s2, "test_type": "smoke"})
        r = client.get(f"/api/v1/runs/?session_id={s1}")
        assert r.json()["total"] == 1

    def test_get_run(self, client):
        sid = self._create_session(client)
        create = client.post("/api/v1/runs/", json={"session_id": sid, "test_type": "smoke"})
        rid = create.json()["run_id"]
        r = client.get(f"/api/v1/runs/{rid}")
        assert r.status_code == 200
        assert r.json()["run_id"] == rid

    def test_update_run_status(self, client):
        sid = self._create_session(client)
        create = client.post("/api/v1/runs/", json={"session_id": sid, "test_type": "smoke"})
        rid = create.json()["run_id"]
        r = client.patch(f"/api/v1/runs/{rid}/status?status=completed")
        assert r.json()["status"] == "completed"
        assert r.json()["completed_at"] is not None
