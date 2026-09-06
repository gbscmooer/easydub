"""翻译服务：LLM 适配器，同时支持 OpenAI 兼容与 Anthropic 兼容两种协议。

时长约束在提示词里完成（"生成端"对齐）：按每段槽位秒数给译文限长，
语速按 CPS_TABLE 估算，从源头减少超时段的数量。
"""
import json
import re
from typing import Dict, List, Optional

import httpx

from .http import post_with_retry

# 各语言旁白语速粗估（字符/秒，含空格），用于给译文设字符数上限
CPS_TABLE = {"en": 14, "es": 14, "ko": 8, "ja": 7, "zh": 4}
LANG_NAME = {"en": "英语", "ja": "日语", "ko": "韩语", "es": "西班牙语", "zh": "中文"}

SYSTEM_PROMPT = (
    "你是资深广告本地化配音译者。把用户给出的中文字幕逐段翻译，要求：\n"
    "1. 口语化、有感染力，符合广告旁白语气，不逐字直译\n"
    "2. 品牌名、数字、型号保持原样\n"
    "3. 严格遵守每段标注的译文长度上限——译文将由 TTS 朗读并填进原视频的时间轴，"
    "超长会导致音画不同步\n"
    '4. 只输出 JSON 数组，格式 [{"i": 段号, "text": "译文"}]，不要输出任何解释'
)


class EchoTranslator:
    """开发桩：不翻译，原样返回。用于无 LLM key 时验证流水线。"""

    def translate_all(self, segments) -> List:
        for seg in segments:
            seg.translated = seg.text
        return segments


class LLMTranslator:
    def __init__(self, api_key: str, base_url: str, model: str,
                 target_lang: str = "en", limit: bool = True,
                 glossary: Optional[Dict[str, str]] = None):
        if not api_key:
            raise ValueError("缺少 LLM_API_KEY")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.target_lang = target_lang
        # limit=False 用于 E1 消融：提示词不带字符预算，纯自然翻译
        self.limit = limit
        # 术语表（品牌名/slogan 跨段译法一致），注入两种提示词
        self.glossary = glossary or {}
        # base_url 含 "anthropic" 即走 Anthropic 协议（如 MiMo 代理）
        self.protocol = "anthropic" if "anthropic" in base_url.lower() else "openai"

    # ---- HTTP ----
    def _chat(self, user_prompt: str, system: str = SYSTEM_PROMPT) -> str:
        if self.protocol == "anthropic":
            def build():
                return httpx.post(
                    f"{self.base_url}/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model, "max_tokens": 4096,
                        "system": system,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                    timeout=120,
                )

            resp = post_with_retry(build, tries=4, backoff=6.0)
            resp.raise_for_status()
            return "".join(
                b.get("text", "") for b in resp.json().get("content", [])
            )

        def build_openai():
            return httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model, "temperature": 0.3,
                    # 不带 max_tokens 时部分供应商按整个上下文预扣补全预算直接 400；
                    # 推理模型（如 glm-5.2）会先消耗大量 reasoning token，4096
                    # 常常不够它输出正文——给到 8192
                    "max_tokens": 8192,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=180,
            )

        resp = post_with_retry(build_openai, tries=4, backoff=6.0)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        content = msg.get("content") or ""
        if not content.strip():
            # 推理模型 token 耗尽时正文为 null，答案可能落在 reasoning 字段里
            content = msg.get("reasoning") or ""
        return content

    # ---- 提示词与解析 ----
    def _build_prompt(self, segments) -> str:
        cps = CPS_TABLE.get(self.target_lang, 14)
        lang = LANG_NAME.get(self.target_lang, self.target_lang)
        lines = [f"把以下字幕翻译成{lang}。"]
        if self.glossary:
            terms = "；".join(f"`{k}`必须译为`{v}`" for k, v in self.glossary.items())
            lines.append(f"术语表（全部段落严格遵守）：{terms}。")
        for i, seg in enumerate(segments):
            if self.limit:
                budget = max(8, int(seg.slot * cps))
                lines.append(
                    f'段{i}（时长{seg.slot:.1f}秒，译文不超过{budget}字符）: {seg.text}')
            else:
                lines.append(f"段{i}: {seg.text}")
        return "\n".join(lines)

    @staticmethod
    def _parse(text: str, n: int) -> Optional[List[str]]:
        if not text:
            return None
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return None
        try:
            items = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(items, list) or len(items) != n:
            return None
        out = []
        for it in items:
            t = (it.get("text") or "").strip() if isinstance(it, dict) else str(it).strip()
            if not t:
                return None
            out.append(t)
        return out

    def translate_all(self, segments) -> List:
        prompt = self._build_prompt(segments)
        for attempt in range(2):
            raw = self._chat(prompt)
            items = self._parse(raw, len(segments))
            if items is not None:
                for seg, t in zip(segments, items):
                    seg.translated = t
                return segments
            # JSON 解析失败：把失败原因追加进提示词重试一次
            prompt += "\n（上一次输出不是合法的 JSON 数组或段数不符，请严格按要求重新输出）"
        raise RuntimeError("翻译输出两次解析失败，请检查模型是否遵循 JSON 格式")

    def retranslate(self, src_text: str, old_text: str, budget: int,
                    hint: str = "") -> str:
        """超时段重译：给定更紧的字符预算，要求更短的译文。

        这是"双向夹逼"生成端的后手——首轮译文实测超时后，
        把实测结果（预算砍半级别的要求）反馈给模型重新生成。
        """
        lang = LANG_NAME.get(self.target_lang, self.target_lang)
        glossary_note = ""
        if self.glossary:
            terms = "；".join(f"`{k}`必须译为`{v}`" for k, v in self.glossary.items())
            glossary_note = f"术语表约束：{terms}。\n"
        prompt = (
            f"这句广告词的{lang}译文太长，配音超出了原视频这句话的时间。\n"
            f"中文原文：{src_text}\n"
            f"超长译文（{len(old_text)}字符）：{old_text}\n"
            f"{glossary_note}"
            f"硬性要求：重新翻译成{lang}，译文不超过 {budget} 个字符，"
            f"保留核心卖点，口语化。\n"
            + (f"补充指导：{hint}\n" if hint else "")
            + '只输出 JSON 数组：[{"text": "新译文"}]'
        )
        for _ in range(2):
            raw = self._chat(prompt)
            items = self._parse(raw, 1)
            if items:
                return items[0]
            prompt += "\n（上一次输出不是合法 JSON，请严格只输出 JSON 数组）"
        raise RuntimeError("重译输出两次解析失败")
