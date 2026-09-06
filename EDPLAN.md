# EDPLAN — M0–M3 执行方案（2026-09-06 定稿）

> 本文是执行期的**上下文锚点与约束清单**：后续任何会话先读本文再动手，
> 防止跳出新约束、遗忘已定决策。完成一项就在 §5 状态表打勾并提交一次 git。

---

## 0. 目标范围

只做 GOAL.md 的 **M0 → M3** 四个里程碑：

- **M0** 仓库规整：.gitignore 修整 + 按主题提交（当前 git log 为空，最高优先级）
- **M1** 核心流水线：✅ 已完成（2026-09-04 出片，2026-09-06 Nike/TED 实验迭代，18 测试绿）
- **M2** lip-sync 打通：**改为全部在本机跑**（见 §1 环境结论），一条命令出 15s 口型样片
- **M3** 产品面：FastAPI 三端点 + SQLite 任务表 + React 拖拽上传页，浏览器内出片

M4（Agent/MCP）、M5（评测）、M6（包装）**不在本轮范围**，勿顺手做。

## 1. 环境结论（与 GOAL §4.4 的差异，以此为准）

| 项 | 实际情况 |
|----|----------|
| 硬件 | 这台 WSL2 机器**自带 RTX 5090 32GB**（nvidia-smi 直通正常）——原 GOAL 的"Mac 开发机 + Windows 5090 服务器"实为同一台机器，lip-sync 全部本地跑，不再需要跨机部署 |
| 主环境 | `easydub/.venv`（uv，Python 3.12）——流水线/测试/CLI 继续用它，不迁移 |
| lip-sync 环境 | conda 环境 `latentsync`：**clone 自 `vllm-cuda129`**（torch 2.10.0+cu128，CUDA 可用，含 cv2 4.13）——克隆是本地复制不下载；再补装 LatentSync 的非 torch 依赖。5090 是 Blackwell(sm_120)，必须 cu128+ 的 torch，**禁止按 LatentSync 官方 requirements 降级 torch** |
| Node | v24.14.1 + npm 11 ✅（前端用） |
| 网络 | HuggingFace 走 `HF_ENDPOINT=https://hf-mirror.com` 镜像；大文件下载一律后台执行 |
| 磁盘/内存 | 384G 空闲 / 76G RAM，LatentSync 仓库 + 权重（约 5–8G）放 `~/LatentSync`（repo 之外，不入 git） |

素材说明：GOAL 假设输入是中文口播，实际手头素材是英文视频（Nike / TED）。
不影响链路验证；lipsync 用 TED（Amanda Montell 正脸讲话）验证，正片演示用其 15–30s 切段。

## 2. 关键技术决策（已定，不再讨论）

1. **LatentSync 本地部署**：仓库 clone 到 `~/LatentSync`；权重用 HF 镜像下载（ByteDance/LatentSync-1.5）；手动跑通一次官方 `inference.py` 后，把可用命令通过环境变量注入 `server/lipsync_server.py`（`LATENTSYNC_DIR` / `LATENTSYNC_CMD`），服务器进程用 `latentsync` conda 环境的 python 起，监听 `:8001`。**HTTP 契约不变**（POST /lipsync → GET /jobs/{id} → GET /jobs/{id}/result），easydub 客户端 `LatentSyncLipSync` 已实现该契约。
2. **人脸分段**：新增 `easydub/services/facedetect.py`，OpenCV Haar 级联（cv2 自带，零新依赖思路：easydub 主环境 pip 装 `opencv-python-headless`，约 40MB，可接受）。采样抽帧判人脸 → 聚类成人脸段（过滤 <1s 碎段）。
3. **lip-sync 接入流水线**（替换 pipeline 第 6 步的 NotImplementedError）：
   人脸段 → ffmpeg 切子视频 + 从 `dub_track` 切对应音频 → 逐段调 lipsync 服务
   → 子视频按时间轴 concat（重编码）→ 最终 mux 时**音频仍用我们的 dub_track**（口型只吃画面结果，不吃它的音轨），避免音频二压。
4. **M3 架构**：`server/app.py`（FastAPI + sqlite3 标准库）三端点 `/api/jobs`、`/api/jobs/{id}`、`/api/jobs/{id}/result`；任务表字段（id、状态、stage、lang、产物路径、错误、时间戳）；pipeline 后台线程跑。前端 `web/`（Vite + React，无 UI 库），构建后由 FastAPI 静态托管 `web/dist`。**同一套端点未来直接给 Dify 用（D8）**。
5. **并发 = 1**（GOAL §4.3）：Web 端一次只跑一个任务，不做队列。
6. 顺手修的规格偏差（不扩 scope）：烧录字幕时同时封 mov_text 软字幕轨；成品音轨 `-ar 48000`；无声视频零段落正常出片不再 raise；httpx 加两次退避重试。**除此之外不动 M1 逻辑。**

## 3. 明确不做（出界，防跑偏）

- 不做说话人分离、BGM 保留混音、实时流式、4K、字幕擦除（GOAL §4.2 原样有效）
- 不做 minimax key 注册（适配器保留即可）、不做 sync.so 实验（E3 是 M5 阶段的事）
- 不做 Dify 画布（D8，等 M3 端点稳定后另起）
- 不做论文/简历素材整理（M6）
- 不迁移主环境到 conda；不升级/降级 torch

## 4. 执行步骤与验收

| 步骤 | 内容 | 验收 |
|------|------|------|
| S1 M0 | .gitignore 补全（媒体/`.DS_Store`/`web/node_modules`/`web/dist`）；按主题提交：docs → 核心流水线+测试 → 配置+脚本 → server 壳；EDPLAN.md 入库 | `git log --oneline` ≥ 4 条主题提交；`git status` 干净（媒体文件被忽略） |
| S2 M2-环境 | clone LatentSync；conda clone 出 `latentsync` 环境；装非 torch 依赖；HF 镜像下权重（后台） | `python -c import` 关键包 OK；`checkpoints/` 就位 |
| S3 M2-推理 | 手动跑通一次 `inference.py`（15s TED 切段 + dub 音频） | 产出无报错的口型 mp4 |
| S4 M2-服务 | `server/lipsync_server.py` 环境变量化；`:8001` 起服务 | `/health` 返回 GPU 名称 |
| S5 M2-接入 | `facedetect.py` + 单测；pipeline 第 6 步真实现；`--lipsync latentsync` | `translate TED切段 --lang zh --lipsync latentsync` 一条命令出口型成品；抽帧人检口型 |
| S6 M3-后端 | `server/app.py` 三端点 + SQLite + 后台线程；pytest 补 API 测试 | curl 全流程：提交→进度→下载成品 |
| S7 M3-前端 | Vite+React 拖拽页；build 后 FastAPI 托管 | 浏览器拖 TED/样例 → 进度 → 播放成品 |
| S8 收尾 | 端到端复跑全绿；GOAL/ROADMAP 状态更新；按主题提交 | `git status` 干净；pytest 全绿 |

## 5. 状态表（动手即更新）

- [ ] S1 M0 仓库规整
- [ ] S2 M2 环境就绪（LatentSync + conda env + 权重）
- [ ] S3 M2 手动推理跑通
- [ ] S4 M2 服务 :8001 起
- [ ] S5 M2 接入流水线 + TED 口型样片
- [ ] S6 M3 后端三端点
- [ ] S7 M3 前端页
- [ ] S8 收尾提交

## 6. 备忘（踩坑记录，持续追加）

- `.venv` 原为 macOS 拷贝，已用 uv 重建（py3.12）；旧环境备份在 `.venv.mac.bak`（确认无用后可删）。
- 本机 ffmpeg 带 libass（字幕会烧录），`subtitles` 滤镜路径必须走 `_subtitles_arg` 转义。
- edge-tts 音频必须过 `trim_silence` 再测时长，否则对齐全毁（Nike 实验：溢出 8→1）。
- 切换目标语言依赖 per-lang 产物隔离（`segments_translated.<lang>.json` / `tts_<lang>/`），勿改回共享文件名。
