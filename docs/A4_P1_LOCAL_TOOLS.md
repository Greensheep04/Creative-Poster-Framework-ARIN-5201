# A4 P1：本地可用工具与接口

更新：2026-10-14。分支 `feature/dev-2026-10-14`，尚未提交。本轮只修改 A4；共享框架、A1/A2/A3 和依赖文件保持原样。

本页保留首批 P1 的操作说明；新增渲染证据、OCR 和质量验证接口见 [P1证据与质量](A4_P1_EVIDENCE_AND_QUALITY.md)，P2 可选参数见 [实施说明](A4_P2_IMPLEMENTATION.md)。

## 1. 本轮实现范围

| P1 项目 | 当前可用能力 | 尚未完成 |
|---|---|---|
| P1-01 实际渲染证据 | A4归一化渲染证据与ink/text/glyph/overlap核对 | 组员实际格式、显式decoder与权威当前来源 |
| P1-02 局部证据/OCR | 本地裁剪、显式缩放、原图/输入绑定、双向坐标映射和像素核验 | 有界OCR适配和可选二次检查已实现，原生合成图OCR已实测；真实海报识别质量待验 |
| P1-03 结构化诊断 | 独立 DiagnosticsEnvelopeV1；CLI 可写旁路文件，框架输出含同一封装 | 真实 A1 消费函数，未代为修改 |
| P1-04 交付可靠性 | 配置共享 SQLite 账本后，回调期限、持久去重、冲突拒绝和显式结果核对 | 外部消费 ACK、跨系统原子事务及生产接收函数 |
| P1-05 冻结验证 | 可移植冻结包、代码/配置/图片/标签漂移检查、固定观察离线回放 | 冻结版真实模型质量实验、正式阈值和质量验收 |

原 LocalManifest v1、AuditReportV3、DispatchResult v1 和 CLI A1 反馈包的字段结构保留。所有新封装都有自己的 schema_version，不向原报告添加 diagnostics 字段。

## 2. 结构化诊断

源码：[structured_diagnostics.py](../src/poster_critic/structured_diagnostics.py)。

```python
envelope = structured_diagnostics(audit, report)
payload = envelope.model_dump(mode="json")
```

输入必须为同一份完整 AuditInputV1 和 AuditReportV3；重新校验真实图片、指纹、全部 ID/检查项及最终调用记录。函数不会发起模型请求，也不会解析中文诊断字符串。

| DiagnosticsEnvelopeV1 | 含义 |
|---|---|
| schema_version | 1 |
| sample_id / image_sha256 / input_fingerprint | 与原输入/报告一致的绑定 |
| decision | 原报告 pass/fail/unknown，不重新评定 |
| items | DiagnosticItem 数组 |
| human_uncertainties | 原 uncertainties 字符串，原样保留 |
| coverage / findings / calls | 原检查覆盖、问题和调用记录 |

DiagnosticItem 字段：code、status、subject_kind、subject_id（可 null）、check_ids、required、blocks_release、doubt、missing_evidence、suggestion、observed_evidence（默认 null）。subject_kind 为 keyword/constraint/copy/check/execution/report_uncertainty。对象 ID 来自实际字段；执行未完成或未定位疑点的 ID 为 null，不编造位置/根因/命令。

`blocks_release` 表示这项诊断是否阻断必需检查，不能代替报告 decision/major 问题规则。可选 unknown 不改成全局 unknown；fail 与未知同时存在时，两者均保留。无有效观察说明“审核未完成”和已记录错误，不猜图片缺陷。calls 中 pending、重复尝试或互相矛盾的状态会拒绝转换。

CLI 选择 `--structured-diagnostics` 后写 `<O>.diagnostics.json`。原 `<O>.a1.json` 保持 v1 字段，不要求旧接收方立刻支持新字段。框架包装输出通过 `review.details.diagnostics` 提供同一 v1 封装，完整报告仍在 content。

## 3. 持久交付与回调期限

源码：[delivery.py](../src/poster_critic/delivery.py)。只使用标准库 SQLite，无新增依赖。

```python
from pathlib import Path

guard = DeliveryGuard(Path("/RUN/shared-delivery.sqlite3"), handler_timeout=30.0)
result = await audit_and_dispatch(
    audit, engine,
    on_release=external_publish,
    on_feedback=external_a1_handler,
    delivery=guard,
)
```

`DeliveryGuard(ledger_path: Path, *, handler_timeout=30.0)`：路径必须为 Path，超时需为有限正数。`deliver(result, handler, *, candidate_identity=None)` 接收 pending 的 DispatchResult，默认候选身份为 audit.sample_id。框架包装使用 run_id/candidate_id/revision 组合，可通过 `QualityCriticAgent(..., delivery=guard)` 配置。

账本键绑定 candidate_identity、完整 input_fingerprint、image_sha256、execution_mode，**不依赖输出路径或 invocation**。不同调用者要使用同一持久账本路径才能共同去重；不同账本不提供全局去重。这个层保护交付，不跳过已经发生的重复审核调用。

| 状态/情况 | 行为 |
|---|---|
| 首次合法交付 | 原子提交 in_flight 后才调用接收函数 |
| 接收函数成功返回 | 写 completed，completion_source=handler_returned；不是 A1 消费 ACK |
| 已完成的重复交付 | DeliveryAlreadyHandledError，携带原 receipt；新请求不调用接收函数，不伪造新的完成标记 |
| 同键但证据/结论/模式路由矛盾 | DeliveryConflictError；不会覆盖旧结果 |
| 接收函数失败、取消、期限到达 | uncertain，不自动重试；可能已发生部分外部副作用 |
| 进程退出留下 in_flight | 保留，不按经过时间自动抢占或重放 |
| Fake pass | test_only，没有生产 claim，不调用任何接收函数 |

冲突检查包括实质报告证据、结论、model/prompt，忽略 calls 的时间/计量波动，但 receipt 保存原完整报告 hash。同一候选改输入或换模式会得到不同键；不能为了重试副作用随意修改身份。

期限是回调的期限，独立于模型请求预算。到期后请求取消，立即返回 DeliveryHandlerTimeoutError；回调若拒绝取消，仍被追踪，其迟到返回不会自动把 uncertain 改为完成。期限不能中断阻塞事件循环的同步代码，也不能撤销远端已经发生的副作用。

公共检查/恢复接口：

```python
key = delivery_key(pending_result)
receipt = guard.lookup(key)
history = guard.history(key)

# 仅在调用方已核实外部结果后调用；不会自动发起任何接收函数。
guard.reconcile(
    key, outcome="confirmed_not_applied",
    evidence="调用方已经取得的外部确认凭据",
    caller_confirmed=True,
)
```

outcome 仅接受 completed/confirmed_not_applied。前者记录外部交付完成证据，后者允许调用方随后**显式**重试；没有证据不开放重放。A4 保存证据声明，不独立认证远端状态；本进程仍运行的回调不能被此接口解除。跨进程调用方须确认其证据覆盖仍运行的外部工作。该接口是故障结果核对，不添加每张海报的人为质量审批。

CLI 用法：

```bash
PYTHONPATH=src python -m poster_critic.cli --input a4_input.json --out result.json --structured-diagnostics --delivery-ledger /RUN/shared-delivery.sqlite3 --handler-timeout 30
```

没有配置 delivery 时保留原调用行为；`--handler-timeout` 单独使用会拒绝。账本及 SQLite journal/wal/shm 必须与显式输入和整个输出族分开，路径别名也会检查，在审核前拒绝冲突。

正常受保护交付会写 `<O>.delivery.json`，含 request_status=handler_completed 和独立 DeliveryReceiptV1。被拒绝/失败的请求为 failed_or_blocked，携带已有 receipt 和 error_type；receipt.completed 可能属于**先前请求**，不代表此次请求成功。此时没有新的成功 dispatch 收据。Fake pass 不写生产交付收据。

DeliveryReceiptV1 包含：schema_version、delivery_key、candidate_identity、input/image 指纹、execution_mode、route/action/recipient、report_sha256、report_summary、state、attempt、claim_id、created_at/updated_at、completion_source、external_evidence、problem。问题只存安全错误类型，不存回调原始消息或响应 body。

框架包装器先完成来源重验和输出构造，再 claim/调用真实回调。最终输出在 `review.details.delivery` 保存 receipt；`agent.last_delivery` 在失败时可保留 uncertain 或已有记录，`last_dispatch` 仅成功时赋值，records/framework_calls 保留消耗。

## 4. 局部图像证据

源码：[evidence.py](../src/poster_critic/evidence.py)。

```python
crop = crop_evidence(audit, (40, 90, 680, 180), output_size=(1280, 180))
local_box = canvas_to_crop_bbox(crop, (50.0, 100.0, 100.0, 140.0))
canvas_box = crop_to_canvas_bbox(crop, local_box)
validate_crop_evidence(crop, audit)
```

输入 bbox 为原图整数 xyxy，必须完全位于 720×1280，不静默裁剪。缩放是显式选择，使用 LANCZOS；输出单边 ≤4096、总像素 ≤4 Mi、文件 ≤20 MiB。新 CropEvidenceV1 明确 evidence_kind=crop，含原图 digest、完整输入指纹、原始 bbox、输出尺寸、scale_x/scale_y 和自身 PNG hash。data 只在内存，JSON 不包含 bytes。

`validate_crop_evidence` 用当前原图重新产生裁剪/缩放结果，再比较绑定及 pixels/bytes，不能只改 metadata/hash 就伪装来源。坐标映射只接受图内且属于裁剪区域的框，不替调用者猜 OCR 位置。

```bash
PYTHONPATH=src python -m poster_critic.evidence --input a4_input.json --bbox 40 90 680 180 --size 1280 180 --out /RUN/title-evidence
```

`write_crop_evidence(crop, new_directory)` 只创建新目录，生成 crop.png，最后写 evidence.json 作为完成标记。`read_crop_evidence(directory, audit)` 拒绝不完整目录、越界文件名、symlink/祖先别名、FIFO/其他非普通文件，并核验当前父图。裁剪图不冒充完整海报，也不直接改变 pass/fail/unknown；OCR/实际视觉复核及其模型/工具预算尚未接入。

## 5. 冻结评估和离线回放

源码：[frozen_evaluation.py](../src/poster_critic/frozen_evaluation.py)。

```bash
PYTHONPATH=src python -m poster_critic.frozen_evaluation freeze --dataset /RUN/data/dataset.json --out /RUN/frozen
PYTHONPATH=src python -m poster_critic.frozen_evaluation verify --bundle /RUN/frozen
PYTHONPATH=src python -m poster_critic.frozen_evaluation replay --bundle /RUN/frozen --out /RUN/offline-replay --split dev --repeats 2
```

源 dataset 使用现有 Dataset v1，需完整 sample_id/source/source_group/split/expected labels；输入、图片、固定观察的引用为 dataset 根目录内的相对路径，拒绝绝对引用、`..`、symlink 和 dev/validation 图像泄漏。旧跨目录 replay_dataset 需先准备包含其引用文件的独立目录，工具不自动扫描/复制整个项目。

冻结包包含：dataset.json、每个规范化 LocalManifest/图片/可选固定观察、可选原标签和安全 ProviderConfig/ModelCatalog，以及明确列出的当前 A4 代码和 v2/v3/v4 提示。包含本轮 P1 模块。manifest.json 列每个文件的 hash/size/kind、输入/图像指纹、配置路径及代码清单，seal 用于内容一致性校验，**不是签名或外部来源真实性证明**。

`freeze_evaluation(dataset_path, output_dir, *, config_path=None, label_source=None)` 的 label_source 为现有 LabelSheet 路径，按当前 input/image fingerprint 验证，不要求再找第二位标注者填写。输出目录不可覆盖；发布失败保留可识别的不完整目录。

`verify_frozen_evaluation(bundle_dir, *, config_path=None)` 拒绝新增文件、hash/标签绑定/输入/代码漂移；冻结了配置就必须显式传入当前 config_path，核对其漂移，不读取 API key 环境变量。

`replay_frozen_evaluation(bundle_dir, output_dir, *, split="dev", repeats=1, max_attempts=2, max_total_attempts=24, config_path=None)` 只用存储的 Fake observations。缺固定观察时可以冻结，但无法离线回放。无效观察、unknown、执行/预算失败保留在分母；记录重复一致性和实际离线时间。Fake 不具有真实 token 用量，token_accounting 标明 not_assessed_for_fixed_observations，不把零次 live 调用当成真实成本成绩。

回放前后都检查冻结数据/代码/配置；中途变化不发布冻结完成收据，已有 run/消耗保留。结果始终 software_demo_only、formal_acceptance=not_assessed、quality_thresholds=not_agreed，不借用旧 pilot 的阈值宣称正式质量通过。

## 6. 测试与下一步

本轮故障测试覆盖跨进程并发 claim/崩溃、不同输出路径重复、拒绝取消的回调、显式恢复、非法账本/路径冲突、诊断字段/兼容性、伪造裁剪像素、冻结篡改/搬移/配置漂移和无效观察分母。最终测试结果更新在 [开发需求](A4_DEVELOPMENT_REQUIREMENTS.md)。

这些能力可以用本地输入独立运行。后续仍只做 A4：拿到实际渲染证据格式后编写只读适配器，需要 OCR 时显式选工具并接预算，最后用冻结数据/模型/配置完成真实质量实验。其他 Agent 的输入产出及 A1 消费实现由其负责人提供。
