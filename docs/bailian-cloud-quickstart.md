# 百炼免费额度生图：队友快速开始

更新：2026-11-18。适用于合并 `feature/visual-producer` 后，希望用云端生成一张海报的成员。使用基础 Python 环境和字体即可，不需要下载 SDXL/Qwen-Image 权重，也不需要 GPU、CUDA 或 `local-gpu` 依赖。A3 的输入输出和 A4 审核包保持相同接口。

## 1. 安装基础环境

先拉取已合并 A3 的代码，进入项目根目录，按[云端安装指南第 2 步](backend-setup.md#云端-images-json合并后的队友操作步骤)安装 Python 3.11 独立环境、锁定的基础依赖和字体。下面命令在同一个 PowerShell 窗口中执行，示例解释器为：

```powershell
Set-Location 'D:\Projects\Creative-Poster-Framework-ARIN-5201'
$taskPython='D:\Dev\conda-envs\creative-poster\python.exe'
```

其他电脑修改为自己的项目和独立环境路径。无需运行 `setup-local-gpu.ps1` 或 `fetch-sdxl.py`。

## 2. 登录、确认免费额度并启用用完即停

1. 打开[百炼控制台](https://bailian.console.aliyun.com/)，本人完成登录、必要的注册和实名认证。已有账号可直接使用。
2. 进入**费用用量 → 免费额度**，搜索 `qwen-image`。本指南采用华北2（北京），不要混用其他地域的 Key 和 endpoint。
3. 确认 `qwen-image-2.1-turbo` 有尚未过期的免费额度。没有额度时先停止，不把页面体验额度或聊天模型 Token 当成生图 API 额度。
4. 开启该模型的**用完即停**并确认；等待页面状态变为**已开启**，再继续调用。该功能默认可能关闭，不应仅凭有免费额度就开始运行。

本次账号调用前实际显示 **10/10 张**，到期日为 **2027-01-01**；这不是对其他账号的额度承诺。队友应以自己的控制台为准，不必为本流程购买 Token Plan 或模型套餐。首次开通符合条件的账号，额度由平台发放，不需要购买“免费额度”；新开通账号发放可能有延迟。规则见[官方新人免费额度](https://help.aliyun.com/zh/model-studio/new-free-quota)。

## 3. 创建项目专用 API Key

1. 在百炼右上角**管理 → API Key**进入密钥管理，确认华北2（北京）和实际业务空间。
2. 点击**创建API-KEY**，描述可填 `Creative Poster A3 云端生图试用`。
3. 选择**自定义**权限，开启**访问模型范围**，搜索 `image`，只选 `Qwen-Image-2.1-Turbo`。如该业务空间未授权模型，先按平台提示完成模型授权。
4. 本人确认创建，复制只显示一次的 Key 并保存在本机。不要重置团队其他用途的既有 Key，也不要在聊天、截图或 Git 提交中放入明文。
5. 同页复制该业务空间的**按量付费 Base URL**，例如 `https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。

“按量付费”是控制台入口名称；是否使用赠送额度仍取决于第 2 步确认的模型免费额度。模型、Key、业务空间和 endpoint 需要匹配。

## 4. 复制配置模板并填入自己的地址

仓库提供[百炼配置模板](../examples/visual/backend-bailian.example.json)，不含 Key 或任何成员的业务空间 ID：

```powershell
New-Item -ItemType Directory -Path .scratch -Force | Out-Null
if (Test-Path -LiteralPath '.scratch/bailian-trial.json') {
    throw '配置已存在，请编辑已有文件；不要覆盖本机设置。'
}
Copy-Item -LiteralPath 'examples/visual/backend-bailian.example.json' -Destination '.scratch/bailian-trial.json'
```

编辑 `.scratch/bailian-trial.json`：把 `endpoint` 中的 `YOUR_WORKSPACE_ID` 改成自己的业务空间 ID，或使用刚复制的 Base URL，在末尾追加 `/images/generations`。其余采用以下值：

```json
{
  "kind": "cloud_api",
  "provider": "aliyun-bailian",
  "model": "qwen-image-2.1-turbo",
  "endpoint": "https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations",
  "api_key_env": "DASHSCOPE_API_KEY",
  "sizes": [[1024, 1024]],
  "timeout_seconds": 600,
  "parameters": {}
}
```

本次验证使用同步 **OpenAI 兼容 Images** 接口，返回 `data[0].url`；使用项目内置 HTTP 后端即可，不用安装 OpenAI/DashScope SDK。不改用 DashScope 的异步任务接口。保持 `parameters={}`，无需填写 OpenAI 专属 quality/style 或强制 base64 返回。接口依据见[百炼千问图像 API](https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference)。

## 5. 设置 Key 并检查（不提交生图）

在接下来运行 Python 的**同一个 PowerShell 窗口**中执行，按提示粘贴 Key。输入不回显，Key 不直接写入命令历史：

```powershell
$taskKey=Read-Host '粘贴百炼 API Key' -AsSecureString
try {
    $env:DASHSCOPE_API_KEY=[System.Net.NetworkCredential]::new('', $taskKey).Password
} finally {
    $taskKey.Dispose()
}
& $taskPython -m poster_visual_producer doctor --backend-config .scratch/bailian-trial.json --fonts examples/visual/fonts.json --font-base . --output-root runs/bailian-check
if ($LASTEXITCODE -ne 0) { throw '配置检查未通过，请先修复检查结果。' }
```

预期 `ready=true`，包含 `CREDENTIAL_PRESENT`、`FONTS_READY` 和 `OUTPUT_WRITABLE`。`REMOTE_NOT_PROBED` 为正常提示：doctor 不访问远端，不能验证账号权限、Key 有效性或剩余额度。

项目不会自动加载 `.env` / `.scratch/bailian.env`，新终端需重新设置环境变量。现有 Key 文件若需保存，只放在被 Git 忽略的本机位置；不要把 Key 填进 JSON 的 `api_key_env`，那里只接受变量名。

## 6. 跑一张海洋海报

确认第 2 步的**所选模型有额度、用完即停已开启**，第 5 步检查通过后，在同一窗口执行一次：

```powershell
$taskOutputRoot='runs/bailian-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $taskPython -m poster_visual_producer.cases --case examples/visual/cases/ocean.json --backend-config .scratch/bailian-trial.json --root $taskOutputRoot --fonts examples/visual/fonts.json --font-base .
if ($LASTEXITCODE -ne 0) { throw '生图未完成，请查看本次运行的 summary.json。' }
```

这一步实际提交生图请求，消耗模型额度。案例只有一个图片素材；一次成功运行生成一张海报。超时或提交状态不明时，不要连续重跑：先查看本次 `summary.json` 和平台调用记录；本地 `rendered` 表示无需重提。每次主动新实验使用新的输出目录，方便保留证据。

在 `$taskOutputRoot/ocean/` 查看：

| 文件 | 用途 |
|---|---|
| `poster.png` | 720×1280 最终海报 |
| `summary.json` | `status=rendered`、耗时及调用计数；`source_mode=provider` |
| `candidate.json` | 素材、候选及其来源引用；在 `assets[].provenance` 中查看实际 `provider` / `model` |
| `render_manifest.json` | 文字、几何及渲染证据 |
| `a3-record.json` | A3 框架运行记录 |

云端生成的是 1024×1024 素材，A3 按 Spec 裁切/适配并绘制正式文案。正式接入队友 A2 输出或在总流程中注册 A3，使用同一份 `.scratch/bailian-trial.json`，按[通用指南第 6 步](backend-setup.md#云端-images-json合并后的队友操作步骤)执行。A4 审核不由云端生图配置替代。

## 2026-11-18 真实验证结果

本次没有下载新权重或加载 SDXL，使用现有 Python 3.11 项目环境和字体：

| 项目 | 已核验结果 |
|---|---|
| 后端 / 模型 | `aliyun-bailian` / `qwen-image-2.1-turbo` |
| 账号保护 | 调用前确认 10 张免费额度、用完即停已开启；新 Key 仅授权该模型 |
| 案例 / 文案 | `ocean`；“静海之蓝”“留一点宁静给自己” |
| 运行状态 | `rendered`；素材来源为真实云端 provider |
| 操作计数 | `model_calls=1`、`image_operations=1`、`critic_vision_calls=0` |
| 案例耗时 | 11.485 秒（单次测量，不承诺其他账号/网络有相同速度） |
| 最终图片 | 720×1280 PNG |
| PNG SHA-256 | `9c23d5df0e047bda9d7ed87859e409657832c23a2811853e4ee852cda4a420b7` |
| 本机证据目录 | `runs/bailian-trial-20261118/ocean/`，由 Git 忽略，不随仓库分发 |

这是 **人工 Brief/Spec → 真实百炼生图 → A3 渲染与证据输出** 的验证。没有调用实时 A1 或 A4，不代表完整四角色联调或视觉质量验收通过。这里使用的云端 Qwen 与可选本地 Qwen-Image 后端是两条不同运行路径。

遇到 `403 AllocationQuota.FreeTierOnly`，先检查额度是否耗尽/过期，保留用完即停保护；不要为继续试验而关闭它。其他 401/403 核对 Key 和模型权限，404 核对工作空间 Base URL，400 核对同步接口/模型/尺寸；通用故障处理见[安装指南](backend-setup.md)。
