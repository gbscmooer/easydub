"""LatentSync 的 HTTP 服务壳（跑在有 GPU 的机器上：本机 WSL 5090 或 Windows 机）。

HTTP 契约（与 easydub/easydub/services/lipsync.py 的客户端约定一致）：
  GET  /health                -> {"gpu": "...", "pending": n}
  POST /lipsync               -> multipart(video, audio) -> {"job_id": "..."}
  GET  /jobs/{job_id}         -> {"state": "queued|running|done|error", "error": ...}
  GET  /jobs/{job_id}/result  -> 口型对齐后的 mp4 文件流

启动（Linux/WSL，latentsync conda 环境）:
  LATENTSYNC_DIR=~/LatentSync python lipsync_server.py     （监听 0.0.0.0:8001）
启动（Windows）:  按 README_WINDOWS.md 装好后直接 python lipsync_server.py
依赖:  pip install fastapi uvicorn python-multipart
"""
import os
import sys
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

# LatentSync 仓库目录（推理命令的工作目录）
CWD = os.environ.get("LATENTSYNC_DIR", r"C:\LatentSync")
# 推理命令模板，{video} {audio} {out} 会替换成实际路径。
# 默认 = LatentSync 1.6 官方推理命令（已在 5090 + torch 2.10/cu128 实测通过）。
# 用 sys.executable 保证与服务器进程同一 Python（conda 环境里没有裸 `python`）。
CMD_TEMPLATE = os.environ.get(
    "LATENTSYNC_CMD",
    f"{sys.executable} -m scripts.inference "
    "--unet_config_path configs/unet/stage2_512.yaml "
    "--inference_ckpt_path checkpoints/latentsync_unet.pt "
    "--inference_steps 20 --guidance_scale 1.5 --enable_deepcache "
    "--video_path {video} --audio_path {audio} --video_out_path {out}")

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
