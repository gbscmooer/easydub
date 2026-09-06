"""Web 三端点测试：pipeline 打桩，不跑真实流水线。"""
import importlib
import sys
from pathlib import Path

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


def test_bgm_and_lipsync_flags_reach_pipeline(client, monkeypatch):
    import server.app as app_mod
    seen = {}

    def fake_run(video, lang, **kw):
        seen.update(kw)
        out = app_mod.ROOT / "nonexistent.dub.en.mp4"
        for stage in ("asr", "done"):
            kw["progress"](stage)
        return out

    monkeypatch.setattr(app_mod, "run_managed", fake_run)
    resp = client.post("/api/jobs",
                       files={"video": ("a.mp4", b"x", "video/mp4")},
                       data={"lang": "en", "bgm": "false", "lipsync": "true",
                             "subtitle_style": "FontSize=30,Outline=2"})
    assert resp.status_code == 200
    import time
    for _ in range(50):
        if client.get(f"/api/jobs/{resp.json()['job_id']}").json()["state"] \
                in ("done", "error"):
            break
        time.sleep(0.05)
    assert seen["keep_bgm"] is False
    assert seen["lipsync_provider"] == "latentsync"
    assert seen["subtitle_style"] == "FontSize=30,Outline=2"


def test_job_report_endpoint(client, tmp_path, monkeypatch):
    import server.app as app_mod
    monkeypatch.setattr(app_mod, "ROOT", tmp_path)

    def fake_run(video, lang, **kw):
        # pipeline 会把 report.<lang>.html 写到 artifacts/<上传文件名stem>/ 下
        out_dir = tmp_path / "artifacts" / Path(video).stem
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"report.{lang}.html").write_text("<h1>ok</h1>")
        for stage in ("asr", "done"):
            kw["progress"](stage)
        return video

    monkeypatch.setattr(app_mod, "run_managed", fake_run)
    import time
    r = client.post("/api/jobs",
                    files={"video": ("a.mp4", b"x", "video/mp4")},
                    data={"lang": "en"})
    jid = r.json()["job_id"]
    for _ in range(50):
        if client.get(f"/api/jobs/{jid}").json()["state"] == "done":
            break
        time.sleep(0.05)
    resp = client.get(f"/api/jobs/{jid}/report")
    assert resp.status_code == 200
    assert "<h1>ok</h1>" in resp.text
    # 未完成任务与不存在任务
    assert client.get("/api/jobs/nope/report").status_code == 404


def test_job_list_returns_history(client):
    import time
    ids = []
    for name in ("a.mp4", "b.mp4"):
        r = client.post("/api/jobs",
                        files={"video": (name, b"x", "video/mp4")},
                        data={"lang": "ja"})
        assert r.status_code == 200, r.text  # 并发=1：前一个没跑完会 409
        ids.append(r.json()["job_id"])
        for _ in range(50):  # 等前一个任务释放并发名额
            if client.get(f"/api/jobs/{ids[-1]}").json()["state"] \
                    in ("done", "error"):
                break
            time.sleep(0.05)
    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 2
    assert {j["lang"] for j in jobs} == {"ja"}
    assert "bgm" in jobs[0]


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


def test_upload_over_limit_413_and_slot_released(client, monkeypatch):
    import server.app as app_mod
    monkeypatch.setattr(app_mod, "MAX_UPLOAD", 1024)  # 1KB 上限
    big = b"x" * (2048)
    r = client.post("/api/jobs",
                    files={"video": ("big.mp4", big, "video/mp4")},
                    data={"lang": "en"})
    assert r.status_code == 413
    # 名额必须归还：413 之后仍能正常提交
    r2 = client.post("/api/jobs",
                     files={"video": ("ok.mp4", b"x", "video/mp4")},
                     data={"lang": "en"})
    assert r2.status_code == 200


def test_delete_terminal_job(client):
    import time
    r = client.post("/api/jobs",
                    files={"video": ("a.mp4", b"x", "video/mp4")},
                    data={"lang": "en"})
    jid = r.json()["job_id"]
    for _ in range(50):
        if client.get(f"/api/jobs/{jid}").json()["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert client.delete(f"/api/jobs/{jid}").status_code == 200
    assert client.get(f"/api/jobs/{jid}").status_code == 404
    assert client.delete(f"/api/jobs/{jid}").status_code == 404


def test_delete_running_job_409(client):
    import sqlite3, time
    r = client.post("/api/jobs",
                    files={"video": ("a.mp4", b"x", "video/mp4")},
                    data={"lang": "en"})
    jid = r.json()["job_id"]
    # 抢在 worker 结束前把状态改成 running（打桩流水线很快）
    import server.app as app_mod
    with sqlite3.connect(app_mod.DB_PATH) as con:
        con.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))
    try:
        assert client.delete(f"/api/jobs/{jid}").status_code == 409
    finally:
        time.sleep(0.3)  # 让 worker 收尾，不污染后续断言


def test_run_managed_wires_progress_callback(monkeypatch):
    # 回归：run() 会用 progress 参数覆盖模块级回调，run_managed 必须显式传，
    # 否则 Web 进度条永远停在提交瞬间（percent 卡 5%）
    import easydub.pipeline as p
    seen = {}

    def fake_run(video, lang, **kw):
        seen.update(kw)
        return video

    monkeypatch.setattr(p, "run", fake_run)
    p.run_managed("v", "en", progress=lambda s: None)
    assert callable(seen.get("progress"))
    assert p._progress_cb is None  # finally 复位仍生效
