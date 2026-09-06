# EasyDub 自主优化迭代记录（第 6–10 轮）

> 规则（用户约定）：每轮先写 plan（任务 + 验收标准）再动手；做完更新
> 状态与结果并提交。每轮 1-3 个主题提交。第 1–5 轮的记录见 EDPLAN.md §8
> 与各轮 docs 提交信息。

## 计划总览（先记录后执行）

| 轮 | 主题 | 任务 | 验收标准 | 状态 |
|----|------|------|----------|------|
| 6 | 可观测性 + 数据 + 产品接线 + 架构文档 | R11 分阶段计时进报告；R12 whisper small 档 E5 补充；R13 Web 结果页挂质量报告；R14 ARCHITECTURE.md 现代化 | report.stage_timings 落盘；冷启动分阶段实测数据；small 档 CER 表；/api/jobs/{id}/report 200；架构文档与新特性一致 | ✅ 6e01b23 |
| 7 | LLM 选型对比（E6） | eval.py 支持 LLM 覆盖（base_url/model/结果键）；deepseek vs OpenRouter 免费档跑 c1/c2 T4；E6 表入档 | E6 表有 ≥2 模型 × 2 素材的溢出/重译收敛/匹配率数据；评测键不串模型 | ✅ feat 提交 |
| 8 | 演示材料重制 | make_demo_video.py 反映新特性（BGM 闪避前后对比）；demo.gif 重制；README 特性列表与截刷新 | 对比视频含"有无 BGM"两版；README 无过时描述 | ✅ feat 提交 |
| 9 | Web 健壮性收口 | 上传大小上限（413）；DELETE /api/jobs/{id}（任务删除）；前端历史删除按钮；API 测试 | 超限上传 413；删除后历史与详情 404；测试绿 | ✅ feat 提交 |
| 10 | 终版收尾 | 全量回归；评测矩阵终版一致性审视；INTERVIEW.md 增补 6-10 轮素材；迭代总结 | pytest 全绿；results.json 无口径混行；面试材料含量化新数字；本文件闭环 | ✅ 完成 |

---

## 第 6 轮（进行中）

### Plan（动手前记录）

- **R11 分阶段计时**：`run()` 每阶段计时 → `report["stage_timings"]`（asr/translate/tts_align/mix/lipsync；render 不含自身），HTML 报告 meta 区展示。
  验收：单测断言 HTML 含计时；冷启动 c1（全新 workdir）产出真实分阶段数据——现有文档墙钟全是缓存重放，需要一次诚实的冷启动数字。
- **R12 whisper small 档**：eval 结果键带模型档（`local:small`），c1/c2/c3/c5 补 small 行。
  验收：E5 表四档数据齐；base→small 的 CER/偏移/耗时对比可引用；异常现象如实记录。
- **R13 Web 挂报告**：`GET /api/jobs/{id}/report` 返回 `artifacts/<job_id>/report.<lang>.html`；前端完成页加"质量报告"链接。
  验收：API 测试（monkeypatch ROOT 造报告文件）+ 200/404 语义。
- **R14 ARCHITECTURE.md 现代化**：文档还停在 M0-M3（"D8 占位"、无 BGM/音色/spill/报告）。
  验收：架构图与分层说明反映现状（双向夹逼 + spill、音色自适应、BGM 闪避、HTML 报告、评测体系）。

### 结果（做完即填）

- R11 ✅ `stage_timings` 落盘（asr/translate/tts_align/mix/lipsync），HTML meta 展示；
  冷启动 c1 实测：asr 1.99s / translate 2.59s / tts_align 2.0s / mix 0.55s，
  **30s 素材全冷 7.1s（RT 0.24）**。顺带：c1 说话人 f0 193Hz → 女声（素材本就是
  女声合成，检测正确）。
- R12 ✅ whisper small：CER c1 0.268→0.220、c3 0.355→0.226（抗噪仍弱）、
  **c5 0.289→0.017**（显著）；但 c5 慢语速偏移退化到 0.443（全局常数补偿不适配
  慢起音，如实记录）；c1 首跑 45s 含模型下载。结论：默认云端/本地 base，small
  为离线精度档。
- R13 ✅ `GET /api/jobs/{id}/report`（200/404/409 语义）+ 前端完成页"质量报告"链接 + 测试。
- R14 ✅ ARCHITECTURE.md 重写：新流水线图（含 BGM 闪避/音色自适应/spill/报告层）、
  分层表、核心决策（缓存键三要素、口径演进）、目录结构。
- 提交：6e01b23（feat）+ 本文件。74 测试绿。

---

## 第 7 轮：LLM 选型对比（E6）

### Plan（动手前记录）

- eval.py 加 `--llm-base-url/--llm-model` 覆盖；run()/stage_translate 透传，
  base_url 含 "openrouter" 时自动用 OPENROUTER_API_KEY（供应商 key 跟着端点走）。
- 结果键扩展为 (素材, 语言, 档位, ASR, LLM)，旧行归一化补 `llm` 字段（默认
  deepseek-chat，与 .env 历史一致）。
- 跑 c1_pure + c2_dense × en × T4，deepseek-chat（现有数据）vs OpenRouter
  免费档 1-2 个模型。先单次探测模型可用性再进矩阵。
- 验收：E6 表入 EVALUATION.md——代理指标 = 溢出前溢出数（限长遵守度）、
  重译轮数（收敛成本）、匹配率；模型不可用/解析失败如实记录。

### 结果

- ✅ 三模型 × 2 素材全绿：首轮溢出 0、零重译、匹配率 100%——限长闭环把模型
  差异对同步的影响压平，选型差异只剩风格/成本/可用性。gemma-4-31b:free 上游
  持续 429 换 minimax-m2.7:free（如实记录）。
- 附带修复两个真实适配器缺口：推理模型 reasoning 吃光 4096 token 正文为 null
  （提额 8192 + reasoning 兜底）；429 纳入 post_with_retry 退避（LLM 调用
  tries=4/backoff=6s）。
- 74 测试绿。提交：9586e21。

---

## 第 8 轮：演示材料重制

### Plan（动手前记录）

- `scripts/make_demo_video.py` 现状审查：demo 是否反映新特性（BGM 闪避、
  音色自适应、HTML 报告）。
- 改造：demo 补"BGM 保留 + 闪避"维度——用真实素材（有音乐的视频）对比
  有/无闪避两版音轨的句间响度差，以字卡/波形呈现；重生成 compare 视频与
  demo.gif。
- README/特性清单通读核对：无过时描述。
- 验收：脚本一键重生成 demo 产物；README 无过时；媒体不入库，脚本即验收依据。

### 结果

- ✅ 新增 `demo_bgm_ab.mp4`（Nike 8-14s：BGM OFF → BGM ON 同画面 A/B），
  句间响度实测 OFF -91dB vs ON -27.7dB（**63dB 差值**，闪避效果可听）；
  compare_ted_zh.mp4 与 demo.gif 按新脚本一键重生成。
- README 通读无过时描述（Dify 仅出现在岗位方向与端点复用两处，均为有效信息）。
- 媒体产物不入库（.gitignore），脚本即验收依据。

## 第 9 轮：Web 健壮性收口

### Plan（动手前记录）

- 上传大小上限（默认 200MB，环境变量可调），超限返回 413；流式落盘改为
  边收边计数、超限即断（防大文件吃满磁盘）。
- `DELETE /api/jobs/{id}`：终态任务可删（DB 行 + 产物目录 + 上传文件），
  running 任务拒绝 409；前端历史行加删除按钮。
- API 测试：413 语义、删除后 GET 404、running 删除 409。
- 验收：测试绿 + 真实服务 curl 验证。

### 结果

- ✅ `EASYDUB_MAX_UPLOAD_MB`（默认 200）边收边计数超限 413；**修复信号灯
  泄漏隐患**：名额获取后任何异常路径（413/磁盘错误）都归还 `_busy`，否则
  服务会永久 409。
- ✅ DELETE 端点：终态任务删 DB 行+上传文件+产物目录；running/queued 409。
  真实服务 E2E：done → DELETE 200 → GET 404。前端历史行加删除按钮。
- 77 测试绿。

## 第 10 轮：终版收尾

### Plan（动手前记录）

- 全量 pytest 回归；评测矩阵 results.json 终审（无口径混行、键完整）。
- INTERVIEW.md 增补第 6-10 轮素材：分阶段计时（冷启动 RT 0.24）、E6 LLM 选型
  结论、推理模型适配、429 退避——更新 bullet 与追问预案。
- ITERATIONS.md 第 8-10 轮结果回填 + 迭代总结（六轮累计量化收益表）。
- 最终提交 + git status 干净核验。

### 结果（第 10 轮）

- ✅ 全量 77 测试绿；results.json 终审 49 行，五维键无重复、无口径混行，
  T4 偏移异常行 0（排除 small c5 已知项）。
- ✅ INTERVIEW.md 增补 bullet 7 与 Q10（六个工程判断：实测推翻静态参数、
  口径跟听感走、真实 E2E 不可省）。

## 迭代总结（第 1-10 轮累计量化收益）

| 维度 | 之前 | 之后 | 出处 |
|---|---|---|---|
| 音画同步（溢出率，听感口径） | 自然翻译 22%~100% | **0%（全矩阵真冲突≈0）** | E1 双口径 |
| 字幕偏移 | 0.156 / 0.167s | **0.036 / 0.047s（双通道 ≤0.04）** | R4/R6 |
| TTS 合成 13 段墙钟 | 20.0s 串行 | **4.7s（4.3×）** | R3 |
| 重跑 LLM 调用（超短槽位） | 每次白烧 2 轮 | **0 调用** | spill 改判 |
| 背景音乐 | 整条丢弃（句间 -91dB） | **保留+闪避（句间 -27.7dB）** | R1/R8 |
| 音色 | 固定男声 | **按说话人 f0 自适应**（111/216Hz 实测） | R5 |
| 选型数据 | 拍脑袋 | **E2 TTS / E5 ASR / E6 LLM 三线实证** | R6/R7 |
| 可观测性 | report.json | **+HTML 报告 + 分阶段计时（冷启动 RT 0.24）** | R9/R11 |
| 产品面 | 三端点 + 单任务页 | **开关/历史/删除/大小上限/报告链接** | R7/R9/R13 |
| 已知回归修复 | — | 翻译缓存回滚、进度回调卡死、信号灯泄漏、推理模型正文为 null 等 **6 个真实 bug** | 各轮 |

每一轮都有：先记录的 plan、可量化验收、真实素材/服务验证、独立提交。
遗留（需用户决策或真人）：D8 Dify 编排、M6 新机 30 分钟复现实测、
3 人网页可用性实测、git remote 推送激活 CI。

---

## 第 11 轮：标准验证片段（用户指定 TED 1:50–2:00）

### Plan（动手前记录）

- 用户指定以 TED 1:50–2:00（110–120s，**纯人物讲话**）为标准片段——这是
  口型对齐的理想素材（人脸连续出镜）。
- 步骤：① 切 10s 带音轨片段 `samples/ted_std10.mp4`（旧 cut_clip 产物是
  -an 无音轨的，标准片段必须带音轨供 ASR）；② 起本机 LatentSync 服务
  （conda `latentsync` 环境，:8001）；③ 全流水线 `--lang zh --lipsync
  latentsync`（延续既有 TED 演示的英→中方向）；④ 核验：段数/分阶段计时/
  音色自适应/人脸段口型拼回/成品时长/HTML 报告。
- 验收：一条命令出含口型的 10s 成品；report.zh.json 完整（keep_bgm、voice、
  stage_timings、summary 无溢出）；口型段成功拼回（或回退逻辑生效）；
  运行手册（EDPLAN §7）补标准片段入口。

### 结果（做完即填）

- ✅ **抓出并修复一个潜伏 bug**：`build_dub_track` 的静音底轨从未参与 amix，
  输出被最后一段配音截短（6.96s vs 10.06s）——长视频尾部整段没声、按 total
  切音频会拿到空文件（口型服务的 whisper 直接炸空张量）。合成素材从未暴露
  （最后一段恰好近片尾），标准片段首跑即抓出。
- ✅ **新增静态脸过滤**（facedetect）：TED 标准片段 0-3.3s 是远景+幻灯片，
  照片人脸被 Haar 检出且 ratio 高达 0.33（比真说话人还大）——用相邻采样帧
  脸框内像素差（motion）区分活人与照片，实测两者间隔 15 倍以上（照片 <0.1
  vs 说话人 24-58）。修复后照片脸段被拒，**两个真说话人段全部口型成功**
  （此前 1/2）。
- ✅ 标准片段一条命令全流程：104s 出 10.24s 成品（含口型 2 段拼回），
  report 完整（voice 自动女声 zh-CN-XiaoxiaoNeural、keep_bgm、0 溢出）；
  81 测试绿。
- 提交：本轮 feat + ITERATIONS/EDPLAN 更新。


## 第 11 轮补充：首次推送 GitHub + CI 修复

- 推送前审查：.gitignore 补全（*.mp3/m4a/gif/webm/onnx/db/log/IDE 文件等）；
  跟踪文件 63 个全部为代码/文档；密钥扫描干净（.env 未入库、.env.example 全空值）。
- 首次 CI 失败诊断链（匿名无日志权限，靠公开注解远程排障）：
  1. `ffmpeg -version | head -1` 管道吞掉退出码 → ffmpeg 缺失却显示"成功"；
  2. pytest 步骤 `2>/dev/null` 吞掉错误输出；
  3. `continue-on-error: true` 会中和后续 `if: failure()` 条件——要用
     `steps.<id>.outcome == 'failure'`。
- 修复：显式 `apt-get install ffmpeg` + `which` 检查 + pipefail + 日志落
  artifact + 失败详情转公开注解。**CI 最终 success**（fc8f1c7）。

---

## 第 12 轮：方向转型——克隆语音配音（用户指定）

### 方向变更（用户原话的工程转译）

> "效果不行……方向应该变为：根据原视频只输出**克隆语音+克隆语气+lip-sync
> 同步发音+模仿翻译**。最终目的就是输出相当于异种语言的**同音、同语气、同节奏**
> 合成音频（不需要处理 BGM 等背景），结果**不叠加 BGM，只输出模仿语音**，只
> 专注于这个。"

翻译成工程目标：**换掉"通用音色 TTS"这个短板**——用原说话人的参考音频做
零样本克隆（cross-lingual：英文参考 → 中文合成），音色/语气/节奏贴原片；
流水线其余能力（ASR 断句、限长翻译、时长对齐、口型）全部复用；BGM 闪避
降级为默认关闭，成品音轨=纯模仿人声。

### Plan（动手前记录）

- **T1 选型**：本机 5090 的开源零样本克隆 TTS。候选 CosyVoice2-0.5B
  （cross-lingual + instruct 语气控制 + 社区成熟）、F5-TTS、IndexTTS2。
  决策依据：跨语种克隆质量、安装成本（clone latentsync 环境复用 torch 2.10
  cu128，避免大下载）、速度参数（粗对齐）。预装权重走 modelscope/hf-mirror。
- **T2 参考音频自动化**：从原视频自动选"最干净的说话人段"做 reference
  （voice_match 的选段逻辑复用）+ 参考文本直接用 ASR 结果（零人工）。
- **T3 接入流水线**：`tts.py` 新增克隆 provider（synth 带 reference/instruct
  参数）；`run()` 加 voice_clone 开关；keep_bgm 默认改 False（输出纯模仿人声轨）。
- **T4 语气**：翻译提示词加"保留原说话人语气/风格"；合成时 instruct 模式
  可控语气。
- **T5 节奏对齐**：克隆合成后仍走现有 slot 对齐（speed 粗调 + atempo 精调 +
  溢出重译），验证指标不退化。
- **验收**：标准片段 ted_std10 一条命令出"Amanda 的声音说中文"的成品；
  时长对齐指标（溢出率/偏移）不退化；音色相似度人耳可辨（用户听）；全程
  无 BGM 叠加。

### 结果

- ✅ **T1 选型落地**：CosyVoice2-0.5B（跨语种零样本克隆 + instruct 语气 + speed）。
  环境 = clone latentsync（torch 2.10 cu128 复用）；踩坑五连：whisper 构建缺
  setuptools、pip 事务回滚、torch 被连带降级 2.3.1（sm_120 无法运行，从
  latentsync 目录级复制恢复）、onnxruntime 1.18 不兼容 numpy2（升 1.20.1）、
  torchaudio 2.10 load/save 迁到 torchcodec（soundfile 全局补丁 + 张量直通）。
  权重走 modelscope（5.3G）。
- ✅ **T2 参考自动化**：最长 ASR 段原声 8.64s + 其转写做参考（零人工）。
- ✅ **T3 接入**：`tts.py` 新增 CosyVoiceCloneTTS（音色指纹进缓存标签）、
  `server/cosyvoice_server.py` 常驻服务 :8002、`run(--voice-clone)` 一条命令；
  克隆模式强制不叠加 BGM。
- ✅ **T4 语气**：翻译提示词加"保留原句语气与节奏感"；服务端 instruct2
  模式支持语气指令（--instruct）。
- ✅ **T5 端到端验收**：标准片段 23.4s 出"Amanda 克隆音色说中文"成品，
  口型 2 段全部拼回；首轮溢出 1 段由重译闭环收敛（实测语速 3.58 cps 被校准
  反馈捕获），最终 fit 2 / 溢出 0——时长对齐指标无退化。
- 遗留（如实）：音色相似度需人耳终评；克隆语速 3.58cps 慢于 edge，短槽位
  重译更频繁（闭环已兜住）；instruct 语气控制效果未做 A/B。
