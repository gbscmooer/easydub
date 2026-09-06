# EasyDub — AI 视频翻译出海流水线

把中文广告视频自动翻译成外语版：**ASR 提取字幕 → LLM 限长翻译 → TTS 配音 → 时长对齐 → 口型对齐（WIP）**，一条命令出成品。

对应岗位方向：广告出海 AI 视频翻译（ASR / TTS / lip-sync / Dify workflow）。

## 快速开始

```bash
# 0. 依赖（ffmpeg 需已安装：brew install ffmpeg）
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1. 配置 LLM key（OpenAI 兼容或 Anthropic 兼容均支持，协议按 base_url 自动识别）
cp .env.example .env   # 编辑填入 LLM_API_KEY

# 2. 生成示例视频（中文广告配音素材）
.venv/bin/python scripts/make_sample.py

# 3. 跑流水线（首次会自动下载 whisper 模型，走国内镜像）
.venv/bin/python -m easydub translate samples/sample.mp4 --lang en

# 4. 查看对齐质量报告
.venv/bin/python -m easydub report artifacts/sample
```

成品输出在 `artifacts/sample/sample.dub.en.mp4`。

## 流水线

```
视频 ──▶ ASR(whisper 时间轴) ──▶ LLM 限长翻译 ──▶ TTS(edge/minimax)
                                                      │
        成品 ◀── ffmpeg 字幕+音轨合成 ◀── 时长对齐(atempo) ◀┘
                  └── (D8) lip-sync 口型对齐
```

- **音画同步**采用"双向夹逼"：提示词按槽位秒数限制译文长度（生成端）+ 变速不变调压回槽位（后处理端），溢出段记录进 `report.json` 供迭代。
- **所有外部能力都是适配器**：LLM 双协议自动识别、TTS/口型可一键换供应商。
- 设计文档见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，开发计划见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 测试

```bash
.venv/bin/python -m pytest tests/ -q   # 纯逻辑单测，无需网络与 key
```
