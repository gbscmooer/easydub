"""外部 API 调用的退避重试：网关抖动 / 限流时自动再试，避免整条流水线报废。"""
import time
from typing import Callable

import httpx


def post_with_retry(build: Callable[[], httpx.Response], tries: int = 3,
                    backoff: float = 2.0) -> httpx.Response:
    """build() 每次重新发请求；5xx 与连接类错误重试，4xx 直接抛。"""
    last: Exception = RuntimeError("unreachable")
    for i in range(tries):
        try:
            resp = build()
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"服务端错误 {resp.status_code}", request=resp.request,
                    response=resp)
            return resp
        except httpx.TransportError as e:  # 连接/超时类
            last = e
        except httpx.HTTPStatusError as e:
            last = e
        if i < tries - 1:
            time.sleep(backoff * (2 ** i))
    raise last
