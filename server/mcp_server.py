"""EasyDub MCP 服务（M4）：把流水线阶段暴露为 MCP 工具，供 Agent/客户端调用。

手写 stdio JSON-RPC 2.0（newline-delimited），零第三方依赖——MCP 协议核心就是
initialize / tools/list / tools/call 三个方法，没必要为此拉一整个 SDK。

启动:  .venv/bin/python server/mcp_server.py          （stdio，被客户端拉起）
测试:  echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python server/mcp_server.py

工具：
  easydub_translate(video, lang, lipsync)  跑全流程，返回成品路径+对齐摘要
  easydub_report(run_dir, lang)            返回该次运行的对齐质量报告
  easydub_overflow_detail(run_dir, lang)   溢出段明细（Agent 决策的输入）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from easydub.pipeline import run  # noqa: E402

SERVER_INFO = {"name": "easydub-mcp", "version": "1.0.0"}

TOOLS = [
    {
        "name": "easydub_translate",
        "description": "跑视频翻译流水线：ASR→限长翻译→TTS→对齐→(口型)→成品。返回成品路径与对齐摘要。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video": {"type": "string", "description": "视频文件路径"},
                "lang": {"type": "string", "description": "目标语言 en/zh/ja/ko/es"},
                "lipsync": {"type": "boolean", "description": "是否启用人脸段口型同步"},
            },
            "required": ["video", "lang"],
        },
    },
    {
        "name": "easydub_report",
        "description": "读取某次运行的对齐质量报告（逐段 slot/tts/tempo/action + 汇总）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_dir": {"type": "string", "description": "artifacts/<视频名> 目录"},
                "lang": {"type": "string", "description": "目标语言"},
            },
            "required": ["run_dir", "lang"],
        },
    },
    {
        "name": "easydub_overflow_detail",
        "description": "列出溢出段明细（段号/槽位/配音时长/译文），供 Agent 决策重译策略。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_dir": {"type": "string", "description": "artifacts/<视频名> 目录"},
                "lang": {"type": "string", "description": "目标语言"},
            },
            "required": ["run_dir", "lang"],
        },
    },
]


def _tool_translate(args: dict) -> str:
    video = ROOT / args["video"] if not Path(args["video"]).is_absolute() \
        else Path(args["video"])
    out = run(video, args.get("lang", "en"),
              lipsync_provider="latentsync" if args.get("lipsync") else "none",
              workdir=str(ROOT / "artifacts"))
    report_p = out.parent / f"report.{args.get('lang', 'en')}.json"
    summary = json.loads(report_p.read_text(encoding="utf-8"))["summary"] \
        if report_p.exists() else {}
    return json.dumps({"output": str(out), "summary": summary},
                      ensure_ascii=False)


def _report_path(args: dict) -> Path:
    d = Path(args["run_dir"])
    d = d if d.is_absolute() else ROOT / d
    return d / f"report.{args.get('lang', 'en')}.json"


def _tool_report(args: dict) -> str:
    p = _report_path(args)
    if not p.exists():
        return json.dumps({"error": f"报告不存在: {p}"}, ensure_ascii=False)
    return p.read_text(encoding="utf-8")


def _tool_overflow(args: dict) -> str:
    report = json.loads(_tool_report(args))
    if "error" in report:
        return json.dumps(report, ensure_ascii=False)
    over = [s for s in report["segments"] if s["action"] == "overflow"]
    return json.dumps({"summary": report["summary"], "overflow_segments": over},
                      ensure_ascii=False)


_HANDLERS = {
    "easydub_translate": _tool_translate,
    "easydub_report": _tool_report,
    "easydub_overflow_detail": _tool_overflow,
}


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        try:
            text = _HANDLERS[name](args)
        except Exception as e:  # 工具内异常按 MCP 约定回 isError
            text = json.dumps({"error": str(e)}, ensure_ascii=False)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": True}}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}]}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid, "error":
                {"code": -32601, "message": f"未知方法: {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
