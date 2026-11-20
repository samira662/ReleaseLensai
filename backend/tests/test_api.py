"""End-to-end smoke test with GitHub mocked and the LLM unavailable (offline heuristic mode)."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_api.db"
os.environ["CHROMA_PATH"] = "./test_chroma"
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from app.services import github_service as gs


class FakeGH:
    def __init__(self, token=None):
        pass

    def list_refs(self, full):
        return {"repo": full, "default_branch": "main", "tags": ["v1", "v2"], "branches": ["main"]}

    def fetch_changes(self, full, base, head, max_commits=None):
        ch = [
            gs.ChangeData("a" * 40, "feat: add export to PDF (#3)", "feat: add export to PDF", "eva", "http://x/a", 3,
                          files=[gs.FileChange("app/export.py", "modified", 40, 2, "+def export(x): ...")]),
            gs.ChangeData("b" * 40, "fix: crash on empty login", "fix: crash on empty login", "mim", "http://x/b",
                          files=[gs.FileChange("app/auth.py", "modified", 5, 1, "")]),
            gs.ChangeData("c" * 40, "docs: update README", "docs: update README", "khusbo", "http://x/c",
                          files=[gs.FileChange("README.md", "modified", 3, 0, "")]),
        ]
        files = [gs.FileChange("app/auth.py", "modified", 5, 1), gs.FileChange("app/export.py", "modified", 40, 2)]
        return ch, files

    def get_file_content(self, full, path, ref, max_bytes=0):
        if path == "app/auth.py":
            return "def login(user, password): pass\n" if ref == "v1" else "def login(user): pass\n"
        return "def export(x): pass\n"

    def file_fix_history(self, *a, **k):
        return 3, 10


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("app.routers.projects.GitHubService", FakeGH)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return TestClient(app)


def test_full_flow(client):
    r = client.post("/api/auth/register", json={"email": "eva@example.com", "name": "Eva", "password": "password123"})
    assert r.status_code == 201
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.get("/api/projects").status_code == 401

    pid = client.post("/api/projects", headers=h, json={"repo_url": "https://github.com/o/r", "base_ref": "v1", "target_ref": "v2", "github_token": "ghp_secret"}).json()["id"]
    p = client.get(f"/api/projects/{pid}", headers=h).json()
    assert p["status"] == "done", p

    changes = client.get(f"/api/projects/{pid}/changes", headers=h).json()
    cats = {c["sha"][0]: c["category"] for c in changes}
    assert cats["a"] == "feature" and cats["c"] == "docs" and cats["b"] == "breaking"  # b: login() signature changed

    assert client.get(f"/api/projects/{pid}/breaking", headers=h).json()[0]["kind"] == "param_removed"
    risk = client.get(f"/api/projects/{pid}/risk", headers=h).json()
    assert risk["level"] in ("Low", "Medium", "High") and risk["factors"]

    notes = client.get(f"/api/projects/{pid}/notes", headers=h).json()
    assert {n["audience"] for n in notes} == {"end_user", "developer", "qa"}
    assert client.get(f"/api/projects/{pid}/notes/developer/export?format=md", headers=h).status_code == 409
    client.post(f"/api/projects/{pid}/notes/developer/approve", headers=h)
    assert client.get(f"/api/projects/{pid}/notes/developer/export?format=md", headers=h).status_code == 200
    pdf = client.get(f"/api/projects/{pid}/notes/developer/export?format=pdf", headers=h)
    assert pdf.content.startswith(b"%PDF")

    qa = client.post(f"/api/projects/{pid}/qa", headers=h, json={"question": "what changed in login?"}).json()
    assert qa["sources"] and qa["mode"] == "extractive"

    # a second user cannot see the project
    h2 = {"Authorization": "Bearer " + client.post("/api/auth/register", json={"email": "b@example.com", "name": "B", "password": "password123"}).json()["access_token"]}
    assert client.get(f"/api/projects/{pid}", headers=h2).status_code == 404
