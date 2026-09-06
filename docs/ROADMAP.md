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

### D3 溢出重译闭环 ✅ 主循环已完成（2026-09-05）
- [x] `find_overflow` 纯函数 + 单测（align.py）→ 重译提示词（translator.py `retranslate`）→ 接入 pipeline 4.5 环节：超时段按字符预算重译、重配音、复测；报告新增 `overflow_before_retry`
- [x] 实测：样例段 49 字符/3.21s(overflow) → 重译 39 字符/2.85s(atempo)，溢出 1→0
- [ ] 术语表：品牌名/slogan 跨段译法一致（提示词注入 glossary）
- [ ] 已知打磨点（D11）：模型不严格遵守字符预算（要 32 给了 39）；atempo 1.23 接近 1.25 上限，可加"仍溢出则二次收紧重译"的循环
- 面试点：翻译质量的量化指标是"时长匹配率"；字符预算只是时长的代理指标，最终以 TTS 实测时长为准

### D4 真实素材 + MVP 存档
- [ ] 自制 3 条素材（口播/带 BGM/短句密集，make_sample.py 扩展，**不用真实品牌广告**）
- [ ] 全链路跑通，录一版"字幕+配音"对比 demo 存档（无 lip-sync 也成立的安全垫）

### D5 lip-sync 技术验证（自建 GPU 服务器方案，2026-09-05 更新）
硬件：用户自有 Windows 服务器（RTX 5090 / 9950X）——不租卡，sync.so 降为可选对比。
- [ ] 按 `server/README_WINDOWS.md` 在 Windows 机装 Python + cu128 版 torch + LatentSync，**手动跑通一次推理**
- [ ] 把跑通的命令填进 `server/lipsync_server.py` 的 CMD_TEMPLATE，起服务（0.0.0.0:8001，防火墙放行）
- [ ] Mac 侧 `.env` 配 `LIPSYNC_SERVER_URL`，跑 `python -m easydub lipsync-test 人脸视频.mp4 音频.wav out.mp4`
- [ ] （可选对比）sync.so 免费额度跑同素材，记录效果/耗时/价格
- 验收：Mac 上一条命令拿到口型正确的 15s 样片
- 面试点：自建 GPU 推理服务（HTTP 契约、任务轮询）vs 商用 API 的取舍；自有 5090 长期零边际成本

### D6 lip-sync 接入流水线
- [ ] 人脸检测分段（OpenCV），只对出镜段做口型对齐
- [ ] 分段结果按时间轴拼回原片，`--lipsync` 开关联通
- 验收：30s 真人口播成品，口型基本同步

### D7 FastAPI 三端点 + React 上传页（产品面）
- [ ] `/api/jobs`（提交）、`/api/jobs/{id}`（进度）、`/api/jobs/{id}/result`——Dify 和前端共用
- [ ] 简版 React 页：拖拽上传 → 进度 → 视频播放器（复用 essay_read 的前端经验）
- 验收：非技术人员不看文档能完成一次翻译

### D8 Dify workflow 编排
- [ ] Dify Cloud：开始 → HTTP(asr) → LLM(翻译提示词) → HTTP(tts-align) → HTTP(render) → 结束
- [ ] 画布截图进 README
- 验收：Dify 画布上点运行，出成品
- 面试点：Dify 与自建服务的边界（重媒体处理为何不进 Dify：节点超时/大文件）

### D9 评测 + 成本核算（简历数字的唯一来源）
- [ ] 5 条素材全链路，评测表：溢出占比、时长匹配率、口型偏移、人评分（优化前后各一次）
- [ ] 单条 30s 广告成本核算（ASR+LLM+TTS+lip-sync 分项）
- 验收：表格数据完整，能写出"33%→8%"这种句子

### D10 打磨：从 demo 到成品
- [ ] 字幕样式（位置/描边/双语排版）、错误处理（无声视频/API 失败重试）、多语言抽查（ja/es 各一条）
- 验收：挑不出一眼假

### D11 包装
- [ ] README：架构图 + demo GIF + Dify 截图 + 一键复现
- [ ] 1–2 分钟对比视频；GitHub Actions 跑 pytest
- [ ] commit 历史按主题整理

### D12 面试材料
- [ ] 简历 3–4 条 bullet（公式：动词 + 方案 + 量化结果）
- [ ] 追问预案：时长对齐 / 云端 ASR 断句 / max_tokens 隐性契约 / lip-sync 选型 / Dify 边界
- [ ] `/project-guide` 生成导学+面经，`/great-resume` 酥化简历

### D13–14 缓冲
欠账、补测、模拟面试。
