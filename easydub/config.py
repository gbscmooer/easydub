"""全局配置：全部从环境变量 / .env 读取，字段留空表示该功能未启用。"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 翻译用 LLM。协议由 base_url 自动识别：
    #   含 "anthropic" -> Anthropic 协议（/v1/messages）
    #   否则           -> OpenAI 兼容协议（/chat/completions）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"

    # MiniMax TTS（可选；不填则用免费的 edge-tts）
    minimax_api_key: str = ""
    minimax_group_id: str = ""

    # lip-sync（Week 2 接入）
    syncso_api_key: str = ""
    # 自建 LatentSync 服务地址（跑在 GPU 服务器上，部署见 server/README_WINDOWS.md）
    lipsync_server_url: str = "http://127.0.0.1:8001"
    # CosyVoice2 克隆语音服务（server/cosyvoice_server.py，本机 GPU）
    cosyvoice_url: str = "http://127.0.0.1:8002"

    # OpenRouter 网关（云端 ASR 转录 / TTS / LLM 翻译，OpenAI 兼容协议）
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_asr_model: str = "fish-audio/transcribe-1"
    openrouter_tts_model: str = "fish-audio/s2.1-pro-free:free"

    # ASR provider: local = faster-whisper 本地 / openrouter = 云端转录
    asr_provider: str = "local"
    # local provider 的模型规格: tiny/base/small，越大越准越慢
    asr_model_size: str = "base"
    # 术语表："品牌名=译法;口号=译法"，翻译/重译提示词都会注入（D3）
    glossary: str = ""
    # 中间产物目录（各阶段缓存落盘，重跑跳过已成功阶段）
    workdir: str = "artifacts"

    model_config = {"env_file": ".env", "extra": "ignore"}
