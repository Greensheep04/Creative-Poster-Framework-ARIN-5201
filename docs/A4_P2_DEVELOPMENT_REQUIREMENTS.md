# A4 P2 详细开发需求与接口约定

更新：2026-10-14。输入依据：[A4 总体开发需求](A4_DEVELOPMENT_REQUIREMENTS.md)、[框架接入说明](A4_FRAMEWORK_INTEGRATION.md)、[P1 本地工具](A4_P1_LOCAL_TOOLS.md)和当前 A4 源码。工作区：`feature/dev-2026-10-14`，基线 `13b4b91d206e39f5538e85ead4499deee6e22ad9`。

**本文件保留已确定的 A4 P2 需求与验收边界，当前已进入实现。可运行接口与实际验证见 [P2 实施说明](A4_P2_IMPLEMENTATION.md)。你只开发 A4；A1/A2/A3、Renderer 和共享框架均不属于开发范围。**

## 1. 已确定的方向

P2 先解决“能否准确测量、能否正确发送、同一份 A4 能否换目录复现”，再凭证据决定优化。默认保持原图、单任务顺序执行、显式 Provider 选择和现有审核规则。

| 顺序 | 确定任务 | 当前可独立开始 | 交付后能声称什么 |
|---|---|---|---|
| 1 | P2-01 统一实验身份、阶段遥测、对照汇总 | 是，受控 Fake/MockTransport 即可 | 程序阶段、调用与错误可追踪；不代表模型准确率或真实性能提升 |
| 2 | P2-02 模型级能力资料及发送前校验 | 是，官方资料和离线协议测试即可 | 已验证特定配置的请求/响应协议；实际账户能力另记 |
| 3 | P2-03 HTTPX 连接生命周期与受控复用 | 是，先验生命周期与请求等价 | 同作用域 client 可复用；速度收益需真实对照 |
| 4 | P2-04 简中、繁中、英文、混排文字政策覆盖 | 是，先做比较/诊断反例集 | 文字比较政策正确；真实图片识别质量另验 |
| 5 | P2-05 环境核验、A4 源码快照及离线复现 | 是，不改共享依赖文件 | 指定环境下该源码快照可核验并运行 |
| 条件任务 | P2-06 新证据格式和高级修复提示 | 依赖真实证据格式和质量验证 | 仅对已提供且可绑定的证据给建议 |
| 条件任务 | P2-07 正式 wheel 安装/分发 | 依赖仓库元数据交付决定 | 指定平台的正式安装与资源烟测通过 |

“P0/P1 完成后再考虑 P2”落实为：独立准备和软件验证可以先做；**改变生产审核行为的功能须先满足相关 P1 验收条件**。不要求所有 P1 外部材料到齐才写遥测或源码工具，也不把 P1 缺口改名为 P2 成果。

### 1.1 固定约定

- 合法的 live `pass` 自动交付已审过的原图；`fail` 和 `unknown` 回给 A1。`unknown` 保留对象、疑点、缺失证据和核实建议。A1 负责后续操作。
- Fake `pass` 仍为 `test_only`。输入、预算、协议或交付错误不伪装成图片 `unknown`。
- `LocalManifest v1`、`AuditInputV1`、`AuditReportV3`、现有 A1 包和两个 async 回调保持兼容。P2 新对象使用独立版本，不把遥测字段塞进旧报告。
- 真实失败尝试与未知用量保留；不隐藏重试、不清零预算、不自动换模型/供应商/区域/密钥类型、不增加产出 token 上限。
- 不增加运行时人工放行、新 Supervisor、布局/生图训练或其他 Agent 控制代码。

### 1.2 实施范围

允许修改：`src/poster_critic/**`、`tests/quality_critic/**`、A4 专属文档和验证记录。新增文件和命令的实现状态以实施说明及验证收据为准。

不可顺带修改：`src/poster_framework/**`、A1/A2/A3/Renderer、共享 schema/工作流、`pyproject.toml`、`uv.lock`。正式打包若必须改变后两者，先由仓库负责人明确决定。不要创建替代的 requirements/pyproject/lock 文件绕过既有交付限制。本次按开发分支交付 A4 源码和专属文档；测试、上述依赖文件、配置密钥、数据集和本地执行产物不上传。

## 2. 已核对的基线与前置条件

| 事实 | 当前证据 | 对 P2 的影响 |
|---|---|---|
| 历史P1软件基线通过 | [验证收据（本地证据汇总）](A4_PROGRESS_AND_NEXT_STEPS.md)：686 passed，7.79 秒，A4 550、框架 136，模型请求 0 | 可以依赖当前程序行为，不能宣称真实模型质量通过 |
| 已有调用计量 | `contracts.py:CallRecordV2`、`runtime.py` | 有单次耗时/传输/校验/用量，没有完整端到端阶段时间 |
| legacy默认每次请求新建 client | `providers/chat.py:ChatProvider.invoke` | 存在连接复用的优化候选，没有证明它是主要瓶颈 |
| 身份清单有两个范围 | `identity.py:software_fingerprint` 比 `frozen_evaluation.py:SOFTWARE_FILES` 窄 | 新实验不能用旧窄清单冒充所有实现身份 |
| 文字政策已存在 | `checks.py:matching_text`：NFC；typographic 另做现有标点、空白、casefold 规则 | 先验证既有政策，不默认放宽 exact |
| 历史本地wheel已检查 | [构建收据（本地证据汇总）](A4_PROGRESS_AND_NEXT_STEPS.md)使用未提交的本地打包配置 | 不等于公开仓库可以安装 A4；当前发行名也不是独立 A4 包 |
| 当前 CLI 使用 POSIX 锁 | `reporting.py:file_lock` 导入 `fcntl` | Windows CLI 不能标为支持；Linux amd64 容器源码检查已验证，平台范围见第7.5节 |

六次固定 Fake 观察重放只证明软件演示，不能用它的亚毫秒耗时推算真实服务延迟、费用或缩图收益。本文件不承诺性能提升百分比、API 价格或课程完成日期。

| 仍未完成的 P1/接入条件 | 哪些 P2 可以先做 | 哪些结论必须等待 |
|---|---|---|
| 实际 RenderManifest、ink bounds/字形证据 | 统一证据身份、受控样例 | 实际渲染缺陷识别与 render 根因建议 |
| OCR/局部视觉复核引擎及真实调用预算 | 比较政策、裁剪绑定测试 | 真实 OCR 质量与二次检查收益 |
| 真实输入格式、当前版本查询权威、生产 handler | A4 离线协议/交付测试 | 完整团队联调、A1 实际消费、生产发布成功 |
| 冻结真实标注集及预先约定的质量门槛 | 测量/汇总工具、固定观察对照 | 新模型、语言识别或图像变换的质量验收 |
| 外部消费 ACK/跨系统原子事务 | 既有账本去重核验 | “恰好一次”跨系统交付或 A1 已完成返工 |

## 3. P2-01：统一身份、遥测与对照实验

### 3.1 目标与文件

已新增 `experiment_identity.py`、`telemetry.py`、`benchmark.py`；完成状态与限制见实施说明。在 A4 的 `engine.py`、`runtime.py`、`cli.py`、`adapters/framework_*` 仅添加可选接入点；不修改共享框架计数或预算接口。

先统一新实验使用的身份函数。旧 V1 冻结包可按原 schema 解析/展示清单与 seal，不重算 seal、不回填历史身份。verify/replay 仍须使用与旧包源码/prompt 完全匹配的原运行时；在改过源码的 P2 运行时按漂移拒绝，不能为兼容而跳过检查。新增源文件、prompt、能力政策或比较政策变化都必须形成新的实验身份与冻结版本。

### 3.2 新接口通用规则

以下 `*V1` 是独立版本的 A4 内部合同。采用现有严格 Contract 规则：禁额外字段，整数拒绝 bool，有限非负耗时，SHA 为 64 位小写 hex；字段必填，允许缺失的数据明确用 `null`。JSON 集合为数组。版本与 kind 不匹配则拒绝，不能静默降级解析。

哈希采用现有 `fingerprint` 的 canonical JSON 规则；计算对象自身 hash 时排除该 hash 字段。文件清单按 portable relative path 排序，记录实际 bytes。敏感值、Authorization、密钥/环境变量值、原始响应正文、异常 message 和用户绝对路径均不进入这些新对象。

### 3.3 ExperimentIdentityV1

| 字段 | 类型/规则 |
|---|---|
| `schema_version`、`kind` | `1`、`a4_experiment_identity` |
| `inventory_version` | 正整数，首次为 1；清单规则变化增版 |
| `source_files` | `{path, sha256, size_bytes}` 数组；覆盖全部 A4 行为代码、所用 prompt、资源；明确清单，不收集私人 config/state |
| `source_fingerprint` | source_files canonical hash；不能只记录 Git commit，因为当前可能未提交 |
| `prompt_version`、`prompt_sha256` | 实际送审版本及 bytes；不以文件名代替内容 |
| `provider_config_sha256`、`capability_sha256` | 允许 null，Fake 或未启用能力资料时为空；live 配置使用类型白名单、排除任何 secret |
| `language_profile_sha256` | 比较/诊断政策的内容 hash；未选额外政策时绑定明确的 legacy 默认政策 |
| `supplemental_policy_sha256` | null 或选定二次检查政策 hash，绑定 backend/config/regions/门槛/预算，不能漏掉启用的检查分支 |
| `execution_policy_sha256` | null 或实际 client 所有权/复用方式、endpoint/region、Executor 次数/期限及 Engine retry 政策 hash；同代码不同复用开关须可区分 |
| `environment_sha256` | 第 7 节 EnvironmentSnapshot 的稳定内容 hash；不含时间或临时目录 |
| `identity_sha256` | 上述对象排除此字段的 canonical hash |

源码清单的遗漏检查须覆盖 P0/P1/P2 文件；不以“手工添加了新模块”作为足够证据。完整 source bundle 文件库存与行为清单可以用途不同，但必须明确差异，不能都叫旧 `software_fingerprint`。

### 3.4 TelemetryEnvelopeV1

| 字段 | 类型/默认与语义 |
|---|---|
| `schema_version`、`kind` | `1`、`a4_telemetry` |
| `measurement_id` | 本次本地测量的唯一 ID；不替代业务 candidate/run 身份 |
| `run_id`、`invocation_id`、`sample_id` | string 或 null；独立运行没有框架身份，加载失败可能没有 sample |
| `input_fingerprint`、`image_sha256` | SHA 或 null；输入合法后必填，不编造失败输入身份 |
| `experiment_identity` | ExperimentIdentityV1；环境采集失败则允许 null 并记 error |
| `execution_mode` | `fake/live`；配置模式，不表示实际已经发请求 |
| `measurement_kind` | `fake_replay/mock_transport/live_service/unverified`；自定义 live=True port 若未证明真实 transport 则 unverified；Mock 不能算真实服务测量 |
| `scope` | audit_only/dispatch；对照只接受 audit_only |
| `tool_summary` | null 或实际二次检查 logical/admitted/process/denied/failed/cancelled 计数；不与模型调用混加 |
| `started_at` | UTC RFC3339，仅为标签 |
| `elapsed_total_ms` | 从本次 A4 入口开始，到 dispatch 返回或终止；含本次输入处理与交付；内存入口不虚构文件加载时间 |
| `stages` | StageTimingV1[]，可空；保留成功、失败、取消、拒绝记录 |
| `calls` | 原始 CallRecordV2[]；实际 executor 发起的所有尝试，不只成功响应 |
| `audit_decision` | `pass/fail/unknown/null`；无合法报告为空 |
| `terminal_outcome` | `completed/input_error/execution_error/delivery_error/cancelled` |
| `measurement_status` | `in_memory/recorded/write_failed`；仅指遥测结果，不能改变实际审核/交付结果；write_failed 可能只存在调用方内存中 |
| `delivery_state` | `not_called/completed/uncertain/rejected/null`；来自实际分流/账本，不表示消费者 ACK |
| `error_type`、`error_code` | 允许 null，只收受控类名/短码，不收原始消息 |
| `measurement_error_code` | null、`identity_capture_failed` 或 `telemetry_write_failed`，与上述审核错误分开；采集失败identity为null，不阻断原审核 |
| `benchmark_case` | BenchmarkCaseV1 或 null；普通运行为空，不凭 sample_id 猜两臂/重复次数 |

StageTimingV1：`stage_id`（本对象唯一）、`name`、`attempt`（未准入/非调用阶段为 null）、`parent_stage_id`（顶层 null）、`elapsed_ms`、`outcome=completed/error/cancelled/rejected/not_run`。name 白名单：`input_load/input_validate/request_prepare/budget_admission/request/observation_validate/retry_wait/report_build/source_revalidate/delivery/evidence_review/telemetry_write`。未执行阶段 elapsed_ms 必须为 null；其余为非负数。`telemetry_write` 只计最终发布前的 payload 准备，截止于收集计时值，不包含 `write_json`、原子发布或文件系统最终落盘耗时；写失败的内存记录标记 error，不重跑业务。

单调钟计时，墙钟不参与耗时相减。嵌套/重叠阶段不能直接相加当总耗时。没有 transport 级证据时，只报告 `request`，不编造 DNS/TLS/首 token 时间。总体耗时截止于实际审核/分流终止，遥测自身最后写盘不计入总体，不自引用为精确完整耗时。

调用记录表示 executor 的实际发起意图，不保证服务收到请求或计费成功。Fake/MockTransport 的 records 可检验程序计量，但真实服务调用数须按 measurement_kind 分开汇总。预算拒绝、预检拒绝没有 CallRecord，不加 `vision_calls`；预检必须在准入前完成，见第 4 节。

遥测默认关闭。开启后预检输出路径并使用现有原子写入；失败不覆盖已有文件。已经执行的调用及已完成副作用不能因遥测写盘失败消失。写入失败设置 measurement_status=write_failed、measurement_error_code=telemetry_write_failed，向调用方传播受控测量异常并携带可用内存记录；terminal_outcome、audit_decision、delivery_state 保留真实结果。无法落盘不能声称保存成功，不自动再次审核或重放回调。

完整的输入失败遥测示例（演示对象，不是运行收据）：

```json
{
  "schema_version": 1,
  "kind": "a4_telemetry",
  "measurement_id": "example-input-error",
  "run_id": null,
  "invocation_id": null,
  "sample_id": null,
  "input_fingerprint": null,
  "image_sha256": null,
  "experiment_identity": null,
  "execution_mode": "fake",
  "measurement_kind": "fake_replay",
  "scope": "dispatch",
  "tool_summary": null,
  "started_at": "2026-10-14T00:00:00Z",
  "elapsed_total_ms": 2.0,
  "stages": [
    {"stage_id": "s1", "name": "input_load", "attempt": null,
     "parent_stage_id": null, "elapsed_ms": 1.5, "outcome": "error"}
  ],
  "calls": [],
  "audit_decision": null,
  "terminal_outcome": "input_error",
  "measurement_status": "recorded",
  "delivery_state": "not_called",
  "error_type": "InputError",
  "error_code": "input_invalid",
  "measurement_error_code": null,
  "benchmark_case": null
}
```

### 3.5 BenchmarkSpecV1 与汇总口径

| 字段 | 类型/规则 |
|---|---|
| `schema_version`、`kind` | `1`、`a4_benchmark_spec` |
| `experiment_id`、`change_id` | 稳定本地标识，例如 `client-reuse-01`；不冒充框架 invocation |
| `scope` | 首版只接受 `audit_only`：对照只调用 AuditEngine.audit，不调用生产 dispatch/真实 handler/生产账本 |
| `dataset_sha256`、`labels_sha256` | 固定集合/完整标注 hash；软件对照 labels 可 null |
| `baseline_identity_sha256`、`candidate_identity_sha256` | 精确绑定两个 ExperimentIdentity |
| `measurement_kind` | 同一对照只用一种，不混 Fake 与 live |
| `sample_ids` | 固定且唯一，不能结果出来后删失败项 |
| `sample_bindings` | `{sample_id, input_fingerprint, image_sha256}`[]；与 sample_ids 一一对应，从核验后的 Dataset/LocalManifest 得到 |
| `repetitions`、`warmup_repetitions` | 正整数/非负整数；运行前固定；预热单独记录，真实请求同样消耗预算 |
| `concurrency` | 首版只接受 1 |
| `order`、`order_seed` | 首版 `baseline_then_candidate/candidate_then_baseline`，每臂 sample 顺序按固定 seed 随机化；不声称交错测量或服务完全稳定 |
| `attempt_budget` | 正整数；两臂合计的 Provider 尝试上限，含预热和重试；不是收费授权 |
| `arm_attempt_budgets` | `{baseline: 正整数, candidate: 正整数}`，合计等于 attempt_budget；各臂运行独立有界，不因分进程扩大上限 |
| `price_profile` | PriceProfileV1 或 null，见下文 |
| `quality_gate` | `{state: not_agreed/agreed, threshold_profile_sha256: SHA/null}`；agreed 必有冻结门槛引用 |
| `spec_sha256` | 全对象排除此字段的 hash |

BenchmarkCaseV1：`spec_sha256`、`arm=baseline/candidate`、`sample_id`、`repetition`（从 1 开始的整数）、`warmup`（严格 bool）、`case_id`（上述字段 canonical hash）。warmup 和正式重复分别计数，每个 case 唯一；普通遥测不得伪装属于实验。

**首版 benchmark CLI 只核验 spec 并汇总既有遥测，不自动执行两个版本、不根据 hash 下载源码或运行命令。**调用方先在明确的 baseline/candidate 源码快照环境各自运行受控 audit-only 测量，核对当前 identity 等于所选臂；将 BenchmarkCase 传给可选遥测 hook。Dataset 使用现有 Dataset/LocalManifest，相对图片路径按现有加载规则解析；汇总时再核验 dataset、标签、sample bindings 和每条遥测的 identity/case。不能用普通生产 CLI 重复 dispatch 代替此对照。

每臂测量调用方须使用 A4 有界 Executor，准入前累计该臂已发起尝试、剩余 arm budget，并同时受原框架预算约束；不足就停止并保留 not_started case。两臂预算预分配，不共享不安全的 executor，也不创建新的框架预算。Fake/Mock 尝试计入软件 attempt budget，HTTP/收费次数另记；纯 prepare/compare 和未准入不计 attempt。每臂每样本预热时计划审核数为 `2*N*(repetitions+warmup_repetitions)`，尚未加重试。首版汇总工具只验证实际结果未越预算，不承担或声称实现外部测量调用方的准入。

BenchmarkSummaryV1：`schema_version=1`、`kind=a4_benchmark_summary`、`spec_sha256`、`status=complete/incomplete/rejected`、`cases[]`、`metrics`、`quality_acceptance=not_assessed/passed/failed`。case 行含 BenchmarkCase、`status=recorded/not_started/missing/duplicate/rejected`、`telemetry_path`（输出目录内相对路径或 null）、`telemetry_sha256`（SHA或null）、`reason_code`。缺记录、超预算、错身份和重复不能当 complete；不能证明未发请求的缺失/拒绝/冲突记录令actual总量、budget状态及total费用为null，已观察消耗下界保留；所有原记录保留，不覆盖先前结果。labels/门槛缺失只能 not_assessed；门槛引用必须可核验，不只看 state=agreed。

正式质量只在 measurement_kind=live_service、独立标签、冻结门槛和全体完整报告可重验时评估。`--quality-link PATH` 显式连接预先冻结的 `BenchmarkQualityPlanV1` 和事后 `BenchmarkQualityLinkV1`，逐臂重验完整质量档案，并逐 case 核对同次运行的 telemetry bytes、最终报告、全部 calls、实际期限/次数政策、工具证据和延迟。条件齐全且两臂门槛全部通过才 passed，有质量门槛失败则 failed；缺连接或证据、Fake/Mock/unverified 仍 not_assessed。详见 [已实现接口](A4_P2_IMPLEMENTATION.md)。benchmark 记录必须 delivery_state=not_called，不接受含实际交付副作用的数据来冒充 audit-only。summary 仅读取验证后的输入目录，输出需新目录；错误记录保留引用和短码，不为“凑齐”而重跑。档案源码仅 hash，不执行、不替换历史身份；hash/时间字段只证明内容和声明的协议绑定，不认证服务、人员或墙钟。

主指标按 baseline/candidate 分开计算。warmup 计入调用与费用预算，但从正式质量和 latency 分母排除并单列；完整性核验仍包含预热 case。delivery 阶段若存在，只允许 outcome=not_run/elapsed_ms=null；任何已执行 handler/delivery 记录都拒绝作为 audit-only 数据。

汇总至少输出以下值，缺失结果不能当耗时 0：

| 指标 | 明确口径 |
|---|---|
| planned / attempted / audit_completed | 计划 case 数、A4 入口次数、合法报告数分别列出；benchmark 的交付计数为 not_assessed，实际生产交付另验 |
| execution_error / delivery_error / cancelled / unknown | 分开计数；业务 unknown 不包含程序错误 |
| latency | 成功与失败分别报 sample 数、median、p95；p95 用 nearest-rank `ceil(0.95*n)`，小样本注明范围限制 |
| actual attempts / retry / rejected admission | 全部 executor records、同审核后续尝试、未准入次数；不把重试当新样本 |
| usage | live reported input/output 总数、未知用量尝试数、明细可用情况；不能合计不互斥的细分量 |
| false pass | 完整标注为必需缺陷的样本中，报告 pass 的次数 / 该类计划次数；同时列该类 unknown/error/missing，不把它们算正确识别 |
| false fail | 完整标注为合格的样本中，报告 fail 次数 / 该类计划次数；另列 unknown/error/missing |
| quality denominator | 每类独立 N、未标注/不完整标签数；缺标签不进入识别质量结论，但不从运行集合删除 |

PriceProfileV1：`schema_version=1`、`price_id`、`provider_profile`、`model_id`、`endpoint_family`、`region`（string或null）、`currency`、`input_per_million`、`output_per_million`、`effective_at`、`source_url`、`price_sha256`。只用于显式普通 input/output 单价估算，不能代表订阅额度或账单。单价有限非负且类型严格；价格不匹配模型/区域、计费政策不能用两种平价表示时拒绝估算，仍保存用量。

费用公式：`(reported_input * input_price + reported_output * output_price) / 1_000_000`。明细如 reasoning/cache/image tokens 是部分总量或重叠量时不再加一次。有未知用量，total estimate 为 null，已知 subtotal 单列。没有价格资料不联网自动获取，也不把未知费用记为零。

真实对照须固定输入/标签/prompt/预算/平台，并记录服务返回 model；“同请求”不保证服务模型完全不漂移。先冻结实验计划和质量门槛再跑，不能用 200 或 p95 更快代替质量验收。报告可接受“无收益”；没有证据不切换生产默认。

## 4. P2-02：模型能力资料与发送前检查

### 4.1 已核实事实与选择

- Qwen JSON Object 只保证 JSON 格式；支持的模式与思考组合按具体型号区分。不能由 `profile=qwen` 推出任意 VL 模型支持 JSON Schema。本地字段/ID/语义校验继续执行。[官方结构化输出](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output)
- Qwen 图片、编码 Data URI 与请求体限制须按型号/接口核对；本次页面说明并不适合提炼成一个通用 endpoint 上限。token 参数和明细也按型号区分。[视觉输入](https://www.alibabacloud.com/help/en/model-studio/vision)、[Chat API](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)
- MiniMax 当前国内文档给出单图 10 MB、请求体 64 MB，M3.1 Preview 思考不可关闭，分离思考字段为 `reasoning_content`。这些是供应商说明，不是本次账号实测。现有 A4 订阅密钥策略和显式参数继续保留。[国内 OpenAI 接口](https://platform.minimax.cn/docs/api-reference/text-openai-api)
- GLM hosted `glm-4.6v-flash` 的当前账号可用性与 JSON 能力本次未确认：具体服务页访问失败，官方索引/开源权重不能替代 hosted API 证据。保留历史 prompt_json 限制，不默认升级型号。[官方索引](https://docs.bigmodel.cn/llms.txt)、[官方模型仓库](https://github.com/zai-org/GLM-V)

P2 首版不增加供应商，先补现有 qwen/glm/minimax/compatible 的模型级资料与离线 fixtures。新增型号只有在有明确需求、协议资料和质量证据时再选；不写“最新最好”。

### 4.2 ProviderCapabilityV1

已新增 `provider_capabilities.py` 和生成不含密钥能力模板的函数。与 ProviderConfig v1、ModelCatalog v2 分开解析；旧配置字段不自动变化。

| 字段 | 类型/规则 |
|---|---|
| `schema_version`、`kind` | `1`、`a4_provider_capability` |
| `capability_id`、`revision` | 非空稳定 ID/版本，内容变化增 revision 和 hash |
| `provider_profile`、`model_id` | 当前 profile 枚举、确切配置型号；不自动跟随 latest 别名 |
| `endpoint_family`、`region`、`allowed_base_urls` | 非空接口类别、string 或 null、显式 HTTPS base 列表；与用户实际配置严格匹配，不猜区域 |
| `input_media_types`、`input_transport` | 首版仅已实现 PNG/JPEG 与 `data_uri`；不因供应商支持就启用额外格式/远程 URL |
| `limits` | LimitEntryV1[]，分别记录 `image_bytes/data_uri_bytes/request_bytes/output_tokens`；未知须显式标识 |
| `request_combinations` | RequestCombinationV1[]，列可接受的 response/thinking/effort/token 参数组合；不是各枚举随便交叉 |
| `usage_mapping` | UsageMappingV1，定义官方路径和总量/明细关系 |
| `response_flags` | ResponseFlagV1[]；只容许离线 fixture 已验证的受控规则，不执行表达式 |
| `official_sources`、`checked_at` | 官方 URL[]、UTC核对时间；页面发布日期未显示就不编造 |
| `evidence` | `{level, evidence_ref, evidence_sha256}`[]；level 为 `documented/offline_contract/live_connectivity/frozen_quality`，后三级需收据/hash |
| `capability_sha256` | 对象内容 hash，绑定实验身份 |

LimitEntryV1 字段：`name`、`value`（正整数或 null）、`status=known/unverified/not_declared`、`required_for_send`（严格 bool）、`origin=official/local_policy`、`documented_value`（原数值/单位说明）、`unit_basis=bytes/decimal_mb/binary_mib/tokens/unknown`、`source_url`。四种 name 各一项。known 的 value 为归一化后的整数 bytes 或 tokens；output_tokens 只用 tokens，其他三类只归一到 bytes；其余 status 的 value 为 null。

具体型号模板必须显式填 required_for_send：已声明的原图/body/output 限制为 true；独立 Data URI 限制只有对应资料明确要求才为 true。官方未单列的 Data URI 限制标 not_declared/false，仍受已知总 body 限制，不凭空称供应商无限制。需核实但未确认的必需限制为 unverified/true，会阻断新 live。A4 自设更小限额标 local_policy，不能充当供应商能力证据。MiniMax 模板的 image/body 以十进制 `10_000_000`/`64_000_000` bytes 作保守策略，Data URI 独立项 not_declared/false；换算不声称官方已明确 MB 底层单位。

RequestCombinationV1 字段：`response_mode=prompt_json/json_object/json_schema`、`thinking_mode=disabled/enabled/adaptive/omit`、`reasoning_effort=low/medium/high/xhigh/max/null`、`output_token_parameter=max_tokens/max_completion_tokens`、`output_limit_scope=answer/thinking_and_answer`。省略/default 必须解析成明确有效组合，不能令 null 猜成 vendor 当前默认。不得借 capability 放宽现有 MiniMax/compatible 的代码安全限制。

UsageMappingV1：`input_total_path`、`output_total_path`、`details[]`；每个 detail 为 `{name, path, relation: subset_of_input/subset_of_output/independent/unknown}`。路径为 JSON 字段段数组，不执行表达式。缺值、负数、bool 或非法类型不补零；unknown relation 不进入费用相加。

ResponseFlagV1：`path`（字段段数组）、`meaning=finish/refusal/business_error/sensitive`、`value_type=string/bool/integer`、`nullable`（严格bool，默认false）、`allowed_values`（该类型的成功/允许继续值白名单）、`missing_policy=allow/reject`。nullable=true 才允许合法 null，且须在 allowed_values 明列 null；missing_policy 只管缺字段。其他类型不符一律协议错误；非白名单值按 meaning 标为未完成/拒绝/业务错误/敏感响应，不进入有效观察；只保留脱敏短码。missing=allow 表示字段非必需，不等于有反面证据；字段缺失/null/非法类型的 fixture 都须测。规则在观察校验前执行，正常传输/用量仍保留，engine 的有限重试/refusal边界不放宽。

初期 `--capability-profile PATH`/构造参数为可选。未提供时维持 legacy 请求行为，不能标为完成 P2 能力校验；**所有新的 P2 live 对照及新增型号必须显式提供资料**。只有 required_for_send=true 且 status!=known 的限制以 capability_limit_unverified 拒绝，不靠试错上传超大文件。旧 GLM 可保留 legacy 路径，但不得伪造 capability 的 live 证据。

### 4.3 PreparedRequestV1：预检与调用计量的边界

当前 A4 接口：

```python
def prepare_request(audit, prompt, config, capability) -> PreparedRequestV1: ...
async def invoke_prepared(prepared: PreparedRequestV1, audit, prompt) -> ModelResponse: ...
```

发送方法属于 ChatProvider；常规接入使用 Engine 调用的同步 `provider.prepare_call(audit, prompt)` context，yield 一个仅当前scope可用、零参数 async invoke，在进入Executor前完成预检与reservation。下面签名只描述该Provider发送方法，不是模块级公共函数。

PreparedRequestV1 是内存对象：`input_fingerprint`、`image_sha256`、`config_sha256`、`capability_sha256`、`prompt_sha256`、`body_sha256`、`image_bytes`、`data_uri_bytes`、`request_bytes`、`body: bytes`、`endpoint`。body 不导出 JSON/遥测；auth 从 Provider 的密钥来源注入，不包含在此对象或 hash 中。URL 保留现有安全限制。

顺序固定：校验图片及指纹 → 构造盲转录 prompt → 确定配置组合 → 实际序列化请求 → 校验原图/完整 Data URI/UTF-8 请求体长度 → Executor 预算准入 → 发送**同一份** body → 记录用量 → 校验观察。Data URI 大小包括 MIME/prefix 和 Base64 文本，不用原图 bytes 代替。重试修正 prompt 必须重新准备/绑定请求。

发送前检查 PreparedRequest 与本次 audit/config/prompt 一致；过期对象拒绝。禁止发送时再另行 JSON 序列化使预检长度与实际不同。engine 在 execute_once/context.call_model 之前完成请求与 session 状态预检/必要作用域 reservation，故该阶段拒绝没有 CallRecord/HTTP请求；若只在 Provider.invoke 内拒绝，会错误增加发起记录，不能作为完成实现。准入之后发生的取消、状态变化、超时等必须保留尝试记录，不能倒删。零准入保证仅属于显式启用的新 prepare 路径，legacy invoke 保持原计量行为。

错误短码：`capability_mismatch/unsupported_request_combination/image_limit_exceeded/data_uri_limit_exceeded/request_limit_exceeded/capability_limit_unverified/prepared_request_mismatch`。均属输入/配置错误，不自动压缩图、不返回质量 unknown；不打印原始 body 或密钥。兼容 Provider.invoke 的旧入口，不强迫外部 Provider 实现新方法；新增 A4 prepare-capable 适配路径明确选择，未选择保持原流程。

### 4.4 离线协议验收

必须覆盖：旧配置请求语义不变、非法组合/超限零准入、边界恰好/超过 1 byte、编码膨胀与 Unicode UTF-8 长度、prompt 修正后重准备、配置/图像变化拒绝、未知型号/区域、business error、敏感标志、refusal、空 content、length、多个 choices、缺/非法 usage、429/Retry-After、取消/timeout。

MiniMax 新 fixture 用现行 `reasoning_content`；本次 Context7 旧摘录仍出现其他字段，不能照抄成已实现协议。现行 Chat 页未明确列 response_format 不等于绝对不支持，继续 prompt_json 和本地校验。[官方响应说明](https://platform.minimax.io/docs/api-reference/text-chat-openai)

## 5. P2-03：HTTPX 生命周期与顺序复用

HTTPX 建议使用受控作用域中的 AsyncClient 获取连接池收益，并显式关闭；connect/read/write/pool 超时各自有含义，不能代替 A4 总期限。[AsyncClient 文档](https://www.python-httpx.org/async/)、[超时文档](https://www.python-httpx.org/advanced/timeouts/)

**选择：先实现同配置、同密钥来源、同 event loop、顺序调用的 scoped session。**不引入全局 client、不更改共享预算、不首先做批量并发。当前通过 `providers/chat.py` 和 A4 Engine/调用入口接入；旧 Provider/Executor port 字段保持兼容。

| 接口/状态 | 确定行为 |
|---|---|
| 旧一次性 invoke | 保持可用，并自行创建/关闭 client；调用方没有 lifecycle 负担 |
| 新 A4 ProviderSession | async context manager；scope 内同 Provider 可顺序复用；进入后 open，退出后 closed |
| 自有 client | A4 创建，退出/取消/异常时关闭一次；闭后调用拒绝 |
| 借用 client | 仅受控 A4 factory 注入；生命周期由 owner 关闭；不得注入未知默认 auth/proxy/redirect/transport retry 配置 |
| 配置隔离 | 不跨 endpoint/区域/密钥实例复用；只凭 api_key_env 名相同不足以证明凭据相同，换密钥必须新 session |
| 并发调用 | 首版明确拒绝同 ProviderSession 同时请求；executor/agent 仍不允许共享并发 |

保留 HTTP transport `retries=0`、`follow_redirects=False`、`trust_env=False`、流式读取响应 2 MiB 上限、现有安全错误/用量记录。不能复制库示例中的 retries=1，不能在库层增加 engine 未记录的重试。测试 transport 和借用 client 都须由受控 factory 构造并验证这些策略。

请求仍受 config timeout、每次 Executor timeout 和整体剩余预算约束；pool 等待也不能越过整体期限。read timeout 是读取阶段限制，完整 wall-clock 期限继续由 Executor 控制。响应流在每条路径关闭。

离线验收：连调两次只建一个 scoped client；一次性旧入口不泄漏；自有/借用关闭权正确；取消、异常、响应超限、重试后可以关闭；跨 loop/已关闭/并发拒绝；JSON字段、报告、调用数、token 和错误码与基线一致。

收益验收：按 BenchmarkSpec 做 before/after。MockTransport 只证明复用逻辑；live latency/cost 另报告。若没有稳定收益，保留可选 session，不宣称更快或默认替换所有入口。

## 6. P2-04：文字政策覆盖与诊断语言

### 6.1 确定范围

首版只做简体中文、繁体中文、英文和混排的比较政策与诊断表达。当前 exact 的 NFC 保留；NFKC 会消除兼容差异，不能自动用来放行固定文案。[Unicode UAX #15](https://www.unicode.org/reports/tr15/)

不做默认简繁转换、翻译、拼写纠正、编辑距离容错、同形字修复、零宽字符删除、全半角合并。typographic 只保留当前明确规则，必须由用户输入选择；不替 Renderer 选字体。

### 6.2 LanguageProfileV1

已新增 `language_profiles.py`，保持 CopyItem.match_policy 和 LocalManifest 不变。

| 字段 | 类型/规则 |
|---|---|
| `schema_version`、`kind` | `1`、`a4_language_profile` |
| `profile_id`、`revision` | 内容变化增版 |
| `text_locales` | 非空唯一数组；首版 `zh-Hans/zh-Hant/en/mixed/und`，仅声明评估/观察范围，不证明识别能力 |
| `diagnostic_locale` | 首版 `zh-Hans/en/legacy`；默认 legacy 保留当前各层实际原文；繁中诊断本轮不承诺 |
| `comparison_policy_version` | 冻结既有 exact/typographic 算法版本，不引入第三种策略 |
| `unicode_data_version` | 运行时 unicodedata.unidata_version，变化更新环境/实验身份 |
| `message_catalog_sha256`、`profile_sha256` | 实际诊断模板和政策 hash；不把模板版本藏在译文里 |

当前普通诊断是中文，结构化模板是英文；legacy 不强行改成同一种语言。选择 zh-Hans/en 时用已核对的 A4 本地模板展示，不增加收费翻译调用。保留 DiagnosticItem 实际机器字段 code/status/subject_kind/subject_id/check_ids/required/blocks_release、原始 observed_evidence 和最终 decision；不虚构现有不存在的 diagnostic_id 或命令类型。只在可选展示对象中新增 `{locale, code, subject_id, doubt, missing_evidence, suggestion}`，模板保留缺失项和建议语义，原 DiagnosticsEnvelopeV1/A1 包不重写。

### 6.3 必须冻结的反例

| 组 | 比较/诊断验收 |
|---|---|
| 正常样本 | 简中、繁中、英文、混排以及数字/货币/日期/标点保持原文和稳定 ID |
| Unicode 等价 | `é` 与 `e + combining acute` 在 exact/NFC 后相等 |
| 不能容错 | 全/半角数字、①/1、m²/m2、繁简字、拉丁/西里尔同形字、零宽字、错误金额/日期继续不同 |
| typographic 选择 | 当前规则允许的引号/连字符/空白/casefold 只在显式 typographic 下生效 |
| 观察异常 | 缺 copy_id、重复/陌生 ID、null/空字符串、不可辨认、截断分别按当前语义处理 |
| 盲转录 | 请求不得泄露 expected copy 或 required_text；语言资料不成为绕过隐藏规则的通道 |
| 诊断翻译 | 同一对象/code在两种展示语言下保持机器字段、结论、证据与建议语义一致 |

测试观察只验证比较逻辑。真实识别验收另冻结含清晰文字、实际错字、截断、小字、混排的原图和独立标签，分语言/缺陷报告分母与 false-pass/false-fail/unknown。没有标签/门槛时只能记待验，不声称“已支持中文准确识别”。

OCR 仍属 P1。若后续接 Tesseract，官方语言数据/TSV 资料只证明工具功能；A4 还需绑定 engine/version、traineddata hash、语言顺序、PSM/OEM、timeout、输出限额及裁剪来源。[语言数据](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)、[TSV/CLI](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)

## 7. P2-05：A4 源码交付与换目录复现

### 7.1 选择与限制

先做源码快照，不修改共享打包配置。Hatch 的 packages 选择决定 wheel 内容；当前 HEAD 没选 A4，不能因源码存在就声称包含 A4。[Hatch 文件选择](https://hatch.pypa.io/latest/config/build/#packages)

源码快照解决指定依赖已存在时的内容核验和运行，不是“新机器无依赖也能完全离线安装”。正式发行依赖属于仓库元数据决定。[PyPA 元数据](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#dependencies-and-requirements)

已新增 `environment.py`、`distribution.py`。范围只在 A4 包、A4 文档和本地验证记录。

### 7.2 EnvironmentSnapshotV1

| 字段 | 类型/规则 |
|---|---|
| `schema_version`、`kind` | `1`、`a4_environment` |
| `scope` | `standalone/framework_adapter`，不可偷换 |
| `expected_framework_revision`、`observed_framework_revision` | SHA/string或null；standalone为空，framework_adapter须显式传入目标且核验实际来源；无法证明则blocked |
| `python_implementation`、`python_version` | 实际运行解释器/完整版本；首版验证目标 CPython 3.11 |
| `os`、`architecture`、`sqlite_version`、`unicode_data_version` | 实际值，不读取用户主目录或机器名 |
| `dependencies` | `{name, version, present, requirement_role}`[]；role 为 `core/live/framework`，缺包 version=null |
| `import_origin` | `source_bundle/installed_wheel/workspace/unknown`；核对真实路径，但导出只留相对 A4 包位置/类别 |
| `capabilities` | `{check_id, status, reason_code}`[]；status 为 `passed/failed/blocked/not_assessed` |
| `environment_sha256` | 稳定内容 hash；不含时间、个人路径、环境变量或 secret |

至少核验：Pydantic/Pillow、所选 live httpx、框架适配所需已安装 framework/依赖、当前 prompt/JS/示例资源、POSIX锁、硬链接原子创建、SQLite可写本地路径。文件系统探测只在新建本地临时目录进行，核验该环境的机制，不声称已检查生产输出目录/账本路径。采集版本不等于功能通过；能力只有实际运行对应检查才 passed。dependencies 按 name、capabilities 按 check_id 排序后计算稳定 hash。

standalone 检查不强制 import 框架/LangGraph/httpx；framework_adapter scope 缺少指定 framework revision 则 blocked，不能从另一个目录偷偷 import 不同版本。

### 7.3 SourceBundleManifestV1 与归档收据

| 字段 | 类型/规则 |
|---|---|
| `schema_version`、`kind` | `1`、`a4_source_bundle` |
| `scope` | 首版 `standalone`；包含可选 adapter 源码不表示框架已安装或通过验收 |
| `bundle_id`、`a4_source_version` | 非空源码交付标识；不是冒充独立已发布的 distribution 名/版本 |
| `source_revision` | 已知 Git commit 或 null；工作树未提交时不能只凭此代表当前 bytes |
| `working_tree` | bool，未提交源码为 true |
| `files` | `{path, role, size_bytes, sha256}`[]，role 为 `python/prompt/javascript/template/native_source/document` |
| `inventory_sha256` | 排序 files 的 canonical hash，不含 manifest 自己 |
| `manifest_sha256` | 整体对象排除此字段 hash |

归档外的 ArchiveReceiptV1：`schema_version=1`、`kind=a4_archive_receipt`、`archive_name`、`archive_sha256`、`manifest_sha256`、`inventory_sha256`、`size_bytes`。archive hash 放在归档外避免自引用。换压缩器造成 zip bytes 不同可以产生新 archive hash；内容清单相同才可称源码等价，不承诺 bit-for-bit reproducible zip。

默认打包明确库存中的 A4 Python、所需 critic_v2/v3/v4 prompt、annotation.js、models.example.json、vision_ocr.swift 源码和选择的 A4 文档；保持 src/poster_critic 目录。历史 critic_v1 可显式选择。排除 tests、真实评测图、私人 API配置、env/key、缓存、账本、备份、整个 `.git`、共享依赖文件和其他模块。文件清单不是依赖安装清单。

路径只用相对 POSIX 形式，拒绝绝对路径、重复、大小写碰撞、软链接、`..`、不受控归档内容；解包前逐项核验尺寸/总量，解包到新目录，不覆盖旧文件。库存外文件也拒绝，不只核验已声明文件。打包时拒绝未获准的文档/文件，禁止把 repo 根路径递归作为 package_root。

首版安全读取上限固定为：archive 64 MiB、最多 512 个文件、单文件 4 MiB、解压总量 64 MiB（含 manifest）；只接受 ZIP stored/deflate，无加密。实际解压流逐块限制，不只信 ZIP header。超过限额拒绝，不自动提高；更大的未来证据包不混入源码 bundle。

### 7.4 OfflineSmokeReceiptV1

当前主要签名：

```python
def collect_environment(*, scope: str, expected_framework_revision=None) -> EnvironmentSnapshotV1: ...
def build_source_bundle(package_root, out, *, docs=()) -> SourceBundleManifestV1: ...
def verify_source_bundle(bundle, receipt) -> BundleVerificationV1: ...
def run_offline_smoke(bundle_root, out, *, scope: str) -> OfflineSmokeReceiptV1: ...
```

BundleVerificationV1：`schema_version=1`、`kind=a4_bundle_verification`、`archive_sha256`、`manifest_sha256`、`inventory_sha256`（三项为SHA或null）、`status=verified/rejected`、`checks`（check_id/status/reason_code数组）。仅实际可读/合法对象计算hash，损坏/无法解析时null；只在实际库存、hash、路径和完整性全通过时 verified，verified 不允许 null。

OfflineSmokeReceiptV1 字段：`schema_version=1`、`kind=a4_offline_smoke`、`inventory_sha256`、`environment_sha256`（成功必填；冷进程因缺依赖不能加载采集器时可为 null 并 blocked）、`scope`、`checks`（同结构）、`controlled_release_callbacks`、`controlled_a1_callbacks`（非负整数）、`network_requests=0`、`paid_model_calls=0`、`quality_claim=software_behavior_only`、`status=passed/failed/blocked`。

从验证后的快照生成小型合成输入/Fake 观察，不依赖未上传 tests/fixtures。仓库外冷进程运行，明确加入快照 src；核对 import 来自该快照，拒绝工作区污染，网络入口禁用。scope 缺依赖或平台能力则 blocked；执行过但行为错误为 failed。

烟测必查：资源读取、Fake 三态、Fake pass test_only、fail/unknown 受控本地 A1 callback、诊断字段、裁剪hash/变换、账本重复与 uncertain恢复、冻结包核验/重放、迁移目录后的输入指纹/决定/问题码/路由一致。Fake 不测试 live release 成功；受控回调不是真实 A1，也不宣称消费者 ACK。

### 7.5 平台矩阵与正式 wheel 条件任务

| 环境 | 首版验收地位 |
|---|---|
| macOS arm64 / CPython 3.11 | 第一目标；工作区回归与仓库外源码快照烟测已验证，实际收据见实施说明 |
| Linux x86_64 / CPython 3.11 | 第二目标；Docker amd64 容器/CPython3.11.17 在 ARM 主机仿真下通过41项冷进程/迁移及9项完整质量连接检查。独立Linux收据见实施说明；不代表原生Linux性能、真实模型/OCR、框架或正式wheel安装 |
| macOS x86_64 / Linux arm64 | 后续候选，各自验证 |
| Windows CLI/evaluate/review | 当前不支持；fcntl 只用于 Unix，纯内存核心或 SQLite 单独通过不代表完整 CLI 支持 |
| Python 3.10 / 3.12+ | 当前共享项目限定 >=3.11,<3.12，本轮不扩大版本范围 |

`py3-none-any` 标签不能替代 OS 功能验证。[Python fcntl](https://docs.python.org/3.11/library/fcntl.html) 账本/输出验证在本地文件系统完成；网络盘和多机共享不在承诺范围。[SQLite 文件系统限制](https://www.sqlite.org/lockingv3.html#how_to_corrupt_your_database_files)

P2-07 前置：仓库负责人决定发行包名称/版本、共享或独立元数据、依赖/extras、允许交付文件。A4 不自动落实这些仓库级变更。获明确决定后，验 METADATA/Requires-Python/Requires-Dist/RECORD、包资源和完整源码 bytes；新虚拟环境、仓库外、清除 PYTHONPATH 后跑 smoke。`pip check` 仅证明依赖关系，不代替行为测试。[wheel 规范](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)、[pip check](https://pip.pypa.io/en/stable/cli/pip_check/)

只有已批准的本地依赖缓存齐备才能做完全离线安装；缺缓存记 blocked，不偷偷联网。[本地安装文档](https://pip.pypa.io/en/stable/cli/pip_install/#examples) 当前资源读取按解包源码或正常安装 wheel 的物理目录验收，不承诺直接 zipimport。

## 8. P2-06 及暂不启用的优化

| 候选 | 本轮确定的边界 | 开放条件 |
|---|---|---|
| 更小/重编码的模型输入 | 默认不改原图；原图 hash 不能冒充实际提交图 hash | 新 SubmissionBinding：parent/submitted hash、算法版本、尺寸/坐标变换、完整/局部标识；冻结真实文字质量对照及明确版本协议 |
| 本地派生证据缓存 | 可以以后做可重建 crop 缓存；每次重新核对原图、完整输入、坐标、算法/库版本 | 测量确认有收益；缓存命中不增加 live 调用，也不能替代全图审核 |
| 生产结论缓存 | 首版不实现；账本是副作用去重，不能当 verdict cache | 新的版本化复用来源/预算/账单/当前候选语义；不能复制旧 live calls 当本次真实调用 |
| 并发/批量审核 | concurrency=1；不能共享 executor/agent | 实测需求、独立实例与整体原子准入；保留取消/已执行消耗，不改共享框架 |
| 跨模型回退 | 不实现，显式选择模型 | 新多来源观察/报告协议；当前 dispatch 要求统一 Provider 身份，不是加一个模型列表就能兼容 |
| OCR/Render 新证据 | 先完成相关 P1，A4 只读外部格式 | 实际字段样例、可靠 pixel/source 绑定、版本/预算和独立质量验证 |
| 高级 repair hint | 只增加有证据的建议，不执行返工 | issue 与证据关联明确、当前版本可核验；不凭一般描述猜 brief/asset/render 根因 |

P2-06 的归一化证据最少保存：evidence_id/type、parent_image_sha256、input_fingerprint、source_revision、producer/version、画布/裁剪变换、实际观察及缺失项、实际调用记录引用。外部格式由显式 A4 decoder 消费，本文不规定 Renderer 或 A1 的业务 schema。

文字 OCR 的 confidence 不直接等于 pass。裁剪仍是局部辅助证据，不能替代完整海报判断；冲突或证据不足保留 unknown。已有确认缺陷与未知并存时仍按 ReportV3 fail 优先，但不得生成不安全自动修复提示。建议最后回 A1，A4 不操作 A2。

## 9. 新接口的接入与兼容策略

以下入口已有实现；具体运行示例见实施说明。沿用现有入口与其他参数，不增加 HTTP 服务或新 Agent。

| 入口/参数 | 首版行为 |
|---|---|
| `poster_critic.cli --telemetry` | 默认关闭；开启生成 O.telemetry.json，加入输出族预检，绝不覆盖 |
| `poster_critic.cli --capability-profile PATH` | 默认 null；仅显式启用新模型级发送前校验 |
| `poster_critic.cli --provider-session` | 显式单次 async scope 复用 client；默认关闭，不宣称速度收益 |
| `poster_critic.cli --provider-region REGION` | 显式与 capability region 相符，不能自动推断区域 |
| `poster_critic.cli --language-profile PATH` | 默认 legacy，绑定新的比较/诊断政策；不改原输入 required checks/match_policy |
| `poster_critic.benchmark summarize --spec PATH --dataset PATH --labels PATH --baseline DIR --candidate DIR --out DIR` | 首版只核验/汇总已记录遥测；软件集可省labels；无网络、不执行模型/两版本命令；当前源码与两臂 hash 绑定的测量由调用方分别取得 |
| `poster_critic.distribution snapshot/verify/smoke` | 新输出目录、无网络、无真实回调；先源码复现，正式 wheel 不在首版命令中 |

框架入口只通过 A4 构造参数选择 telemetry/capability/language/session，不改 AgentInput/AgentOutput 共享字段。可选展示字段放在现有 A4 details 扩展位置，顶层报告和 review/action/target 约定不变；action/target 保持 null，防止 A1 callback 后再触发框架返工。

新政策/config hash 变化不授权再次交付同一候选。继续使用当前 DeliveryGuard key/冲突处理，不能改 namespace、换输出路径或删账本绕过去重；生产回调失败/uncertain 不自动重放。

新 identity、能力资料、环境或遥测 hash 是内容绑定，不是来源真实性证明。外部当前来源查询仍必须按 P0 约定独立核验。

## 10. 实施工作包与完成条件

| ID | A4 修改位置 | 最小完成条件 |
|---|---|---|
| P2-01a | 新 experiment_identity.py；受控更新冻结/评估接入 | 完整清单覆盖、代码/prompt/config/政策/环境漂移能识别；旧包可解析，核验/重放须原匹配运行时，不重写 seal |
| P2-01b | 新 telemetry.py；runtime/engine/CLI/A4 adapter 可选 hook | 三态、输入错误、预算拒绝、传输/解析失败、取消、交付失败/重复/uncertain 均有正确记录；无隐私正文 |
| P2-01c | 新 benchmark.py | 首版只汇总既有audit-only遥测；固定 spec/case/来源、失败分母、warmup分离、预算与median/p95/使用量口径；无自动跑模型，Fake仅软件 |
| P2-02 | 新 provider_capabilities.py；chat/engine的 A4 prepare 路径 | 特定模型组合和实际请求 bytes 校验；新 prepare 路径的准入前预检拒绝零准入，准入后失败保留消耗；旧配置兼容 |
| P2-03 | chat/ports/A4 factory | client 所有权与顺序复用正确；无隐藏重试/预算变化；请求语义和错误等价 |
| P2-04 | 新 language_profiles.py；A4诊断模板/测试资料 | NFC/明确typographic/数字反例、盲转录和两语言既有机器字段/语义通过；不声称真实识别验收 |
| P2-05 | 新 environment.py/distribution.py | 指定平台源码快照的库存/路径/资源/冷进程/迁移/无网络烟测通过；缺环境明确blocked |
| P2-06/07 | 仅满足前置条件后细化的 A4 adapter/分发验证 | 不计入首批完成；外部 schema/元数据决定不能由 A4 擅自补齐 |

建议先交付 P2-01a/01b，再 P2-02 与 P2-03；P2-04/05 可独立推进。先做局部 meaningful tests，再跑 A4 回归；任何框架包装接入变化额外跑现有框架回归。测试只本地，不自动上传 tests 或依赖文件。

### 10.1 统一验收记录

每个工作包留 A4 本地收据：分支/base SHA、实际源码/政策 hash、完成项/未完成项、命令和结果、平台/依赖版本、测试数量、measurement_kind、真实/模拟调用数、已知/未知用量、输出相对路径。不能拿计划数量当实际通过数。

| 关卡 | 通过条件 | 缺条件时 |
|---|---|---|
| 软件合同 | 实际字段/错误/生命周期/身份/输出测试通过，原行为无回归 | 修复或记失败，不标完成 |
| 离线源码复现 | 验证后的同份源码在目标环境、仓库外运行，所有必要 checks passed | 环境缺失为blocked，不能称已跨平台 |
| live 协议 | 指定账户/区域/型号得到合法响应，保留实际calls/usage/错误 | 只有官方文档/offline证据，标未实测 |
| 质量/性能切换 | 冻结真实数据/标签/门槛及比较计划，结果达门槛且错误/费用透明 | 可以完成工具实现，生产优化默认不切换 |
| 实际交付 | 当前绑定重验通过、回调真实完成、账本状态明确 | 不宣称A1消费或跨系统原子完成 |

P2 软件准备完成与正式生产质量验收分开记录。既有 ReportV3、原词/硬约束、固定文字和 A1 分流不得为追求速度/便宜而削弱。

## 11. 调研来源与未确认项

本次核对日期统一为 2026-10-14。库文档使用 Context7 与官方原页；型号能力、价格、账户额度是可变信息，上线前再核对对应官方页及受控实测，不默认沿用本页为永久承诺。

| 来源 | 用途/本次边界 |
|---|---|
| HTTPX 官方 async/timeout，Context7 `/encode/httpx` | lifecycle/pooling/阶段超时；没有本项目速度收益证据 |
| Qwen 官方结构化/视觉/Chat API | 型号组合/大小/用量；不同页面请求体限制需针对具体 endpoint 复核 |
| MiniMax 官方国内/国际接口 | 图片/思考/响应边界；没有本次收费调用，也不证明其他密钥类型可用 |
| GLM 官方索引/权重仓库 | hosted 4.6V 服务页未核实，明确保留未确认状态 |
| Unicode/Tesseract 官方文档 | normalization/工具功能；OCR适配已实现，真实语言质量未验 |
| Hatch/PyPA/pip/Python/SQLite 官方文档 | 元数据/文件选择/安装/资源/平台和账本限制；本次未重新构建或安装 |

当前不需要向组员索取信息即可实施第 10 节的独立准备部分。涉及真实证据、外部权威查询、生产回调或发行元数据时，只记录 A4 所需条件，等外部提供后消费，不替组员开发。
