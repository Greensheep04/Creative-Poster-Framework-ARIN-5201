# Agent 3 安装与后端配置

A3 的共同生产链路是：校验 PosterSpec/CreativeBrief → 按素材指纹决定生成或复用 → 按 Spec 渲染正式文字和版式 → 保存 PosterCandidate/证据 → 接受已验证的局部返工授权。生成后端只是其中一种工具。A1/A2 的实现、提示词、registration 和格式没有修改。

## 选择后端

| 后端 kind | 需要安装 | 用户配置 | 首版范围 |
|---|---|---|---|
| `sdxl` | 基础环境 + local-gpu | 固定 SDXL 权重目录 | NVIDIA CUDA；本地生成，支持 seed 和独立负面提示 |
| `qwen_image` | 基础环境 + local-gpu | 原版 Qwen-Image 固定权重目录 | NVIDIA CUDA；float16/bfloat16，支持 true_cfg_scale、seed 和负面提示 |
| `cloud_api` | 基础环境 | provider、model、完整 endpoint、Key 环境变量名、支持尺寸 | Images JSON 同步生成协议；不需要 Torch、GPU 或模型权重 |

三个后端均由用户明确选择，没有自动回退或后台下载。离线 fixture 仅用于工程测试。`factory` 保留给应用自己的 SDK 适配器；异步任务、签名鉴权或不同请求格式的云服务应实现 ImageGenerator 工厂，不能只改一个 URL。

## 从 GitHub 安装基础环境

进入克隆的仓库后运行以下命令。Python 必须为 3.11。示例 Windows 目录遵循本项目的 D 盘约定；其他电脑可自行改变本地目录。

```powershell
Set-Location 'D:\Projects\Creative-Poster-Framework-ARIN-5201'
$env:CONDA_PKGS_DIRS='D:\Dev\conda-pkgs'
& 'D:\Dev\miniconda3\Scripts\conda.exe' create --prefix 'D:\Dev\conda-envs\creative-poster' python=3.11 pip -y
$taskPython='D:\Dev\conda-envs\creative-poster\python.exe'
$env:UV_CACHE_DIR='D:\Dev\uv-cache'
$env:UV_PYTHON_DOWNLOADS='never'
New-Item -ItemType Directory -Path .scratch -Force | Out-Null
uv export --python $taskPython --locked --no-dev --no-emit-project --output-file .scratch/base-dependencies.txt
uv pip install --python $taskPython -r .scratch/base-dependencies.txt --editable .
& $taskPython scripts/fetch-visual-fonts.py
```

已存在的独立环境可跳过 create；不要安装进 Conda base。uv 需在 PATH 中；这里显式指定 Conda Python，不运行会另建 `.venv` 的 uv sync。需要测试时把 `--no-dev` 换成 `--all-groups` 后重新 export/install。现有 `scripts/setup-visual.ps1` 提供基础环境、字体和测试的一键流程，参数可指定环境路径。

Linux/macOS 使用同一锁文件和基础依赖；云端无需 CUDA。对应的终端命令：

```bash
conda create -n creative-poster python=3.11 pip -y
conda activate creative-poster
mkdir -p .scratch
uv export --python "$CONDA_PREFIX/bin/python" --locked --no-dev --no-emit-project --output-file .scratch/base-dependencies.txt
uv pip install --python "$CONDA_PREFIX/bin/python" -r .scratch/base-dependencies.txt --editable .
python scripts/fetch-visual-fonts.py
```

本轮实测平台是 Windows。Linux/macOS 安装命令未在真实设备验证；本地 GPU 仅支持 CUDA，不宣称 Apple MPS 或 CPU 推理可用。权重和字体下载需要网络；字体脚本核对仓库锁定的 SHA256，失败即停止。字体许可随清单保存。

## 配置引导

以下命令中的 `python` 指项目环境解释器；Windows 可用 `& $taskPython` 替换。

```bash
python -m poster_visual_producer setup --output .scratch/backend.json
```

交互选择后端，或显式指定 `--backend sdxl`、`--backend qwen_image`、`--backend cloud_api`。模板不包含凭据、机器路径或自动生图。已有文件需显式 `--overwrite` 才能覆盖。`examples/visual/backend-qwen.example.json`、`backend-cloud.example.json` 和 `backend-sdxl.json` 可作为格式参考。

```bash
python -m poster_visual_producer doctor --backend-config .scratch/backend.json --fonts examples/visual/fonts.json --font-base . --output-root runs/my-poster
```

doctor 的 JSON 包含 ready/checks/code/message/remediation，未就绪退出码为 1。它检查配置、Key 是否存在、字体与字符覆盖、目录可写性，以及本地 CUDA/依赖/权重完整性；不加载大型 pipeline、不下载或收费生图。云端 `REMOTE_NOT_PROBED` 表示只完成本地检查，不能证明 Key 有效、模型可用或服务协议兼容；需要用户按服务文档完成实际调用。工厂配置由应用自行诊断。

## SDXL / Qwen-Image 本地安装

只在选择本地模型后安装 local-gpu（版本已锁定，不需要升级锁文件）：

```powershell
uv export --python $taskPython --locked --extra local-gpu --no-dev --no-emit-project --emit-index-url --output-file .scratch/gpu-dependencies.txt
uv pip install --python $taskPython --index-strategy unsafe-best-match -r .scratch/gpu-dependencies.txt --editable .
& $taskPython -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

Windows 锁定 Torch 2.8.0 CUDA 12.8 wheel；驱动和硬件必须支持所选安装包。Linux 依赖按锁文件的平台分支解析，需自行核实 CUDA。离线镜像/缓存办法见 [现有 GPU 指南](local-gpu.md)。Qwen 不需要执行会下载 SDXL 的 setup-local-gpu 默认流程。

SDXL 权重：

```powershell
$env:POSTER_SDXL_MODEL_DIR='D:\Models\sdxl-base-1.0'
& $taskPython scripts/fetch-sdxl.py --model-dir $env:POSTER_SDXL_MODEL_DIR
& $taskPython -m poster_visual_producer setup --backend sdxl --output .scratch/sdxl.json
```

Qwen-Image 权重：

```powershell
$env:POSTER_QWEN_MODEL_DIR='D:\Models\Qwen-Image'
& $taskPython scripts/fetch-qwen-image.py --model-dir $env:POSTER_QWEN_MODEL_DIR
& $taskPython -m poster_visual_producer setup --backend qwen_image --output .scratch/qwen.json
```

Qwen 首版固定 [官方原版提交](https://huggingface.co/Qwen/Qwen-Image/commit/75e0b4be04f60ec59a75f475837eced720f823b6)，不是 Edit、量化或其他加速 checkpoint。模型体积较大，应先查看官方仓库和硬件需求，预留磁盘、系统内存与显存；cpu_offload 会降低常驻显存需求，但仍可能 OOM。bfloat16 需要支持该 dtype 的 CUDA 硬件，不支持时明确选择 float16，不会自动替换精度或模型。

脚本使用固定 revision 的 Hub 文件列表、恢复下载、组件完整性及内容哈希核验，全部成功才发布 `poster-model-manifest.json`。中断后可重跑同一命令；不要手工伪造 manifest。生成阶段使用 `local_files_only=True`，只核对本地文件。目录通过环境变量注入，含空格的路径需按 shell 规则引用。不同电脑可设置不同模型目录，通用代码不含 D 盘路径。

## 云端 Images JSON：合并后的队友操作步骤

更新：2026-11-18。适用于已合并 A3 分支、希望在自己电脑调用云端生图的成员。云端生成素材，A3 在本地按 A2 的 PosterSpec 排版并绘制正式文字，输出相同的 PosterCandidate 和审核证据；A4 接口不因生图后端改变。

**本路径只需基础 Python 环境、项目字体和自己的生图服务账号/API Key，不需要 NVIDIA GPU、CUDA、Torch、SDXL 或 Qwen-Image 权重。** 不运行 `scripts/setup-local-gpu.ps1`、`scripts/fetch-sdxl.py` 或 `scripts/fetch-qwen-image.py`，不安装 `local-gpu` extra。

**百炼免费额度路径已真实验证：**需要具体平台操作时，按 [百炼免费额度快速开始](bailian-cloud-quickstart.md) 开通自己的账号、启用用完即停、创建项目 Key，再复制 [百炼模板](../examples/visual/backend-bailian.example.json)。2026-11-18 已用 `qwen-image-2.1-turbo` 实际生成一张海报；以下通用流程仍适用于其他兼容服务。

### 1. 确认服务协议

内置 `cloud_api` 适配的是同步 Images JSON 协议。向服务商确认以下条件后再填配置：

- 使用 HTTPS 完整 `/images/generations` 地址，通过 `Authorization: Bearer <Key>` 鉴权。
- 接受 JSON 请求中的 `model`、`prompt`、`n=1` 和 `size`（例如 `1024x1024`）。
- 同步返回一个图片条目：`data[0].b64_json` 或可直接下载的 `data[0].url`；图片下载请求不附带 API Key。

模板里的 URL 只是协议示例，不代表已选定服务或模型。如果服务是“提交任务 → 返回 task_id → 轮询结果”，或需要签名、其他请求/响应格式，需通过 `factory` 实现自定义 ImageGenerator 适配器，不能仅替换 URL。生图 API 配置与 A1/A4 的模型配置分别提供。

### 2. 安装基础环境和字体（Windows / PowerShell）

先拉取包含 A3 的合并代码，并进入项目根目录。以下示例使用本项目 D 盘约定；队友可按自己的安装位置修改路径。需要已安装 Conda 和 PATH 中可用的 uv；安装和字体下载需要网络。

```powershell
Set-Location 'D:\Projects\Creative-Poster-Framework-ARIN-5201'
$env:CONDA_PKGS_DIRS='D:\Dev\conda-pkgs'
$taskPython='D:\Dev\conda-envs\creative-poster\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & 'D:\Dev\miniconda3\Scripts\conda.exe' create --prefix 'D:\Dev\conda-envs\creative-poster' python=3.11 pip -y
    if ($LASTEXITCODE -ne 0) { throw 'Conda environment creation failed' }
}
$env:UV_CACHE_DIR='D:\Dev\uv-cache'
$env:UV_PYTHON_DOWNLOADS='never'
New-Item -ItemType Directory -Path .scratch -Force | Out-Null
uv export --python $taskPython --locked --no-dev --no-emit-project --output-file .scratch/base-dependencies.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency export failed' }
uv pip install --python $taskPython -r .scratch/base-dependencies.txt --editable .
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $taskPython scripts/fetch-visual-fonts.py
if ($LASTEXITCODE -ne 0) { throw 'Font setup failed' }
```

已配置好的项目环境可跳过安装，但仍需字体。Linux/macOS 的基础安装命令见本文“从 GitHub 安装基础环境”；随后使用项目环境中的 `python` 替换下文 `& $taskPython`，Key 设置改为 `export POSTER_IMAGE_API_KEY='自己的Key'`。本项目实测平台为 Windows，其他平台尚未实机验收。

### 3. 创建并填写云端配置

```powershell
& $taskPython -m poster_visual_producer setup --backend cloud_api --output .scratch/cloud.json
```

编辑 `.scratch/cloud.json`，将内容改为下例，并替换所有服务占位值。已有配置不要反复运行 setup；如确需重建，显式加 `--overwrite`。

```json
{
  "kind": "cloud_api",
  "provider": "YOUR_PROVIDER_NAME",
  "model": "YOUR_IMAGE_MODEL_ID",
  "endpoint": "https://YOUR_PROVIDER_HOST/v1/images/generations",
  "api_key_env": "POSTER_IMAGE_API_KEY",
  "sizes": [[1024, 1024]],
  "timeout_seconds": 120,
  "parameters": {}
}
```

| 字段 | 填写方式 |
|---|---|
| `provider` | 自定义服务来源标签，用于记录来源 |
| `model` | 服务商账号实际可调用的生图模型 ID，不是聊天模型名 |
| `endpoint` | 服务商文档提供的完整生图 URL，必须以 `/images/generations` 结尾，不带 query |
| `api_key_env` | 保存 Key 的环境变量名；此处填写变量名，不能填写 Key 本身 |
| `sizes` | 该模型实际支持的宽高列表；`[[1024,1024]]` 仅为格式示例，须按服务能力修改 |
| `parameters` | 默认 `{}`；仅按服务需要填写 `quality`、`output_format`、`response_format`、`style`，值为字符串 |

云端请求不发送 seed 或独立 negative_prompt；A3 按后端能力编译提示词。云端生成尺寸与最终海报尺寸不同：A3 会按 Spec 适配素材，最终仍输出 720×1280 海报。切换服务时应核对尺寸和可选参数，不沿用 SDXL 参数。

在运行程序的同一个 PowerShell 窗口设置 Key：

```powershell
$env:POSTER_IMAGE_API_KEY='替换为自己的API Key'
```

此变量只在当前进程及子进程有效，新终端需重新设置。不要把真实 Key 写进 JSON、源码或提交到 Git；`.scratch/` 已被忽略。A3 不把 Key 写入素材指纹、候选证据和日志。

### 4. 运行配置检查（不生图、不调用远端）

```powershell
& $taskPython -m poster_visual_producer doctor --backend-config .scratch/cloud.json --fonts examples/visual/fonts.json --font-base . --output-root runs/cloud-smoke
```

预期退出码为 0，JSON 中 `ready=true`，并包含 `SCHEMA_VALID`、`CREDENTIAL_PRESENT`、`FONTS_READY`、`OUTPUT_WRITABLE`。`REMOTE_NOT_PROBED` 是正常提示，表示只完成本地检查；**不能据此判断 Key 有效、模型可用或服务协议兼容**。占位域名/模型也可能通过本地检查，必须在下一步前填入真实资料。

### 5. 先跑一张真实云端样例

下一条命令会向所选服务提交生图请求，消耗服务额度；`doctor` 和 `setup` 不会。首次只运行一个案例：

```powershell
& $taskPython -m poster_visual_producer.cases --case examples/visual/cases/ocean.json --backend-config .scratch/cloud.json --root runs/cloud-smoke --fonts examples/visual/fonts.json --font-base .
```

成功时查看：

```text
runs/cloud-smoke/ocean/poster.png             # 最终 720×1280 海报
runs/cloud-smoke/ocean/summary.json           # status=rendered, source_mode=provider
runs/cloud-smoke/ocean/candidate.json         # 候选与素材引用
runs/cloud-smoke/ocean/render_manifest.json   # 渲染证据
runs/cloud-smoke/ocean/a3-record.json         # A3 框架运行记录
```

该样例使用人工 Brief/Spec，验证“云端素材 → A3 渲染与证据输出”；不调用真实 A1 或 A4。`rendered` 表示生成完成，审核通过仍由 A4 决定。再次实验使用新的 `--root`，例如 `runs/cloud-smoke-02`，便于保留历史记录。

### 6. 接到正式 A2 输出或团队工作流

**从 A2 导出记录运行 A3：** 在同一项目目录、环境和 Key 设置下执行：

```powershell
$taskA2Record='替换为真实A2导出记录.json'
$taskArtifactRoot='替换为A2运行时使用的artifact根目录'
& $taskPython -m poster_visual_producer --upstream-record $taskA2Record --root $taskArtifactRoot --output-record runs/a3-cloud-output.json --fonts examples/visual/fonts.json --font-base . --backend-config .scratch/cloud.json
```

`--upstream-record` 必须是框架发布的 A2 运行记录，不能直接传裸 PosterSpec JSON；`--root` 必须与该记录使用的 artifact root 一致，保持上游文件可访问。输出记录中的 `poster_ref` / `candidate_ref` 是受框架管理的引用。A3 同时需要当前 CreativeBrief，版本、必需文案和来源校验仍会执行。

**在现有总流程中注册 A3：** 将集成入口创建生成器的部分改为读取该配置，其他角色沿用队友自己的注册对象：

```python
import json
from pathlib import Path
from poster_visual_producer.backends import create_generator
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.registration import registration

project_root = Path.cwd()  # 从项目根目录启动
config = json.loads((project_root / '.scratch/cloud.json').read_text(encoding='utf-8'))
generator = create_generator(config)
fonts_config = json.loads((project_root / 'examples/visual/fonts.json').read_text(encoding='utf-8'))
fonts = FontRegistry.from_config(fonts_config, project_root)
a3_registration = registration(generator, fonts)
# 将 a3_registration 放入现有 AgentSet 的 visual_producer 位置。
```

完整工作流注册示例见 [团队集成](a3-upstream-integration.md)，A4 审核包和返工见 [QC 交接](qc-handoff.md)。仅设置 `.scratch/cloud.json` 不会自动替换某个仍硬编码 SDXL 或 FixtureImageGenerator 的入口；集成方必须实际使用 `create_generator(config)` 的返回值。

### 常见问题与验收范围

| 现象 | 处理 |
|---|---|
| `MISSING_CREDENTIAL` | 在启动程序的同一终端设置与 `api_key_env` 同名的变量 |
| `INVALID_CONFIGURATION` | 检查必填字段、完整 endpoint、尺寸列表和可选参数；配置不可含 `api_key` 字段 |
| `MISSING_FONT` | 运行字体下载脚本，并从项目根目录使用 `--font-base .` |
| HTTP 401/403/404 | 核对服务 Key、账号权限、完整 endpoint 和模型 ID |
| HTTP 400/422 | 核对模型支持的尺寸及参数；返回任务 ID 的服务需自定义适配 |
| HTTP 429 | 核对额度/限流；程序仅在允许的预算内对可安全重试的拒绝做有限重试 |
| 超时、图片下载失败、响应无 `data` 图片 | 检查服务任务状态/账单和协议；提交状态不明时不会自动重提，避免重复收费 |
| 总流程仍尝试加载 SDXL | 确认实际入口已使用云端配置创建 generator，而不是原来的本地生成器 |

通用协议已通过本地测试，百炼 `qwen-image-2.1-turbo` 还完成了[单张真实生图验证](bailian-cloud-quickstart.md#2026-11-18-真实验证结果)。其他服务和队友自己的账号仍需以第 5 步实际结果为接入依据；本次未运行真实 A4 审核。

## 使用 A2 原输出运行 A3

保留旧入口，不要求 A2 新增字段或方法：

```bash
python -m poster_visual_producer --upstream-record PATH_TO_A2_RECORD.json --root SAME_ARTIFACT_ROOT --output-record runs/a3-output.json --fonts examples/visual/fonts.json --font-base . --backend-config .scratch/backend.json
```

root 必须与上游 artifact root 一致，不能把任意 JSON 当成已发布的文件引用。也可用人工开发案例确认后端：

```bash
python -m poster_visual_producer.cases --case examples/visual/cases/ocean.json --backend-config .scratch/backend.json --root runs/backend-smoke --fonts examples/visual/fonts.json --font-base .
```

案例是工程数据发布器，不代表真实 A1/A2 模型。输出 status=rendered 仅表示生产完成；是否通过必须交给 A4。新版交接与授权入口见 [QC 交接指南](qc-handoff.md)。在 Windows 上建议使用短的输出根目录，避免未启用长路径的机器触发 MAX_PATH 限制。
