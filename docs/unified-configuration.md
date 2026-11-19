# 海报项目快速上手（Windows）

第一次先装好 Git 和 `uv`。装完如果命令找不到，关掉 PowerShell 再打开。

## 队友从克隆开始

### 1. 下载项目，进入文件夹

在资源管理器里打开你想存放项目的位置，点地址栏，输入 `powershell`，按回车。在弹出的窗口粘贴：

```powershell
git clone --branch main https://github.com/Greensheep04/Creative-Poster-Framework-ARIN-5201.git
cd Creative-Poster-Framework-ARIN-5201
explorer .
```

最后一行会打开项目文件夹。看到 `README.md` 和 `config` 文件夹就对了。PowerShell 窗口先别关。

### 2. 放入队友给你的配置

把收到的 `project.local.toml` 复制进项目的 `config` 文件夹。同名文件已存在时，先备份旧文件，再替换。最终位置应为 `config/project.local.toml`，注意不要变成 `project.local.toml.txt`。

没收到配置、只想先试跑？把 `config/project.live-sdxl.example.toml` 复制一份，改名为 `project.local.toml`。**转发配置前先清空其中的 `api_key`；不要把含密钥的配置发群里或提交到 Git。**

### 3. 安装并运行

回到刚才的 PowerShell，逐行运行：

```powershell
uv sync --locked --python 3.11
uv run --no-sync python scripts/fetch-visual-fonts.py
uv run --no-sync python -m examples.team_workflow --config config/project.local.toml --offline --keywords "深海" "鲸鱼" "孤独"
```

前两行通常只需跑一次。以后改最后一行的三个关键词即可。运行结束后，打开项目下的 `runs` 文件夹，在最新的运行目录里找 `poster.png`，也可以看终端打印的 `poster` 路径。离线会跑完 A1–A4，不用密钥或 GPU；A4 是测试审核，出现 `needs_review` 属于预期。

### 想跑真实生图？

用队友发来的 SDXL 配置时，先准备自己的 Hugging Face 和 MiniMax 密钥，分别填进 `agents.a1.api_key` 和 `agents.a4.api_key`。本地生图还需要 NVIDIA CUDA 显卡、GPU 依赖与 SDXL 权重；按 [GPU 安装说明](backend-setup.md) 准备后，把你电脑上的权重目录填到 `agents.a3.backend.model_dir`。复制配置文件不会复制权重，没有 NVIDIA 显卡可按后端指南使用云端 A3。

先跑第一行检查配置，通过后再跑第二行：

```powershell
uv run --no-sync python -m examples.team_workflow --config config/project.local.toml --live --check
uv run --no-sync python -m examples.team_workflow --config config/project.local.toml --live --keywords "深海" "鲸鱼" "孤独"
```
