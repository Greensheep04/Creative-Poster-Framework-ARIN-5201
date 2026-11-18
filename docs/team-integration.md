# 本地四角色集成与运行

本分支集成完成的 A1/A2/A3/A4，统一入口为 `python -m examples.team_workflow`。当前阶段只执行离线验证；不使用真实密钥、不调用服务、不推送远程。

## 环境

在项目根目录使用已有独立 Conda 环境。PowerShell：

```powershell
Set-Location 'D:\Projects\Creative-Poster-Framework-ARIN-5201'
$taskPython='D:\Dev\conda-envs\creative-poster\python.exe'
$env:PYTHONPATH="$PWD/src;$PWD"
```

其他电脑自行调整项目/环境路径。共享代码不包含本机模型绝对路径。新环境按 environment.yml 创建 Python 3.11 环境，激活后使用 `uv sync --active --locked --all-groups`；本地 SDXL 另加 `--extra local-gpu`。字体按现有安装指南准备。不要安装到 Conda base，也不要额外创建 .venv。

## 离线运行

每次指定新的短路径输出目录，避免 Windows 260 字符限制：

```powershell
& $taskPython -m examples.team_workflow --offline --root runs/team-offline-NEW
& $taskPython -m examples.team_workflow --offline --offline-repair revise_brief --root runs/team-brief-repair-NEW
& $taskPython -m examples.team_workflow --offline --offline-repair revise_layout --root runs/team-layout-repair-NEW
& $taskPython -m examples.team_workflow --offline --offline-repair rerender --root runs/team-rerender-NEW
& $taskPython -m examples.team_workflow --offline --offline-repair regenerate_asset --root runs/team-regenerate-NEW
```

`--keywords` 接收 2–5 个关键词，默认为 ocean/plastic/hope。A1 使用组员实现的显式 deterministic offline 模式；A2 为实际团队规划器和真实字体测量；A3 为实际 Pillow 渲染器，素材生成使用 fixture；A4 为实际 AuditEngine，视觉观察为显式 unknown fixture。所有结果应为 needs_review，不作视觉质量通过或生产放行。

`--offline-repair` 是应用显式授权的受控返工测试，不表示 A4 的 unknown 自动授权生图。版式测试明确修改标题对齐且绑定旧值；最多一轮返工，总模型操作额度为 6，图像操作和审核各最多 2 次。素材重新生成测试增加一次图像操作；文案变化和重新渲染允许复用已有素材。真实模式不接受这些测试开关。

## 产物与接口

根目录保存 summary.json、result.json、brief.json、poster.png；store 保存统一运行记录、每角色输出、原始 A1 JSON 和版本历史；reviews 保存审核输入、当前候选绑定、图片、渲染证据和 A4 报告。只有审核过的同一候选可以成为输出。已有非空输出目录拒绝覆盖。

A1 wrapper 保留 v2 原始 JSON 的独立文件与 SHA-256，将其转换为 poster.creative-brief@0.1.0。返工时只向原 A1 暴露私有 raw-v2 记忆视图，保留共享依赖身份；重新发布 Brief 时增加 brief_version。完整关键词、精确文案、原始设计内容保留在规范 Brief 和来源证据中。

统一入口启用 A2 的测量模板尝试，优先 split_stack，保留完整标题；必需 CTA 使用实际字体测量添加槽位。放不下时明确失败，不删文案、不把长标题截短。通用旧入口默认行为保持既有配置。布局返工可通过应用提供的 revision_patch 接入 A2 已有旧值校验。

## 后续真实运行需要的配置

1. A1：在当前进程安全设置 HF_TOKEN，具备 Hugging Face Inference Providers 权限和可用额度。默认 Qwen/Qwen3-4B-Instruct-2507，现有 Featherless 路由、提示词和参数不变；可显式传 --a1-model。
2. A3：设置 POSTER_SDXL_MODEL_DIR 指向已有 SDXL；--backend-config 使用 examples/visual/backend-sdxl.json。不下载新模型。显存有其他占用时，在本地配置中显式启用 cpu_offload，保留生成参数记录。
3. A4：提供组员实际使用的 provider JSON（HTTPS base_url、视觉 model、profile、api_key_env 等），在当前进程设置对应密钥；多模型目录用 --critic-model 指定。仓库 models.example.json 是模板，未找到实际账号配置，不能把它认作实测服务凭据。

先做不联网的配置检查：

```powershell
& $taskPython -m examples.team_workflow --live --check --backend-config examples/visual/backend-sdxl.json --critic-config .scratch/a4-provider.json
```

此检查只确认配置/凭据入口，不证明远程权限、额度、协议或模型质量。A3 的权重/CUDA/字体完整检查继续使用已有 doctor。后续用户补齐配置并继续真实验证时，再执行：

```powershell
& $taskPython -m examples.team_workflow --live --root runs/team-live-NEW --backend-config examples/visual/backend-sdxl.json --critic-config .scratch/a4-provider.json
```

真实入口会调用 A1、A3、A4。默认没有自动返工授权：fail/unknown 保存完整反馈并停止，供 A1/应用确认后续操作，不把建议直接作为修改命令。本阶段已离线验证返工机制；后续真实自动返工还需提供受信授权策略和具体修复约束。审核质量 pass 和工程执行完成分别报告。

## 验证

```powershell
& $taskPython -m pytest -q -p no:cacheprovider --basetemp D:/Projects/.team-tests-NEW
uv lock --check --offline --python $taskPython
```

具体本轮结果见 docs/team-integration-verification.md。运行产物、凭据、字体二进制和权重不提交 Git。
