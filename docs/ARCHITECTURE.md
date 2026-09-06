# EasyDub 架构设计

> AI 视频翻译出海流水线：把中文广告视频自动翻译成外语版（字幕 + 配音 + 口型对齐）。

## 总体流水线

```
输入视频 (mp4)
   │
   ▼
┌─────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐
│  ASR    │ → │  翻译     │ → │  TTS    │ → │ 时长对齐  │ → │  合成    │
│ whisper │   │ LLM 限长 │   │ edge/   │   │ atempo   │   │ ffmpeg  │
│ 时间轴  │   │ 提示词   │   │ minimax │   │ 双向夹逼 │   │ 字幕+音轨│
└─────────┘   └──────────┘   └─────────┘   └──────────┘   └────┬────┘
                                                               ▼
                                                    (D8) lip-sync 口型对齐
                                                               ▼
                                                        输出外语版视频
```

## 分层

| 层 | 模块 | 职责 | 关键设计 |
|----|------|------|----------|
| 编排层 | `pipeline.py` | 串阶段、管缓存、出报告 | 阶段产物落盘，重跑跳过已成功阶段 |
| 服务层 | `services/*` | 外部能力适配 | 每个能力一个协议接口 + N 个 provider，换供应商不动上游 |
| 媒体层 | `media.py` | ffmpeg/ffprobe 唯一出口 | 命令可追溯，报错打印完整命令 |
| 契约层 | `models.py` + `align.py` | 数据结构与纯算法 | `Segment` 贯穿全程；对齐是纯函数，可单测可实验 |

## 核心决策（面试可讲）

### 1. 时间轴是第一公民：`Segment(start, end, text, translated, ...)`

ASR 产出它，翻译只补 `translated`，TTS 只补音频字段，对齐只补 `tempo/action`。
每个阶段"读上一阶段的 json、写自己的 json"，于是：

- 天然支持断点续跑（重跑只补失败阶段）；
- 每个阶段可独立单测、独立重放；
- 接 Dify 时，每个阶段直接映射为一个 HTTP 节点。

### 2. 音画同步 = 生成端 + 后处理端"双向夹逼"

翻译后的外语比中文长（中文信息密度高，英文通常多 20%~40% 音节），硬塞回原时间轴必然超时。单一手段救不回来，所以两头同时做：

- **生成端**（`services/translator.py`）：提示词里按每段槽位秒数给译文限长，
  语速按 `CPS_TABLE`（英语 ≈14 字符/秒）估算字符预算；
- **后处理端**（`align.py` + `media.py`）：TTS 实际时长出来后，
  `ratio = tts时长 / 槽位时长`，分三档处理：

| ratio | action | 处理 |
|-------|--------|------|
| ≤ 1.0 | `fit` | 原速，准时开始 |
| 1.0 ~ 1.25 | `atempo` | ffmpeg atempo 变速不变调，压回槽位 |
| > 1.25 | `overflow` | 变速到上限仍放不下，记入报告（后续用提示词重译更短版本）|

限幅 1.25 的原因：再快听感明显发急。溢出段全部记录在 `report.json`，
这是优化提示词的数据依据，也是面试里"你怎么量化音画同步质量"的答案。

### 3. 适配器模式隔离所有外部依赖

TTS（edge/minimax）、LLM（OpenAI/Anthropic 双协议自动识别）、ASR（模型规格可调）、
lip-sync（sync.so/LatentSync）全部是"协议接口 + provider"。
供应商是项目里最不稳定的部分（额度、价格、可用性），换它不该牵动流水线。

### 4. ffmpeg 是媒体操作唯一出口

所有音视频操作集中在 `media.py`：音轨合成用 `adelay` 定位 + `amix(normalize=0)`
叠加到静音底轨，保证每段配音精确落在原时间戳上。字幕优先烧录（需 libass），
ffmpeg 构建不带 libass 时自动降级为 mov_text 软字幕轨（播放器可开关、
视频流免重编码）——实测本机 ffmpeg 8.1 无 libass，走软字幕轨。

### 5. 口型对齐的接入策略（D8）

- 只对**人脸出镜段**做 lip-sync（人脸检测跳过空镜/产品镜头）——省算力也省钱；
- `lipsync.py` 先定死接口：`apply(视频段, 音频) -> 视频段`，
  sync.so（API，主）与 LatentSync（开源+租卡，备）两条腿，谁先跑通用谁。

## Dify 编排映射（Week 2）

Dify 不做重媒体处理，只做**可视化编排 + 提示词调参界面**：

| Dify 节点 | 对应本项目的 |
|-----------|--------------|
| 开始节点（视频输入） | CLI 的 video 参数 |
| HTTP 节点 ×1 | `POST /api/asr`（包 `transcribe`） |
| LLM 节点 | 翻译提示词搬进 Dify，可视化迭代 |
| HTTP 节点 ×2 | `POST /api/tts-align`、`POST /api/render` |
| 结束节点 | 返回成品视频 URL |

即：FastAPI 把三个阶段包成端点，Dify 负责串。面试时能讲清
"为什么视频处理不塞进 Dify"（Dify 节点有超时限制、大文件不走其内存）本身就是加分项。

## 目录结构

```
easydub/
├── easydub/
│   ├── pipeline.py        # 编排
│   ├── config.py          # 配置（.env）
│   ├── models.py          # Segment 契约
│   ├── align.py           # 时长对齐纯算法
│   ├── media.py           # ffmpeg 唯一出口
│   ├── __main__.py        # CLI
│   └── services/
│       ├── asr.py         # faster-whisper + 碎段合并
│       ├── translator.py  # LLM 双协议 + 限长提示词
│       ├── tts.py         # edge / minimax
│       └── lipsync.py     # D8 接入点（接口已定）
├── scripts/make_sample.py # 生成示例素材
├── tests/                 # 纯逻辑单测（无网络依赖）
└── docs/                  # 本文档 + ROADMAP
```
