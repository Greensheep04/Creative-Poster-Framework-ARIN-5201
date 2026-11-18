# 一个本地文件管理四个 Agent

统一入口支持 `--config config/project.local.toml`。实际密钥直接填写在这个文件中，不需要设置环境变量，也不修改组员各自的配置文件。

## 两个文件的用途

- `config/project.example.toml`：提交 Git 的完整模板，所有 api_key 为空。
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
