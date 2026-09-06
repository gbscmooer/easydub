"""CosyVoice2 克隆语音服务：零样本跨语种克隆。

参考音频（原说话人 5~15s + 其转写文本）→ 目标语言语音，音色/语气/节奏
贴原说话人。模型加载一次常驻（约 30s），推理走 HTTP（与 lipsync_server
同模式，流水线不背模型加载成本）。

启动（cosyvoice conda 环境）:
  COSYVOICE_MODEL=/home/kokomilove/models/CosyVoice2-0.5B \
  /home/kokomilove/miniconda3/envs/cosyvoice/bin/python \
  server/cosyvoice_server.py     # :8002

契约:
  GET  /health  → {"gpu": ..., "ready": true}
  POST /clone   {text, ref_wav_b64, ref_text, instruct?, speed?}
              → {"wav_b64", "sr"}   # 22050 Hz
"""
import base64
import os
import sys
import tempfile

# CosyVoice 仓库与其 Matcha-TTS 子模块必须在 sys.path 上
COSYVOICE_DIR = os.environ.get("COSYVOICE_DIR", os.path.expanduser("~/CosyVoice"))
sys.path.insert(0, COSYVOICE_DIR)
sys.path.insert(0, os.path.join(COSYVOICE_DIR, "third_party", "Matcha-TTS"))

import torch  # noqa: E402
import torchaudio  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

MODEL_DIR = os.environ.get(
    "COSYVOICE_MODEL", os.path.expanduser("~/models/CosyVoice2-0.5B"))

import soundfile as sf

# torchaudio 2.10 把 load 迁到 torchcodec 后端且 CosyVoice 内部多处直接调用
# torchaudio.load：全局补丁为 soundfile 实现（吞掉 backend 参数），
# 返回 (channels, time) 张量，与 torchaudio 约定一致
_orig_ta_load = torchaudio.load


def _sf_load(uri, *args, **kwargs):
    if isinstance(uri, torch.Tensor):
        # CosyVoice 内部（_extract_speech_feat 等）会把 16k 提示张量再传回
        # load_wav：直接原样返回并标注 16k，由调用方的 Resample 完成重采样
        return uri, 16000
    data, sr = sf.read(uri, dtype="float32", always_2d=True)
    return torch.from_numpy(data.T), sr


torchaudio.load = _sf_load

app = FastAPI(title="cosyvoice clone")
_model = None


def get_model():
    global _model
    if _model is None:
        from cosyvoice.cli.cosyvoice import CosyVoice2
        _model = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False,
                            fp16=True)
    return _model


def _load_ref_16k(path):
    """参考音频加载：torchaudio 2.10 的 load 迁移到 torchcodec 后端，
    用 soundfile + 功能性 resample 绕开（纯计算，无 I/O 依赖）。"""
    import soundfile as sf
    data, sr_in = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    t = torch.from_numpy(data)[None]
    if sr_in != 16000:
        t = torchaudio.resample(t, sr_in, 16000)
    return t


class CloneReq(BaseModel):
    text: str
    ref_wav_b64: str
    ref_text: str
    instruct: str = ""          # 语气指令，如 "用激动的语气说这句话"
    speed: float = 1.0          # 节奏粗调（流水线 atempo 做精调）


@app.get("/health")
def health():
    return {"gpu": torch.cuda.get_device_name(0)
            if torch.cuda.is_available() else "cpu", "ready": True}


@app.post("/clone")
def clone(req: CloneReq):
    if not req.text.strip():
        raise HTTPException(400, "text 为空")
    ref_bytes = base64.b64decode(req.ref_wav_b64)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(ref_bytes)
        ref_path = f.name
    try:
        prompt_speech = _load_ref_16k(ref_path)
    finally:
        os.unlink(ref_path)

    model = get_model()
    try:
        if req.instruct:
            gen = model.inference_instruct2(
                req.text, req.instruct, prompt_speech,
                speed=req.speed, stream=False)
        else:
            gen = model.inference_zero_shot(
                req.text, req.ref_text, prompt_speech,
                speed=req.speed, stream=False)
    except Exception as e:
        raise HTTPException(500, f"合成失败: {e}")

    chunks = [c["tts_speech"] for c in gen]
    if not chunks:
        raise HTTPException(500, "合成结果为空")
    speech = torch.cat(chunks, dim=1) if len(chunks) > 1 else chunks[0]
    import io as _io
    buf = _io.BytesIO()
    # torchaudio 2.10 的 save 同样迁移到 torchcodec，直接用 soundfile 写 wav
    sf.write(buf, speech.squeeze(0).cpu().numpy(), 22050, format="WAV")
    return {"wav_b64": base64.b64encode(buf.getvalue()).decode(),
            "sr": 22050}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8002")))
