# EasyDub 自主优化迭代记录（第 6–10 轮）

> 规则（用户约定）：每轮先写 plan（任务 + 验收标准）再动手；做完更新
> 状态与结果并提交。每轮 1-3 个主题提交。第 1–5 轮的记录见 EDPLAN.md §8
> 与各轮 docs 提交信息。

## 计划总览（先记录后执行）

| 轮 | 主题 | 任务 | 验收标准 | 状态 |
|----|------|------|----------|------|
| 6 | 可观测性 + 数据 + 产品接线 + 架构文档 | R11 分阶段计时进报告；R12 whisper small 档 E5 补充；R13 Web 结果页挂质量报告；R14 ARCHITECTURE.md 现代化 | report.stage_timings 落盘；冷启动分阶段实测数据；small 档 CER 表；/api/jobs/{id}/report 200；架构文档与新特性一致 | ✅ 6e01b23 |
| 7 | LLM 选型对比（E6） | eval.py 支持 LLM 覆盖（base_url/model/结果键）；deepseek vs OpenRouter 免费档跑 c1/c2 T4；E6 表入档 | E6 表有 ≥2 模型 × 2 素材的溢出/重译收敛/匹配率数据；评测键不串模型 | ✅ feat 提交 |
| 8 | 演示材料重制 | make_demo_video.py 反映新特性（BGM 闪避前后对比）；demo.gif 重制；README 特性列表与截刷新 | 对比视频含"有无 BGM"两版；README 无过时描述 | ⬜ |
| 9 | Web 健壮性收口 | 上传大小上限（413）；DELETE /api/jobs/{id}（任务删除）；前端历史删除按钮；API 测试 | 超限上传 413；删除后历史与详情 404；测试绿 | ⬜ |
| 10 | 终版收尾 | 全量回归；评测矩阵终版一致性审视；INTERVIEW.md 增补 6-10 轮素材；迭代总结 | pytest 全绿；results.json 无口径混行；面试材料含量化新数字；本文件闭环 | ⬜ |

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
- 74 测试绿。

