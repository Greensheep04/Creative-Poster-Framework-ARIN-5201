# Visual Producer 使用与交接说明

代码目录：D:/Projects/Creative-Poster-Framework-ARIN-5201  
Python 环境：D:/Dev/conda-envs/creative-poster，Python 3.11  
Conda 缓存：D:/Dev/conda-pkgs；uv 缓存：D:/Dev/uv-cache  
代码分支：codex/visual-producer。环境隔离使用 Conda，代码版本使用 Git。

## 本次实现

A3 接收 poster.spec@0.1.0，读取框架记忆中的当前 CreativeBrief，校验文案、关键词与依赖身份，输出 poster.candidate@0.1.0。它是受控执行策略 Agent：选择生成、复用、有限安全重试、指定素材替换或报错。没有额外 LLM 调用来重复决定确定性步骤。

运行时持有文件身份与哈希；原始素材以 asset 发布，处理后图片与清单以 evidence 发布，720×1280 RGB PNG 以 candidate 发布。每次调用只返回一张新候选。status=rendered 不等于 passed。

Python 模块：
- contracts.py：PosterSpec / CreativeBrief 与跨文档一致性。
- fonts.py / renderer.py：真实字体、字重、字形覆盖、共同测量和绘制。
- imaging.py：安全解码、EXIF 方向、cover/contain、等比适配及变换。
- generators.py：供应商能力、真实调用参数、可替换 SDK 回调。
- agent.py：预算、缓存、返工、产物与持久化复用。
- tools.py：A2/A3 共用 poster.measure_text_block。
- registration.py：格式声明、AgentRegistration 和项目预算。
- __main__.py：从 A2 单角色导出记录执行 A3。

## 环境安装与验证

在项目根目录执行：

```powershell
powershell -NoProfile -File scripts/setup-visual.ps1
& 'D:\Dev\conda-envs\creative-poster\python.exe' -m pytest -q
```

安装脚本始终指定 Conda 解释器，并通过现有 uv.lock 安装依赖，不创建 .venv、不安装进 base。environment.yml 固定 Python 3.11 环境；uv.lock 固定 Python 包。字体由官方 Noto 项目下载，manifest.json 固定 SHA-256；模型代码不能提供字体路径。保留 OFL 许可，字体二进制不进入 Git。

本机无法创建符号链接时，原框架的符号链接检查明确跳过；路径穿越与跨 run 检查仍执行。不会修改 Windows 开发者模式或管理员权限。

## 查看预期产物

```powershell
& 'D:\Dev\conda-envs\creative-poster\python.exe' examples/visual/render_fixture.py
& 'D:\Dev\conda-envs\creative-poster\python.exe' examples/visual/workflow_demo.py --root runs/visual-demo --scenario revise_layout
& 'D:\Dev\conda-envs\creative-poster\python.exe' examples/visual/workflow_demo.py --root runs/visual-demo-en --language en
& 'D:\Dev\conda-envs\creative-poster\python.exe' examples/visual/acceptance_report.py
```

render_fixture 输出 PNG 和真实渲染清单。workflow_demo 使用完整框架与真实 PNG，但 A1/A2/A4 是明确标识的确定性示例，主视觉是程序图形。其 passed 仅表示工程集成场景通过，不代表真实视觉审核结论。

acceptance_report 输出中文、英文、标题移动、主视觉返工和重渲染样张，保存实际调用数、文件哈希、文字保留和运行记录。稳定查看入口在 runs/visual-producer-preview/。

## 与组员连接

A2 输出格式必须声明 SPEC_FORMAT（poster.spec@0.1.0），业务 JSON 遵循 docs/schemas/PosterSpec.json。文案来自当前 Brief 的 copy 字段；Python 类型内名称是 copy_blocks。示例 k1/k2/k3 在真实框架中应映射到 OriginalRequest 生成的 keyword_id，不能另造 ID。

把 registration(generator, fonts) 放入原有 AgentSet；把 build_tool_registry(fonts, generator) 交给 PosterWorkflow，A2 调用同一文字测量工具。创建工作流时显式传 budget=project_budget()，采用最多 2 轮返工、3 次图像操作、3 次视觉审核。单角色 CLI 继承上游记录中的预算。

SDK 接入示例：

```python
from poster_visual_producer import CallableImageGenerator, GeneratorCapabilities, GeneratedImage, registration

capabilities = GeneratorCapabilities(
    provider="your-provider",
    model="exact-model-version",
    sizes=((1024, 1536),),  # 仅列出已确认支持的尺寸
    supports_seed=False,
    supports_negative_prompt=False,
)

async def invoke_sdk(request):
    # 凭据从环境/安全配置读取，不放入请求日志或 provenance。
    # 在这里把 request 映射为供应商实际参数，取得图片 bytes。
    response = await your_client.generate(
        prompt=request.prompt, width=request.size[0], height=request.size[1]
    )
    return GeneratedImage(response.image_bytes, request_id=response.request_id)

generator = CallableImageGenerator(capabilities, invoke_sdk)
visual_registration = registration(generator, fonts)
```

尺寸按接近目标比例选择。独立负面提示不受支持时，默认附加到正向提示并记录 adaptation；可用 negative_policy="error" 要求拒绝。显式 seed 不受支持会报错。模型素材仍可能包含伪文字或主体瑕疵，交由 A4 在真实图片上检查。

## 单角色入口

```powershell
& 'D:\Dev\conda-envs\creative-poster\python.exe' -m poster_visual_producer --upstream-record PATH_TO_A2_RECORD --root SAME_UPSTREAM_ROOT --output-record PATH_TO_A3_RECORD --fonts examples/visual/fonts.json --font-base . --fixture PATH_TO_TRUSTED_LOCAL_IMAGE
```

--fixture 是显式离线测试模式。真实调用改用 --generator-factory your_application.image_backend:create_generator。这个 factory 来自受信应用配置，不来自模型输出。上游 root 必须与原记录一致，记录必须是 run_single_agent_from_upstream/from_request 的单角色导出结果。

## 返工与失败

- rerender：保留原素材，只发布新候选，图像请求增量 0。
- regenerate_asset：只替换指定 framework asset_id/revision，用 replaces 记录关系。
- revise_layout：文案与素材语义未变时复用；语义改变时替换对应素材。
- RepairPlan 的 expected_versions 与当前业务版本不一致时拒绝。
- 素材缓存包含 provider、模型/权重、实际提示、参数等；修复实际指纹与基础语义指纹分开保存，避免重渲染撤销已修好的素材。
- 缺字体/字形、文字溢出、非法资源和失配规格在生图前失败。
- ProviderError 只有 retry_safe=True 且 submission_status 为 not_submitted/rejected 才允许最多一次重试；未知提交状态/普通超时不盲目重发。
- 每次尝试通过 context.call_model(kind="image_operation") 计数。预算耗尽由框架返回 needs_review。失败不会生成占位成功结果。
- 供应商元数据在进入业务产物前递归脱敏，费用未知时保持 null。

## 当前边界

新增本地 SDXL GPU 后端及人工 Brief/Spec 案例入口；实际实测状态见 [本地 GPU 进度](local-gpu-progress.md)，运行说明见 [本地 GPU 指南](local-gpu.md)。外部 API 供应商仍未选定，SDK 回调接口保留。真实质量、延迟和成本只能依据对应运行记录报告。本次不实现像素级 inpainting、自动抠图、在线编辑器或其他三个 Agent 的真实模型版本。

支持固定 RGB PNG、image/text 元素及三个模板 ID；每个素材槽位大小必须匹配 asset_plan.target_size。归一化主体区域是生成意图，不能当作检测到的主体证据。A4 应结合实际图像和 RenderManifest 审核裁切、文字可读性与风格。

独立代码审查与连续返工回归结果见 [审查与修复记录](visual-producer-review.md)。Windows CRLF/CR 仅在排版中规范化，正式文案原文保留；必需文案不能遗漏。SDK 元数据中的 header/value 及签名 URL 查询凭据会隐藏。


## 可移植后端与新版 QC（2026-10-12）

新增 `cloud_api`、`qwen_image` 与原有 `sdxl_local` 并列，统一经过同一个 A3 生产链路。配置、能力预检、可选 GPU 依赖及模型下载见 [backend-setup](backend-setup.md)。运行 CLI 与 A1/A2 旧输出保持兼容。

PosterCandidate 增加 `execution_decisions`（generate/regenerate/reuse_current/cache 的实际决定）、issued `index_ref`、素材 `element_ids/source_size/processed_bindings` 和 text_manifest `copy_ref`。同一素材可关联多个元素，processed_bindings 保存各自裁切/处理结果；旧 processed_ref/asset_to_canvas 明确定义为首个元素，保持单元素消费者兼容。

新版 QC bridge 导出严格 LocalManifest v1 与独立身份链接，校验完整 AuditReport v3，审核前后验证当前候选与依赖。外部 RepairAuthorization 控制返工范围，A4 hint 不直接执行，unknown 不隐式生图。细节和接入代码见 [qc-handoff](qc-handoff.md)，验收账本见 [portable progress](visual-producer-portable-progress.md)。A1/A2 实现、registration、正式格式和提示词不修改。
