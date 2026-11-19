# 一个本地文件管理四个 Agent

统一入口支持 `--config config/project.local.toml`。实际密钥直接填写在这个文件中，不需要设置环境变量，也不修改组员各自的配置文件。

## 队友从克隆开始

以下是 Windows PowerShell 实测环境的步骤。先安装 Git、`uv` 与 Miniconda；命令从克隆的仓库根目录运行。按本机路径传入 `-CondaExecutable`、`-EnvironmentPrefix`、`-UvCache`，并让 `-TestTempRoot` 保持简短，避免 Windows 长路径限制。

```powershell
git clone https://github.com/Greensheep04/Creative-Poster-Framework-ARIN-5201.git
Set-Location Creative-Poster-Framework-ARIN-5201
# 在集成分支尚未合入 main 时，先运行：git switch codex/unified-local-config
$env:CONDA_PKGS_DIRS = 'D:\Dev\conda-pkgs'
.\scripts\setup-visual.ps1
$python = 'D:\Dev\conda-envs\creative-poster\python.exe'
if (-not (Test-Path config/project.local.toml)) {
    Copy-Item config/project.live-sdxl.example.toml config/project.local.toml
}
& $python -m examples.team_workflow --config config/project.local.toml --offline --keywords "深海" "鲸鱼" "孤独"
```

安装脚本创建隔离的 Python 3.11 环境，使用 `uv.lock` 并下载经 SHA-256 校验的 Noto 字体。离线命令使用确定性素材与审核夹具验证四角色接口，不会访问生成或审核服务，也不代表真实审核通过。

要复验真实生图，在具备可用 CUDA 的 Windows NVIDIA 机器上运行 `.\scripts\setup-local-gpu.ps1`。该脚本安装锁定的 CUDA 依赖并下载固定提交的 SDXL。然后在忽略的 `config/project.local.toml` 填写自己的 `agents.a1.api_key`（有模型调用权限的 Hugging Face token）、`agents.a4.api_key`（有视觉模型调用权限的 MiniMax key）及 `agents.a3.backend.model_dir`（默认安装到 `D:/Models/sdxl-base-1.0`）。不要提交该文件。

```powershell
& $python -m examples.team_workflow --config config/project.local.toml --live --check
& $python -m examples.team_workflow --config config/project.local.toml --live --keywords "深海" "鲸鱼" "孤独"
```

示例配置使用验收时的模型 ID、SDXL revision、seed、A2 面积门槛与返工预算；`--check` 不调用模型，不证明账号权限或服务可达。没有 NVIDIA GPU 的队友可以用 [云端 A3](backend-setup.md) 跑同一框架，但生成素材会不同。即便使用同样设置，在线 A1/A4 服务响应及不同 GPU 的推理也不保证逐像素复现；应对照实际 Agent 记录和 A2 选版结果，而不是要求图片哈希相同。

## 两个文件的用途

- `config/project.example.toml`：提交 Git 的完整模板，所有 api_key 为空。
- `config/project.live-sdxl.example.toml`：与真实 SDXL 验收对齐的公开配置，不含密钥及本机模型路径，默认仍为离线。
- `config/project.local.toml`：本人实际配置，已加入 .gitignore；模板复制后仅编辑这个文件。提交时无需清空或覆盖实际文件。

本机已创建空密钥的 local 文件，并填入既有 SDXL 权重目录。已有 local 文件不会自动覆盖。

## 各配置区

| 配置区 | 参数 |
|---|---|
| runtime | mode、output_root、keywords、constraints、seed |
| budget | 最大返工轮数、图像/审核/总模型/工具次数 |
| agents.a1 | Hugging Face 模型、完整 endpoint、api_key、max_tokens、temperature、timeout_seconds |
| agents.a2 | font_family_key、adaptive、可选 template_id |
| agents.a3 | api_key，以及 backend 中原有 SDXL/Qwen/Cloud 原生字段 |
| agents.a4 | api_key、审核重试和期限；provider 中原有单模型配置字段 |
| fonts | 字体 family/weight 下的 path、index |

A3 为本地 SDXL 时 api_key 保持空；model_dir 填实际权重目录。云端 A3 的 api_key 填在 agents.a3，backend 内 api_key_env 只为兼容原生 schema；统一入口直接注入本地密钥。A4 同理，原生 api_key_env 在统一模式不触发环境变量回退。

相对路径以 TOML 所在目录为基准；output_root 是运行集合目录，每次自动创建新子目录。显式 --root 可指定单次运行目录。支持 seed 的后端才应用 runtime.seed；离线 fixture 或不支持 seed 的云端后端在有效快照中记录 null。

## 使用命令

在项目根目录及专用环境中：

```powershell
$taskPython='D:/Dev/conda-envs/creative-poster/python.exe'
$env:PYTHONPATH="$PWD/src;$PWD"
& $taskPython -m examples.team_workflow --config config/project.local.toml --check
& $taskPython -m examples.team_workflow --config config/project.local.toml
```

模板默认 offline，空密钥即可运行。只检查后续真实模式的缺项，不发网络请求：

```powershell
& $taskPython -m examples.team_workflow --config config/project.local.toml --live --check
```

补齐实际 A1/A4 服务后，需明确继续真实验证，再使用 --live 运行；本次实现不执行真实调用。

覆盖规则：--offline/--live、--root、--keywords 显式参数优先于统一配置。--offline-repair 继续是受控离线测试。统一入口不允许同时指定 --backend-config/--critic-config/--a1-model 等旧配置选项，避免混合配置源；未使用 --config 的旧命令继续按原规则运行。

## 密钥、验证与兼容

A1/A3/A4 密钥使用私有构造参数传给客户端，不写入 os.environ、AgentRegistration.config、原生 JSON 或脱敏运行快照。配置解析错误隐藏字段值。effective-config.json 和统一记录中的 project_config 均脱敏；缺真实密钥时列出准确字段并在调用前停止。空的统一密钥不会偷偷使用环境中的其他账号。

所有输出目录拒绝覆盖既有运行，预算耗尽即使尚无图片也会保存完整结果。零预算可能只有 result/summary/config 快照，没有 poster.png。字体、模型存在性和服务权限仍须按实际环境检查；--check 不提交模型请求。

各 Agent 保留原来的独立配置和运行入口：A1 仍可读取 HF_TOKEN/HF_MODEL，A3 仍可读取原生 backend JSON 与凭据变量，A4 仍可使用原生单模型/多模型 JSON。新增密钥注入参数均可选，旧默认值保留。

请使用 git status/git check-ignore 确认 local 文件被忽略；源码分发清单只包含 example 模板。具体离线测试与分发结果见[验证记录](unified-configuration-verification.md)。


## 真实返工与日志

开启 `runtime.auto_repair` 后，统一入口按审核问题选择返工对象。缺失视觉主体交给 A3 重生素材，文字可读性或排版缺陷交给 A2 重排，合成错误交给 A3 重新渲染，文案内容或创意冲突交给 A1。无法可靠分类的失败保留复核。A2 在生成首张图前耗尽版式容量时也可请求 A1 缩短文案；所有途径共用一次返工预算。返工后图片哈希相同则保留待复核状态并跳过重复送审。

统一入口的 `agents.a2.adaptive` 开启后读取 A1 的构图、阅读顺序和排字意图，以实测字宽、字形与行高拟合标题、副标题和行动号召。显式指定的 template_id 优先。A2 的 layout-decision.json 记录候选得分、淘汰原因、断行与最终选择。

默认启用的 `agents.a2.composition_aware` 会按后端源图尺寸过滤主图过小的双列候选，详见 [A2 模板与面积门槛](design/A2-template-catalog-integration.md)。

返工次数受 budget.max_revision_rounds 限制，当前最多一轮。达到预算且复审仍失败时如实返回 needs_review；unknown 或接口故障不触发盲目返工。此开关不修改 A4 的审核规则，也不把执行完成视为质量通过。

每次使用新的输出目录。summary.json/result.json 和 store/runs/*/run-record.json 保留调用顺序、用量、请求、版本和返工历史；store/*/invocations 下保留每轮 A1/A2 输出、A3 图像与 A4 审核，reviews 下保留逐次送审证据。命令输出可重定向到本地日志；有效配置快照脱敏，实际密钥仍只存放在忽略的 local TOML。


## A1 校验前证据与换行匹配

真实 A1 的每次文本响应会先保存为调用目录下的 `model-response.json`，再进行 JSON/schema 校验；`raw_text` 保留响应文本格式并遮盖凭据。无效输出也会留存。`validation.json` 单独记录 accepted/rejected、错误类别和规范化字段，避免把 HTTP/模型调用成功误认为 Agent 输出有效。HTTP 传输失败、或 Chat Completions 包装本身无效而未返回文本时，以控制台及模型事件记录为准。

返工时原请求的关键词不可修改。缺失、空值或结构错误的 keywords 从 original_request.keywords 恢复，并写入 normalized_fields 和 normalization_source；有效但被替换/重排的关键词仍被拒绝。其他创意字段继续严格校验，不补造缺失文案。`original-a1.json` 是通过校验的 A1 输出；诊断模型原文应查看 `model-response.json`。

统一送审的海报文案使用显式 `line_wrap` 策略：只允许观察文本中的 CR/LF 对应原文中的一个空格，或不对应字符（中文/长词行间断开）。不忽略错别字、数字、大小写、标点、普通空格或零宽字符。原文主动包含换行时仍按原文逐字比较。独立 A4 的默认 exact 以及可选 typographic 策略保留，required_text 用户约束仍保持原来的严格规则。


实际模型还可能在首轮将 keywords 输出为带 text/keyword_id 的对象数组。仅当每项 text 与原请求逐字、逐项、同序一致，且没有未知对象字段时，入口才提取字符串并记录规范化；不匹配的初始响应仍拒绝。该适配由 2026-11-19 校验前日志捕获的真实响应复现测试覆盖。
