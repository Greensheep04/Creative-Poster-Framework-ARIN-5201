# Portable Visual Producer and QC Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改 A1/A2 的前提下，实现三种可选生成后端、配置引导、新版 QC 交接与授权局部返工，证明 A3 的完整生产链路。

**Architecture:** 保留现有 Agent.run、Spec、Pillow renderer、缓存、预算和 artifact store。新后端实现同一 ImageGenerator；QCBridge 从共享上下文构造审核包，外部接入层负责取得返工授权，A3 和框架只验证并执行授权目标。A1/A2 的代码、registration、输入输出格式、提示词均保持原状，不要求新增方法。

**Tech Stack:** Python 3.11、Pydantic 2、Pillow、fontTools、现有 LangGraph；本地推理使用已锁定 Diffusers 0.35.1/PyTorch 可选依赖；云端使用标准库 HTTP 与明确 Images JSON 协议。

**Spec:** ../specs/2026-10-12-a3-portable-qc-design.md；2026-10-12 用户追加约束：Agent 1 和 Agent 2 都不需要修改。

## Global Constraints

- 用户从 GitHub 下载项目并安装必要环境，自行选择 SDXL 本地、Qwen-Image 本地或云端生图 API。三个生产后端并列，不默认云端、不自动下载全部模型、不静默切换后端。
- 保留四个 Agent。A3 不改正式文案、布局、字体或颜色，不自行宣称 passed。
- 沿用这些组件及已有 SPEC_FORMAT/CANDIDATE_FORMAT。新增可选字段保留旧字段兼容性，不把框架 artifact ID 替换为业务素材 ID。
- Python 3.11、diffusers 0.35.1 的已锁定接口作为首个适配目标。
- 所有真实图像操作通过 context.call_model(kind=image_operation) 计入预算和记录。
- 仅明确未提交/已拒绝且可安全重试的情况最多重试一次。
- image.path：审核包中的 poster.png，单帧 PNG，720x1280，<=20 MiB。
- 不得往 LocalManifest 塞 run_id/candidate_id/based_on 等额外字段。
- A4 repair_hint 仅是建议，不直接派给 A2/A3。unknown 不自动生图。
- A1 和 A2 实现、原 registration、输入输出格式及提示词保持不变；本次不创建 A1/A2 业务代码。
- 本机 code/runtime 在 D:/Projects；环境 D:/Dev/conda-envs/creative-poster；权重 D:/Models。通用配置无机器路径，维持 uv.lock 与独立 Conda，不创建额外 .venv。

## Review Focus

- 同一素材关联多个 image element：每个处理结果都保留，不被最后一个元素覆盖。Task 1。
- 旧图片哈希相同但 candidate revision 不同：旧 QC 报告不能绑定新候选。Task 2。
- unknown 经外部核实取得授权：保留原始 unknown 报告，不伪造 fail/pass，同时执行范围受限。Task 3。
- 云端已生图但图片获取失败/超时：不能自动再次提交图像请求，Authorization 不能泄漏到图片下载。Task 4。
- 云端使用者未安装 Torch/Diffusers，或安装目录带空格：配置/渲染/云端路径仍正常。Tasks 4/6/7。

## Execution setup and boundaries

工作目录为已存在的 feature checkout D:/Projects/Creative-Poster-Framework-ARIN-5201，分支 codex/visual-producer；不操作 main、不推送或发布。实施前记录 BASE SHA 和 git status，沿用已有独立 Conda。计划账本放在 docs/visual-producer-portable-progress.md，逐任务保存 RED/GREEN 命令与结果、变更和裁决。

建议本会话 Native 执行：我依序实现，最终一位独立只读审查者检查整个改动。该建议尚待用户选择；书面计划审阅前不写业务代码。

以下命令默认在项目根目录执行。每个任务先加入测试并确认预期失败，再实施；任务局部测试通过后运行全套。Windows 使用较短的项目 scratch 临时路径，避免已有长路径问题。

通用全套命令：
`& 'D:\Dev\conda-envs\creative-poster\python.exe' -m pytest -q`
Expected: exit 0，所有用例通过；仅已有 WinError 1314 symlink capability skip 可保留。不得把本地 Qwen/付费云 API 未实测隐去。

## Task 1: 保留生产闭环并补齐可审核执行证据

**Files:** Modify src/poster_visual_producer/{agent.py,renderer.py,registration.py}; Test tests/visual/test_production_evidence.py，以及既有 test_agent.py/test_revision_sequences.py。

**Interfaces:**
- Consumes: PosterSpec、CreativeBrief、ImageGenerator、context.files/cache/memory、现有框架授权 target。
- Produces: Candidate 可选字段 execution_decisions；assets 增加 element_ids/source_size/processed_bindings；text_manifest 和 Renderer text row 增加 copy_ref。processed_bindings 每项为 element_id/processed_ref/processed_size/asset_to_canvas。
- 一个元素时保留已有 processed_ref/asset_to_canvas 兼容字段；多个元素时 processed_bindings 为完整权威映射，兼容字段仅明确定义为首个元素，禁止依赖循环最后值。
- execution_decisions 每项包括 asset_id/action/reason/fingerprint_key/target；action 限 reuse_current/cache/generate/regenerate。用量仍来自真实 runtime。

- [ ] Step 1: 新增 test_multiple_elements_keep_independent_processing_refs，构造 hero_image 关联 hero 与 second_hero；检查两个 element_id 和两个处理引用均可读取，变换对应两组坐标。
- [ ] Step 2: 新增 test_layout_move_records_reuse_and_zero_image_ops 与 test_authorized_asset_repair_preserves_unaffected_assets；分别断言额外 image_ops=0、原素材哈希不变，以及仅授权目标素材版本递增、文字记录不变。
- [ ] Step 3: Run `python -m pytest tests/visual/test_production_evidence.py -q`，Expected: 因缺 copy_ref/完整 processed bindings/执行记录而 FAIL。
- [ ] Step 4: 在既有分支位置增加实际执行证据；保留 file_ref 原始素材别名、修复指纹与已有版本校验，不改变 A1/A2 格式。
- [ ] Step 5: Run 新用例及 test_agent.py/test_revision_sequences.py，再全套；Expected: 全通过且渲染输出仍为真实 PNG。
- [ ] Step 6: Commit `feat: record A3 production decisions and element asset bindings`，更新账本。

## Task 2: 严格 QC 合同、可搬移审核包与报告验证

**Files:** Create src/poster_visual_producer/{qc_contracts.py,qc_bridge.py}; docs/design-baseline/2026-10-12-QC接口文档.md; docs/schemas/LocalManifestV1.json; Test tests/visual/test_qc_bridge.py。

**Interfaces:**
- `build_local_manifest(request: OriginalRequest, brief: CreativeBrief, spec: PosterSpec, candidate: dict, *, sample_id: str, image_path: str) -> LocalManifestV1`。
- `export_review_bundle(context: AgentContext, candidate: dict, *, destination: Path) -> ReviewBundle`。从当前 context.memory 读 Brief/Spec，并从受控 store 取 candidate/manifest/index；不得把 destination 当作模型输入。
- `ReviewBundle` 提供 manifest_path、candidate_link_path、manifest 和候选身份链接。
- `validate_audit_report(report: dict, bundle: ReviewBundle, current_candidate: dict) -> ValidatedAudit`；保留完整原始报告并提供 decision/production_pass/feedback。
- `qc_registration(audit: Callable[[Path, AgentContext], Awaitable[dict]], *, bundle_root: Path) -> AgentRegistration`：输入仍接收现有 CANDIDATE_FORMAT，在 quality_critic context 内读取共享记录、调用受信 A4 回调、保存完整审核证据、转换为框架 ReviewRecord。
- QC ReviewRecord.details 使用 qc_contract=local-manifest-v1/audit-report-v3 标识，含 raw report/candidate_link/production_pass/feedback；不提供来自 repair_hint 的 action/target。

- [ ] Step 1: 加入 test_bundle_uses_original_copy_ids_and_relative_image；title 声明框 [36,64,684,216]，subtitle [36,1160,684,1216]；hero asset_key=hero_image、element_ids=[hero]；image.path=poster.png。约束 ID 由初始 request identity+原始位置稳定生成，文案从 Brief 取。
- [ ] Step 2: 加入输入 schema 用例：多余字段拒绝，layout_context={} 拒绝，null/空数组区别保留，越界 bbox 不截断，原始约束 JSON 值不改写，required_text 启用必需文案检查。
- [ ] Step 3: 加入 test_old_report_rejected_for_identical_pixels_new_revision；分别篡改 sample_id/hash/revision/dependency，均拒绝。
- [ ] Step 4: 加入 test_fake_or_incomplete_pass_cannot_release：Fake pass、必需检查未覆盖、blocking/major、关键词/约束/文案 ID 覆盖失配均不可生产放行。原完整输出未知扩展字段可留在 raw report，不丢 calls/model 等记录。
- [ ] Step 5: Run `python -m pytest tests/visual/test_qc_bridge.py -q`，Expected: 新桥不存在或功能缺失 FAIL。
- [ ] Step 6: 实施严格输入模型与包导出。入站 report 要求合同核心完整，不能按文档节选当完整报告；调用记录类型未在文档定死时仅保留原 JSON，不猜 SDK 特有结构。
- [ ] Step 7: 检查 20 MiB/单帧/720x1280、候选身份与 artifact hash；审核前后均验证当前身份。支持搬移目录后图片可打开；执行错误保存错误，不能伪造 unknown。
- [ ] Step 8: Run 局部与全套，Expected: PASS；Commit `feat: export version-bound A4 QC review bundles`，更新账本。

## Task 3: 外部授权接入与局部返工，不修改 A1/A2

**Files:** Modify src/poster_framework/{types.py,workflow.py,__init__.py}; src/poster_visual_producer/agent.py; Create examples/visual/qc_collaboration.py; Test tests/visual/test_qc_collaboration.py。

**Interfaces:**
- `RepairAuthorization`：authorization_id、based_on_candidate(VersionDependency)、action(RevisionAction)、target(VersionDependency)、expected_versions({brief_version,spec_version})、constraints(tuple[str,...])、preserve(tuple[str,...])；由受信外部接入回调返回，不从 A4 hint 自动产生。
- `RepairAuthorizer = Callable[[dict, AgentContext], Awaitable[RepairAuthorization | None]]`。
- PosterWorkflow 可选参数 `repair_authorizer=None`；旧合同路径保持不变，新 QC fail/unknown 导出完整反馈，再调用已配置的外部授权入口。没配置或返回 None → needs_review，不重跑 A1/A2、不生图。
- callback 是接入程序的 A1 授权适配入口，不要求 A1 类增加 coordinate_review 或改 Agent.run。调用记录明确区分外部授权事件与真正 Agent.run，不伪造一次 A1 模型调用。
- 授权写入运行证据与 revision history；保留原 raw decision。授权仅用于路由和范围校验，不能把 unknown 改成 fail 以满足旧 ReviewRecord.action 校验。

- [ ] Step 1: 新增 test_qc_unknown_without_authorization_never_generates_again：初始 image_ops=1，保存完整 uncertainty 与反馈，结果 needs_review；A1/A2 初始各运行一次。
- [ ] Step 2: 新增 test_external_authorization_regenerates_only_target，双素材中仅 hero 替换；检查另外素材哈希、文案/布局不变；新候选再进入 A4；不额外运行 A1/A2。
- [ ] Step 3: 新增 test_unknown_authorization_preserves_original_report 和 test_stale_authorization_rejected_before_image_call，真实未知报告保留、当前依赖和 target 全部校验。
- [ ] Step 4: 新增 test_qc_hint_does_not_authorize、test_fake_qc_pass_stops_before_release，以及重渲染授权零额外生图；repair_hint 的 asset/layout/render 字样均不能自动改变路线，raw decision=pass 但 production_pass=False 也不得放行。
- [ ] Step 5: Run `python -m pytest tests/visual/test_qc_collaboration.py -q`，Expected: 缺外部授权入口和正确 QC routing FAIL。
- [ ] Step 6: 在新 QC marker 路径先校验 production_pass 放行条件，再接入反馈/授权阶段；利用已有 _validate_revision_target/_begin_revision/预算与防重复问题机制。新的阶段数量计入 graph recursion limit，旧分支不变。
- [ ] Step 7: 校验并持久化授权身份/expected_versions/constraints/preserve；拒绝 stale/非法目标/字段类型，不把未知反馈直接传入 generate。将已验证修复约束放入现有 repair_plan 通道。
- [ ] Step 8: 实施联调示例：明确标记的工程 A1/A2 发布器、真实 A3 renderer、Fake A4 合同和受信授权回调；最终输出工程协作证据，不宣称生产视觉通过。
- [ ] Step 9: Run 新协作测试、tests/test_f3_workflow.py、既有视觉回归，再全套；Expected: PASS 且旧 agent 接口未变；Commit `feat: gate QC repairs on external authorization`，更新账本。

## Task 4: 云端 Images JSON 后端

**Files:** Create src/poster_visual_producer/cloud_backend.py; Modify backends.py; Create examples/visual/backend-cloud.example.json; Test tests/visual/test_cloud_backend.py。

**Interfaces:**
- `CloudAPIConfig` 严格配置：provider/model、完整 generations endpoint、api_key_env、sizes、timeout_seconds、固定允许 sampling parameters；API Key 只在执行时取环境。
- `CloudImageGenerator(config: CloudAPIConfig)` 实现现有 capabilities 与 generate；`create_generator(kind=cloud_api)` 返回它，kind=factory 继续有效。
- 使用标准库 urllib；构造单图片请求，响应支持 b64_json 或 URL；按实际响应生成 GeneratedImage 与 request_id/usage/provenance。Key 不进入 fingerprint/log/provenance。

- [ ] Step 1: 用受控本地 HTTP 服务测试真实请求/响应：test_base64_image_flows_through_real_a3，检查实际 POST、PNG/manifest、image_ops=1，并非只 assert mock 被调用。
- [ ] Step 2: URL 返回由另一路由供图：test_image_download_has_no_api_authorization，下载端拒绝含 Authorization 的请求，检查仍能生成正确 Candidate。
- [ ] Step 3: 测试无 Key、未知字段、损坏 base64/图片、过大响应、非成功状态和超时；明确已提交的图片下载失败仅一次 generations 请求且不发布 Candidate。
- [ ] Step 4: 无 Torch/Diffusers 的 subprocess import guard 运行云端真实 A3 路径；不同 endpoint/model/provider 不误用缓存。
- [ ] Step 5: Run `python -m pytest tests/visual/test_cloud_backend.py -q`，Expected: 缺 cloud backend FAIL。
- [ ] Step 6: 实施异步 to_thread、严格参数/URL 配置和体积限制。response_format/quality/output_format 等仅由明确协议配置发送；seed/独立负面提示默认不支持，沿用现有显式适配或拒绝。
- [ ] Step 7: 传输分类默认 unknown，确认 rejected/not_submitted 才可 retry_safe；错误信息不回显凭据或签名 URL。生成响应 revised_prompt 若存在保留为实际来源信息。
- [ ] Step 8: Run 局部与全套；Expected: PASS；Commit `feat: add configurable cloud image generation backend`，更新账本。未提供用户凭据时不作真实外部 API 成功声明。

## Task 5: Qwen-Image 本地后端及显式下载流程

**Files:** Create src/poster_visual_producer/qwen_backend.py; scripts/fetch-qwen-image.py; Modify backends.py; Create examples/visual/backend-qwen.example.json; Test tests/visual/test_qwen_backend.py/test_qwen_download.py。

**Interfaces:**
- `QwenImageConfig`：model_dir、model_id=Qwen/Qwen-Image、40 字符 revision、steps(1..100)、true_cfg_scale(>1)、dtype=float16|bfloat16、cpu_offload、sizes；默认不宣称其他 checkpoint 支持。
- `LocalQwenImageGenerator(config)` 实现现有接口，延迟导入 torch/QwenImagePipeline，local_files_only=True；实际 parameters 进入 fingerprint。
- 下载脚本仅显式运行：指定 --model-dir/--revision，使用已锁定 huggingface-hub 的恢复下载缓存与固定 revision；根据官方文件列表验证完整组件、内容哈希/大小，再写 poster-model-manifest.json。生成阶段只核验/读本地。
- `verify_qwen_weights(config)` 比较 manifest/model_id/revision，必要组件和全部分片存在；不能接受任意文件集合伪装完整权重。

- [ ] Step 1: 在推理边界使用轻量 test double，真实 generator/A3/fingerprint 不替换；检查 true_cfg_scale、negative_prompt、尺寸、seed、dtype/offload 的实际传入。
- [ ] Step 2: test_qwen_sampling_change_invalidates_cache 与 test_qwen_repair_uses_runtime_budget；使用真实 A3 记录，配置改变重新生成、目标修复保留其他素材。
- [ ] Step 3: 检查缺依赖/无 CUDA/不支持 bf16/缺分片/错 revision/错哈希；失败无占位图，不隐式下载。验证未完成 manifest 不被接受。
- [ ] Step 4: 下载测试仅替换远端 hub I/O，校验固定 revision、恢复/已有文件核验、缺文件或校验失败不发布完整 manifest。模型目录带空格仍可用。
- [ ] Step 5: Run 两个新文件；Expected: Qwen backend/download 不存在 FAIL。
- [ ] Step 6: 实施配置/能力/loader、CPU rng、推理锁、PNG 编码、实际用量/来源与异常分类；不引入 editing/inpainting。
- [ ] Step 7: 实施显式下载脚本与 manifest 生成；保持 SDXL 已有下载行为兼容。
- [ ] Step 8: Run 局部与全套；Expected: PASS；Commit `feat: add optional local Qwen Image backend`，更新账本。没有本机完整权重时标记真实 Qwen GPU 验证未执行，不发起大型下载。

## Task 6: 后端配置引导与无生图环境诊断

**Files:** Create src/poster_visual_producer/configuration.py/setup_cli.py; Modify __main__.py/backends.py; Test tests/visual/test_setup_cli.py；配套三个 example profile。

**Interfaces:**
- `backend_template(kind: str) -> dict` 为三个后端生成安全模板；model path/Key 通过环境变量名配置，不预填本机绝对路径。
- `write_backend_config(path: Path, config: dict, *, overwrite: bool=False) -> Path`。
- `diagnose_backend(config: dict, *, fonts_config: Path | None, font_base: Path, output_root: Path | None) -> dict`：ready/checks，问题条目含 code/message/remediation；stdout 无 Key。
- `python -m poster_visual_producer setup --backend KIND --output PATH`；省略 backend 时交互选择；`doctor --backend-config PATH --fonts PATH --font-base PATH [--output-root PATH]` 返回 JSON/exit 0 ready，exit 1 未就绪。旧 A3 CLI 参数路径继续有效。

- [ ] Step 1: subprocess 执行三种 setup，检查生成可读 JSON、缺必要参数能由 doctor 精确发现；已有文件不覆盖，--overwrite 才覆盖。
- [ ] Step 2: doctor 检查环境变量、字体、输出目录可写性、配置 schema；不调用 generator.generate、不下载或安装，不把只安装未配置说成 ready。
- [ ] Step 3: 云端 doctor 在没有 Torch/Diffusers 时可运行；传入包含敏感值的错误配置时输出不得回显 Key；路径含空格可用。
- [ ] Step 4: Run `python -m pytest tests/visual/test_setup_cli.py -q`，Expected: 未实现子命令 FAIL。
- [ ] Step 5: 实施只选择/写模板和非收费诊断；避免通过导入 factory 自动执行不受信任代码，factory doctor 仅标明需应用负责检测。
- [ ] Step 6: Run 新用例及 test_cli_tools.py/test_backend_cli.py，再全套；Expected: PASS；Commit `feat: guide backend setup and report environment readiness`，更新账本。

## Task 7: 交付、安装文档、协作证据及完整验证

**Files:** Modify README.md/docs/visual-producer.md/scripts/package-manifest.txt；Create docs/backend-setup.md/docs/qc-handoff.md；必要时更新 pyproject.toml/uv.lock 的可选 backend extras；Create examples/visual/portable_acceptance.py；Test tests/visual/test_portable_acceptance.py。

**Interfaces:**
- `portable_acceptance.py --root PATH`：离线运行真实生产/渲染/缓存/QC/授权目标校验，保存 Candidate、审核包、feedback、授权、前后哈希、实际用量和 call sequence。
- 汇总必须区分工程合同通过与 production visual pass；真实 A4 模型未接入不能声称质量放行。
- 文档提供 GitHub 获取、Conda Python 3.11、uv 显式 targeting、基础 locked 依赖、selected backend 安装、字体下载和 CLI 命令；三路径并列。

- [ ] Step 1: 新 acceptance 用例先验证标题移动额外 image_ops=0、指定素材修复额外=1、文字/无关素材保留、旧 QC 拒绝与 unknown 无授权停止；Expected: 未有验收程序 FAIL。
- [ ] Step 2: 实施验收程序，运行并输出结构化证据。CLI 的指导流程不能替代实际协作测试。
- [ ] Step 3: 编写基础/SDXL/Qwen/cloud 安装文档和支持矩阵；Linux/macOS 命令仅标注安装路径、未经实测的 GPU 配置不能标为已验证；不要求 A1/A2 修改。
- [ ] Step 4: 配置示例与安装方式检查；若新增 extras，更新 uv.lock 并检查 uv lock --check；保留既有 local-gpu 安装兼容性，不升级已锁定栈来追逐新模型。
- [ ] Step 5: 更新明确 source archive 清单；build wheel、source archive，逐成员哈希验证。wheel 使用受控 scratch target 检查包导入、三个后端 factory、CLI help 和资源定位；模型/Key 不进入包。
- [ ] Step 6: Run 全套 pytest、三个 setup/doctor smoke、旧 upstream CLI、portable_acceptance；Expected: 全通过，验收 JSON 能追溯到真实文件与用量。
- [ ] Step 7: 一位独立只读 reviewer 核对用户目标、未修改 A1/A2、关键授权边界、测试盲点与文档。严重发现以 RED→GREEN 修复并重跑相关/全套；未完成真实后端/A4 验证明确留在交付说明。
- [ ] Step 8: Commit `docs: ship portable A3 setup and QC collaboration evidence`，更新账本；报告实际结果、路径和限制。保持分支本地，不合并、不推送、不发布。

## Plan self-review

已覆盖批准设计各节和用户追加的 A1/A2 不修改约束。接口顺序：Task 1 metadata → Task 2 QC bundle → Task 3 authorization loop；Tasks 4/5 使用现有生成接口；Task 6 只引用后端配置与 factory；Task 7 汇总验收。五个 Review Focus 均有对应行为用例。

仅隔离外部 HTTP 或大型本地推理层；保留真实 Runtime/Renderer/QC 组装/授权路由。不以源码字符串匹配作为功能测试，不为人类说明文档添加镜像测试。不把 Fake 审核或测试推理 double 当真实模型质量证据。

本计划等待用户书面审阅与执行方式选择。推荐 Native，本会话依序实施并最终进行独立只读审查；不默认分派多个实现 Agent。

