# A3 与新版团队 Agent 接入（2026-11-08）

## 原目标与更新来源

沿用已批准的 2026-10-12 A3 设计：SDXL/Qwen-Image/云 API 三后端并列；生成素材与批准文字渲染分离；精确文案、缓存复用、证据、有限预算和外部授权局部返工。A3 只发布 rendered，真实审核通过才放行。A1/A2 的实现、正式格式和提示词不改。

基于本地 a9bb090，集成远程 main fc64d89（CD 样例）、feature/layout-planner-v2 0ad25ca（A2 真实测量/模板/检查）及 feature/dev-2026-10-14 4d839ac（A4 接入、证据和计量）。远程 feature/visual-producer 90cf900 已在本地历史中。A2 合并的 README 冲突保留双方说明。所有工作在 codex/a3-upstream-integration；原 codex/cd-sdxl-preview 保留。

## 接入层

`poster_visual_producer.team_integration.layout_registration(fonts, font_family_key=...)` 将原 Brief.copy 对象转换为 A2 的只读字符串视图，原始共享 Brief 不变。调用实际 LayoutPlannerAgent，用 A3 配置的同一字体文件和字重构造 A2 注册表。输出仍为 poster.spec@0.1.0；使用 A3 validate_pair 校验所有必需文案、版本和关键词，任何不兼容在生图前失败，不替用户删文案。A2/A3 分别保留其换行测量实现，A3 仍执行最终 preflight；不承诺任意长文本都能适配模板。

`critic_audit(provider)` 将真实审核包交给团队的 load_input、AuditEngine 和 FrameworkExecutor。每次调用经过现有 context.call_model，共享预算不足转成框架 BudgetExceededError 并结束 needs_review。provider 必须与工作流执行模式一致，不把 Fake 冒充 live。A4 的 live 报告及字符串 preserve 建议按原样保留；旧 real 报告和列表 preserve 继续兼容。repair_hint 始终只是建议，仍需受信 repair_authorizer 才能返工。

## 集成代码

```python
from poster_framework import AgentSet, PosterWorkflow
from poster_visual_producer.registration import registration, project_budget
from poster_visual_producer.team_integration import layout_registration, critic_audit
from poster_visual_producer.qc_bridge import qc_registration

# a1_registration、generator、fonts、a4_provider 由应用配置。
# generator 继续使用已选 SDXL/Qwen/cloud 后端；a4_provider 是 A4 自己的配置。
workflow = PosterWorkflow(
    AgentSet([a1_registration,
              layout_registration(fonts, font_family_key="noto_sans_sc"),
              registration(generator, fonts),
              qc_registration(critic_audit(a4_provider), bundle_root=review_root)]),
    root=runtime_root, budget=project_budget(),
    repair_authorizer=confirmed_repair_authorizer,
)
result = await workflow.run(keywords)  # 默认 real；A4 provider.live 必须 True
```

受信授权回调的字段和版本要求见 [QC 交接](qc-handoff.md)。provider 构造遵循 A4 [接入说明](A4_FRAMEWORK_INTEGRATION.md)；Key 分别由应用环境提供。此模块不主动下载模型、切换后端或配置密钥。

## 可复现离线入口

在项目目录及项目专用环境运行：

```powershell
python -m examples.visual.team_integration --root runs/team-integration
python -m examples.visual.team_integration --root runs/team-integration-en --language en --template split_stack_v1
python -m pytest -q --basetemp .scratch/u
```

离线入口使用显式 A1 数据发布器、真实团队 A2、真实 A3/Pillow 和真实团队 A4 算法；仅素材生成和视觉观察是替身。观察全部 unknown，不会伪造读到的文字或审美结论。输出 summary.json、PNG、便携 QC 包及统一记录。较短 basetemp 避免 Windows 260 字符路径限制；不修改系统长路径策略。

## 验证范围

回归覆盖三模板 × 中英文 × 初始/授权 rerender/授权 regenerate_asset；验证文案保留、素材身份、图像增量与审核计数，Fake pass 不放行；另测真实合同 live 报告、字符串修复建议、模式不匹配拒绝、分发模块。保留现有双素材返工、过期报告和授权、旧缓存及三后端回归。

真实 Qwen GPU、收费云生图与真实 A4 视觉审核仍需相应权重/服务配置后验证。离线链路通过不等于这些生产质量已通过。


## 本次执行结果

2026-11-08：全量 `python -m pytest -q --basetemp .scratch/u` 为 389 passed、1 skipped，耗时 130.71 秒。跳过项为 Windows 账户缺少创建符号链接权限。初次使用较长 basetemp 的五个授权返工路径失败已在短目录复核通过；没有修改系统长路径设置。

30 项新版链路与原授权返工测试通过。独立只读审查无重要可操作问题。`uv lock --check --offline` 通过，未更改 uv.lock。`uv build --offline --out-dir dist` 构建 wheel/sdist；冷子进程将 wheel 放在 sys.path 首位，确认 A2/A3/A4 实际从 wheel 导入且 A4 提示词可读。源码 zip 按显式清单生成，新版运行模块及资源包含在内。

中英文 CLI 示例均实际执行，每次 1 次素材操作、1 次 A4 调用，状态 needs_review。证据位于 runs/team-integration/summary.json 与 runs/team-integration-en/summary.json；包验证位于 runs/team-integration/package-verification.json。runs、权重、字体二进制与 Key 不进入 Git。完整可配置真实推理验证的边界保持上一节说明。
