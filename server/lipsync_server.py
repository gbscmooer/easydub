"""LatentSync 的 HTTP 服务壳（跑在 GPU 服务器 / RTX 5090 Windows 机上）。

HTTP 契约（与 easydub/easydub/services/lipsync.py 的客户端约定一致）：
  GET  /health                -> {"gpu": "...", "pending": n}
  POST /lipsync               -> multipart(video, audio) -> {"job_id": "..."}
  GET  /jobs/{job_id}         -> {"state": "queued|running|done|error", "error": ...}
  GET  /jobs/{job_id}/result  -> 口型对齐后的 mp4 文件流

启动:  python lipsync_server.py     （监听 0.0.0.0:8001）
依赖:  pip install fastapi uvicorn python-multipart
"""
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

# ============ 装好 LatentSync 后，按实际情况修改这两行 ============
# LatentSync 仓库目录
CWD = r"C:\LatentSync"
# 推理命令模板。先手动在命令行跑通一次官方推理，把能用的命令填进来，
# {video} {audio} {out} 会被替换成实际路径。
# 注意：若 --result_path 实际是"输出目录"而不是文件路径，请改成先输出到
# 子目录、再把生成的 mp4 路径赋给 out_path 的写法。
CMD_TEMPLATE = (r"python inference.py --inference_config configs/inference.yaml "
                r"--video_path {video} --audio_path {audio} --result_path {out}")
# ================================================================

app = FastAPI(title="easydub lipsync server")
_jobs = {}  # job_id -> {"state", "error", "out_path"}


def _run(job_id: str, video: str, audio: str, out_path: str) -> None:
    _jobs[job_id]["state"] = "running"
    cmd = CMD_TEMPLATE.format(video=video, audio=audio, out=out_path)
    proc = subprocess.run(cmd, cwd=CWD, shell=True,
                          capture_output=True, text=True)
    if proc.returncode != 0 or not Path(out_path).exists():
        _jobs[job_id].update(state="error", error=proc.stderr[-1500:])
    else:
        _jobs[job_id].update(state="done", out_path=out_path)


@app.get("/health")
def health():
    try:
        import torch
        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() \
            else "无可用 CUDA"
    except Exception:
        gpu = "torch 未安装"
    pending = sum(1 for j in _jobs.values() if j["state"] in ("queued", "running"))
    return {"gpu": gpu, "pending": pending}


@app.post("/lipsync")
async def lipsync(video: UploadFile = File(...), audio: UploadFile = File(...)):
    job_id = uuid.uuid4().hex[:12]
    tmp = Path(tempfile.mkdtemp(prefix=f"easydub_{job_id}_"))
    v, a, out = tmp / "in.mp4", tmp / "dub.wav", tmp / "out.mp4"
    v.write_bytes(await video.read())
    a.write_bytes(await audio.read())
    _jobs[job_id] = {"state": "queued", "error": None, "out_path": str(out)}
    threading.Thread(target=_run, args=(job_id, str(v), str(a), str(out)),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return {"state": job["state"], "error": job["error"]}


@app.get("/jobs/{job_id}/result")
def result(job_id: str):
    job = _jobs.get(job_id)
    if job is None or job["state"] != "done":
        raise HTTPException(404, "result not ready")
    return FileResponse(job["out_path"], media_type="video/mp4",
                        filename="lipsync.mp4")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
