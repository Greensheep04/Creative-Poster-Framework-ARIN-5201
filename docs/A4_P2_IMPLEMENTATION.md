# A4 P2：实现、调用与交付说明

更新：2026-10-14。当前 A4 开发分支 `feature/dev-2026-10-14`，基线 `13b4b91d206e39f5538e85ead4499deee6e22ad9`。本次交付 A4 源码和专属文档；共享框架、其他模块和原依赖文件保持原状。最新进度、资料清单及验收缺口见 [进度与交接](A4_PROGRESS_AND_NEXT_STEPS.md)，提交状态以分支历史为准。

P2 的独立准备部分采用可选接入。原 live pass 自动放行审过的 bytes，fail/unknown 仍回 A1；Fake pass 仍为 test_only。没有切换模型、降低审核标准或改变原图。质量和性能收益需要真实冻结对照，当前不能据软件测试作结论。

## 1. 可选 CLI

以下命令需要当前源码位于 PYTHONPATH 和已有项目依赖。`INPUT` 是现有 LocalManifest，`NEW_REPORT` 及完整输出族不能已存在。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.cli --input INPUT --out NEW_REPORT --telemetry
```

| 参数 | 输入与输出 |
|---|---|
| --telemetry | 默认关闭。开启写 `<O>.telemetry.json`，含阶段时间、完整真实尝试记录、原图/输入/实验绑定、审核结论、交付结果。没有 prompt、原文案、图片 bytes 或原始异常消息；identity采集失败以null/identity_capture_failed记录，原审核继续 |
| --capability-profile PATH | 显式 sealed `ProviderCapabilityV1`，只用于 chat；对具体 model/base/region/请求组合及实际 bytes 做准入前预检；缺必需已核实限额直接阻断 |
| --provider-region REGION | 与选定 capability 严格一致，不猜区域，也不切换服务地址 |
| --provider-session | 显式在同一 async scope 内使用自有 HTTP client，默认关闭；关闭与取消不漏 client，不增加重试 |
| --language-profile PATH | 严格 `LanguageProfileV1`；写原 `<O>.diagnostics.json` 和独立 `<O>.diagnostics-display.json`。展示语言为 en/zh-Hans/legacy，原机器字段、证据及决定保留 |
| --secondary-review PATH | P1 的显式工具/区域政策，输出 `<O>.evidence.json`；工具记录与模型记录分别计算 |

新参数对应输出加入预检，并避开输入与账本/journal。既有 A1 v1 包不变。审核/回调实际执行后若遥测写盘失败，返回受控 `telemetry_write_failed`，保留实际报告、calls、账本和交付状态，不自动重跑。

## 2. 身份和本地展示

入口：[experiment_identity.py](../src/poster_critic/experiment_identity.py)、[language_profiles.py](../src/poster_critic/language_profiles.py)。

```python
from importlib.resources import files
from poster_critic.experiment_identity import build_experiment_identity
from poster_critic.language_profiles import language_profile, render_diagnostics

policy = language_profile(diagnostic_locale="en")
identity = build_experiment_identity(
    prompt_version="critic-v4",
    prompt_bytes=files("poster_critic").joinpath("prompts/critic_v4.txt").read_bytes(),
    provider_config=selected_config, capability=selected_capability,
    language_profile=policy, supplemental_policy_sha256=selected_secondary_policy_sha256,
    execution_policy_sha256=selected_execution_policy_sha256,
)
display = render_diagnostics(machine_diagnostics, policy)
```

未选择 capability 或附加检查时相应输入为 null。实际 prompt 必须与当前 inventory 中对应版本 bytes 相同。inventory 覆盖 A4 Python、全部已存在 critic prompts、JS、安全示例配置和 Swift 源码，拒绝私有文件、未受控资源和软链。包目录参数为 `src/poster_critic`，不接收整个仓库递归库存。源码 hash 包含未提交 bytes；Git commit 不能替代它。运行记录另绑定实际 client lifecycle、Executor 次数/期限、Engine retry 政策，避免同源码不同复用开关得到无法区分的实验身份。

文本政策固定为既有 NFC/显式 typographic，不做 NFKC，不改 exact。全半角数字、①/1、金额/日期、繁简字、同形字、零宽字仍不同。诊断本地翻译不传给模型，不暴露盲转录预期文案。中文识别质量没有因比较反例通过而获得验收。

## 3. 请求预检与 client 所有权

入口：[provider_capabilities.py](../src/poster_critic/provider_capabilities.py)、[chat.py](../src/poster_critic/providers/chat.py)。ProviderConfig v1 和 ModelCatalog v2 的字段保持不变。

`capability_template(config, checked_at=UTC, region=explicit_region)` 可生成当前确切配置的保守资料模板。未知必需限额明确 unverified，不能把模板当作账号已授权/已联网/质量通过的证明。模板不自动发送请求，不自动下载官方资料。

`ChatProvider(config, capability=profile, region=region)` 显式选择能力资料。Engine 调用 `prepare_call(audit, prompt)`，在 Executor 准入前核验真实图片、config/prompt/输入、请求组合、原图 bytes、完整 Data URI bytes 和实际 UTF-8 body；发送同一份已核验 body。重试重新准备；PreparedRequest 只在内存，不导出 body。

```python
from poster_critic.providers.chat import ChatProvider, ProviderSession

provider = ChatProvider(selected_config, capability=selected_capability, region=explicit_region)
async with ProviderSession(provider):
    report = await engine.audit(audit)
```

自有 session 关闭自有 client。借用只接受受控 `ProviderClient`，由 `create_provider_client(provider)` 创建；借用 session 不关闭它，owner 必须最终 `await client.aclose()`。同 event loop、相同配置/endpoint/region/key 身份、顺序请求；并发或过期 reservation 在准入前拒绝。capability/response flags 的 finish/refusal/business/sensitive 校验在观察解析之前执行，传输和有效 usage 保留。

没有 capability/session 的 legacy 路径保持旧计量行为。新 P2 live 对照必须显式 capability，不能以旧路径成功代替新预检验收。当前无新增收费模型调用，也没有真实复用性能对照。

## 4. 内存遥测与框架入口

入口：[telemetry.py](../src/poster_critic/telemetry.py)。

```python
from poster_critic.telemetry import TelemetryRecorder, provider_measurement_kind

recorder = TelemetryRecorder(
    execution_mode="live" if provider.live else "fake",
    measurement_kind=provider_measurement_kind(provider), scope="audit_only",
    benchmark_case=case_or_none,
)
engine = AuditEngine(provider, executor, telemetry=recorder, language_profile=policy_or_none)
report = await engine.audit(audit)
measurement = recorder.snapshot()
recorder.write(new_measurement_path)
```

Recorder 属于一次 invocation，finish 后不能继续审核或修改阶段。audit_only 入口不调用生产 handler/ledger，delivery 状态为 not_called，阶段为 not_run/null。dispatch 由 CLI/Agent 在实际回调结束或失败后 finalize，记录真实 delivery completed/uncertain/rejected；completed 不是 A1 消费 ACK。

阶段使用单调钟。request 指实际有界 invoke；budget_admission 只测本地/共享准入，预检拒绝没有 calls；不编造 DNS/TLS/首 token。total 包括入口处理和实际 dispatch，截止于终止，不含最后遥测自身写盘；嵌套阶段不能相加当总耗时。`telemetry_write.elapsed_ms` 只记录最终发布前的 payload 准备，不包含 `write_json` 或原子发布的完整耗时；成功写入保存 completed，写失败的内存对象保留 error，业务 total/calls/决定/交付状态不变。

measurement_kind 为 fake_replay/mock_transport/live_service/unverified。自定义 live=True port 未证明真实 transport 时记 unverified。CallRecord 的 live 是配置/执行模式，不能独自证明真实服务调用或费用。未知用量仍 unknown。

`QualityCriticAgent(..., telemetry=True, language_profile=policy)` 可显式传入 `expected_framework_revision=SHA` 核验框架环境；未提供该证据时环境字段保留 blocked。在 `review.details.telemetry/diagnostics_display` 提供可选结果；`last_telemetry` 即使运行抛异常也可取已完成记录。未启用遥测不增加字段。原共享类型和预算未修改，action/target 始终 null。capability/session 通过显式选定 Provider 传入，A4 不替组员注册服务。

## 5. 对照汇总

入口：[benchmark.py](../src/poster_critic/benchmark.py)。首版只核验已存在的 audit-only 测量，不自动运行模型、两个源码版本或任意外部命令。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.benchmark summarize --spec SPEC --dataset DATASET --baseline BASELINE_DIR --candidate CANDIDATE_DIR --out NEW_DIR
```

软件集可不提供 labels，结果为 not_assessed。完整质量连接通过可选 `--labels LABELS --quality-link LINK` 启用；没有 LINK 不升级质量结论。正式质量所需材料为固定独立 labels、预先冻结且可重验的门槛及真实服务证据。spec 固定两臂 identity、样本输入/图片 hash、重复/预热、顺序种子和总/两臂尝试预算；每条 telemetry 有明确 BenchmarkCase，不能靠文件名或 sample_id 猜归属。

缺失、拒绝或冲突结果使实际总尝试/未超预算/总费用保持 null，已观察记录仅给消耗下界；明确 not_started 标记可证明本次计划入口未执行。只有绑定通过的 recorded 测量参与可比延迟，拒绝记录不隐藏其观察到的消耗。

汇总保留全部计划 case 和失败分母，预热单列但计入尝试/费用预算。成功/失败 latency 分开报告 median 与 nearest-rank p95，缺记录不作 0。已知 tokens、未知用量尝试和可选 flat-rate 估算分开；reasoning/cache/image 明细不重复加进总量。Fake/Mock、缺独立标签或门槛永远不能给 quality_acceptance=passed。

### 5.1 两臂质量连接：输入和输出

入口：[benchmark_quality.py](../src/poster_critic/benchmark_quality.py)、[quality_archive.py](../src/poster_critic/quality_archive.py)。连接工具不执行模型、回调、命令或历史 Python 源码。

| 对象/函数 | 必需输入 | 返回/输出 |
|---|---|---|
| `freeze_benchmark_quality_plan(spec, out, *, baseline_bundle, candidate_bundle)` | 已封存 BenchmarkSpecV1、两臂 V2 冻结质量包、新 plan 文件。两包必须覆盖完全相同的验证样本、输入/图片、明确 labels、门槛及重复次数；身份分别与 spec 两臂对应。draft 可做软件内容连接，不冒充独立金标准或约定门槛 | BenchmarkQualityPlanV1；实际调用时 UTC `frozen_at`，不提供回填时间参数 |
| `run_frozen_quality(bundle, out, *, benchmark_cases=...)` | 该臂匹配的源码运行时、明确冻结政策、`Mapping[job_index, BenchmarkCaseV1]`，新的输出目录 | 实际同次 audit 的报告、calls、case policy、可选工具证据、`NNNN.telemetry.json`、质量 start/execution/receipt；正式 live 仍需显式授权开关 |
| `make_quality_link(**fields)` | 事后实际文件路径和 SHA、plan 原文件 SHA/seal、每臂 receipt/execution 文件 SHA与每个 formal case 的 measurement_id/telemetry bytes SHA/report bytes SHA | sealed BenchmarkQualityLinkV1，仅封存给定引用，不制造审核或证明文件 |
| `verify_archived_quality(bundle_root, run_root, receipt_path, execution_binding_path)` | 四个现有路径，V2 完整档案 | VerifiedQualityArchive：manifest/receipt/dataset/labels/thresholds/jobs/rows/reports/全部 calls/每 case calls/实际 artifact SHA及 manifest/receipt/execution/agreement文件 SHA；不写源目录 |
| `summarize_benchmark(..., quality_link_path=PATH)` / `summarize --quality-link PATH` | 原 summary 输入、显式 labels 和 quality link | 原 BenchmarkSummaryV1，另 `quality_evidence.json` 和 `metrics.quality_evidence`：状态、短原因码、文件引用、各臂重验结果 |

Plan 顶层：`schema_version=1`、`kind=a4_benchmark_quality_plan`、`spec_sha256`、`dataset_sha256`、`labels_sha256`、`threshold_profile_sha256`、`agreement_file_sha256`（draft 必为 null）、`frozen_at`、`baseline`、`candidate`、`plan_sha256`。threshold SHA 总是实际冻结门槛，draft/agreed 两臂不能混合、内容必须一致；draft 不接受 agreement 文件，agreed 必须与 spec 引用及真实 agreement SHA 匹配。每臂包含 `arm`、`bundle_path`、`manifest_file_sha256`、`bundle_seal_sha256`、完整 `experiment_identity`、`validation_path`、`secondary_tool_sha256`、`formal_cases`。formal case 必含 `case_id/sample_id/repetition/quality_job_index`，一一覆盖质量 job；warmup 不属于质量 job。

Link 顶层：`schema_version=1`、`kind=a4_benchmark_quality_link`、`spec_sha256`、`plan_path`、`plan_file_sha256`、`plan_sha256`、`baseline`、`candidate`、`link_sha256`。每臂包含 `arm/run_path/receipt_path/execution_binding_path/receipt_file_sha256/receipt_sha256/execution_file_sha256/formal_cases`。每个 measured case 必含 `case_id/measurement_id/telemetry_sha256/report_sha256`。路径必须是 link/plan 所在公共容器内的相对路径，拒绝 traversal 和 symlink，不接受绝对私人路径。

先封存 plan，后按固定 spec 测量，再填 Link。每个 formal case 必须是该次质量 audit 封存的 telemetry **同一份 bytes**，完整 calls 和 latency 也要对应；复制别轮的调用列表或通过收据会被拒绝。档案源码按各自实际 bytes 重验，不要求旧 baseline 等于当前 candidate，也不执行归档代码。原 V1 执行/回放仍要求匹配原运行时，不补历史 identity。

`BenchmarkQualityAssessmentV1`：`status=verified/unavailable/rejected`、`quality_acceptance=not_assessed/passed/failed`、`reason_codes[]`、`references[]`、`arms`、`timestamp_claim=protocol_binding_only`。verified 表示内容连接完整；只有真实服务模式、独立标签/同意门槛、完整结果、实际 calls 与两臂正式质量都可评估时才产生 passed/failed。有门槛失败则 failed；证据不足或连接被拒绝保留 not_assessed。文件 hash 和时间字段不认证外部服务、真人签名或墙钟来源。

## 6. 源码交付与环境

入口：[environment.py](../src/poster_critic/environment.py)、[distribution.py](../src/poster_critic/distribution.py)。不创建替代 dependency manifest，不修改共享打包配置。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.distribution snapshot --package-root src/poster_critic --out NEW_BUNDLE --doc docs/A4_P2_IMPLEMENTATION.md --doc docs/A4_P1_EVIDENCE_AND_QUALITY.md
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.distribution verify --bundle NEW_BUNDLE/a4-source.zip --receipt NEW_BUNDLE/archive_receipt.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m poster_critic.distribution smoke --bundle-root NEW_BUNDLE/snapshot --out NEW_SMOKE
```

快照输出 `a4-source.zip`、归档外 SHA 收据和 `snapshot/manifest.json`。只含 A4 源码/明确选择的 A4 文档/资源，包括 Swift 源码，不含编译工具、tests、真实图、账本、secret、依赖文件或其他模块。ZIP 路径/重复/大小写/软链/数量/解压尺寸严格核验；解包只写新目录。验证后的快照运行时使用 python -B 或 PYTHONDONTWRITEBYTECODE=1，避免生成库存外缓存；隔离解释器 -I 忽略此环境变量，须同时用 -B。文档中指向原仓库历史验证记录的链接不在源码包中，源码包不携带这些外部材料。

environment 实测 CPython、OS/arch、依赖版本、资源、POSIX锁/硬链接/SQLite，稳定 hash 不含时间和私人路径。standalone 不强制导入框架或 httpx；framework_adapter 必须明确目标 framework revision 并核验实际来源，缺证据记 blocked。

烟测生成合成图/Fake，不依赖仓库 tests，在两个冷进程和实际迁移目录中核对三态、test_only、受控 A1 callback、诊断、crop、ledger、冻结/replay、来源一致，网络与收费次数为 0。它证明本机指定依赖下的源码行为，不是无依赖新机器安装或正式模型质量。

`verify_existing_wheel` 核验已有 wheel 的 METADATA/RECORD/资源/源码，不构建或安装。可选 typed `WheelMetadataPolicyV1(name, version, requires_python, requires_dist, provides_extra)` 比较完整预期元数据：规范化包名/extras、版本、Python specifiers、每项 dependency specifiers/markers。只解析 marker，不按本机条件删依赖；当前交付政策只支持公共索引依赖，拒绝 direct URL/敏感值，拒绝收据的元数据字段清空，不导出密码或私人路径。额外检查现有 `packaging` 安装缺失时拒绝，普通源码入口不导入它，不修改共享依赖；返回 `metadata_policy_sha256` 绑定给定政策。具体语法参照 [官方 requirements](https://packaging.pypa.io/en/stable/requirements.html)、[specifiers](https://packaging.pypa.io/en/stable/specifiers.html) 和 [core metadata](https://packaging.python.org/en/latest/specifications/core-metadata/)。预期政策声明本身不认证负责人批准。

Tesseract 冻结质量 producer 与 benchmark 两臂收据连接已实现，真实工具/模型质量仍待验。其他新证据格式需实际 schema；正式发行名/版本/依赖/extras 和公开交付范围尚未由仓库负责人决定，P2-07 正式安装验收未完成。macOS arm64/CPython 3.11 已验证，Linux amd64 容器验证见下；Windows CLI 的 fcntl 条件不满足，其他 Python 版本不扩展承诺。

### 6.1 Linux 源码验收范围

同一份55文件 A4 源码在 Docker `linux/amd64`、CPython3.11.17 环境通过41项冷进程/迁移和9项完整 draft/Fake 质量连接检查，12次实际Fake尝试，审核期间网络/付费调用为0。源码和798份已安装依赖文件的实际 bytes 前后保持一致。[进度与交接](A4_PROGRESS_AND_NEXT_STEPS.md)汇总该验收范围；本地收据另保留平台、完整依赖版本/库存 hash、环境和源码身份，不随本次文档交付。

Docker引擎是Linux/aarch64，amd64用户空间在ARM主机上仿真；这是该容器环境的软件源码验收，不是原生硬件性能、真实模型或Tesseract识别质量、框架接入或正式A4 wheel安装验收。审核容器禁网，源码/脚本/依赖挂载只读，只写新的A4输出；临时容器已移除。准备环境时另行下载了官方Python镜像和依赖，不把这些下载记为模型调用。原 `pyproject.toml`/`uv.lock` 未改。镜像平台和挂载规则参照 [Docker官方多平台说明](https://docs.docker.com/build/building/multi-platform/)、[只读挂载](https://docs.docker.com/engine/storage/bind-mounts/)和[禁网网络](https://docs.docker.com/engine/network/drivers/none/)；实际OCI摘要记录在交付收据。

## 7. 验证与待补材料

当前验证汇总见 [进度与交接](A4_PROGRESS_AND_NEXT_STEPS.md)：A4 1470 项与框架 136 项分别运行通过，共 1606 项；在 A4 行为源码精确一致的前提下复用这些回归记录。随后同一源码快照在 macOS 和 Linux amd64 仿真容器分别完成 41 项冷进程/迁移及 9 项完整 draft/Fake 质量连接检查，共 100 项。两次质量连接各记录 12 次实际 Fake 尝试，正式质量仍为 `not_assessed`。

原始测试 XML、执行/交付收据、旧快照、真实 Apple Vision 合成图接入记录均在本地按版本保留，未上传本分支。GitHub 文档已更新公开交接信息；本次行为源码与已验证快照的 55 份源码/资源一致，不能把旧 ZIP 的文档快照等同于当前文档，也不能将历史软件证据升级为生产或质量验收。

待外部材料：实际 RenderManifest+decoder、权威版本查询、生产发布/A1 回调、完整独立验证标签、共同确认的质量门槛。A4 只消费这些输入，不替组员编写对应模块。缺材料不妨碍独立工具交付，但不能声称完整团队联调、正式质量或生产优化默认切换已完成。
