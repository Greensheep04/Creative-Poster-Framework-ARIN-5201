# Agent 3 安装与后端配置

A3 的共同生产链路是：校验 PosterSpec/CreativeBrief → 按素材指纹决定生成或复用 → 按 Spec 渲染正式文字和版式 → 保存 PosterCandidate/证据 → 接受已验证的局部返工授权。生成后端只是其中一种工具。A1/A2 的实现、提示词、registration 和格式没有修改。

## 选择后端

| 后端 kind | 需要安装 | 用户配置 | 首版范围 |
|---|---|---|---|
| `sdxl_local` | 基础环境 + local-gpu | 固定 SDXL 权重目录 | NVIDIA CUDA；本地生成，支持 seed 和独立负面提示 |
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

交互选择后端，或显式指定 `--backend sdxl_local`、`--backend qwen_image`、`--backend cloud_api`。模板不包含凭据、机器路径或自动生图。已有文件需显式 `--overwrite` 才能覆盖。`examples/visual/backend-qwen.example.json`、`backend-cloud.example.json` 和 `backend-sdxl.json` 可作为格式参考。

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
& $taskPython -m poster_visual_producer setup --backend sdxl_local --output .scratch/sdxl.json
```

Qwen-Image 权重：

```powershell
$env:POSTER_QWEN_MODEL_DIR='D:\Models\Qwen-Image'
& $taskPython scripts/fetch-qwen-image.py --model-dir $env:POSTER_QWEN_MODEL_DIR
& $taskPython -m poster_visual_producer setup --backend qwen_image --output .scratch/qwen.json
```

Qwen 首版固定 [官方原版提交](https://huggingface.co/Qwen/Qwen-Image/commit/75e0b4be04f60ec59a75f475837eced720f823b6)，不是 Edit、量化或其他加速 checkpoint。模型体积较大，应先查看官方仓库和硬件需求，预留磁盘、系统内存与显存；cpu_offload 会降低常驻显存需求，但仍可能 OOM。bfloat16 需要支持该 dtype 的 CUDA 硬件，不支持时明确选择 float16，不会自动替换精度或模型。

脚本使用固定 revision 的 Hub 文件列表、恢复下载、组件完整性及内容哈希核验，全部成功才发布 `poster-model-manifest.json`。中断后可重跑同一命令；不要手工伪造 manifest。生成阶段使用 `local_files_only=True`，只核对本地文件。目录通过环境变量注入，含空格的路径需按 shell 规则引用。不同电脑可设置不同模型目录，通用代码不含 D 盘路径。

## 云端 Images JSON

选择 cloud_api 后编辑模板中的 provider/model/endpoint/sizes。endpoint 是完整 `/images/generations` 地址；provider 是来源标签，model 必须是所选服务真实模型名。示例 URL 只是协议模板，不代表默认选择该服务。配置不可写入 api_key；api_key_env 是环境变量名称。

```powershell
$env:POSTER_IMAGE_API_KEY='用户自己的 Key，仅在当前 shell 设置'
& $taskPython -m poster_visual_producer setup --backend cloud_api --output .scratch/cloud.json
# 编辑 cloud.json 填写真实服务资料，然后运行 doctor
```

Key 不进入素材指纹、候选证据或日志。请求使用 Bearer Key、model/prompt/n=1/size；支持 `data[0].b64_json` 或 `data[0].url`。图片 URL 下载不携带 API Authorization。自定义 parameters 只允许协议已适配的 quality/output_format/response_format/style，按服务能力明确填写；不默认发送 seed、独立 negative_prompt 或编辑参数。A3 将不支持的独立负面提示显式纳入提示适配并记录，不伪造硬性约束。尺寸先从配置能力选取，再按 Spec 裁切，因此 sizes 必须填写服务真实支持的值。

生产 endpoint 需要 HTTPS（仅 localhost 工程测试允许 HTTP）。重定向不自动转发凭据；已提交但下载失败、响应损坏或超时不会盲目再次提交，避免重复收费。明确未提交或可安全重试的拒绝才由 A3 在预算内最多重试一次。Key、额度、网络或图片错误会明确失败，不使用占位素材。

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
