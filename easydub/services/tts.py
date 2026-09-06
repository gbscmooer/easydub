"""TTS 服务：统一接口，四个实现。

- EdgeTTS      免费、无需 key（微软神经语音，广告质感够用）
- MinimaxTTS   JD 指定的 minimax，需 key
- OpenRouterTTS OpenRouter 网关的 TTS 模型（fish-audio 等），免费档可用
- CosyVoiceCloneTTS 零样本音色克隆（原说话人参考 → 跨语种合成），本机 GPU 服务
"""
import asyncio
import base64
from typing import Dict, Optional

import httpx

from .http import post_with_retry

# 各 provider 的默认音色（可被 --voice 覆盖）
VOICES: Dict[str, Dict[str, str]] = {
    "edge": {
        "en": "en-US-AndrewNeural",
        "zh": "zh-CN-YunxiNeural",
        "ja": "ja-JP-KeitaNeural",
        "es": "es-ES-AlvaroNeural",
    },
    "minimax": {
        "en": "male-qn-qingse",
        "zh": "male-qn-qingse",
    },
}


class EdgeTTS:
    name = "edge"

    def __init__(self, lang: str = "en", voice: Optional[str] = None,
                 rate: Optional[str] = None):
        self.voice = voice or VOICES["edge"].get(lang, VOICES["edge"]["en"])
        self.rate = rate  # 如 "-20%"，评测素材的慢语速用

    def synth(self, text: str, out_path) -> None:
        import edge_tts

        async def _save():
            kwargs = {"rate": self.rate} if self.rate else {}
            com = edge_tts.Communicate(text, self.voice, **kwargs)
            await com.save(str(out_path))

        asyncio.run(_save())


class MinimaxTTS:
    """TODO(D3): 拿到 key 后实测。文档 https://platform.minimaxi.com/"""

    name = "minimax"

    def __init__(self, api_key: str, group_id: str,
                 lang: str = "en", voice: Optional[str] = None):
        if not api_key:
            raise ValueError("缺少 MINIMAX_API_KEY")
        self.api_key = api_key
        self.group_id = group_id
        self.voice = voice or VOICES["minimax"].get(lang, "male-qn-qingse")

    def synth(self, text: str, out_path) -> None:
        resp = httpx.post(
            f"https://api.minimax.chat/v1/t2a_v2?GroupId={self.group_id}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "speech-01-turbo",
                "text": text,
                "voice_setting": {"voice_id": self.voice, "speed": 1.0,
                                  "vol": 1.0, "pitch": 0},
                "audio_setting": {"sample_rate": 32000, "format": "mp3"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        # minimax 返回 hex 编码的音频
        audio_hex = resp.json()["data"]["audio"]
        out_path = str(out_path)
        with open(out_path, "wb") as f:
            f.write(bytes.fromhex(audio_hex))


class OpenRouterTTS:
    """OpenAI 兼容 /audio/speech 端点。

    必须显式 response_format="mp3"：不传时网关返回裸 PCM 流（无容器，
    后续 ffprobe 无法测时长）；wav 不支持。:free 档有速率限制，
    长视频逐段请求需留意 429（D10 统一加重试）。
    """

    name = "openrouter"

    def __init__(self, api_key: str, lang: str = "en", voice: Optional[str] = None,
                 model: str = "fish-audio/s2.1-pro-free:free",
                 base_url: str = "https://openrouter.ai/api/v1"):
        if not api_key:
            raise ValueError("缺少 OPENROUTER_API_KEY")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.voice = voice or "alloy"

    def synth(self, text: str, out_path) -> None:
        resp = httpx.post(
            f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text, "voice": self.voice,
                  "response_format": "mp3"},
            timeout=120,
        )
        resp.raise_for_status()
        with open(str(out_path), "wb") as f:
            f.write(resp.content)


class CosyVoiceCloneTTS:
    """零样本音色克隆 TTS（方向转型的核心）：原说话人参考音频 →
    目标语言语音，音色/语气贴原片。服务端模型常驻（server/cosyvoice_server.py），
    参考音频在 run() 里自动从原视频提取（voice_match 选段 + ASR 转写零人工）。
    """

    name = "cosyvoice"

    def __init__(self, base_url: str = "http://127.0.0.1:8002",
                 ref_wav=None, ref_text: str = "", instruct: str = "",
                 speed: float = 1.0):
        if ref_wav is None:
            raise ValueError("克隆模式需要参考音频（原说话人段）")
        import hashlib
        self.base_url = base_url.rstrip("/")
        ref_bytes = open(ref_wav, "rb").read()
        # 音色指纹进缓存标签：换参考音频（换说话人/换视频）不错拿旧配音
        self.voice = "clone-" + hashlib.md5(
            ref_bytes + ref_text.encode()).hexdigest()[:6]
        self.ref_wav_b64 = base64.b64encode(ref_bytes).decode()
        self.ref_text = ref_text
        self.instruct = instruct
        self.speed = speed

    def synth(self, text: str, out_path) -> None:
        payload = {"text": text, "ref_wav_b64": self.ref_wav_b64,
                   "ref_text": self.ref_text, "instruct": self.instruct,
                   "speed": self.speed}
        resp = post_with_retry(
            lambda: httpx.post(f"{self.base_url}/clone", json=payload,
                               timeout=300),
            tries=3, backoff=4.0)
        resp.raise_for_status()
        data = resp.json()
        with open(str(out_path), "wb") as f:
            f.write(base64.b64decode(data["wav_b64"]))


def make_tts(provider: str, lang: str = "en", voice: Optional[str] = None,
             settings=None, rate: Optional[str] = None, **kw):
    if provider == "edge":
        return EdgeTTS(lang, voice, rate=rate)
    if provider == "minimax":
        return MinimaxTTS(settings.minimax_api_key, settings.minimax_group_id,
                          lang, voice)
    if provider == "openrouter":
        return OpenRouterTTS(settings.openrouter_api_key, lang, voice,
                             settings.openrouter_tts_model,
                             settings.openrouter_base_url)
    if provider == "cosyvoice":
        return CosyVoiceCloneTTS(
            base_url=getattr(settings, "cosyvoice_url", None)
            or "http://127.0.0.1:8002",
            ref_wav=kw.get("ref_wav"), ref_text=kw.get("ref_text", ""),
            instruct=kw.get("instruct", ""), speed=kw.get("speed", 1.0))
    raise ValueError(f"未知 TTS provider: {provider}")
