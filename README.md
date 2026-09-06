# EasyDub — AI 视频翻译出海流水线

把中文口播视频自动翻译成外语版：**ASR 提取字幕 → LLM 限长翻译 → TTS 配音 →
时长对齐 → 人脸段口型同步**，一条命令（CLI）或一次网页拖拽（Web）出成品，
并自动产出逐段量化的对齐质量报告。

对应岗位方向：广告出海 AI 视频翻译（ASR / TTS / lip-sync / Dify workflow）。

## 快速开始

```bash
# 0. 依赖：ffmpeg（系统级）+ Python 3.11/3.12
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1. 配置 LLM / 云端 ASR key（OpenAI 兼容或 Anthropic 兼容，协议按 base_url 自动识别）
cp .env.example .env   # 编辑填入 LLM_API_KEY / OPENROUTER_API_KEY

# 2. 生成示例视频（中文广告配音素材）
.venv/bin/python scripts/make_sample.py

# 3. 跑流水线
.venv/bin/python -m easydub translate samples/sample.mp4 --lang en

# 4. 查看对齐质量报告
.venv/bin/python -m easydub report artifacts/sample --lang en
```

成品输出在 `artifacts/sample/sample.dub.en.mp4`（烧录双语字幕 + mov_text 软字幕轨 + AAC 48kHz）。

默认**保留原视频背景音乐**：配音开口时原声自动闪避（sidechaincompress），
句间自动抬回——广告片的配乐不再被整条丢掉；`--no-bgm` 可关闭。

## Web 产品面

```bash
.venv/bin/python -m uvicorn server.app:app --port 8000
# 浏览器打开 http://127.0.0.1:8000：拖入视频 → 选语言 → 进度条 → 内嵌播放成品
```

三端点（Dify 亦可直接调）：`POST /api/jobs`（multipart: video/lang/lipsync/bgm）、
`GET /api/jobs/{id}`（状态+阶段进度）、`GET /api/jobs/{id}/result`（成品 mp4）、
`GET /api/jobs`（历史任务）。SQLite 任务表 `artifacts/jobs.db`，并发=1。
前端支持保留背景音乐/口型同步开关与历史任务回看。

## 口型同步（lip-sync）

人脸出镜段自动检测（OpenCV），只对口型段做 LatentSync 推理，切出镜头保留原画面；
单段失败自动回退，不阻断全片。需要先起本地 LatentSync 服务：

```bash
# LatentSync 部署见 EDPLAN.md §7；服务壳：
LATENTSYNC_DIR=~/LatentSync python server/lipsync_server.py    # :8001
# 全流程：
.venv/bin/python -m easydub translate 视频.mp4 --lang zh --lipsync latentsync
```

音轨始终使用自产配音（dub_track），口型只取画面结果。

## 评测体系（M5）

```bash
.venv/bin/python scripts/make_eval_set.py   # 5 条素材：纯口播/短句密集/低BGM/人脸出镜/慢语速
.venv/bin/python scripts/eval.py            # E1 消融：4 档 × 素材 × 语言 → 指标表
.venv/bin/python scripts/eval.py --clips c1_pure --tiers T4_full   # 只跑基线
.venv/bin/python scripts/eval.py --asr local --asr-model base      # E5：本地 whisper 对比云端
```

自动产出：溢出率、时长匹配率、CER（合成素材带标准文稿）、字幕时间轴偏移、
端到端耗时（RT 系数）、单条成本分项（单价表在 `easydub/eval_metrics.py` 可改）。
E1 消融 / E2 TTS 选型 / E5 ASR 选型数据见 [docs/EVALUATION.md](docs/EVALUATION.md)。

## 流水线

```
视频 ──▶ ASR(云端/whisper, 字级断句) ──▶ LLM 限长翻译 ──▶ TTS(edge/minimax/openrouter)
                                                              │
          成品 ◀── ffmpeg 字幕+音轨合成 ◀── 时长对齐(atempo) ◀┘
                    └── 人脸分段 → lip-sync(LatentSync) → 拼回
```

- **音画同步**采用"双向夹逼"：提示词按槽位秒数限制译文长度（生成端）+ 变速不变调压回
  槽位（后处理端），仍超时则按实测语速反推的字符预算重译；能借句间空隙容纳的溢出
  不算真冲突（spill），真冲突记录进 `report.json`。
- **音色自适应**：按说话人基频自动选男女声（`--voice` 显式指定优先），
  云 ASR 起点偏晚自动前移补偿（`--onset-shift` 可调）。
- **TTS 合成与重译并发**（4 线程），13 段配音合成 20s → 4.7s。
- **云 TTS 固定静音头尾**会在 TTS 后自动修剪（trim_silence），否则短句对齐全毁。
- **所有外部能力都是适配器**：LLM 双协议自动识别、ASR/TTS/口型可一键换供应商。
- **断点续跑**：各阶段产物落盘缓存（翻译/TTS 按目标语言与译文指纹隔离），
  中断重跑只补失败阶段，已完成的付费调用不重复。
- 设计文档见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，
  开发计划见 [docs/ROADMAP.md](docs/ROADMAP.md)，
  执行锚点见 [EDPLAN.md](EDPLAN.md)。

## 测试

```bash
.venv/bin/python -m pytest tests/ -q   # 纯逻辑单测 + API 打桩测试，无需真实网络
```
