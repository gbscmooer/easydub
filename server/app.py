"""Web 产品面：FastAPI 三端点 + SQLite 任务表 + React 前端静态托管。

  POST /api/jobs           multipart(video, lang) -> {"job_id"}
  GET  /api/jobs/{job_id}  -> {"state": queued|running|done|error, "stage", "error"}
  GET  /api/jobs           -> 任务列表（调试用）
  GET  /api/jobs/{job_id}/result -> 成品 mp4 文件流

同一套端点留给 Dify（D8）HTTP 节点复用。并发 = 1（GOAL §4.3）：
有任务在跑时提交返回 409。前端构建产物由根路径静态托管。

启动: .venv/bin/python -m uvicorn server.app:app --port 8000
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from easydub.pipeline import run_managed

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = Path(os.environ.get("EASYDUB_UPLOAD_DIR", ROOT / "artifacts" / "uploads"))
DB_PATH = Path(os.environ.get("EASYDUB_JOBS_DB", ROOT / "artifacts" / "jobs.db"))
ALLOWED_LANGS = {"en", "zh", "ja", "ko", "es"}
ALLOWED_EXT = {".mp4", ".mov"}

app = FastAPI(title="easydub web")
_busy = threading.Semaphore(1)

# 阶段 → 大致进度百分比（给前端进度条用，粒度到阶段即可）
STAGE_PERCENT = {
    "upload": 5, "asr": 20, "translate": 40, "tts": 55, "retry": 65,
    "mix": 75, "lipsync": 88, "done": 100,
}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,          -- queued|running|done|error
                stage TEXT NOT NULL DEFAULT '',
                lang TEXT NOT NULL,
                video TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL
            )
        """)
        for col in ("lipsync INTEGER NOT NULL DEFAULT 0",
                    "percent INTEGER NOT NULL DEFAULT 0",
                    "bgm INTEGER NOT NULL DEFAULT 1"):
            try:  # 旧库平滑加列
                con.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass


_init_db()


def _worker(job_id: str, video: Path, lang: str, lipsync: bool,
            bgm: bool = True) -> None:
    def progress(stage: str) -> None:
        with _db() as con:
            con.execute("UPDATE jobs SET stage=?, "
                        "percent=MAX(percent, ?) WHERE id=?",
                        (stage, STAGE_PERCENT.get(stage, 0), job_id))

    try:
        out = run_managed(video, lang, progress=progress,
                          lipsync_provider="latentsync" if lipsync else "none",
                          keep_bgm=bgm,
                          workdir=str(ROOT / "artifacts"))
        with _db() as con:
            con.execute("UPDATE jobs SET state='done', stage='done', result=? "
                        "WHERE id=?", (str(out), job_id))
    except Exception as e:  # 任何失败都落到任务表，前端可读
        with _db() as con:
            con.execute("UPDATE jobs SET state='error', error=? WHERE id=?",
                        (str(e)[-800:], job_id))
    finally:
        _busy.release()


@app.post("/api/jobs")
async def submit_job(video: UploadFile = File(...),
                     lang: str = Form("en"),
                     lipsync: bool = Form(False),
                     bgm: bool = Form(True)) -> dict:
    ext = Path(video.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"仅支持 {'/'.join(ALLOWED_EXT)}，收到 {ext or '无名文件'}")
    if lang not in ALLOWED_LANGS:
        raise HTTPException(400, f"不支持的目标语言 {lang}")
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "已有任务在跑（并发=1），请稍后再试")

    job_id = uuid.uuid4().hex[:12]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dst = UPLOAD_DIR / f"{job_id}{ext}"
    with open(dst, "wb") as f:  # 流式落盘，避免大视频吃内存
        while chunk := await video.read(1 << 20):
            f.write(chunk)

    with _db() as con:
        con.execute(
            "INSERT INTO jobs (id, state, stage, lang, video, result, error,"
            " created, lipsync, percent, bgm) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, "queued", "upload", lang, str(dst), "", "", time.time(),
             int(lipsync), STAGE_PERCENT.get("upload", 0), int(bgm)))
    threading.Thread(target=_worker, args=(job_id, dst, lang, lipsync, bgm),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs")
def list_jobs() -> list:
    with _db() as con:
        rows = con.execute("SELECT * FROM jobs ORDER BY created DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/jobs/{job_id}")
def job_state(job_id: str) -> dict:
    with _db() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"任务不存在: {job_id}")
    return dict(row)


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    with _db() as con:
        row = con.execute("SELECT state, result FROM jobs WHERE id=?",
                          (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"任务不存在: {job_id}")
    if row["state"] != "done":
        raise HTTPException(409, f"任务未完成（{row['state']}）")
    return FileResponse(row["result"], media_type="video/mp4",
                        filename=Path(row["result"]).name)


# 前端构建产物存在时托管（放最后，避免吞掉 /api 路由）
_dist = ROOT / "web" / "dist"
if _dist.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
