"""Integration tests for Session API endpoints."""


class TestSessionAPI:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "active_sessions" in data

    def test_create_session_sit(self, client):
        r = client.post("/api/v1/sessions/", json={
            "user_id": "user1", "environment": "sit", "task": "test login"
        })
        assert r.status_code == 201
        data = r.json()
        assert data["user_id"] == "user1"
        assert data["environment"] == "sit"
        assert data["access_mode"] == "full"
        assert data["can_write"] is True

    def test_create_session_prod_is_readonly(self, client):
        r = client.post("/api/v1/sessions/", json={
            "user_id": "user1", "environment": "prod", "task": "observe checkout"
        })
        assert r.status_code == 201
        data = r.json()
        assert data["access_mode"] == "read_only"
        assert data["can_write"] is False

    def test_create_session_invalid_env(self, client):
        r = client.post("/api/v1/sessions/", json={
            "user_id": "user1", "environment": "staging", "task": "test"
        })
        assert r.status_code == 422  # Pydantic validation

    def test_list_sessions(self, client):
        client.post("/api/v1/sessions/", json={"user_id": "u1", "environment": "sit", "task": "a"})
        client.post("/api/v1/sessions/", json={"user_id": "u2", "environment": "uat", "task": "b"})
        r = client.get("/api/v1/sessions/")
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_list_sessions_filter_by_user(self, client):
        client.post("/api/v1/sessions/", json={"user_id": "u1", "environment": "sit", "task": "a"})
        client.post("/api/v1/sessions/", json={"user_id": "u2", "environment": "sit", "task": "b"})
        r = client.get("/api/v1/sessions/?user_id=u1")
        assert r.json()["total"] == 1

    def test_get_session(self, client):
        create = client.post("/api/v1/sessions/", json={
            "user_id": "u1", "environment": "uat", "task": "test"
        })
        sid = create.json()["session_id"]
        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["session_id"] == sid

    def test_get_session_not_found(self, client):
        r = client.get("/api/v1/sessions/nonexistent")
        assert r.status_code == 404

    def test_cancel_session(self, client):
        create = client.post("/api/v1/sessions/", json={
            "user_id": "u1", "environment": "sit", "task": "test"
        })
        sid = create.json()["session_id"]
        r = client.delete(f"/api/v1/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

    def test_validate_action_write_in_sit(self, client):
        create = client.post("/api/v1/sessions/", json={
            "user_id": "u1", "environment": "sit", "task": "test"
        })
        sid = create.json()["session_id"]
        r = client.post(f"/api/v1/sessions/{sid}/validate-action?action=write")
        assert r.json()["allowed"] is True

    def test_validate_action_write_in_prod(self, client):
        create = client.post("/api/v1/sessions/", json={
            "user_id": "u1", "environment": "prod", "task": "test"
        })
        sid = create.json()["session_id"]
        r = client.post(f"/api/v1/sessions/{sid}/validate-action?action=write")
        assert r.json()["allowed"] is False

    def test_auth_required(self, unauth_client):
        r = unauth_client.get("/api/v1/sessions/")
        assert r.status_code == 401

    def test_health_no_auth_needed(self, unauth_client):
        r = unauth_client.get("/health")
        assert r.status_code == 200
