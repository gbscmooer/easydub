# EasyDub 架构设计

> AI 视频翻译出海流水线：把中文广告视频自动翻译成外语版（字幕 + 配音 + 口型对齐）。

## 总体流水线

```
输入视频 (mp4)
   │
   ▼
┌─────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐
│  ASR    │ → │  翻译     │ → │  TTS    │ → │ 时长对齐  │ → │  合成    │
│ 云端/   │   │ LLM 限长 │   │ edge/   │   │ atempo   │   │ ffmpeg  │
│ whisper │   │ 提示词   │   │ minimax │   │ 双向夹逼 │   │ 字幕+音轨│
└─────────┘   └──────────┘   └─────────┘   └──────────┘   └────┬────┘
                                                               ▼
                                        人脸分段 → lip-sync(LatentSync) → 拼回
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

实测补充（Nike/TED 实验）：**云 TTS 固定带 0.2s 头静音 + 最长近 1s 尾静音**，
1 个字的配音实测 1.51s、语音只占 0.33s——不修剪的话短句对齐全毁。
TTS 后统一过 `trim_silence`（保留 0.05s 头/0.10s 尾垫），溢出率 8/13 → 1/13。

### 3. 适配器模式隔离所有外部依赖

TTS（edge/minimax）、LLM（OpenAI/Anthropic 双协议自动识别）、ASR（模型规格可调）、
lip-sync（sync.so/LatentSync）全部是"协议接口 + provider"。
供应商是项目里最不稳定的部分（额度、价格、可用性），换它不该牵动流水线。

### 4. ffmpeg 是媒体操作唯一出口

所有音视频操作集中在 `media.py`：音轨合成用 `adelay` 定位 + `amix(normalize=0)`
叠加到静音底轨，保证每段配音精确落在原时间戳上。字幕双层输出：有 libass 时
烧录（任何播放器可见）**同时**封 mov_text 软字幕轨（可开关、可换语言元数据）；
无 libass 自动降级为仅软字幕轨。音轨统一 AAC 48kHz。
`subtitles` 滤镜参数有专门的转义函数（filtergraph 单引号层 + 选项 `\:` 层），
文件名里的逗号/空格/括号不会炸滤镜图。

### 5. 口型对齐：人脸分段 + 分段推理 + 失败回退（已实现）

- **人脸分段**（`services/facedetect.py`）：Haar 级联抽样判帧（0.5s 步进），
  无人脸间隔 <1s 并入同段、<1s 碎段丢弃——纯 OpenCV，无需 GPU/新模型；
- **分段推理**：先统一 25fps 出 master 档（LatentSync 原生 25fps），
  人脸段切子视频 + 从 dub_track 切对应音频 → 逐段提交 LatentSync 服务
  （HTTP 契约：提交/轮询/下载）→ concat 拼回时间轴；
- **两条容错**：本地 LatentSync 打了"单帧检不到脸沿用上一帧人脸框"补丁
  （TED 切出镜头/低头帧不可避免）；流水线侧单段失败回退原画面不阻断全片；
- **音轨始终用自产 dub_track**：只取口型结果的画面，不吃它的音轨（避免二压）；
- 服务壳 `server/lipsync_server.py` 环境变量化（LATENTSYNC_DIR/CMD），
  本机 WSL 5090 与 Windows 机通用。

## Dify 编排映射（Week 2）

Dify 不做重媒体处理，只做**可视化编排 + 提示词调参界面**：

| Dify 节点 | 对应本项目的 |
|-----------|--------------|
| 开始节点（视频输入） | Web 端 video 参数 |
| HTTP 节点 ×1 | `POST /api/jobs`（提交任务） |
| LLM 节点 | 翻译提示词搬进 Dify，可视化迭代 |
| HTTP 节点 ×2 | `GET /api/jobs/{id}`（进度）、`GET /api/jobs/{id}/result`（成品） |
| 结束节点 | 返回成品视频 URL |

即：FastAPI 把三个阶段包成端点，Dify 负责串。面试时能讲清
"为什么视频处理不塞进 Dify"（Dify 节点有超时限制、大文件不走其内存）本身就是加分项。

## 目录结构

```
easydub/
├── easydub/
│   ├── pipeline.py        # 编排（含消融开关 limit/atempo/retry）
│   ├── config.py          # 配置（.env）
│   ├── models.py          # Segment 契约
│   ├── align.py           # 时长对齐纯算法
│   ├── media.py           # ffmpeg 唯一出口（切/拼/变速/字幕/封装）
│   ├── eval_metrics.py    # 评测指标纯函数（CER/偏移/成本）
│   ├── __main__.py        # CLI
│   └── services/
│       ├── asr.py         # 云端转录 + faster-whisper + 字级断句
│       ├── translator.py  # LLM 双协议 + 限长提示词 + 退避重试
│       ├── tts.py         # edge / minimax / openrouter + 静音修剪
│       ├── facedetect.py  # Haar 人脸分段
│       ├── http.py        # 退避重试
│       └── lipsync.py     # LatentSync 客户端 / sync.so 预留
├── server/
│   ├── app.py             # Web 三端点 + SQLite 任务表
│   └── lipsync_server.py  # LatentSync 服务壳（可环境变量配置）
├── web/                   # React 拖拽上传页（Vite，构建产物由 FastAPI 托管）
├── scripts/
│   ├── make_sample.py     # 示例素材
│   ├── make_eval_set.py   # 5 条评测素材（带标准文稿）
│   └── eval.py            # E1 消融编排 + 指标表
└── tests/                 # 纯逻辑单测 + API 打桩测试（35 例）
```
