"""Integration tests for Environment API."""


class TestEnvironmentAPI:
    def test_list_environments(self, client):
        r = client.get("/api/v1/environments/")
        assert r.status_code == 200
        assert r.json()["total"] >= 3

    def test_get_sit(self, client):
        r = client.get("/api/v1/environments/sit")
        assert r.status_code == 200
        assert r.json()["access_mode"] == "full"

    def test_get_prod(self, client):
        r = client.get("/api/v1/environments/prod")
        assert r.status_code == 200
        assert r.json()["access_mode"] == "read_only"
        assert r.json()["approval_required"] is True

    def test_get_nonexistent(self, client):
        r = client.get("/api/v1/environments/staging")
        assert r.status_code == 404
