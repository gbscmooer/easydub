"""Web 三端点测试：pipeline 打桩，不跑真实流水线。"""
import importlib
import sys

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYDUB_JOBS_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("EASYDUB_UPLOAD_DIR", str(tmp_path / "uploads"))
    from fastapi.testclient import TestClient
    import server.app as app_mod
    importlib.reload(app_mod)

    def fake_run(video, lang, **kw):
        out = tmp_path / "out.dub.en.mp4"
        out.write_bytes(b"fake mp4")
        for stage in ("asr", "translate", "mix", "done"):
            kw["progress"](stage)
        return out

    monkeypatch.setattr(app_mod, "run_managed", fake_run)
    return TestClient(app_mod.app)


def test_full_job_flow(client):
    resp = client.post("/api/jobs",
                       files={"video": ("a.mp4", b"fakevideo", "video/mp4")},
                       data={"lang": "en"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    import time
    state = {"state": "queued"}
    for _ in range(50):  # 后台线程异步执行，轮询到终态
        state = client.get(f"/api/jobs/{job_id}").json()
        if state["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert state["state"] == "done"
    assert state["stage"] == "done"

    result = client.get(f"/api/jobs/{job_id}/result")
    assert result.status_code == 200
    assert result.content == b"fake mp4"


def test_rejects_bad_ext_and_lang(client):
    r1 = client.post("/api/jobs",
                     files={"video": ("a.txt", b"x", "text/plain")},
                     data={"lang": "en"})
    assert r1.status_code == 400
    r2 = client.post("/api/jobs",
                     files={"video": ("a.mp4", b"x", "video/mp4")},
                     data={"lang": "fr"})
    assert r2.status_code == 400


def test_unknown_job_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/result").status_code == 404
