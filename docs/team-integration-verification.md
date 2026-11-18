# 本地集成验证记录 — 2026-11-18

## 范围

执行用户批准的本地合并、接口适配和离线验证。基线 A3 1e9042f；合入 A1 creative-director 827c058。A3 基线已包含 main fc64d89、A2 v2 0ad25ca、A4 4d839ac。分支 codex/team-integration-20261118；不推送远程，不改 main。

合并提交 050aabb；计划 4b8efb4；锁文件更新 886b3cd。依赖合并保留四个 Python 包、A1 提示词和 A3 GPU extras；显式 httpx >=0.28,<0.29 满足 A1/A4 共同范围。

## 本轮修复

- A1 v2 输出转换成 A2/A3 规范 Brief，并保存原始 JSON/哈希/关键词/文案；A1 返工使用私有 raw-v2 记忆视图。
- Brief 版本随返工递增；Spec 从已验证授权中的旧业务版本递增；不依赖已被框架失效的旧布局。
- A2 接收原配色、视觉风格和明确模板约束；对未指定模板的长标题进行真实测量选择，不截断文案。
- 必需 CTA 保留并测量；布局 patch 不重复添加 CTA，满版背景与 CTA 明确声明有意叠放。
- 总模型预算覆盖两次 A1、两次图像操作和两次 A4（上限 6），避免真实 A1 调用形态提前耗尽预算；已有替身回归验证两次 A4 均执行。
- 原始 source 与适配视图隔离，防止共享字典修改原始 JSON。
- 离线测试授权与真实执行分离；未知报告不自动放行/重生，版本、来源和预算校验沿用框架。

## 软件验证

最终全量回归：**602 passed、1 skipped，185.29 秒**。新增与修复后聚焦回归为 17 passed；真实 A1 调用形态的二次审核预算回归另已通过。唯一跳过是 Windows 创建符号链接权限 WinError 1314。

完整命令：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
& 'D:/Dev/conda-envs/creative-poster/python.exe' -m pytest -q -p no:cacheprovider --basetemp D:/Projects/.team-complete1118
uv lock --check --offline --python D:/Dev/conda-envs/creative-poster/python.exe
```

日志位于本机 .scratch/team-integration-complete-tests.log，不进入 Git。

## 实际统一 CLI 离线运行

本机产物在 runs/t1118f，各场景使用独立输出目录，不覆盖旧结果。

| 场景 | 返工轮数 | 图像操作 | Critic 调用 | 结果 |
|---|---:|---:|---:|---|
| initial | 0 | 1 | 1 | needs_review |
| revise_brief | 1 | 1 | 2 | needs_review |
| revise_layout | 1 | 1 | 2 | needs_review |
| rerender | 1 | 1 | 2 | needs_review |
| regenerate_asset | 1 | 2 | 2 | needs_review |

第一项完整执行四角色；返工场景执行对应真实路由后达到一轮预算。A1 为实际模块 deterministic offline，A2 为实际规划器，A3 为实际 Pillow 渲染加 fixture 素材，A4 为实际 AuditEngine 加 unknown 观察替身。文案/版式返工与重绘允许复用素材；显式素材重生增加一次操作。

每项产物：summary.json、result.json、brief.json、poster.png、store 统一记录及 reviews 审核证据包。图像生成与视觉审核均未调用外部服务；全部 production_pass=false，不能作为真实模型效果或质量验收。

## 构建与审查

- uv lock --check --offline 通过。
- uv build --offline 构建 wheel/sdist；冷进程从解包的 wheel 目录导入四模块并读取 A1 初始/返工提示词通过。没有在已有环境重新安装依赖。
- 显式源码包 254 个成员逐个核验通过；中文路径保持 UTF-8，不含密钥、字体二进制、权重及 runs。
- 分发检查位于 .scratch/team-delivery/verification.json。
- 独立只读审查发现的配色/风格、模板约束、Spec 版本及 CTA 组合问题均已复现、修复并补回归；最后复核无 Important/Critical 遗留。

## 后续真实验证的具体缺项

1. A1 当前进程的 HF_TOKEN，需要 Inference Providers 权限与可用额度。沿用 Qwen/Qwen3-4B-Instruct-2507 和既有 Featherless 路由，不更换提示词。
2. A4 已实测的配置 JSON：profile、视觉 model、HTTPS base_url、api_key_env，以及该变量对应的有效密钥。多模型目录需指出所选 alias。仓库只提供模板，未找到真实账号配置。
3. 真实服务额度/调用许可补齐后，从单案例开始。A3 已有 SDXL 权重与环境，运行前在本机设置 POSTER_SDXL_MODEL_DIR 并按已有 doctor 检查。

本阶段不执行真实调用。真实入口默认 fail/unknown 保存反馈后停止；自动实际返工需应用提供受信授权与明确修复范围，不把离线测试授权当成生产策略。
