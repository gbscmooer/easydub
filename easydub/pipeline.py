"""流水线编排：asr → translate → tts → align → mix → lipsync → mux。

每个阶段是独立函数（stage_*），输入上一阶段的 Segment 列表、产物落盘——
CLI 的 run() 只是顺序编排，MCP 工具 / Dify 节点 / Web 端直接调用单个阶段。
缓存语义：阶段产物文件存在且指纹匹配即跳过（翻译按目标语言+源文本、
TTS 按译文 MD5），断点续跑只补失败阶段。
"""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .align import (apply_onset_shift, calibrate_cps, find_overflow,
                    plan_alignment, reclassify_spill)
from .config import Settings
from .media import (SUBTITLE_STYLE, build_dub_track, change_tempo,
                    concat_videos, cut_clip, extract_audio,
                    extract_audio_slice, extract_bgm, gen_srt,
                    has_audio_stream, mix_with_bgm, mux, normalize_fps,
                    probe_duration, trim_silence)
from .models import Segment, load_segments, save_segments
from .services.http import retry_call
from .services.asr import transcribe, transcribe_openrouter
from .services.tts import make_tts
from .services.translator import CPS_TABLE, EchoTranslator, LLMTranslator

# Web 端注入的进度回调（并发=1，模块级单回调即可）
_progress_cb = None

# TTS/重译的并发线程数：都是网络 IO，串行是纯浪费；
# 4 是 edge-tts 免费通道无报限流、LLM 供应商也普遍友好的保守值
IO_WORKERS = 4


def _log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)
    if _progress_cb is not None:
        try:
            _progress_cb(stage)
        except Exception:  # 回调异常不影响流水线
            pass


def _parse_glossary(raw: str) -> dict:
    """.env 的 GLOSSARY 形如 "EasyDub=易达布;长白山=Changbai Mountain"。"""
    out = {}
    for pair in (raw or "").split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


# ---------------- 阶段 1：ASR ----------------
def stage_asr(video: Path, out_dir: Path, settings: Settings, *,
              asr_provider: str = "", asr_model=None,
              onset_shift: float = 0.0) -> list:
    seg_file = out_dir / "segments.json"
    if seg_file.exists():
        segments = load_segments(seg_file)
        _log("asr", f"命中缓存，{len(segments)} 段")
        return segments

    if not has_audio_stream(video):
        # 无音轨视频走零段落规格：正常出片（静音成品），而不是 ffmpeg 报错
        _log("asr", "原视频无音轨，按零段落处理")
        save_segments([], seg_file)
        return []

    wav = extract_audio(video, out_dir / "audio_zh.wav")
    provider = asr_provider or settings.asr_provider
    if provider == "openrouter":
        _log("asr", f"云端转录（{settings.openrouter_asr_model}）...")
        raw = transcribe_openrouter(wav, settings.openrouter_api_key,
                                    settings.openrouter_asr_model,
                                    settings.openrouter_base_url)
    else:
        _log("asr", f"本地识别（模型 {asr_model or settings.asr_model_size}）...")
        raw = transcribe(wav, asr_model or settings.asr_model_size)
    segments = [Segment(**d) for d in raw]
    if onset_shift and segments:
        n = apply_onset_shift(segments, onset_shift)
        _log("asr", f"起点平移补偿 {onset_shift}s（{n} 段，"
                    f"按 ASR 通道的时间轴偏差校准）")
    save_segments(segments, seg_file)
    _log("asr", f"识别到 {len(segments)} 段")
    return segments


# ---------------- 阶段 2：翻译 ----------------
def stage_translate(segments: list, target_lang: str, out_dir: Path,
                    settings: Settings, *, translator_mode: str = "llm",
                    limit_translation: bool = True,
                    glossary: dict = None) -> list:
    tr_file = out_dir / f"segments_translated.{target_lang}.json"
    if tr_file.exists():
        cached = load_segments(tr_file)
        # 源文本变了（ASR 重跑、断句策略调整）时旧译文作废，防止错位；
        # 文本没变也不能直接返回缓存对象——那会把旧时间轴带回来，悄悄
        # 回滚 ASR 侧的起点补偿等时间轴修正。只借译文，时间轴用新的。
        if ([s.text for s in cached] == [s.text for s in segments]
                and len(cached) == len(segments)):
            for seg, c in zip(segments, cached):
                seg.translated = c.translated
            _log("translate", "命中缓存（沿用译文，时间轴取当前）")
            return segments
    if not segments:
        return segments

    if translator_mode == "echo":
        tr = EchoTranslator()
    elif settings.llm_api_key:
        tr = LLMTranslator(settings.llm_api_key, settings.llm_base_url,
                           settings.llm_model, target_lang,
                           limit=limit_translation, glossary=glossary)
    else:
        _log("translate", "未配置 LLM_API_KEY，退化为 echo 模式（不翻译）")
        tr = EchoTranslator()
    tr.translate_all(segments)
    save_segments(segments, tr_file)
    protocol = getattr(tr, "protocol", "echo")
    _log("translate", f"完成，目标语言 {target_lang}（协议 {protocol}）")
    return segments


# ---------------- 阶段 3+4：TTS 合成与时长对齐（含溢出重译闭环） ----------------
def stage_tts_align(segments: list, target_lang: str, out_dir: Path,
                    settings: Settings, *, tts_provider: str = "edge",
                    translator_mode: str = "llm", voice=None,
                    allow_atempo: bool = True,
                    retry_overflow: bool = True, tts_rate: str = None,
                    glossary: dict = None, max_retry_rounds: int = 2,
                    video_total: float = None,
                    max_workers: int = IO_WORKERS) -> dict:
    """返回 {"tts": 名称, "overflow_before_retry": 首轮溢出数,
    "retry_rounds": 轮数, "measured_cps": 实测语速}"""
    tts = make_tts(tts_provider, target_lang, voice, settings, rate=tts_rate)
    # 缓存键含供应商与音色：切供应商/音色都不会错拿旧音频
    tag = "".join(c for c in (voice or getattr(tts, "voice", "") or "")
                  if c.isalnum() or c == "-")
    tts_dir = out_dir / (f"tts_{tts_provider}_{target_lang}"
                         + (f"_{tag}" if tag else ""))
    tts_dir.mkdir(exist_ok=True)

    def _synth(text, path):
        path = Path(path)
        # 先写 .part 再原子替换：中途崩溃不会留下"看似有效"的截断缓存
        part = path.with_suffix(path.suffix + ".part")
        # edge-tts 的 WSS 连接实测会瞬断：合成包一层退避重试
        retry_call(lambda: tts.synth(text, part), tries=3, backoff=2.0)
        trim_silence(part, path)
        part.unlink(missing_ok=True)

    def _tts_path(i: int, text: str) -> Path:
        # 文件名带译文指纹：译文变了（重译）不会错拿旧配音
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        return tts_dir / f"seg_{i:03d}_{h}.mp3"

    def _align(i, seg):
        audio = _tts_path(i, seg.translated)
        if not audio.exists():
            _synth(seg.translated, audio)
        try:
            seg.audio_duration = probe_duration(audio)
        except RuntimeError:
            # 历史遗留的截断缓存：删掉重合成，而不是让整条流水线报废
            _log("tts", f"缓存音频损坏，重合成：{audio.name}")
            audio.unlink(missing_ok=True)
            _synth(seg.translated, audio)
            seg.audio_duration = probe_duration(audio)
        seg.audio_path = str(audio)
        plan = plan_alignment(seg.slot, seg.audio_duration,
                              allow_atempo=allow_atempo)
        seg.tempo, seg.action = plan["tempo"], plan["action"]

    # 未命中缓存的合成并发跑（缓存命中只需本地 probe，串行无妨）
    todo = [(i, seg) for i, seg in enumerate(segments)
            if not _tts_path(i, seg.translated).exists()]
    if len(todo) > 1 and max_workers > 1:
        _log("tts", f"{len(todo)} 段需合成，{max_workers} 并发...")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(lambda p: _synth(p[1].translated,
                                         _tts_path(p[0], p[1].translated)),
                        todo))
    for i, seg in enumerate(segments):
        _align(i, seg)

    # 溢出重分类：音频能借句间空隙容纳的不算真冲突，不烧重译（align.reclassify_spill）
    n_spill = reclassify_spill(segments, video_total)
    if n_spill:
        _log("align", f"{n_spill} 段溢出可借句间空隙容纳，改判 spill（不重译）")

    # 溢出重译闭环（可多轮，逐轮收紧预算 25%）。双向夹逼的后手：
    # 首轮译文实测超时后，把实测约束反馈给生成端。
    # 重译预算不用静态 CPS 表：用本次 TTS 实测语速反推（换供应商不漂移）。
    cps = calibrate_cps(segments, CPS_TABLE.get(target_lang, 14))
    n_overflow_before = sum(1 for s in segments if s.action == "overflow")
    rounds = 0
    can_retry = (retry_overflow and translator_mode == "llm"
                 and settings.llm_api_key)
    if can_retry:
        while rounds < max_retry_rounds:
            overflows = find_overflow(segments, cps=cps)
            if not overflows:
                break
            rounds += 1
            n_round_start = len(overflows)
            shrink = 0.75 ** (rounds - 1)
            tr = LLMTranslator(settings.llm_api_key, settings.llm_base_url,
                               settings.llm_model, target_lang,
                               glossary=glossary)
            _log("retry", f"第{rounds}轮：{n_round_start} 段配音超时，"
                          f"按字符预算重译（实测语速 {cps} 字符/秒）")

            def _redo(p):
                seg = segments[p["index"]]
                budget = max(2, int(p["budget"] * shrink))
                return p["index"], budget, \
                    tr.retranslate(seg.text, seg.translated, budget)

            if len(overflows) > 1 and max_workers > 1:
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    results = list(ex.map(_redo, overflows))
            else:
                results = [_redo(p) for p in overflows]
            for idx, budget, new_text in results:
                seg = segments[idx]
                seg.translated = new_text
                _align(idx, seg)
                _log("retry", f"段{idx}：预算 {budget} 字符 → "
                              f"译文 {len(new_text)} 字符，配音 "
                              f"{seg.audio_duration:.2f}s，动作 {seg.action}")
            reclassify_spill(segments, video_total)
            # 重译结果写回翻译缓存，重跑不重复花钱
            save_segments(segments,
                          out_dir / f"segments_translated.{target_lang}.json")
            # 止损：本轮没有净减少溢出（预算已到 TTS 底噪/垫尾极限），再收紧无益
            n_now = len(find_overflow(segments, cps=cps))
            if n_now >= n_round_start:
                _log("retry", f"第{rounds}轮无净改善（{n_now}/{n_round_start}），"
                              f"提前止损")
                break
        remaining = len(find_overflow(segments, cps=cps))
        if remaining:
            _log("retry", f"重译 {rounds} 轮后仍有 {remaining} 段溢出，记入报告")
    else:
        n = len(find_overflow(segments, cps=cps))
        if n:
            why = "重译已关闭（消融档）" if not retry_overflow \
                else "echo 模式无 LLM"
            _log("retry", f"{n} 段配音超时；{why}，跳过重译")

    save_segments(segments, out_dir / f"segments_final.{target_lang}.json")
    return {"tts": tts.name, "overflow_before_retry": n_overflow_before,
            "retry_rounds": rounds, "measured_cps": cps,
            "voice": getattr(tts, "voice", None)}


# ---------------- 阶段 5：配音轨合成 ----------------
def stage_mix(segments: list, target_lang: str, video: Path,
              out_dir: Path, *, keep_bgm: bool = True) -> dict:
    """合成配音轨，返回 {"dub": 纯配音轨, "render": 成片用音轨}。

    keep_bgm=True 时 render 是"配音 + 原声闪避"混合轨：配音开口时原声
    （BGM/环境声）被自动压低、句间自动抬回——广告片的配乐不再被整条丢掉。
    口型对齐只吃纯配音轨（混入 BGM 会污染口型特征提取）。
    """
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
    dub = build_dub_track(clips, total,
                          out_dir / f"dub_track.{target_lang}.wav")

    def _plain(note: str) -> dict:
        _log("mix", note)
        return {"dub": dub, "render": dub}

    if not keep_bgm:
        return _plain(f"音轨合成完毕（{len(clips)} 段，未保留背景音乐）")
    if not has_audio_stream(video):
        return _plain("原视频无音轨，跳过背景音乐保留")
    if not segments:
        # 零段落（无声视频）：原声直通，成片保留完整原声
        return _plain("零段落：原声直通作为成片音轨")
    render = mix_with_bgm(dub, extract_bgm(video, out_dir / "bgm.wav"),
                          out_dir / f"dub_track_bgm.{target_lang}.wav")
    _log("mix", f"音轨合成完毕（{len(clips)} 段 + 原声闪避混入）")
    return {"dub": dub, "render": render}


# ---------------- 阶段 6：lip-sync ----------------
def stage_lipsync(video: Path, track: Path, target_lang: str, out_dir: Path,
                  settings: Settings, provider_name: str) -> Path:
    """只对人脸出镜段对口型；返回用于封装的视频路径（无口型时返回原视频）。

    音轨始终是自产 dub_track——口型只吃画面结果，不吃它的音轨。
    """
    if provider_name == "none":
        return video

    from .services.facedetect import detect_face_segments
    from .services.lipsync import make_lipsync

    provider = make_lipsync(provider_name, settings)
    _log("lipsync", f"检测人脸出镜段（{provider.name}）...")
    face_segs = detect_face_segments(video)
    total = probe_duration(video)
    master = normalize_fps(video, out_dir / "master_25fps.mp4")
    pieces, cur = [], 0.0
    for s, e in face_segs:
        if s > cur + 0.05:
            pieces.append((cur, s, False))
        pieces.append((max(s, cur), e, True))
        cur = e
    if cur < total - 0.05:
        pieces.append((cur, total, False))

    if not any(is_face for _, _, is_face in pieces):
        _log("lipsync", "未检测到人脸段，跳过口型同步")
        return video

    ls_dir = out_dir / "lipsync"
    ls_dir.mkdir(exist_ok=True)
    synced = {}
    for idx, (s, e, is_face) in enumerate(pieces):
        if not is_face:
            continue
        out_clip = ls_dir / f"sync_{idx:03d}.mp4"
        if not out_clip.exists():
            _log("lipsync", f"段{idx} [{s:.1f}-{e:.1f}s] 口型同步中...")
            clip = cut_clip(master, s, e, ls_dir / f"clip_{idx:03d}.mp4")
            audio = extract_audio_slice(track, s, e,
                                        ls_dir / f"audio_{idx:03d}.wav")
            try:
                provider.apply(clip, audio, out_clip)
            except Exception as e:
                # 切出镜头/单帧无脸会让整段失败：回退原画面，不阻断全片
                _log("lipsync", f"段{idx} 失败（{e}），该段回退原画面")
                out_clip.unlink(missing_ok=True)
                continue
        synced[idx] = out_clip
    if not synced:
        _log("lipsync", "全部人脸段口型失败，整片使用原画面")
        return video
    final_clips = [
        synced[idx] if idx in synced
        else cut_clip(master, s, e, ls_dir / f"clip_{idx:03d}.mp4")
        for idx, (s, e, is_face) in enumerate(pieces)
    ]
    video_for_mux = concat_videos(final_clips,
                                  out_dir / f"sync_master.{target_lang}.mp4")
    _log("lipsync", f"口型段拼回时间轴（{len(synced)} 段成功）")
    return video_for_mux


# ---------------- 阶段 7+8：字幕封装与报告 ----------------
def stage_render(video_for_mux: Path, track: Path, segments: list,
                 target_lang: str, video: Path, out_dir: Path, *,
                 burn_subs: bool = True,
                 tts_name: str = "?", tts_stats: dict = None,
                 bgm_kept: bool = False) -> Path:
    srt = gen_srt(segments, out_dir / f"subs.{target_lang}.srt",
                  bilingual=True) if segments else None
    out_video = out_dir / f"{video.stem}.dub.{target_lang}.mp4"
    mux(video_for_mux, track, out_video, srt if burn_subs else None,
        lang=target_lang)

    stats = tts_stats or {}
    report = {
        "video": str(video),
        "target_lang": target_lang,
        "tts_provider": tts_name,
        "retry_rounds": stats.get("retry_rounds", 0),
        "measured_cps": stats.get("measured_cps"),
        "voice": stats.get("voice"),
        "keep_bgm": bgm_kept,
        "segments": [
            {"i": i, "start": round(s.start, 3), "end": round(s.end, 3),
             "slot": round(s.slot, 2), "tts": round(s.audio_duration, 2),
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
        "spill": actions.count("spill"),
        "overflow": actions.count("overflow"),
        "overflow_before_retry": stats.get("overflow_before_retry", 0),
    }
    (out_dir / f"report.{target_lang}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    from .report_html import write_html_report
    write_html_report(report, out_dir / f"report.{target_lang}.html")
    return out_video


# ---------------- CLI/默认编排 ----------------
def run(video, target_lang: str = "en", *, tts_provider: str = "edge",
        translator_mode: str = "llm", voice=None, asr_provider: str = "",
        asr_model=None, burn_subs: bool = True, workdir=None,
        lipsync_provider: str = "none", progress=None,
        limit_translation: bool = True, allow_atempo: bool = True,
        retry_overflow: bool = True, tts_rate: str = None,
        keep_bgm: bool = True, asr_onset_shift: float = None,
        auto_voice: bool = True,
        subtitle_style: str = SUBTITLE_STYLE) -> Path:
    global _progress_cb
    _progress_cb = progress
    t0 = time.time()
    settings = Settings()
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    out_dir = Path(workdir or settings.workdir) / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    glossary = _parse_glossary(settings.glossary)
    # ASR 时间轴偏差随通道方向不同（E5 实测）：fish 云端偏晚 ~0.15s → +0.12
    # 前移；本地 whisper 偏早 ~0.08s → -0.07 后移。显式传参可覆盖。
    provider = asr_provider or settings.asr_provider
    if asr_onset_shift is None:
        asr_onset_shift = 0.12 if provider == "openrouter" else -0.07

    segments = stage_asr(video, out_dir, settings, asr_provider=asr_provider,
                         asr_model=asr_model, onset_shift=asr_onset_shift)
    if not segments:
        # 规格要求无声视频"正常完成（零段落）"：跳过翻译/配音，出静音成品
        _log("asr", "未识别到语音，按零段落处理：输出静音成品")

    # 音色自适应：未显式指定音色时按说话人基频选男女声（edge 音色带性别）
    if voice is None and auto_voice and tts_provider == "edge" and segments:
        from .services.voice_match import auto_pick_voice
        picked, f0 = auto_pick_voice(video, segments, out_dir, target_lang)
        if picked:
            voice = picked
            _log("voice", f"说话人基频 ≈{f0:.0f}Hz → 自动选音色 {picked}")

    segments = stage_translate(segments, target_lang, out_dir, settings,
                               translator_mode=translator_mode,
                               limit_translation=limit_translation,
                               glossary=glossary)
    tts_stats = stage_tts_align(segments, target_lang, out_dir, settings,
                                tts_provider=tts_provider,
                                translator_mode=translator_mode, voice=voice,
                                allow_atempo=allow_atempo,
                                retry_overflow=retry_overflow,
                                tts_rate=tts_rate, glossary=glossary,
                                video_total=probe_duration(video))
    tracks = stage_mix(segments, target_lang, video, out_dir,
                       keep_bgm=keep_bgm)
    video_for_mux = stage_lipsync(video, tracks["dub"], target_lang, out_dir,
                                  settings, lipsync_provider)
    out_video = stage_render(video_for_mux, tracks["render"], segments,
                             target_lang, video, out_dir, burn_subs=burn_subs,
                             tts_name=tts_stats["tts"], tts_stats=tts_stats,
                             bgm_kept=tracks["render"] != tracks["dub"])

    _log("done", f"输出 {out_video}，耗时 {time.time() - t0:.1f}s，"
                 f"对齐 {json.loads((out_dir / f'report.{target_lang}.json')
                                    .read_text())['summary']}")
    return out_video


def run_managed(video, target_lang: str = "en", **kw) -> Path:
    """带进度回调复位保护的 run：Web 后台线程用它，避免回调泄漏到下一次任务。"""
    global _progress_cb
    progress = kw.pop("progress", None)
    try:
        # 回调必须显式传给 run()：run() 内部会用 progress 参数（默认 None）
        # 覆盖模块级回调——不传就会被清掉，Web 进度条永远停在提交瞬间
        return run(video, target_lang, progress=progress, **kw)
    finally:
        _progress_cb = None
