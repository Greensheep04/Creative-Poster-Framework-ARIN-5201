# A4 P1：渲染证据、OCR 与正式质量验证接口

更新：2026-10-14。只涉及 A4，当前实现位于 `feature/dev-2026-10-14`，未提交。原输入、报告、分流、A1 包保持兼容。

A4 侧证据读取、实际 OCR 适配、附加检查和冻结验证工具已实现。组员尚未提供实际 Renderer 格式、权威当前版本查询、生产回调、完整独立标签或共同确认的阈值，所以真实团队联调和正式质量验收仍未完成。

## 1. 渲染证据：接入方需要提供什么

入口：[render_evidence.py](../src/poster_critic/render_evidence.py)。A4 定义自己的归一化对象，不替 Renderer 规定业务 schema。

```python
bound = read_render_evidence(audit, mapping)
checked = validate_render_evidence(audit, bound)
analysis = analyze_render_evidence(audit, checked)
```

`mapping` 为 `RenderEvidenceMapping`，必须显式给出 `source_format`、证据文件 `reference`、`read(reference)`、`decode(bytes)` 和 `current_source()`。decoder 返回 A4 `RenderEvidenceV1`；查询返回 `CurrentRenderEvidenceSource`。查询必须来自接入方可信的当前状态，不是复述输入中自称的版本。

| 输入 | A4 用途 |
|---|---|
| 实际证据 bytes、文件 SHA、evidence_id/revision/source_format | 核验读取内容、格式与来源身份 |
| image_sha256 / input_fingerprint、当前候选与证据版本 | 确认这份证据对应正在审的原图和完整输入 |
| authority / producer_id / renderer_version / font_config_revision | 重查当前来源、渲染器与字体配置，拒绝过期证据 |
| canvas / color_mode | 与真实图片解码尺寸及颜色模式相符 |
| 每个 element_id / copy_id、实际 ink bounds | 对应原布局和正式文案，检测实际出界或重叠 |
| final_text / final_lines / line_breaks、明确换行政策 | 检查实际排版文案，没有字段不能猜正确 |
| 字体 SHA/字号/字重、missing_glyphs、允许重叠关系 | 对已声明证据检查字形及遮挡，没有证据保留缺口 |

输出是绑定证据和 `RenderEvidenceAnalysis`，含确定发现、缺失事实和安全建议。干净的 Renderer 元数据不能独立证明像素质量 pass；缺事实保留 unknown；过期版本、错误图片或内容指纹会直接拒绝。只有当前、可重验的明确事实才能产生 render 建议。A4 不执行建议，返回 A1 处理。

## 2. OCR：显式选择工具，不做自动回退

两种适配入口：[ocr.py](../src/poster_critic/ocr.py)、[macos_vision.py](../src/poster_critic/macos_vision.py)。调用都接受当前 `AuditInputV1`、由 `crop_evidence` 生成并核验的区域、共享有界工具预算。

```python
attempt = await TesseractOCR(config).observe(audit, crop, budget)
# 或在 macOS 显式选择已编译的 Apple Vision helper
attempt = await VisionOCR(config).observe(audit, crop, budget)
```

| 后端 | 必需输入 | 实际记录 |
|---|---|---|
| Tesseract | 已安装 executable 的绝对路径、tessdata_dir、语言顺序、PSM/OEM、期限、输出限额 | executable SHA、实际版本、每份 traineddata SHA、裁剪/父图/完整输入绑定、版本和 OCR 子进程计数 |
| Apple Vision | 显式 helper_directory、语言顺序、期限、输出限额 | helper/source SHA、OS/build、Vision request revision、选定语言、实际子进程计数；系统模型权重不可见，model_data_sha256 为 null |

`status` 为 observed/failed/unavailable/cancelled/budget_refused。空文本、低置信度、工具不可用不能当 pass。取消、超时、输出洪泛或子进程失败保留实际消耗，终止并回收进程；不另选其他工具。预期文案不会传给 OCR。

Vision 保存原始 bottom-left bbox 和转换后的局部/画布坐标。政策 `vision-bbox-epsilon-1e-9-v1` 只校正归一化边界不超过 1e-9 的浮点误差，保留 `raw_bbox_bottom_left` 和 `coordinate_adjustment`；明显越界仍拒绝，没有缩放或裁剪位置猜测。

本机真实生成图片的 OCR 已读出 `POSTER TEST 2026`，1 次 OCR 进程。Codex 沙箱内首次识别失败，获准的沙箱外检查成功。主审核使用 Fake，结果仍为 test_only；这证明工具接入，不证明真实海报文字准确率。Tesseract 尚未在本机实际安装验证。

## 3. 附加检查：A4 需要的选择与返回

入口：[evidence_review.py](../src/poster_critic/evidence_review.py)。

```python
reviewer = EvidenceReviewer(
    renderer=bound, require_renderer=True,
    ocr=selected_backend, regions=regions,
    max_tool_attempts=2, tool_timeout_seconds=30.0,
)
engine = AuditEngine(provider, executor, evidence_reviewer=reviewer)
report = await engine.audit(audit)
```

`regions` 为 `OCRRegionV1[]`：copy_id、原图像素 bbox_xyxy、required_for_release、minimum_confidence。copy_id 必须存在，区域不能越界。工具预算和模型预算分别保留；框架中的 OCR 包装通过真实 `context.tools.call` 使用现有共享工具预算，不能新建预算绕过限制。

执行顺序为证据预检 → 有界主审核 → 附加读取/观察 → 重验来源与绑定 → 更新原报告。附加一致证据不把主审核 unknown 提升为 pass；OCR 冲突只形成疑点，不凭一次 OCR 判成确定错字。必需证据缺口阻止放行，有已确认严重缺陷时仍为 fail。unknown 不带猜测性修复操作。

`engine.last_evidence` 返回 `SupplementalReviewV1`，字段为 schema_version/kind、sample_id、image_sha256、input_fingerprint、renderer/renderer_binding、ocr、findings、gaps、proposed_hints。每个 gap 含 check_id、subject_id、code、required_for_release、doubt、missing_evidence、suggestion。实际调用和工具身份在 OCR attempt 中保存。

CLI `--secondary-review PATH` 读取严格 `SecondaryReviewConfigV1`。只选择一个 backend 和其配置，1–8 个唯一 copy 区域，显式工具次数与期限。输出 `<O>.evidence.json`，不向原报告或 A1 v1 包增加字段。框架构造参数 `evidence_reviewer_factory(audit, binding)` 必须返回有效 `EvidenceReviewer`，显式选择后不能返回 null 偷关检查；可选结果位于 `review.details.supplemental_evidence`。

## 4. 正式质量工具和验收边界

入口：[quality_validation.py](../src/poster_critic/quality_validation.py)。原 `frozen_evaluation.py` 的 V1 软件包保留，不重写历史 seal。

冻结需要现有 Dataset/LocalManifest、完整 LabelSheet、明确 label_kind、阈值文件和计划。`independent_human` 标签才可能用于正式验收，模型代理或软件控制标签不能代替。Dataset 必须为 human_labeled，validation 样本的完整输入和实际图片必须与标签绑定。

阈值 `ThresholdProfileV1` 包含 profile_id、state、validation_samples、repeats、gates。gates 必须覆盖缺陷检出、false-pass、false-fail、unknown/执行失败、首次有效响应；两轮计划还需要重复一致性。draft 不作正式验收。agreed 必须带 agreed_by、agreed_at、agreement_reference、agreement_evidence_sha256，以及实际冻结的 agreement 文件；自称 agreed 不够。

冻结、运行和验收按固定计划保留所有样本/重复，包括失败、取消、未开始和缺结果。Fake/Mock、缺标签、缺同意门槛、未验证真实调用一律不作正式质量 pass。缺结果不能从质量分母删除。`verify_quality_receipt` 重新核验源数据、源码、运行记录和门槛，不能只相信收据自己的 hash。

目前没有用户提供的正式材料，质量结论为 **not_assessed**。新实验选择 `freeze_quality_validation_v2` 或 CLI `--protocol-version 2`，原 V1 默认及 seal 不改。V2 绑定完整 ExperimentIdentity、fake/live mode、capability、language、实际执行政策以及选定的审核分支。

| V2 输入 | 约束 |
|---|---|
| validation_path | primary_only 或 primary_plus_local_ocr；前者拒绝 secondary 参数，后者必须提供实际可冻结 producer 的政策 |
| execution_mode | fake/live，运行 allow_live 必须与冻结选择相符；新的正式 live run 在任何请求/输出前拒绝实际 Mock 或 custom transport |
| capability_path / language_profile_path | 当前类型严格核验，内容 hash 与冻结文件一致；live V2 必须显式 capability |
| secondary_policy_path | 冻结、运行和重验都显式选择同内容政策；支持 Apple Vision 或已选定 Tesseract；缺实际 producer/字库/工具则拒绝，不降级为 primary |
| client_policy | per_request（默认）或 scoped_owned；live 可显式在整轮计划内复用自有 client，实际政策进入身份；Fake 拒绝 scoped_owned，不冒充连接复用 |
| benchmark_cases | `run_frozen_quality` 可显式接收 `Mapping[int, BenchmarkCaseV1]`，键为完整计划中的 job index。输出前核对 sample/hash/repeat/spec/arm；同次审核实际 Recorder 生成 `NNNN.telemetry.json`，进入执行收据的文件库存。没有该参数不增加测量文件 |

软件检查示例，LABELS 必须明确为软件控制标签，不能用下面的示例声明人工金标准：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.quality_validation freeze --protocol-version 2 --dataset DATASET --label-source LABELS --thresholds DRAFT_THRESHOLDS --label-kind software_control --execution-mode fake --validation-path primary_only --out NEW_BUNDLE
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.quality_validation run --bundle NEW_BUNDLE --out NEW_RUN
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.quality_validation assess --bundle NEW_BUNDLE --run-dir NEW_RUN/run --execution-binding NEW_RUN/quality_execution.json --out NEW_RECEIPT
```

V2 receipt 只声明 `claim_scope=selected_audit_path_only`。保存并重验每个报告的完整需求 ID、primary report、实际选定 OCR 的 producer/ROI/attempts 和最终附加结果。没有冻结附加政策的 primary-only 验证不代表 OCR/Renderer 路径质量已通过。实际累计剩余预算缩小时，case policy 与同次 telemetry 绑定缩小后的身份，不能回填计划身份。

Tesseract 冻结使用已明确选定的现有 executable 和 traineddata bytes。冻结时实际运行有界 `--version`，保存版本进程/响应 hash；稳定 tool 身份绑定 executable、版本、语言顺序及每份字库 hash、PSM/OEM、ROI、置信度/必需政策、次数/期限/输出限额。冻结和每次 audit 均执行已核验 binary bytes 的私有快照；audit 的 version/OCR 两阶段用同一快照，结束核对原 bytes、快照、字库和裁剪，避免原路径临时替换后恢复造成版本/hash错配。每次 audit 仍计数自己的 version/OCR 子进程。自定位依赖工具在快照中无法执行时明确失败，不从原路径重试。仅传入工具名、缺字库或换工具不产生冻结完成收据，不自动安装。本机测试用了实际受控脚本子进程，不能称为真实 Tesseract 文字准确率验收。

同步入口 `freeze_quality_validation_v2(...)` 供普通 CLI 使用；已有 async loop 时调用 `await async_freeze_quality_validation_v2(...)`，同步入口明确拒绝套入现有 loop。`--client-policy per_request|scoped_owned` 对应上述显式政策。

跨版本只读重验入口：[quality_archive.py](../src/poster_critic/quality_archive.py) 的 `verify_archived_quality(bundle_root, run_root, receipt_path, execution_binding_path)`。只支持完整 V2 身份，核对冻结实际源码、输入/图片/标签/门槛、计划、全部报告/calls/工具证据/同次 telemetry，重算完整 receipt。它不调用工具或模型、不写锁/临时回放、不执行 archived Python，也不把当前环境冒充历史环境。原 `verify_frozen_quality`/run 仍须当前源码、环境及工具符合冻结值。历史环境和服务来源字段保留为声明，内容 hash 不认证外部真实性。

每个工具 invocation 进入前有 durable `*.tool.pending.json`，结束后与证据 sidecar 核对。合作式取消或 KeyboardInterrupt 保留真实进程记录；硬中断恢复时无法确定的 local_tool_attempts/process_starts 为 null，另列 known 下界、unresolved 次数和 local_tool_accounting_incomplete，不能补零或给正式质量结论。

## 5. 不需要组员材料也已可做的检查

P1 历史阶段已跑 A4 943 项回归，真实 macOS OCR 的合成图接入检查成功。P2 第二轮当前 A4 全量1470项通过，框架136项另跑；新工具、政策和包装使用本地受控测试，正式质量仍未验收。实际接入只需后续提供第 1 节的文件/decoder/权威查询和已有生产回调，A4 消费它们，不修改其他模块。

软件测试、原生 OCR 接入和换机源码检查的范围见 [进度与交接](A4_PROGRESS_AND_NEXT_STEPS.md)。详细执行日志和原始收据在 A4 开发者本地保留，不包含在本次源码/文档交付；没有新增收费模型请求。
