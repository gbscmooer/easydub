"""外部 API 调用的退避重试：网关抖动 / 限流时自动再试，避免整条流水线报废。"""
import time
from typing import Callable

import httpx


def retry_call(fn: Callable, tries: int = 3, backoff: float = 2.0):
    """通用重试壳：任何抛异常的调用（如 TTS 的 WebSocket 合成）包一层。

    与 post_with_retry 的差别：不关心 httpx 语义，按"异常=可重试"处理，
    适合云 TTS 这类偶发连接超时（edge-tts WSS 实测会瞬断）。
    """
    last: Exception = RuntimeError("unreachable")
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(backoff * (2 ** i))
    raise last


def post_with_retry(build: Callable[[], httpx.Response], tries: int = 3,
                    backoff: float = 2.0) -> httpx.Response:
    """build() 每次重新发请求；5xx、429 限流与连接类错误重试，其余 4xx 直接抛。

    429 必须重试：免费档（OpenRouter :free 等）按分钟限流是常态，退避后
    通常就过了；不重试会让整条流水线死于一次限流。
    """
    last: Exception = RuntimeError("unreachable")
    for i in range(tries):
        try:
            resp = build()
            if resp.status_code >= 500 or resp.status_code == 429:
                raise httpx.HTTPStatusError(
                    f"服务端错误/限流 {resp.status_code}",
                    request=resp.request, response=resp)
            return resp
        except httpx.TransportError as e:  # 连接/超时类
            last = e
        except httpx.HTTPStatusError as e:
            last = e
        if i < tries - 1:
            time.sleep(backoff * (2 ** i))
    raise last
