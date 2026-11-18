# 统一配置验证记录 — 2026-11-18

## 实现范围

基线 dbba187；分支 codex/unified-local-config。实现用户批准的单个本地 TOML 管理模式：api_key 直接保存在私有文件，提交完整空密钥模板，保留四个 Agent 的原生配置和运行入口。本阶段无远程推送或真实模型请求。

- config/project.example.toml：完整模板，A1/A3/A4 api_key 均为空。
- config/project.local.toml：被 Git 忽略；本机创建时填入已有 SDXL 目录，密钥仍为空；不会自动覆盖已有 local。
- Strict Pydantic/tomllib 加载器解析 runtime/budget/agents/fonts；所有相对路径从配置目录解析。
- 密钥以 SecretStr 保存，通过私有构造参数交给 A1、云端 A3、A4；不写入全局环境变量或 AgentRegistration.config。
- 统一运行记录及 effective-config.json 保存脱敏后的实际模式、输出目录、关键词、预算和参数。
- 格式/Schema 错误隐藏值；拒绝空白/控制/非 ASCII 密钥字符；统一 CLI/调用函数对运行错误再次脱敏。
- --offline/--live、--root、--keywords 可显式覆盖中央设置；旧单 Agent 配置方式继续可用。
- 零预算在候选产生前停止也能导出 coherent summary/result/config，不假造图片。

## 验证

新增专项：21 passed，9.33 秒。覆盖模板/路径/严格校验、repr/快照/异常脱敏、A1 私有地址和采样参数、A3 实际本地 HTTP 请求凭据、A4 HTTP Mock 凭据、环境变量隔离、空密钥不回退环境账号、CLI 模式覆盖及零预算。HTTP 测试使用 MockTransport 或本机测试服务器，无真实模型调用。

兼容专项此前 56 passed，包含 A1 原生 Hugging Face 与返工、旧 A3 云端参数和环境密钥行为。最终全量回归：**623 passed、1 skipped，196.04 秒**。唯一跳过为已有 Windows 符号链接权限 WinError 1314；日志在 .scratch/unified-config-tests.log。

实际运行命令（项目专用 Python 3.11 环境）：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
& 'D:/Dev/conda-envs/creative-poster/python.exe' -m examples.team_workflow --config config/project.local.toml --offline --check
& 'D:/Dev/conda-envs/creative-poster/python.exe' -m examples.team_workflow --config config/project.local.toml --offline
& 'D:/Dev/conda-envs/creative-poster/python.exe' -m examples.team_workflow --config config/project.local.toml --offline --offline-repair revise_brief
```

离线检查 ready=true、network_requests=0。初始链路返回 needs_review，1 次图像操作和 1 次审核；A1 受控返工返回 needs_review，1 次图像操作和 2 次审核，文案版本递增并复用素材。两项图片均为 720×1280，脱敏配置中所有 api_key 均为 [REDACTED]。

初始本机记录：D:\Projects\Creative-Poster-Framework-ARIN-5201\runs\unified\run-75969eb3744e\store\runs\run-7e1967fd57fb47318d7478a160ca4b4e\run-record.json

返工本机记录：D:\Projects\Creative-Poster-Framework-ARIN-5201\runs\unified\run-82ca314dc73c\store\runs\run-6001b9708fe4492dba9c463fac14023f\run-record.json

实际字体测量、A2/A3 渲染和 A4 算法运行；A1 为自身 deterministic offline 模式，素材与视觉观察使用显式替身。needs_review 是预期，不能作真实视觉质量通过。

## 分发与审查

git check-ignore 确认 config/project.local.toml 被忽略；git ls-files 确认该文件未被跟踪；显式源码包清单仅包含 example，不包含 local。锁文件校验通过。wheel/sdist 构建成功；冷进程从解包的 wheel 导入四模块、读取 A1 提示词并加载统一配置通过。源码包 261 个成员逐项验证通过，包内模板密钥全部为空且私有 local 文件缺席；分发收据在 .scratch/unified-config-delivery/verification.json。

独立只读审查发现一项异常密钥 HTTP 头错误泄露问题，已先用 6 个失败用例复现，再补 Schema/客户端校验及错误脱敏；最终静态复核无 Important/Critical 问题。

## 后续真实运行

只需在本地 TOML 填写 agents.a1.api_key、agents.a4.api_key、agents.a4.provider 的实际服务地址与模型。A3 本机 SDXL 配置已可沿用。--live --check 不联网，只列缺失字段；本次实际输出正是这三类缺项。密钥不用提交，也无需设置环境变量。真实权限、额度和效果待用户补齐配置并继续验证。
