# EasyDub 架构设计

> AI 视频翻译出海流水线：把中文口播视频自动翻译成外语版（字幕 + 配音 + 口型对齐），
> 并自动产出可量化的对齐质量报告（JSON + 可视化 HTML）。

## 总体流水线

```
输入视频 (mp4)
   │
   ▼
┌─────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐
│  ASR    │ → │  翻译     │ → │  TTS    │ → │ 时长对齐  │ → │  合成    │
│ 云端/本地│   │ LLM 限长 │   │ edge/   │   │ atempo   │   │ ffmpeg  │
│ 起点补偿 │   │ 实测语速 │   │ minimax │   │ + spill  │   │ 字幕+音轨│
└─────────┘   └──────────┘   └─────────┘   └──────────┘   └────┬────┘
        │             ▲              ▲                         ▼
        │      ┌──────┴──────┐  ┌────┴─────┐          ┌────────────┐
        └────→ │ 音色自适应   │  │ 溢出重译  │          │ lip-sync   │
       f0 探测 │ 男女声选择   │  │ (Agent   │          │ 人脸段口型 │
               └─────────────┘  │  可接管) │          └─────┬──────┘
                                └──────────┘                ▼
                        原声 ──闪避──┐              输出外语版视频
                        (BGM 保留) ─┴── 成品音轨        + report.json/.html
```

## 分层

| 层 | 模块 | 职责 | 关键设计 |
|----|------|------|----------|
| 编排层 | `pipeline.py` | 串阶段、管缓存、出报告、分阶段计时 | 阶段产物落盘，重跑跳过已成功阶段；`stage_*` 可单独被 MCP/复用 |
| 服务层 | `services/*` | 外部能力适配 | asr / translator / tts / lipsync / facedetect / **voice_match**（f0 音色自适应）/ http（退避重试） |
| 媒体层 | `media.py` | ffmpeg/ffprobe 唯一出口 | 命令可追溯；BGM 闪避滤镜图是纯函数（`bgm_filtergraph`） |
| 契约层 | `models.py` + `align.py` | 数据结构与纯算法 | `Segment` 贯穿全程；对齐/校准/改判全是纯函数，可单测可实验 |
| 报告层 | `eval_metrics.py` + `report_html.py` | 指标折算 + 可视化 | report.json（机器）+ report.html（人看），单文件零依赖 |
| 产品面 | `server/app.py` + `web/` | FastAPI 三端点 + React | 任务表落 SQLite；前端拖拽/开关/历史 |

## 核心决策（面试可讲）

### 1. 时间轴是第一公民：`Segment(start, end, text, translated, ...)`

ASR 产出它，翻译只补 `translated`，TTS 只补音频字段，对齐只补 `tempo/action`。
每个阶段"读上一阶段的 json、写自己的 json"：

- 天然支持断点续跑（重跑只补失败阶段）；
- 每个阶段可独立单测、独立重放；
- 接 Dify 时，每个阶段直接映射为一个 HTTP 节点。

**缓存键三要素**（踩坑换来的）：目标语言（切语言不串）、译文 MD5（重译不拿旧
音频）、供应商+音色（换 TTS/换男女声不错拿）。翻译缓存命中只借译文、时间轴
一律用当前 ASR 的——否则 ASR 侧时间轴修正会被旧对象静默回滚。

### 2. 音画同步 = 生成端 + 后处理端"双向夹逼" + 口径演进

中文信息密度高，直译外语通常长 20%~40%。三层手段：

- **生成端**（translator）：提示词按槽位秒数限长；**重译预算不用静态 CPS 表**，
  用本次 TTS 实测语速中位数反推（`calibrate_cps`，只下修、60% 表值下限）；
- **后处理端**（align/media）：`ratio = tts/槽位` 分三档——≤1.0 fit、
  1.0~1.25 atempo（变速不变调限幅）、>1.25 overflow（1.25 以上听感发急）；
- **重译闭环**：超时段按预算×0.75^轮次重译（最多 2 轮），无净改善即止损。

**spill 口径演进**（E1 消融双口径）：溢出音频若能借句间空隙播完、撞不到下一句
配音/视频结尾，改判 `spill`——听感是"准时开始、停顿里收尾"，不算真冲突、不烧
LLM、不计溢出率。消融实验在新旧两个口径下梯度都成立（见 EVALUATION.md）。

**起点双向补偿**（`apply_onset_shift`）：时间轴偏差方向随 ASR 通道相反——
fish 云端偏晚 ~0.15s（+0.12 前移）、whisper 偏早 ~0.08s（-0.07 后移），
起止整体平移保槽位时长，借空隙受相邻段约束。补偿后双通道字幕偏移 ≤40ms。

### 3. 适配器模式隔离所有外部依赖

TTS（edge/minimax/openrouter）、LLM（OpenAI/Anthropic 双协议自动识别）、
ASR（云端 fish / 本地 faster-whisper，E5 选型数据见 EVALUATION）、
lip-sync（LatentSync 本地为主）全部是"协议接口 + provider"。
**音色自适应**（voice_match）：说话人基频 f0 检测（FFT 自相关，仅 numpy）
自动选男女声——女声说话人不再配出男声。

### 4. ffmpeg 是媒体操作唯一出口

所有音视频操作集中在 `media.py`：音轨合成 `adelay` 定位 + `amix(normalize=0)`。
**BGM 保留 + 自动闪避**：配音作 sidechain 触发信号，`sidechaincompress` 压低
原声、句间自动抬回（广告配乐不再被整条丢掉），`alimiter` 防削波；口型对齐只吃
纯配音轨（BGM 会污染口型特征）。字幕烧录（libass）+ mov_text 软字幕轨双层。

### 5. 口型对齐的接入策略

只对**人脸出镜段**对口型（Haar 级联检测跳过空镜）——省算力；逐段失败自动回退
原画面不阻断全片；LatentSync 原生 25fps，接入前 `normalize_fps` 出主档。

### 6. 可观测性是一等公民

每次 run 自动产出：`report.<lang>.json`（逐段动作/槽位/配音时长/实测语速/
分阶段计时）+ `report.<lang>.html`（动作着色时间轴，悬停看详情）；
Web 端任务进度百分比贯通到前端；E1/E2/E5 评测矩阵一键重放
（`scripts/eval.py`，结果按 素材×语言×档位×ASR 通道增量合并）。

## Dify 编排映射（D8，预留）

Dify 不做重媒体处理，只做**可视化编排 + 提示词调参界面**：
FastAPI 三端点（提交/进度/成品）即 HTTP 节点契约，LLM 翻译提示词可搬进 Dify
节点可视化迭代。面试时能讲清"为什么视频处理不塞进 Dify"（节点超时、大文件
不走其内存）本身就是加分项。

## 目录结构

```
easydub/
├── easydub/
│   ├── pipeline.py        # 编排（阶段化 + 计时 + 进度回调）
│   ├── config.py          # 配置（.env）
│   ├── models.py          # Segment 契约
│   ├── align.py           # 对齐纯算法（plan/calibrate/spill/onset_shift）
│   ├── media.py           # ffmpeg 唯一出口（含 BGM 闪避）
│   ├── eval_metrics.py    # 指标纯函数（CER/偏移/成本）
│   ├── report_html.py     # 可视化报告生成
│   ├── __main__.py        # CLI（translate/report/lipsync-test）
│   └── services/
│       ├── asr.py         # 云端/本地 + 断句合并
│       ├── translator.py  # LLM 双协议 + 限长 + 重译
│       ├── tts.py         # edge / minimax / openrouter
│       ├── voice_match.py # 说话人 f0 → 男女声
│       ├── facedetect.py  # Haar 人脸分段
│       ├── lipsync.py     # LatentSync 客户端
│       └── http.py        # 退避重试
├── server/                # FastAPI 产品面 + LatentSync 服务壳
├── scripts/               # eval / make_eval_set / make_demo_video / agent
├── tests/                 # 75 项：纯逻辑单测 + ffmpeg e2e + API 打桩
├── docs/                  # 本文档 + ROADMAP + EVALUATION + INTERVIEW
└── ITERATIONS.md          # 自主优化迭代记录（第 6-10 轮）
```
