"""CLI 入口：
  python -m easydub translate samples/sample.mp4 --lang en
  python -m easydub report artifacts/sample
"""
import argparse
import json
import shutil
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="easydub", description="AI 视频翻译出海流水线（ASR→翻译→TTS→对齐→口型）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("translate", help="视频 -> 外语配音版")
    t.add_argument("video")
    t.add_argument("--lang", default="en", help="目标语言 (en/ja/ko/es)")
    t.add_argument("--tts", default="edge",
                   choices=["edge", "minimax", "openrouter"])
    t.add_argument("--translator", default="llm", choices=["llm", "echo"],
                   help="echo=开发桩，不调用 LLM")
    t.add_argument("--voice", default=None, help="覆盖默认音色")
    t.add_argument("--asr", dest="asr_provider", default="",
                   choices=["", "local", "openrouter"],
                   help="ASR 通道，默认读 .env 的 ASR_PROVIDER")
    t.add_argument("--asr-model", default=None, help="local: tiny/base/small")
    t.add_argument("--onset-shift", type=float, default=None,
                   help="ASR 起点前移补偿秒数（缺省：云 ASR 0.12 / 本地 0）")
    t.add_argument("--no-auto-voice", action="store_true",
                   help="关闭音色自适应（默认按说话人基频自动选男女声）")
    t.add_argument("--subtitle-style", default=None,
                   help="ASS force_style 样式串（如 FontSize=24,Outline=2）")
    t.add_argument("--lipsync", default="none",
                   choices=["none", "syncso", "latentsync"])
    t.add_argument("--no-subs", action="store_true", help="不烧录字幕")
    t.add_argument("--no-bgm", action="store_true",
                   help="不保留原视频背景音乐（默认配音下自动闪避混入原声）")
    t.add_argument("--force", action="store_true", help="清缓存全量重跑")

    r = sub.add_parser("report", help="查看某次运行的对齐报告")
    r.add_argument("run_dir", help="artifacts/<视频名> 目录")
    r.add_argument("--lang", default=None,
                   help="目标语言；缺省时打印目录下所有语言的报告")
    r.add_argument("--html", action="store_true",
                   help="把报告渲染成可视化 HTML（report.<lang>.html）")

    l = sub.add_parser("lipsync-test",
                       help="单测 lip-sync 服务：视频+音频 -> 口型视频（不跑全流程）")
    l.add_argument("video", help="人脸出镜的视频段")
    l.add_argument("audio", help="对齐用的外语音频")
    l.add_argument("out", help="输出 mp4 路径")
    l.add_argument("--provider", default="latentsync",
                   choices=["latentsync", "syncso"])

    args = parser.parse_args(argv)

    if args.cmd == "translate":
        from .pipeline import run
        if args.force:
            out_dir = Path("artifacts") / Path(args.video).stem
            if out_dir.exists():
                shutil.rmtree(out_dir)
        out = run(args.video, args.lang,
                  tts_provider=args.tts, translator_mode=args.translator,
                  voice=args.voice, asr_provider=args.asr_provider,
                  asr_model=args.asr_model,
                  burn_subs=not args.no_subs, lipsync_provider=args.lipsync,
                  keep_bgm=not args.no_bgm, asr_onset_shift=args.onset_shift,
                  auto_voice=not args.no_auto_voice,
                  **({"subtitle_style": args.subtitle_style}
                     if args.subtitle_style else {}))
        print(f"\n成品: {out}")
        return 0

    if args.cmd == "lipsync-test":
        from .config import Settings
        from .services.lipsync import make_lipsync
        provider = make_lipsync(args.provider, Settings())
        print(f"提交到 {provider.name} 服务（{getattr(provider, 'base_url', 'api')}）...")
        out = provider.apply(args.video, args.audio, args.out)
        print(f"口型视频: {out}")
        return 0

    run_dir = Path(args.run_dir)
    if args.lang:
        paths = [run_dir / f"report.{args.lang}.json"]
    else:
        # 旧布局（无语言后缀）优先，再列多语言版本
        paths = sorted(run_dir.glob("report.json")) + \
            sorted(run_dir.glob("report.*.json"))
    paths = [p for p in paths if p.exists()]
    if not paths:
        print(f"未找到报告: {run_dir}/report*.json", file=sys.stderr)
        return 1
    for p in paths:
        if args.html:
            from .report_html import write_html_report_file
            out = write_html_report_file(p)
            print(f"HTML 报告: {out}")
            continue
        print(json.dumps(json.loads(p.read_text(encoding="utf-8")),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
