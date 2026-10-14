# A4 开发进度、后续方向与资料交接

更新日期：**2026-10-14**。开发分支：`feature/dev-2026-10-14`，当前工作分支基线为 `13b4b91d206e39f5538e85ead4499deee6e22ad9`。该基线不称为最新 `main`；本次保存当前 A4 开发成果，不合入组员的新改动。

**当前结论：A4 不依赖组员材料的 P1/P2 软件实现已完成，可交付源码；真实团队联调、正式质量验收和真实性能对照仍未完成。** 本页保存当前进度、可用接口及下一步所需资料，不把软件测试通过写成模型质量通过。

## 1. 开发责任与固定行为

本开发者**只负责 A4 Quality Critic**，包括输入适配、审核、证据核验、诊断、分流、计量、交付保护和验证工具。A1/A2/A3、Renderer、共享框架、共享打包配置及依赖文件由相应负责人维护；本次不替他们实现业务逻辑。

A1/A2/A3 各自产出自己的信息；接入程序读取同一候选版本的原始要求、文案、布局、素材和最终图片，汇总后交给 A4。A4 不要求多个 Agent 逐层改写原始要求。

| 审核结果 | 当前固定行为 | 接收方需要处理的内容 |
|---|---|---|
| live `pass` | 来源与报告绑定核验通过后，自动调用成品交付回调 | 发布本次已审核的同一份图片 bytes 和审核记录，不重新读取可能变化的图片路径 |
| `fail` | 不放行，完整反馈只交给 A1 | 已确认问题、证据、建议、保留要求及完整输入；后续任务由 A1 协调 |
| `unknown` | 不放行，完整反馈只交给 A1 | 对象、大致疑点、缺失证据、建议核实方向；取得证据后再审 |
| Fake `pass` | `test_only`，交付和 A1 回调均不调用 | 只能证明离线软件行为 |
| 输入、版本、预算或交付执行错误 | 按对应错误处理，保留已发生调用和交付状态 | 不能把接入错误伪装成图片 `unknown` 或新的成功交付 |

`unknown` 建议不是执行命令，不能凭看不清文字就认定有错字或安排整图重生。已知严重缺陷与关键未知并存时保留 `fail` 和未知诊断，不生成猜测性修复操作。框架输出的 `review.action`、`review.target` 均为 `null`，避免 A1 收到回调后再触发另一条直接返工路径。

运行时审核保持自动执行，不加入逐张人工审批。预算用尽或仍无法可靠判断时保存原因并返回；不静默换模型、换区域、降标准或重试可能已有副作用的回调。

## 2. P1 逐项进度

| 需求 | 已实现的软件与入口 | 软件验证覆盖 | 真实验收仍需什么 |
|---|---|---|---|
| A4-P1-01 渲染证据 | [render_evidence.py](../src/poster_critic/render_evidence.py)：显式 reader/decoder/current-source、归一化证据、ink/text/glyph/overlap 核验 | 错图、指纹漂移、过期来源、错误引用、缺事实和严重缺陷处理 | 实际 RenderManifest、字段语义、可信当前来源查询；不替 Renderer 生成事实 |
| A4-P1-02 局部证据/OCR | [evidence.py](../src/poster_critic/evidence.py)、[ocr.py](../src/poster_critic/ocr.py)、[macos_vision.py](../src/poster_critic/macos_vision.py)：裁剪绑定、坐标映射、有界工具调用 | 裁剪像素/坐标伪造、取消/超时/洪泛、工具和模型分别计数；真实 Apple Vision 合成图接入 | 选定审核路径的真实海报 OCR 质量和共享工具预算接入；不要求两个 OCR 后端都上线 |
| A4-P1-03 结构化诊断 | [structured_diagnostics.py](../src/poster_critic/structured_diagnostics.py)：`DiagnosticsEnvelopeV1`，对象定位、缺证据、建议，保留原结论/字符串 | required 与 optional unknown、已知 fail 伴随未知、无有效观察、typed ID 投影 | 实际 A1 接收函数及消费结果；A4 不实现 A1 的任务分配 |
| A4-P1-04 交付可靠性 | [delivery.py](../src/poster_critic/delivery.py)：共享 SQLite 账本、回调期限、跨路径去重、冲突拒绝、显式恢复 | 并发 claim、进程退出、取消、迟到回调、重复提交、uncertain 恢复 | 生产发布/A1 handler 和外部结果凭据；handler 返回不等于 A1 已消费 |
| A4-P1-05 冻结质量验证 | [quality_validation.py](../src/poster_critic/quality_validation.py)：V2 冻结、运行、重验，输入/图片/源码/政策/工具绑定 | 漂移拒绝、完整计划分母、失败/缺结果、同次报告与调用记录、只读历史重验 | 冻结 validation、完整独立标签、运行前确认的门槛、真实服务固定计划实验 |

证据读取、OCR 和附加检查通过不会独自把主审核 `unknown` 提升为 `pass`。必需证据缺失保持阻断；一次 OCR 冲突先形成疑点，不能自动认定确定错字。

当前有 **6 张真实开发集人工参考标签**，可以继续用于开发评估。它们没有被改成冻结验证集；不要求第二名标注者重复填写，也不把此前建议的 24 张样本数变成强制条件。正式数据规模、覆盖和门槛需事先确定。

## 3. P2 逐项进度

| 需求 | 已实现的软件与入口 | 当前边界/下一步 |
|---|---|---|
| P2-01a 完整实验身份 | [experiment_identity.py](../src/poster_critic/experiment_identity.py)：源码/资源/prompt/config/capability/语言/附加及执行政策身份 | Git SHA 不代替实际源码 bytes；旧包保持原 seal，执行仍需匹配其原运行时 |
| P2-01b 遥测 | [telemetry.py](../src/poster_critic/telemetry.py)：一次 invocation 的阶段时间、实际尝试、绑定和交付状态 | 可选启用，不保存 prompt/原文案/图片 bytes/原始异常正文；不编造网络细分或未知 usage |
| P2-01c 对照汇总 | [benchmark.py](../src/poster_critic/benchmark.py)、[benchmark_quality.py](../src/poster_critic/benchmark_quality.py)、[quality_archive.py](../src/poster_critic/quality_archive.py)：固定 case、两臂质量 plan/link、完整档案重验 | 只核验已存在测量，不自动跑模型；Fake/draft 可验证内容连接，质量仍 `not_assessed` |
| P2-02 发送前校验 | [provider_capabilities.py](../src/poster_critic/provider_capabilities.py)：具体 model/base/region/组合及实际图片、Data URI、UTF-8 body 校验 | 未核实的必需限额拒绝发送；模板和官方文档不证明当前账号可用 |
| P2-03 连接生命周期 | [providers/chat.py](../src/poster_critic/providers/chat.py)：显式 client 所有权、顺序复用和关闭 | `per_request`/`scoped_owned` 显式选择；没有隐藏重试或真实提速结论，默认行为未切换 |
| P2-04 文字政策 | [language_profiles.py](../src/poster_critic/language_profiles.py)：NFC/显式 typographic、诊断展示政策及反例 | exact 不放宽；金额/日期、全半角、繁简、同形及零宽字符保留差异；未证明中文识别质量 |
| P2-05 源码复现 | [environment.py](../src/poster_critic/environment.py)、[distribution.py](../src/poster_critic/distribution.py)：库存、ZIP/资源核验、冷进程及目录迁移 | 已验 Mac 与明确 Linux 容器环境；源码交付需要已有依赖，不等于正式 wheel 安装 |
| P2-06 新证据/高级提示 | 现有显式 decoder 和有证据的安全建议机制可复用 | 条件扩展，不计首批必做；实际新 schema/来源/预算/质量需求到位后才细化 |
| P2-07 正式分发 | 已有 wheel 可做源码/资源/元数据政策检查 | 条件扩展，不计首批必做；负责人先决定发行元数据和改动范围，再构建与干净环境安装验收 |

首批独立软件实现已完成。完整目标仍缺外部真实验收材料；当前没有据软件测试修改生产优化默认项，也没有承诺性能提升百分比。

## 4. 对组员公开的输入接口

### 4.1 独立入口：LocalManifest v1

完整约定和示例见 [团队输入/输出合同](QUALITY_CRITIC_TEAM_CONTRACT.md)。入口为 [local_input.py](../src/poster_critic/adapters/local_input.py) 的 `load_input`，加载后得到 `AuditInputV1`，不直接把 JSON 路径当成图片身份。

| 字段 | 要求 | 主要提供者 |
|---|---|---|
| `schema_version` | 整数 `1`，默认 1 | 接入程序 |
| `sample_id` | 当前候选审核标识，非空 | 接入程序 |
| `keywords[]` | 2–5 个原始 `{keyword_id, text}`，ID 唯一、顺序保留 | A1 的原始请求记录 |
| `user_constraints[]` | `{constraint_id, name, value}`；没有约束用 `[]`，同名约束也逐项保留 | 原始请求/接入程序 |
| `image.path` | 单帧 PNG/JPEG，720×1280，≤20 MiB；相对输入 JSON 所在目录 | A3/Renderer 的当前成品 |
| `approved_copy[]` | `{copy_id, text, required, match_policy}`；后两项默认 true/exact | A1 的正式文案 |
| `layout_context.elements[]` | `{element_id, bbox_xyxy, copy_id}`；最终画布像素 xyxy，左上角原点 | A2 当前布局 |
| `asset_context.assets[]` | `{asset_key, element_ids, description}` | A3 当前素材记录 |
| `design_context` | 可选字符串，未提供可为 null | A1/A2 |
| `render_context.messages[]` | 可选诊断字符串，不代替实际渲染证据 | Renderer |
| `required_checks[]` | 四项基础检查必须保留；按需要增加文字准确性/布局几何 | 接入程序/审核政策 |

基础检查为 `keyword_alignment`、`user_constraints`、`text_readability`、`layout_quality`；附加为 `text_accuracy`、`layout_geometry`。`required_text` 约束必须是非空字符串，A4 补成 `copy_id=constraint:<constraint_id>` 的 required/exact 文案。`approved_copy=null` 表示未提供，`[]` 表示没有列出的文案。

JSON 禁止额外字段。外部 run/candidate/revision 不直接塞进 LocalManifest；框架身份另走下面的绑定接口。若提供 layout/asset 对象，必须带 elements/assets，可为 `[]`。

```json
{
  "schema_version": 1,
  "sample_id": "team-candidate-r1",
  "keywords": [
    {"keyword_id": "kw-coffee", "text": "咖啡"},
    {"keyword_id": "kw-plants", "text": "植物"}
  ],
  "user_constraints": [],
  "image": {"path": "poster.png"},
  "approved_copy": [
    {"copy_id": "title", "text": "COFFEE & PLANTS", "required": true, "match_policy": "exact"}
  ],
  "layout_context": {
    "elements": [
      {"element_id": "title", "copy_id": "title", "bbox_xyxy": [40, 90, 680, 180]}
    ]
  }
}
```

示例需另配合法真实图片才可运行；它不是组员真实格式或质量实验。

### 4.2 框架入口：显式格式、候选和当前来源

[框架接入说明](A4_FRAMEWORK_INTEGRATION.md)及 [framework_input.py](../src/poster_critic/adapters/framework_input.py) 为详细依据。

`FrameworkInputMapping(source_format, candidate, decode, current_sources)` 的四个参数均需显式提供。`source_format` 与实际 `AgentInput.content_format` 相同；`candidate` 是当前 run 的明确 `ArtifactReference`，`purpose=candidate`，不能取 `files[0]` 猜候选。

`decode(inputs)` 返回 `AuditContextV1`：仅 `approved_copy/design_context/layout_context/asset_context/render_context/required_checks`。原始 keywords/user_constraints 来自运行初始记录，由 A4 核对并保留，decoder 不覆盖原要求。

`current_sources(context)` 返回 `CurrentAuditSources(authority, candidate, version_dependencies)`。依赖为完整的 `VersionDependency(artifact_id, revision)` tuple，包含候选，不混修订、不重复；查询必须实际读取可信当前状态，不能把输入声明原样返回来证明“最新”。

`build_audit_input(inputs, context, mapping)` 返回 `(AuditInputV1, AuditBinding)`。绑定保存 candidate、完整依赖、原始请求、格式、authority、constraint_ids、content/input 指纹和真实图像。审核后、交付前再次核验；接收方用明确版本和已审 bytes 发布。

渲染证据另走 `RenderEvidenceMapping(source_format, reference, read, decode, current_source)`。reference 为 `RenderEvidenceReference(evidence_id, revision, sha256)`；decoder 返回 A4 `RenderEvidenceV1`；当前查询返回 `CurrentRenderEvidenceSource`。这只是 A4 归一化接口，不要求 Renderer 改为某个新业务 schema。

## 5. A4 返回什么，A1 怎样接收

### 5.1 原报告与回调

完整 `AuditReportV3` 含 `schema_version/sample_id/image_sha256/input_fingerprint/decision`、`keyword_checks/constraint_checks/text_checks/coverage/issues/uncertainties/repair_hint`，以及 `model/prompt_version/execution_mode/calls/vision_calls`。机器字段来源见 [contracts.py](../src/poster_critic/contracts.py)。

`issues[]` 保存 `code/severity/source/evidence/suggestion/element_id/bbox_xyxy`；位置无法确定时为 null。`uncertainties[]` 保存原诊断字符串。`repair_hint` 为 null 或 `{stage, element_id, asset_key, reason, preserve}`，不是 A4 已执行的修改。

独立入口 `audit_and_dispatch(audit, engine, on_release=..., on_feedback=..., delivery=...)` 的两种 async handler 均接收 `DispatchResult`：

| 字段 | 值/用途 |
|---|---|
| `schema_version` | 1，内部报告仍为 v3 |
| `route` | release/a1/test_only |
| `action` | release_candidate/return_to_a1/test_only，与 route 对应 |
| `recipient` | a1 时 creative_director，其余 null |
| `handler_status` | handler 内为 pending；成功返回后 completed；test_only 为 not_called |
| `report`、`audit` | 完整报告、完整本次上下文；已审图片在 `audit.image.data` |
| `input_fingerprint` | 绑定图片和完整要求，禁止旧结果套到新文案/布局 |

`completed` 仅证明该 handler 成功返回，不能证明 A1 已读、已修改或已重生。生产消费凭据由 A1/发布方提供；A4 不补造 ACK。回调异常/取消不自动重放副作用，需保留 ledger/calls 后核实外部状态。

### 5.2 框架输出与 unknown 诊断

`QualityCriticAgent(provider, mapping, on_release=..., on_feedback=...)` 的 handler 接收 `FrameworkDispatchResult(dispatch, binding, output)`。完整报告在 `output.content`，格式为 `ContentFormat(name="a4.audit_report", version="3")`；`review` 含真实候选 ID/revision、审核身份、三态和全部原始关键词结果。

`review.details` 保存 binding、diagnostics、dispatch、framework_calls；显式选择时另有 supplemental_evidence、telemetry、diagnostics_display、delivery。其他 Agent 的共享顶层类型不改。

`DiagnosticsEnvelopeV1` 包含 `schema_version/sample_id/image_sha256/input_fingerprint/decision/items/human_uncertainties/coverage/findings/calls`。每个 `DiagnosticItem` 提供：

- `code/status/subject_kind/subject_id/check_ids`：由 typed 字段定位对象，不解析中文猜 ID。
- `required/blocks_release`：指出缺口是否影响必需检查，不能替代全局 decision。
- `doubt/missing_evidence/suggestion/observed_evidence`：说明大致问题、还缺什么、可核实方向和已有观察。

例如标题无法可靠转录，返回 `subject_kind=copy`、实际 `subject_id`、`status=unknown` 和文字检查 ID，说明需要核对当前文案及可读渲染证据；不返回“确定错字”或自动执行重生命令。

CLI 可用 `--structured-diagnostics` 输出独立 `<O>.diagnostics.json`。原 A1 v1 包保持兼容：`recipient/status/candidate_path/input_fingerprint/audit/report`；状态 `awaiting_a1` 表示等待消费，不表示 A1 已处理。新增旁路诊断不塞进旧包字段。

## 6. 已执行的验证与证据限制

| 验证项 | 实际结果 | 可支持的结论 |
|---|---|---|
| A4 回归 | 1470 项通过 | 软件合同、绑定、故障和生命周期；不是视觉准确率 |
| 共享框架回归 | 136 项另行通过，总计 1606 | 框架已有接口通过回归检查；不是完整团队联调 |
| Mac 源码交付检查 | 41 项冷进程/实际迁移 +9 项完整 draft/Fake 质量连接 | 指定 macOS arm64/CPython 3.11 环境的源码行为 |
| Linux 源码交付检查 | 41+9 项，共 50；Mac/Linux 合计 100 项新检查 | Linux amd64/CPython 3.11.17 容器源码行为；该 amd64 用户空间在 ARM 主机仿真 |
| 两平台质量连接 | 各有 12 次实际 Fake 审核，连接 verified，质量 not_assessed | 同次报告/calls/telemetry/冻结计划可以绑定，不是模型质量通过 |
| 实际 Apple Vision | 合成图文字识别接入成功 | 工具可执行；不是正常/缺陷海报识别质量 |

1606 项回归来自此前运行，本轮仅在**实际源码指纹精确相同**后复用结果，未宣称重新执行。源码交付检查则对交付快照新执行。详细收据和测试原始输出保存在本地，GitHub 文档提供结果摘要，源码不携带这些私人运行资料。

本轮模型网络请求及付费调用均为 0。Linux 准备阶段下载公共 Python 镜像和依赖，审核容器禁网、源码及依赖只读；不把环境下载算为模型请求。该验证不证明原生硬件性能、Linux 框架接入、真正 Tesseract 识别质量或正式 wheel 安装。

当前正式质量验收和真实性能收益均为 **not_assessed**。source-ready 只表示可核验源码及软件行为已就绪，不表示独立无依赖安装、当前账号 live 协议或生产效果通过。

## 7. 所需资料清单

用户已确认目前只有 A4 完成，下面的生产与正式验收资料**暂无**。提供者列说明责任归属，不要求本开发者替其他模块实现；资料可先给脱敏本地样例和接口说明，密钥不得写进文档、JSON 或 Git。

| 资料 | 由谁提供 | 最小内容/格式 | 用途与缺失行为 | 收到后 A4 下一步及验收产物 |
|---|---|---|---|---|
| 当前候选与原始要求 | A1/A2/A3 负责人及接入方 | 原始 keyword/约束、正式 copy、当前布局/素材、720×1280 PNG/JPEG；实际 AgentInput/ContentFormat、明确 candidate/run/revision 和完整 dependencies | 保留原要求并绑定同一候选；缺实际格式不能宣称真实 decoder 已接入 | A4 编写只读 decoder；核验原词/顺序/固定文案/ID/坐标及真实图；输出绑定与三态报告 |
| 实际 RenderManifest | Renderer 负责人 | 实际文件 bytes、source_format、evidence_id/revision/SHA；字段说明和一份实际成品。归一化需 parent_image_sha256/input_fingerprint、source_revisions、producer_id/renderer_version/font_config_revision、canvas/color_mode、element/copy ID、spec/ink bbox、draw_state、final_text/lines/breaks、copy_extent、font/glyph/overlap 事实；缺事实保留 null/unknown | 核对实际文字/框/字形；未知字段不猜，缺必需事实不放行，干净 metadata 不独立证明 pixels pass | 实现 A4 decoder；当前/过期/缺事实样例分别重验，保存 findings/gaps 和有证据的建议 |
| 权威当前版本查询 | 运行时/接入方负责人 | 真实只读入口与查询语义；`CurrentAuditSources` 的 authority/candidate/完整 version_dependencies；证据查询另返回 `CurrentRenderEvidenceSource` 的 authority/producer/renderer/font/reference/source_revisions/parent_image_sha256/input_fingerprint | 读图前后及交付前核对来源；没有查询或只回显输入属于接入错误，不能证明最新 | 绑定实际查询；测试换候选/换依赖/换字体版本拒绝，输出可重验来源记录 |
| 生产发布与 A1 handler | 发布接入方、A1 负责人 | 两个 async handler；明确使用 DispatchResult 或 FrameworkDispatchResult；传入同一受保护账本和 callback 期限；说明真实结果查询与失败语义 | pass 发布 bytes，fail/unknown 给 A1；当前只有受控 callback，不能宣称生产消费 | 接现有函数，验证三个结果的唯一去向、hash、回调计数、异常/重复/uncertain；保存实际交付及消费结果 |
| 实际消费结果/恢复凭据 | A1/发布方负责人 | 对当前 run/candidate/revision/input 指纹的实际处理结果或明确未应用证据；能与本次反馈/发布记录对应 | handler completed 不等于消费 ACK；uncertain 不按时间重试 | 对账 `DeliveryReceiptV1`；有凭据时显式 reconcile；确认未应用才允许调用方显式重试 |
| 冻结 validation 与独立标签 | 项目验证负责人/标注者 | Dataset v1（purpose=human_labeled、split=validation、sample_id/input_path/source_group/source/expected_decision/expected_issue_codes），完整 LocalManifest/图片；LabelSheet v1（annotator/items），每项 sample_id/input_sha256/image_sha256/decision/issue_codes/evidence，可选 regions/repair_stages；明确 label_kind=independent_human | 防开发/验证混用，逐输入/图片绑定；6 张 dev 不替正式 validation，模型代标不替独立人工 | 验证覆盖和 hash、冻结 V2；运行后保留全部计划样本/重复、失败和缺结果，再评估质量 |
| 运行前约定质量门槛 | 项目负责人及验证负责人 | ThresholdProfileV1：profile_id/state/validation_samples/repeats/gates；正式 agreed 还需 agreed_by/agreed_at/agreement_reference/agreement_evidence_sha256 和实际 agreement 文件 | gates 覆盖 detection、false-pass、false-fail、unknown/执行失败、首次有效；两轮加一致性。draft 仅内容核验，不作正式通过 | 冻结原门槛和确认材料；同一计划重验后形成 passed/failed/not_assessed 与各指标分子/分母 |
| 当前账号 live 协议及预算 | 账号/服务配置负责人 | 明确 ProviderConfig、model/base_url/region/key 类型、现行官方来源、已核实请求/输出限额与响应/usage 政策；sealed ProviderCapabilityV1、允许 live 的执行选择和总尝试/期限预算；密钥只通过环境注入 | 文档/模板/旧 HTTP 200 不证明当前账号和组合；必需限额 unverified 拒绝发送，不自动换 Provider | 确认 exact 配置，先有限协议检查，再固定 V2 质量路径/调用预算；保留实际 calls/usage/错误，不作无限重试 |
| 两臂真实比较材料 | 实验负责人/A4 侧按确认计划执行 | 固定 baseline/candidate 实际源码和政策 identity、同一数据/独立标签/门槛、BenchmarkSpecV1、重复/预热/顺序/总预算；测前 quality plan，测后同次 telemetry/report/calls 与 link | 缺数据/协议时真实 gain 未评估；不同轮结果不能拼成一次对照，缺记录不补零 | 先冻结 plan，执行两臂后只读重验汇总；交付质量、成功/失败 latency、median/p95、实际尝试和已知/未知用量 |
| 正式 wheel 决策（条件项） | 仓库负责人 | 发行 name/version/requires_python/requires_dist/provides_extra、共享或独立打包方案、文件和公开交付范围 | 未决定前仅源码交付，不改共享 metadata/lock；现有 wheel 检查不等于安装成功 | 负责人授权范围内另做构建、元数据/资源核验及干净环境安装；单独留正式分发收据 |

这里的格式是已有 A4 接口或其所需原材料，不是给 A1/A2/A3 新增业务 schema。可先提供一份最小真实样例；A4 再按样例决定必要的 decoder，不要求先完成所有条件扩展。

## 8. 后续开发与验收顺序

1. **先接真实候选和来源查询。** 核对外部格式、原始要求、明确 candidate/完整依赖、真实 bytes。产物：A4 只读适配器和来源绑定记录；验收：旧候选、混修订、错误 hash、审核中更换来源均拒绝。
2. **接生产发布和 A1 接收。** 沿用已有三态及同一账本。产物：真实 dispatch/delivery 和接收方消费凭据；验收：pass 只发布已审 bytes，fail/unknown 只送 A1，重复/异常/uncertain 无假完成或自动重放。
3. **冻结 validation、独立标签和门槛。** 保留 6 张 dev 用途；确认正式样本覆盖、1/2 次重复和预算。产物：V2 冻结包、标签/门槛/确认文件 hash；验收：任一图片、输入、标签、源码或政策漂移即拒绝。
4. **做有界真实协议与正式质量实验。** 显式当前 capability、区域和执行选择；验证仅覆盖冻结的 primary-only 或已选 OCR 路径。产物：quality_start/execution/receipt、完整报告/calls/工具记录；验收：缺结果留分母，Fake/Mock/未证真实 transport 不给正式 pass。
5. **有质量依据后再测优化候选。** 测前固定两臂 plan，逐次保存同次 measurement，先看质量是否达到共同门槛，再看真实延迟/使用量。产物：BenchmarkSummaryV1 和 quality_evidence；验收：不隐藏失败或未知消耗，不要求实验必须得到提速结果。
6. **按实际需要决定条件扩展。** 新 Renderer/OCR 证据格式和高级 hints 需明确来源与质量价值；正式 wheel 先由负责人决定 metadata。产物：单独范围说明、验收和分发记录；不得据此接管 A1/A2/A3。

已有工具可复现源码交付检查，所有输出使用新的目录：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.distribution snapshot --package-root src/poster_critic --out NEW_BUNDLE --doc docs/A4_P1_EVIDENCE_AND_QUALITY.md --doc docs/A4_P2_IMPLEMENTATION.md
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.distribution verify --bundle NEW_BUNDLE/a4-source.zip --receipt NEW_BUNDLE/archive_receipt.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.distribution smoke --bundle-root NEW_BUNDLE/snapshot --out NEW_SMOKE
```

这些检查要求已有适用依赖；合成图/Fake/受控回调仅做软件验证，不调用收费模型。snapshot 只接受其既有文档白名单，本进度文档作为 GitHub 文档独立保存，不加入旧源码包或重写旧收据。完整操作与输出边界见 [P1 本地工具](A4_P1_LOCAL_TOOLS.md)、[P1 证据与质量](A4_P1_EVIDENCE_AND_QUALITY.md)、[P2 实施说明](A4_P2_IMPLEMENTATION.md)。

## 9. 组员阅读入口

- 只看我需要什么和我返回什么：[团队接口合同](QUALITY_CRITIC_TEAM_CONTRACT.md)。
- 接当前框架和真实回调：[A4 框架接入](A4_FRAMEWORK_INTEGRATION.md)。
- 接 Renderer/OCR，或准备正式质量材料：[P1 证据与质量接口](A4_P1_EVIDENCE_AND_QUALITY.md)。
- 启用遥测、模型预检、连接政策和对照汇总：[P2 实现与调用](A4_P2_IMPLEMENTATION.md)。
- 核对原始任务和完成条件：[总体开发需求](A4_DEVELOPMENT_REQUIREMENTS.md)、[P2 详细开发需求](A4_P2_DEVELOPMENT_REQUIREMENTS.md)。

目前最小下一步是提供**一份真实候选/RenderManifest 及可信当前版本查询说明**。A4 侧先完成这个接入，再按现有接口接生产回调；这不要求 A4 开发者修改其他模块。
