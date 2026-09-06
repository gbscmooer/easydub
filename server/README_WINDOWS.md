# Windows 服务器部署指南（RTX 5090 / 9950X）

目标：把你那台 Windows 服务器变成 easydub 的「口型对齐」推理服务器。
Mac 上的 easydub 把视频和外语音频发过去，服务器用开源的 LatentSync
生成口型对齐的视频发回来。

前提确认：Mac 和这台服务器要能互相访问（同一个局域网，或者都能上同一
VPN）。在服务器上用 `ipconfig` 查它的局域网 IP（形如 192.168.x.x），
后面会用到。

## 第 1 步：装基础软件

1. **Python 3.11**：去 python.org 下载安装，**勾选 "Add Python to PATH"**。
2. **Git**：git-scm.com 下载，一路下一步。
3. **FFmpeg**：管理员 PowerShell 执行 `winget install Gyan.FFmpeg`，装完重开终端。

## 第 2 步：装支持 5090 的 PyTorch

5090 是新架构（Blackwell），必须装 CUDA 12.8 版本的 PyTorch，普通
`pip install torch` 装出来的是老版本会不认卡。开 PowerShell：

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -c "import torch; print(torch.cuda.get_device_name(0))"
```

最后一条命令应该打印出 RTX 5090。报错就把报错发我。

## 第 3 步：装 LatentSync（字节的口型同步开源框架）

```powershell
cd C:\
git clone https://github.com/bytedance/LatentSync.git
cd LatentSync
pip install -r requirements.txt
```

下载模型检查点（国内网络先切 HuggingFace 镜像）：

```powershell
set HF_ENDPOINT=https://hf-mirror.com
pip install -U huggingface_hub
huggingface-cli download ByteDance/LatentSync-1.6 --local-dir checkpoints
```

> 提示：如果 LatentSync 的 requirements 和 torch 版本冲突（它可能把
> torch 降级），装完 requirements 后**重新执行一遍第 2 步**的命令把
> cu128 版 torch 装回来。

## 第 4 步：手动跑通一次推理（关键，别跳过）

先准备一段几秒钟的人脸说话视频（test.mp4）和一段音频（test.wav），
在 `C:\LatentSync` 目录下跑：

```powershell
python inference.py --inference_config configs/inference.yaml --video_path test.mp4 --audio_path test.wav --result_path out.mp4
```

> 具体参数以仓库 README 为准，不同版本参数名略有差异。跑成功会生成
> 口型对齐的视频。**能跑通的那条命令就是下一步要填的模板。**

## 第 5 步：配置并启动服务

用编辑器打开 `lipsync_server.py`，改顶部两行：

- `CWD`：改成你的 LatentSync 目录（如 `r"C:\LatentSync"`）
- `CMD_TEMPLATE`：改成第 4 步跑通的命令，把路径换成 `{video} {audio} {out}` 占位

然后：

```powershell
pip install fastapi uvicorn python-multipart
python lipsync_server.py
```

看到 `Uvicorn running on http://0.0.0.0:8001` 即成功。

## 第 6 步：放行防火墙 + Mac 连通测试

1. Windows 防火墙放行 8001 端口：设置 → 防火墙 → 高级设置 → 入站规则
   → 新建规则 → 端口 → TCP 8001 → 允许。
2. 在 Mac 上测试（把 192.168.x.x 换成服务器的 IP）：

```bash
curl http://192.168.x.x:8001/health
```

应该返回 `{"gpu": "RTX 5090", ...}`。

3. Mac 的 `~/easydub/.env` 加一行：

```
LIPSYNC_SERVER_URL=http://192.168.x.x:8001
```

## 第 7 步：端到端联调（D5 验收）

在 Mac 上，随便拿一段人脸出镜视频 + 一段音频：

```bash
cd ~/easydub
.venv/bin/python -m easydub lipsync-test 人脸视频.mp4 音频.wav out.mp4
```

播放 `out.mp4`：嘴型跟着音频动，D5 验收通过。

## 常见问题

- **`python` 不是内部或外部命令**：Python 没加 PATH，重装勾选 Add to PATH。
- **torch 报 CUDA 不可用 / 显卡不支持**：第 2 步的 cu128 没装上，或第 3 步
  依赖把 torch 降级了，重装 cu128 版。
- **模型下载很慢/失败**：确认 `HF_ENDPOINT=https://hf-mirror.com` 已生效。
- **Mac 访问不到 /health**：防火墙没放行，或两台机器不在同一网络。
- **卡在 running 很久**：正常，LatentSync 15 秒素材大约要 1~3 分钟。
