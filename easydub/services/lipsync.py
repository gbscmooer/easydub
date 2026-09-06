"""lip-sync 服务：对口型的执行器，统一接口 apply(视频, 音频) -> 口型视频。

- LatentSyncLipSync  自建 GPU 服务器（RTX 5090 Windows 机）上的 LatentSync
                     HTTP 服务客户端，服务端部署见 server/README_WINDOWS.md
- SyncSoLipSync      sync.so 商用 API（JD 提到的 "sync"），备用对比通道
"""
import time
from pathlib import Path

import httpx


class LatentSyncLipSync:
    """自建 LatentSync 服务的客户端。

    HTTP 契约（服务端 server/lipsync_server.py 实现同一契约）：
      POST /lipsync               multipart(video, audio) -> {"job_id": ...}
      GET  /jobs/{job_id}         -> {"state": "queued|running|done|error", "error"?}
      GET  /jobs/{job_id}/result  -> 口型对齐后的 mp4 文件流
    """

    name = "latentsync"

    def __init__(self, base_url: str, poll_interval: float = 5.0,
                 timeout: float = 1800):
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.timeout = timeout

    def apply(self, video, audio, out_path) -> Path:
        with open(video, "rb") as fv, open(audio, "rb") as fa:
            resp = httpx.post(
                f"{self.base_url}/lipsync",
                files={"video": (Path(video).name, fv, "video/mp4"),
                       "audio": (Path(audio).name, fa, "audio/wav")},
                timeout=120,
            )
        resp.raise_for_status()
        job_id = resp.json()["job_id"]

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            state = httpx.get(f"{self.base_url}/jobs/{job_id}",
                              timeout=30).json()
            if state.get("state") == "done":
                break
            if state.get("state") == "error":
                raise RuntimeError(f"lip-sync 服务报错: {state.get('error')}")
            time.sleep(self.poll_interval)
        else:
            raise TimeoutError(
                f"lip-sync 任务 {job_id} 超过 {self.timeout}s 未完成")

        with open(str(out_path), "wb") as f:
            with httpx.stream("GET",
                              f"{self.base_url}/jobs/{job_id}/result",
                              timeout=300) as stream:
                stream.raise_for_status()
                for chunk in stream.iter_bytes():
                    f.write(chunk)
        return Path(out_path)


class SyncSoLipSync:
    """sync.so API，效果最稳、免自建，但按量付费。

    TODO(可选对比): 注册拿 key 后按 https://docs.sync.so 实现提交+轮询+下载，
    用于和自建 LatentSync 做效果/成本对比（简历素材）。
    """

    name = "syncso"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("缺少 SYNCSO_API_KEY")
        self.api_key = api_key

    def apply(self, video_clip, audio, out_path):
        raise NotImplementedError("可选对比通道，D5 验证完自建方案后再决定是否实现")


def make_lipsync(provider: str, settings):
    if provider == "none":
        return None
    if provider == "latentsync":
        return LatentSyncLipSync(settings.lipsync_server_url)
    if provider == "syncso":
        return SyncSoLipSync(settings.syncso_api_key)
    raise ValueError(f"未知 lip-sync provider: {provider}")
