"""流水线编排：asr → translate → tts → align → mux。

各阶段产物落盘到 workdir/<视频名>/，重跑时命中缓存直接跳过
（--force 全量重算）。接 Dify 时每个阶段包成 HTTP 端点，
即可一一映射为 workflow 节点。
"""
import hashlib
import json
import time
from pathlib import Path

from .align import find_overflow, plan_alignment
from .config import Settings
from .media import (build_dub_track, change_tempo, extract_audio, gen_srt,
                    mux, probe_duration, trim_silence)
from .models import Segment, load_segments, save_segments
from .services.asr import transcribe, transcribe_openrouter
from .services.tts import make_tts
from .services.translator import EchoTranslator, LLMTranslator


def _log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}")


def run(video, target_lang: str = "en", *, tts_provider: str = "edge",
        translator_mode: str = "llm", voice=None, asr_provider: str = "",
        asr_model=None, burn_subs: bool = True, workdir=None,
        lipsync_provider: str = "none") -> Path:
    t0 = time.time()
    settings = Settings()
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    out_dir = Path(workdir or settings.workdir) / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. ASR：原声 -> 带时间轴的字幕段
    provider = asr_provider or settings.asr_provider
    seg_file = out_dir / "segments.json"
    if seg_file.exists():
        segments = load_segments(seg_file)
        _log("asr", f"命中缓存，{len(segments)} 段")
    else:
        wav = extract_audio(video, out_dir / "audio_zh.wav")
        if provider == "openrouter":
            _log("asr", f"云端转录（{settings.openrouter_asr_model}）...")
            raw = transcribe_openrouter(wav, settings.openrouter_api_key,
                                        settings.openrouter_asr_model,
                                        settings.openrouter_base_url)
        else:
            _log("asr", f"本地识别（模型 {asr_model or settings.asr_model_size}）...")
            raw = transcribe(wav, asr_model or settings.asr_model_size)
        segments = [Segment(**d) for d in raw]
        save_segments(segments, seg_file)
        _log("asr", f"识别到 {len(segments)} 段")
    if not segments:
        raise RuntimeError("未识别到任何语音，请确认视频里有清晰人声")

    # 2. 翻译：按槽位秒数限长的 LLM 翻译
    # 翻译/TTS/成品都按目标语言隔离——同一视频可并行出多语言版本，切语言不串缓存
    tr_file = out_dir / f"segments_translated.{target_lang}.json"
    cached_ok = False
    if tr_file.exists():
        cached = load_segments(tr_file)
        # 源文本变了（ASR 重跑、断句策略调整）时旧译文作废，防止错位
        cached_ok = ([s.text for s in cached]
                     == [s.text for s in segments])
        if cached_ok:
            segments = cached
            _log("translate", "命中缓存")
    if not cached_ok:
        if translator_mode == "echo":
            tr = EchoTranslator()
        elif settings.llm_api_key:
            tr = LLMTranslator(settings.llm_api_key, settings.llm_base_url,
                               settings.llm_model, target_lang)
        else:
            _log("translate", "未配置 LLM_API_KEY，退化为 echo 模式（不翻译）")
            tr = EchoTranslator()
        tr.translate_all(segments)
        save_segments(segments, tr_file)
        protocol = getattr(tr, "protocol", "echo")
        _log("translate", f"完成，目标语言 {target_lang}（协议 {protocol}）")

    # 3. TTS 逐段合成 + 4. 时长对齐
    tts = make_tts(tts_provider, target_lang, voice, settings)
    tts_dir = out_dir / f"tts_{target_lang}"
    tts_dir.mkdir(exist_ok=True)

    def _synth(text, path):
        tts.synth(text, path)
        trim_silence(path, path)

    def _tts_path(i: int, text: str) -> Path:
        # 文件名带译文指纹：译文变了（重译）不会错拿旧配音
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        return tts_dir / f"seg_{i:03d}_{h}.mp3"

    for i, seg in enumerate(segments):
        audio = _tts_path(i, seg.translated)
        if not audio.exists():
            _synth(seg.translated, audio)
        seg.audio_path = str(audio)
        seg.audio_duration = probe_duration(audio)
        plan = plan_alignment(seg.slot, seg.audio_duration)
        seg.tempo, seg.action = plan["tempo"], plan["action"]

    # 4.5 溢出重译：配音塞不进槽位的段，用更紧的字符预算重新翻译、重新配音。
    # 这是"双向夹逼"的闭环——首轮译文实测超时后，把实测约束反馈给生成端。
    n_overflow_before = sum(1 for s in segments if s.action == "overflow")
    overflows = find_overflow(segments)
    if overflows and translator_mode == "llm" and settings.llm_api_key:
        tr = LLMTranslator(settings.llm_api_key, settings.llm_base_url,
                           settings.llm_model, target_lang)
        _log("retry", f"{len(overflows)} 段配音超时，按字符预算重译")
        for p in overflows:
            seg = segments[p["index"]]
            new_text = tr.retranslate(seg.text, seg.translated, p["budget"])
            seg.translated = new_text
            audio = _tts_path(p["index"], new_text)
            _synth(new_text, audio)  # 覆盖旧配音
            seg.audio_duration = probe_duration(audio)
            plan = plan_alignment(seg.slot, seg.audio_duration)
            seg.tempo, seg.action = plan["tempo"], plan["action"]
            _log("retry", f"段{p['index']}：预算 {p['budget']} 字符 → "
                          f"译文 {len(new_text)} 字符，配音 "
                          f"{seg.audio_duration:.2f}s，动作 {seg.action}")
        # 重译结果写回翻译缓存，重跑不重复花钱
        save_segments(segments, tr_file)
    elif overflows:
        _log("retry", f"{len(overflows)} 段配音超时；echo 模式无 LLM，跳过重译")
    save_segments(segments, out_dir / f"segments_final.{target_lang}.json")

    # 5. 合成配音轨：变速段先生成，再全部放到静音底轨的原始时间戳上
    total = probe_duration(video)
    clips = []
    for seg in segments:
        if seg.action == "atempo":
            adjusted = change_tempo(
                seg.audio_path,
                str(seg.audio_path).replace(".mp3", f"_t{seg.tempo}.mp3"),
                seg.tempo,
            )
            clips.append((seg.start, str(adjusted)))
        else:
            clips.append((seg.start, seg.audio_path))
    track = build_dub_track(clips, total, out_dir / f"dub_track.{target_lang}.wav")
    _log("mix", f"音轨合成完毕（{len(clips)} 段）")

    # 6. lip-sync（D8 接入点）
    if lipsync_provider != "none":
        from .services.lipsync import make_lipsync
        provider = make_lipsync(lipsync_provider, settings)
        raise NotImplementedError(
            f"lip-sync（{provider.name}）在 D8 接入，当前先跑通字幕+配音链路"
        )

    # 7. 字幕 + 封装成品（有 libass 烧录，否则软字幕轨）
    srt = gen_srt(segments, out_dir / f"subs.{target_lang}.srt", bilingual=True)
    out_video = out_dir / f"{video.stem}.dub.{target_lang}.mp4"
    mux(video, track, out_video, srt if burn_subs else None, lang=target_lang)

    # 8. 对齐质量报告：后续优化提示词 / 面试讲数据都靠它
    report = {
        "video": str(video),
        "target_lang": target_lang,
        "tts_provider": tts.name,
        "segments": [
            {"i": i, "slot": round(s.slot, 2), "tts": round(s.audio_duration, 2),
             "tempo": s.tempo, "action": s.action,
             "text": s.text, "translated": s.translated}
            for i, s in enumerate(segments)
        ],
    }
    actions = [s.action for s in segments]
    report["summary"] = {
        "total": len(segments),
        "fit": actions.count("fit"),
        "atempo": actions.count("atempo"),
        "overflow": actions.count("overflow"),
        "overflow_before_retry": n_overflow_before,
    }
    (out_dir / f"report.{target_lang}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _log("done", f"输出 {out_video}，耗时 {time.time() - t0:.1f}s，"
                 f"对齐 {report['summary']}")
    return out_video
