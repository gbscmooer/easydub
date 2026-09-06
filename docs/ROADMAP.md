# EasyDup 成品级路线图 v2 — 对齐 JD 逐行验收

> **成品级验收标准**：把链接发给一个不懂技术的人——打开网页，拖入中文广告视频，
> 选目标语言，看进度条，播放英文成品（双语字幕 + 配音 + 口型同步）。
> 全程无命令行，端到端 ≤ 10 分钟。外加一张 Dify 工作流画布截图证明编排能力。

## JD 逐行对照

| JD 原文 | 项目对应 | 状态 |
|---------|----------|------|
| ASR 语音识别提取中文字幕 | fish-audio/transcribe-1 + 字级时间戳断句 | ✅ 已通 |
| 翻译成外语 | DeepSeek 限长创译（槽位时长→字符预算） | ✅ 已通 |
| minimax TTS 合成外语音频 | `MinimaxTTS` 适配器 | 代码就绪，**待 key 实测** |
| sync / 开源框架对上口型，音频驱动画面 | `lipsync.py` 客户端 + 自建 LatentSync 服务（Windows 5090，`server/`） | D5–D6 |
| 在 dify 平台搭建 workflow | Dify Cloud + 3 个 HTTP 节点串 FastAPI | D8 |
| 优化节点提示词：分段处理、框定时间段匹配中文时长 | 限长创译 + report.json 溢出闭环 | 核心已通，D3 补溢出重译 |
| 音频视频动态缩放解决音画不同步 | atempo 双向夹逼（限幅 1.25）+ 溢出重译 | ✅ 已通，持续调优 |
| （成品级自加）用户可直接使用的产品面 | React 上传页 + FastAPI 端点 | D7 |

## 任务表

### D1 ✅ 已完成（2026-09-04）
全链路真实跑通：OpenRouter 云 ASR + DeepSeek 翻译 + edge TTS + 对齐合成，13.3s 出英文成品，8 单测全绿。

### D2 ✅ TTS 选型定案（2026-09-05 用户拍板）
- [x] TTS 主通道 = OpenRouter `fish-audio/s2.1-pro-free:free`（免费、已实测通过）；edge-tts 保留为免费后备；minimax **不注册了**，适配器保留——面试可讲"接口就绪、随时可切换"
- 变更理由：全链路统一走一个 OpenRouter key，成本≈0，供应商管理最简
- Dify Cloud 已注册（Professional 额度：50 应用 / API 无限制），D8 随时可做

### D3 溢出重译闭环 ✅ 完成（2026-09-06 收尾）
- [x] `find_overflow` 纯函数 + 单测（align.py）→ 重译提示词（translator.py `retranslate`）→ 接入 pipeline 4.5 环节：超时段按字符预算重译、重配音、复测；报告新增 `overflow_before_retry`
- [x] 实测：样例段 49 字符/3.21s(overflow) → 重译 39 字符/2.85s(atempo)，溢出 1→0
- [x] 术语表：品牌名/slogan 跨段译法一致（提示词注入 glossary）
- [x] 二次收紧重译循环（预算 ×0.75^(轮-1)，最多 2 轮）+ 无净改善止损；
      spill 重分类后真冲突才触发重译（见第二轮自主优化）
- 面试点：翻译质量的量化指标是"时长匹配率"；字符预算只是时长的代理指标，最终以 TTS 实测时长为准

### D4 真实素材 + MVP 存档
- [ ] 自制 3 条素材（口播/带 BGM/短句密集，make_sample.py 扩展，**不用真实品牌广告**）
- [ ] 全链路跑通，录一版"字幕+配音"对比 demo 存档（无 lip-sync 也成立的安全垫）

### D5 lip-sync 技术验证 ✅ 已完成（2026-09-06，环境=本机 WSL+RTX 5090）
- [x] LatentSync 1.6 部署在 `~/LatentSync`，conda 环境 `latentsync`（clone 自 vllm-cuda129，torch 2.10+cu128），权重走 hf-mirror 下载
- [x] `server/lipsync_server.py` 环境变量化（LATENTSYNC_DIR/CMD），:8001 服务实测 /health 正常
- [x] `python -m easydub lipsync-test` HTTP 契约全通（7.5s 样片 91s 出片）
- [ ] （可选对比）sync.so 免费额度跑同素材，记录效果/耗时/价格
- 验收：Mac 上一条命令拿到口型正确的 15s 样片
- 面试点：自建 GPU 推理服务（HTTP 契约、任务轮询）vs 商用 API 的取舍；自有 5090 长期零边际成本

### D6 lip-sync 接入流水线 ✅ 已完成（2026-09-06）
- [x] `facedetect.py` Haar 抽样分段；只对出镜段做口型对齐；单段失败回退原画面
- [x] 25fps 主档切分→逐段口型→concat 拼回→音频仍用 dub_track
- 验收 ✅：TED 40s 切段 `--lang zh --lipsync latentsync` 216s 出片，口型段拼回成功
- 注意：本地 LatentSync 打了补丁（单帧检不到脸沿用上一帧人脸框），见 EDPLAN §6

### D7 FastAPI 三端点 + React 上传页（产品面）✅ 已完成（2026-09-06）
- [x] `/api/jobs`（提交）、`/api/jobs/{id}`（进度）、`/api/jobs/{id}/result`——Dify 和前端共用（SQLite 任务表，并发=1）
- [x] React 页（Vite）：拖拽上传 → 进度轮询 → 视频播放器，构建产物由 FastAPI 静态托管
- 验收：API 级 E2E 全通（提交→进度→下载 200）；3 名非技术人员实测待安排

### D8 Dify workflow 编排
- [ ] Dify Cloud：开始 → HTTP(asr) → LLM(翻译提示词) → HTTP(tts-align) → HTTP(render) → 结束
- [ ] 画布截图进 README
- 验收：Dify 画布上点运行，出成品
- 面试点：Dify 与自建服务的边界（重媒体处理为何不进 Dify：节点超时/大文件）

### D9 评测 + 成本核算 ✅ 主体完成（2026-09-06，数据见 docs/EVALUATION.md）
- [x] `scripts/make_eval_set.py` 5 条素材（纯口播/短句密集/低BGM/人脸出镜/慢语速）+ 标准文稿
- [x] `scripts/eval.py` E1 消融四档自动跑：**溢出率 38%~100% → 0%**；匹配率 100%；
      单条成本 ¥0.01~0.03；RT 系数 0.3~1.9
- [x] `easydub/eval_metrics.py` 指标纯函数（CER/字幕偏移/成本分项，35 测试绿）
- [ ] 口型偏移人工评分（E3，待 5090 空档批量跑）
- [x] CER 数字/标点归一化后再评：0.09~0.26 → 0.0~0.073（剩余为真实识别差异）

### D10 打磨 ✅ 完成（2026-09-06）
- [x] 字幕样式 force_style（FontSize/Outline/Shadow/MarginV，可覆盖）
- [x] 错误处理：无声视频零段落出片、API 退避重试、断点续跑 kill 实测通过
- [x] 多语言抽查 ja/es（见 E4）
- 验收：成片抽帧人检通过（双语字幕/口型段/音轨电平）

### D11 包装 ✅ 完成（2026-09-06，除 Dify 截图外）
- [x] README：流水线图 + 快速开始/Web/口型/评测四板块（Dify 截图随 D8 另补）
- [x] 对比视频 artifacts/demo/compare_ted_zh.mp4 + demo.gif（scripts/make_demo_video.py 可再生）
- [x] GitHub Actions：.github/workflows/test.yml（pytest）
- [x] commit 历史按主题整理（12+ 条）

### D12 面试材料 ✅ 完成（2026-09-06）
- [x] docs/INTERVIEW.md：4 条简历 bullet（动词+方案+量化数字）+ 7 个追问预案
- [x] 数据底座：docs/EVALUATION.md（E1/E2/E4 + 成本）
- [ ] `/project-guide`、`/great-resume` 按需另行生成（外部技能）

### 第二轮自主优化 ✅ 完成（2026-09-06 下午，de50a02 / 1b94704 / 7b1ebd2）

用户授权"自己布置任务自己推进优化"，三项全部量化验收（详见 EDPLAN §8、EVALUATION.md）：

- [x] **BGM 保留 + 配音自动闪避**（sidechaincompress，`--no-bgm` 可关）：
      Nike 句间 -91dB（死寂）→ -29dB（音乐回归），配音电平不受影响
- [x] **spill 溢出重分类 + 实测语速校准重译预算 + 重译止损**：
      Nike 重跑 2 轮 LLM → 0 调用；es 全绿（溢出 1→0）
- [x] **TTS/重译并发**：13 段 edge-tts 串行 20.0s → 4.7s（4.3×）

### 第三轮自主优化 ✅ 完成（2026-09-06 傍晚，bbfc582 / a2e3277）

- [x] **ASR 起点前移补偿**（云 ASR 系统性偏晚 ~0.15s）：字幕偏移 mean
      0.156/0.167 → **0.036/0.047**（≤100ms 目标线达成）；连带修复翻译缓存
      整对象返回导致时间轴修正被回滚的 bug、TTS 瞬断/截断缓存健壮性
- [x] **配音音色自适应**：f0 检测自动选男女声（Nike 男声 111Hz、TED 女声
      216Hz 实测正确），音色并入 TTS 缓存键（修复换 --voice 错拿旧音频）

### D13–14 缓冲
欠账、补测、模拟面试。
