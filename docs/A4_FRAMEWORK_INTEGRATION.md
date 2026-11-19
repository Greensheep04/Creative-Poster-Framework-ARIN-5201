# A4 框架接入：已实现接口与使用边界

更新：2026-10-14。实现位于当前 `feature/dev-2026-10-14` 工作区，尚未提交。改动只涉及 A4 源码、测试和文档，共享框架及 A1/A2/A3 保持原样。

P1 已补独立结构化诊断与可选持久交付保护，使用方式见 [P1本地工具](A4_P1_LOCAL_TOOLS.md)。共享框架不变。

## 1. 当前可用能力

| 模块 | 接口 | 用途 |
|---|---|---|
| [输入与绑定](../src/poster_critic/adapters/framework_input.py) | `build_audit_input`、`validate_binding_snapshot`、`validate_binding` | 显式选择候选，保留原始要求，读取真实图像，核对来源/版本 |
| [共享执行](../src/poster_critic/adapters/framework_executor.py) | `FrameworkExecutor` | 每次模型尝试通过现有 `context.call_model(kind="critic_vision")` |
| [A4 包装](../src/poster_critic/adapters/framework_agent.py) | `QualityCriticAgent`、`to_framework_output` | 返回现有 AgentOutput，并自动调用成品交付或 A1 反馈函数 |

生产 pass 调用交付函数；fail/unknown 调用 A1 反馈函数。Fake pass 为 test_only，两个函数都不调用；Fake fail/unknown 会调用反馈函数，离线运行必须接测试函数。

本次验证使用真实格式图片和真实 RunSession 文件/预算 API，模型观察及接收函数为受控测试替身。它证明程序接入行为，不证明模型视觉质量、真实 A1 消费或完整团队联调。

## 2. 输入接口

```python
FrameworkInputMapping(
    source_format=actual_input_format,
    candidate=explicit_candidate_reference,
    decode=decode_provided_content,
    current_sources=query_current_sources,
)
```

| 字段 | 类型/来源 | 必须满足 |
|---|---|---|
| source_format | 已提供的 ContentFormat | 与 AgentInput.content_format 一致；不自动猜格式或解析组员业务 schema |
| candidate | 显式 ArtifactReference | purpose=candidate、run 匹配、revision 在依赖中；不使用 files[0] |
| decode | 同步 `Callable[[AgentInput], AuditContextV1]` | 只归一化已提供的内容；返回严格 A4 类型，不能以它覆盖原始请求或候选 |
| current_sources | 同步 `Callable[[AgentContext], CurrentAuditSources]` | 从调用方明确提供的当前来源查询读取；缺失则拒绝 |

`AuditContextV1` 只包含现有 `approved_copy`、`design_context`、`layout_context`、`asset_context`、`render_context`、`required_checks`，默认值及嵌套类型沿用 A4 合同。它没有 keywords、user_constraints、image、run_id 字段，不为其他模块定义业务协议。坐标统一为最终画布 720×1280 的像素 xyxy；来源为 xywh 或归一化框时，由明确的 decoder 转换。越界声明框保留给 QC 判断；错误 copy_id/element 引用直接拒绝。

`CurrentAuditSources` 是 A4 的查询结果封装：

```python
CurrentAuditSources(
    authority="调用方实际查询入口的名称",
    candidate=current_candidate_reference,
    version_dependencies=complete_current_dependencies,
)
```

三个字段全部必填。authority 必须非空；dependencies 为 VersionDependency tuple，必须包含 candidate，不得包含重复 artifact_id 或混合修订。查询结果与输入/context 的完整依赖核对，不能只查图片而忽略 Brief/Spec 等已经声明的依赖。名称本身不是查询权威的证明；生产接入方负责让此函数实际查询当前记录，不能把输入包原样返回来冒充“最新”。

`build_audit_input(inputs, context, mapping)` 返回 `(AuditInputV1, AuditBinding)`：

- keywords 从 context/original_request 核对后原样读取，保留 ID、文本和顺序。
- 同名约束全部保留；constraint_id 为 `uc-` 加 canonical `{run_id, position, name, value}` 的 SHA-256。位置纳入 hash，不用 name 字典吞掉重复条目。
- required_text 补成 `copy_id="constraint:<constraint_id>"` 的 required/exact 文案；冲突拒绝。
- sample_id 为 `framework:<run_id>:<candidate.artifact_id>:<candidate.revision>`。
- 用 `context.files.read(candidate)` 读取登记内容，核对 hash、真实格式、单帧、720×1280 和 20 MiB 上限。WebP 明确拒绝。
- 在读图前后查询当前候选/依赖，保存完整输入指纹、原始输入/请求快照和来源指纹。

`AuditBinding` 保存 identity、source_format、candidate、version_dependencies、authority、original_request、constraint_ids、content_fingerprint、input_fingerprint、audit。它是 A4 私有的内存绑定，包含用于检测修改的快照；不要把原始对象当成可以改写的工作草稿。

`validate_binding_snapshot(binding)` 只核对快照和报告转换需要的身份，不查外部来源。`validate_binding(binding, context, mapping)` 还重新查询当前来源、读取并比较真实 bytes，在查询/读取后再核对内存对象。纯输出转换通过不表示候选现在仍有效。

## 3. QualityCriticAgent

```python
agent = QualityCriticAgent(
    configured_provider,
    mapping_or_factory,
    on_release=publish_reviewed_candidate,
    on_feedback=return_feedback_to_a1,
)
output = await agent.run(inputs, context)
```

| 参数 | 默认/类型 | 规则 |
|---|---|---|
| provider | 必填，现有 Provider | live 必须对应 context 的 real；fake 对应 fake |
| mapping | 必填 | FrameworkInputMapping，或同步 `(inputs, context) -> mapping` 工厂；工厂支持每次选不同的明确候选 |
| on_release / on_feedback | 两个都必填，async | 均接收 FrameworkDispatchResult；由接入方实现真正交付/接收 |
| max_attempts | 2 | 1 或 2，由共享预算进一步限制 |
| max_retries | 1 | 0 或 1，仅 AuditEngine 控制重试 |
| request_timeout / total_timeout | 30 / 90 秒 | 请求及重试时间，不包含接收函数耗时 |
| on_update | null | 可选同步 `(started_attempts, records) -> None`，保存调用意图和计量 |
| delivery | null | 可选DeliveryGuard；配置同一持久账本后提供期限、去重及未知交付状态 |

本轮 FrameworkExecutor 支持现有框架的具体 RunContext。包装器不会创建/重置 RunSession、补造原始请求、登记候选或自动配置其他 Agent。单实例只能顺序运行；并发调用需另建实例。

直接 `agent.run()` 核对 provenance 的 run_id，但不会遍历运行时私有文件索引来确认 content 与某份导出来源相等。需要完整导出来源核验时，使用下文已验证的 `run_single_agent` 入口，或只接受已做等效核验的输入；不能仅凭直接调用成功声称完成了真实 Brief/Spec 内容来源验证。

两个接收函数获得：

```python
async def publish_reviewed_candidate(packet: FrameworkDispatchResult) -> None:
    # 调用方使用 packet.dispatch.audit.image.data 发布审过的同一份 bytes。
    # candidate/run/revision 来自 packet.binding，不重读可能被替换的路径。
    await supplied_publisher(packet)

async def return_feedback_to_a1(packet: FrameworkDispatchResult) -> None:
    # 完整问题/unknown 建议在 packet.dispatch.report。
    # packet.output 可直接序列化，包含报告和来源绑定。
    await supplied_a1_receiver(packet)
```

`FrameworkDispatchResult` 是 frozen dataclass，字段为 `dispatch: DispatchResult`、`binding: AuditBinding`、`output: AgentOutput`。接收时 dispatch.handler_status 为 pending。只有函数成功返回后，最终输出和 `agent.last_dispatch` 才标 completed；它不表示 A1 已读、修改或重生。

回调失败/取消直接传播，不自动重放副作用；`agent.last_dispatch` 保持 null。`agent.records`、`agent.framework_calls` 保存已发生的调用。配置 delivery 时，last_delivery 可保留uncertain或原记录；它不是本次成功或A1消费的证明。没有内置成品目录写入或 A1 网络发送，也没有默认空接收函数来伪装完成。

## 4. 外层输出

`to_framework_output(report, binding)` 返回现有 AgentOutput：

| 字段 | 内容 |
|---|---|
| content_format | `ContentFormat(name="a4.audit_report", version="3")`，常量 REPORT_FORMAT |
| content | 完整 AuditReportV3 JSON，没有删掉问题、未知或消耗 |
| artifacts | 空 tuple；不把已有候选冒充 A4 新产物 |
| review | 真实候选 ID/revision、当前 A4 reviewer_identity、三态 decision、原始顺序的全部关键词结果 |
| review.action / target | 均 null；A1 协调路径不同时触发框架直接返工 |
| review.details.binding | 绑定 v1、identity、source_format、candidate、完整依赖、authority、原始请求、constraint_ids、content/input 指纹 |
| review.details.supplemental_evidence | 显式EvidenceReviewer factory选择时提供P1证据/缺口/实际工具记录 |
| review.details.telemetry / diagnostics_display | 显式选择的P2内存测量与本地展示；原报告/诊断字段保留 |
| review.details.diagnostics | 独立DiagnosticsEnvelopeV1，typed问题/unknown对象、缺证据、建议及原报告字段 |

包装器输出还在 review.details 中加入 dispatch（route/action/recipient/handler_status/input_fingerprint）和 framework_calls（identity/executor_id/request_index/attempt/executed）。`repair_hint` 原样留在完整报告中，A1 后续如何处理由它的负责人决定。

配置DeliveryGuard后，最终details.delivery带独立DeliveryReceiptV1。来源与输出预检先完成，才claim并执行实际接收函数；重复/未知/冲突拒绝时不返回新的成功AgentOutput。

框架默认 unknown 和无动作 fail 仍会结束 needs_review；A4 接收函数已完成时，不能把此默认状态误读为未执行反馈。包装器没有修改共享工作流的路由或停止规则。

## 5. 预算和故障

FrameworkExecutor 每次尝试只调用一次现有 `context.call_model(kind="critic_vision")`。model_calls 与 critic_vision_calls 共享额度以运行时为准，不重复扣计数，也不通过 StandaloneExecutor 绕过它。Fake 同样消费模拟框架计数，但 A4 vision_calls 为 0、billing_status 为 not_applicable。

共享预算在 Provider 发起前拒绝时抛 A4 BudgetExceededError，不伪造 CallRecord 或收费尝试。发起后失败、取消、超时、无效观察均保留实际尝试，未知 usage 保持 unknown，不补零。非法响应元数据会抛固定 ProviderError，已消耗尝试仍入记录；独立合法的 TokenUsage 和其他元数据保留，坏值不进入日志。请求与重试超时受 A4 本地上限进一步限制。

`framework_calls` 是公开 identity + 每个 Executor 的随机 executor_id + request_index 的关联键，request_data 中也保存同一关联信息；它不是服务账单或框架事件 ID。不同 Executor 的编号不会混淆。现有框架取消时会计入消耗但未写 ModelCallEvent，A4 保留 CancelledError/调用关联记录；本轮不修改此框架行为。

输入/版本/格式错误属于接入错误，不伪装成图像 unknown。模型观察不合法的有限修正逻辑继续由已有 engine 处理。审核后、回调前会重查来源；此检查不是跨系统原子事务。接收函数应使用明确版本和已审核 bytes；配置P1共享账本后有跨路径去重和不确定状态保存，外部消费ACK及自动副作用重试仍不提供。

## 6. 注册与验证

接入方在现有注册接口中声明自己的真实输入格式和 A4 输出格式即可，不用改共享类型：

```python
registration = AgentRegistration(
    role=AgentRole.quality_critic,
    instance_factory=lambda config: configured_a4_agent,
    input_formats=frozenset({actual_input_format}),
    output_formats=frozenset({REPORT_FORMAT}),
)
```

离线测试覆盖注册和现有 `run_single_agent`，并创建登记候选和导出的来源 evidence/provenance；不能用一份无 provenance 的任意输入包声称通过该入口。

上述接入故障用例由 A4 开发者在本地验证，测试未包含在本次 GitHub 交付。公开源码的冷进程、目录迁移和 Fake 三态检查入口见 [P2 实施说明](A4_P2_IMPLEMENTATION.md)，验证范围见 [进度与交接](A4_PROGRESS_AND_NEXT_STEPS.md)。

当前全量回归结果见 [开发需求](A4_DEVELOPMENT_REQUIREMENTS.md)。独立 CLI、LocalManifest v1、AuditReportV3、DispatchResult v1 保持原接口；导入 poster_critic 独立核心不自动导入框架。

真实联调仍需要实际格式/引用、当前来源查询、生产交付/A1接收函数。P1已实现局部证据、冻结离线回放、结构化诊断和可选回调期限/去重；A4渲染归一化证据/OCR工具和预算包装已实现；真实Renderer格式/查询/生产消费及正式质量材料仍缺。新构造参数、last_evidence/last_telemetry和可选Provider政策见 [P1证据与质量](A4_P1_EVIDENCE_AND_QUALITY.md)和 [P2实施说明](A4_P2_IMPLEMENTATION.md)。
