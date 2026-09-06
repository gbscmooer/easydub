"""评测编排（GOAL §8 E1/E4/E5）：批量跑素材×语言×消融档，自动算 §6.1 指标。

用法:
  .venv/bin/python scripts/make_eval_set.py          # 先生成素材
  .venv/bin/python scripts/eval.py                   # 5 素材 × en × 4 消融档
  .venv/bin/python scripts/eval.py --clips c1_pure,c2_dense --langs en,zh

产出 artifacts/eval/results.json + 终端 markdown 表，论文/简历数字从这里抄。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from easydub.eval_metrics import cost_estimate, run_metrics  # noqa: E402
from easydub.media import probe_duration  # noqa: E402
from easydub.pipeline import run  # noqa: E402

# E1 消融四档（GOAL §8：无限长 → 仅限长 → 限长+atempo → 限长+atempo+重译）
TIERS = {
    "T1_free": dict(limit_translation=False, allow_atempo=False,
                    retry_overflow=False),
    "T2_limit": dict(limit_translation=True, allow_atempo=False,
                     retry_overflow=False),
    "T3_limit_atempo": dict(limit_translation=True, allow_atempo=True,
                            retry_overflow=False),
    "T4_full": dict(limit_translation=True, allow_atempo=True,
                    retry_overflow=True),
}


def one_run(video: Path, lang: str, tier: str, workroot: Path,
            tts_provider: str = "edge", asr_provider: str = "",
            asr_model=None) -> dict:
    meta = json.loads((video.parent / "meta.json").read_text(encoding="utf-8"))
    info = next(m for m in meta if m["name"] == video.stem)
    video_seconds = probe_duration(video)

    t0 = time.time()
    suffix = f"{video.stem}_{tier}_{lang}" + (
        f"_{tts_provider}" if tts_provider != "edge" else "") + (
        f"_{asr_provider or 'cloud'}" if (asr_provider and asr_provider != "openrouter")
        else "")
    out = run(video, lang, workdir=str(workroot / suffix),
              tts_provider=tts_provider, asr_provider=asr_provider,
              asr_model=asr_model, **TIERS[tier])
    wall = time.time() - t0

    rdir = workroot / suffix / video.stem
    report = json.loads((rdir / f"report.{lang}.json").read_text(encoding="utf-8"))
    asr_text = "".join(s["text"] for s in json.loads(
        (rdir / "segments.json").read_text(encoding="utf-8")))

    m = run_metrics(report, wall, video_seconds,
                    lines=info["lines"] or None, asr_text=asr_text)
    n_retry = sum(1 for s in report["segments"] if s["action"] == "overflow")
    m["cost"] = cost_estimate(video_seconds,
                              llm_calls=1 + (report["summary"]["overflow_before_retry"]
                                            - n_retry if tier == "T4_full" else 0),
                              tts_chars=sum(len(s["translated"])
                                            for s in report["segments"]),
                              tts_provider=report["tts_provider"])
    m["clip"], m["lang"], m["tier"] = video.stem, lang, tier
    m["asr"] = asr_provider or "openrouter"
    print(f"[eval] {video.stem} {lang} {tier} asr={m['asr']}: "
          f"溢出率 {m['overflow_rate']:.0%} 匹配率 {m['match_rate']:.0%} "
          f"耗时 {m['wall_seconds']}s", flush=True)
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "samples" / "eval_set"))
    ap.add_argument("--clips", default="", help="逗号分隔，缺省全部")
    ap.add_argument("--langs", default="en")
    ap.add_argument("--tiers", default=",".join(TIERS),
                    help="缺省跑全部消融档；只要基线用 T4_full")
    ap.add_argument("--tts", default="edge", choices=["edge", "openrouter"],
                    help="E2 TTS 选型对比用")
    ap.add_argument("--asr", default="", choices=["", "local", "openrouter"],
                    help="E5 ASR 选型对比用；local=本地 faster-whisper")
    ap.add_argument("--asr-model", default=None,
                    help="local: tiny/base/small")
    args = ap.parse_args()

    set_dir = Path(args.set)
    metas = json.loads((set_dir / "meta.json").read_text(encoding="utf-8"))
    clips = args.clips.split(",") if args.clips else [m["name"] for m in metas]
    langs = args.langs.split(",")
    tiers = args.tiers.split(",")

    results = []
    for clip in clips:
        video = set_dir / f"{clip}.mp4"
        if not video.exists():
            print(f"跳过（素材不存在）: {video}")
            continue
        for lang in langs:
            for tier in tiers:
                results.append(one_run(video, lang, tier,
                                       ROOT / "artifacts" / "eval" / "runs",
                                       tts_provider=args.tts,
                                       asr_provider=args.asr,
                                       asr_model=args.asr_model))

    out = ROOT / "artifacts" / "eval" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # 按 (clip, lang, tier, asr) 合并进历史结果：增量重跑不冲掉其他行
    if out.exists():
        old = json.loads(out.read_text(encoding="utf-8"))
        key = lambda m: (m.get("clip"), m.get("lang"), m.get("tier"),
                         m.get("asr", "openrouter"))
        merged = {}
        for m in old:
            m.setdefault("asr", "openrouter")  # 旧行归一化，新键不丢历史
            merged[key(m)] = m
        for m in results:
            merged[key(m)] = m
        results = list(merged.values())
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print("\n| 素材 | 语言 | 档位 | 段数 | 溢出率 | 匹配率 | CER | 字幕偏移mean | 墙钟 | RT系数 | 成本¥ |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for m in results:
        off = m.get("subtitle_offset")
        print(f"| {m['clip']} | {m['lang']} | {m['tier']} | {m['segments']} "
              f"| {m['overflow_rate']:.0%} | {m['match_rate']:.0%} "
              f"| {m.get('cer', '-')} | {off['mean'] if off else '-'} "
              f"| {m['wall_seconds']}s | {m['rt_factor']} | {m['cost']['total']} |")
    print(f"\n完整结果: {out}")


if __name__ == "__main__":
    main()
