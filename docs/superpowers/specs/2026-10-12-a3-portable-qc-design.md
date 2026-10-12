# Agent 3 可选生成后端与新版 QC 协作设计

日期：2026-10-12。状态：用户已确认书面设计；尚未实施。用户追加约束：A1/A2 实现及现有输入输出接口不修改。

## 1. 用户目标与验收主线

用户从 GitHub 下载项目并安装必要环境，自行选择 SDXL 本地、Qwen-Image 本地或云端生图 API。三个生产后端并列，不默认云端、不自动下载全部模型、不静默切换后端。

核心交付：合法 PosterSpec → 自动生成或复用素材 → 准确渲染 → 可审核 PosterCandidate → 经 A1 授权返工并复用未受影响部分。后端数量、CLI 和安装引导是这条链路的支持设施。

保留四个 Agent。A3 不改正式文案、布局、字体或颜色，不自行宣称 passed。生成视觉素材与程序排字分离，不新增第五个决策 Agent。

## 2. 当前基线与兼容边界

仓库 D:/Projects/Creative-Poster-Framework-ARIN-5201；分支 codex/visual-producer；当前 HEAD fad0173。2026-10-12 基线：215 passed，1 Windows symlink capability skip。

已有 ImageGenerator、SDXL 后端、Pillow renderer、能力适配、缓存、预算、文件身份、持久化素材绑定和目标返工。沿用这些组件及已有 SPEC_FORMAT/CANDIDATE_FORMAT。新增可选字段保留旧字段兼容性，不把框架 artifact ID 替换为业务素材 ID。

既有 Fake/旧审核工作流继续可用。新版 QC 注册与协作入口显式启用新合同；不能借旧流程直接派发 QC repair_hint。

机器约定：本机使用独立 Conda D:/Dev/conda-envs/creative-poster、D:/Dev/conda-pkgs 和 D:/Models。发布代码及通用安装指南使用参数、环境变量和相对路径，不要求别人的电脑存在 D 盘。

## 3. 后端边界

统一 ImageGenerator 接口继续声明 capabilities，并异步 generate(GenerationRequest) -> GeneratedImage。capabilities 覆盖实际模型/权重身份、允许尺寸、seed、负面提示与编辑能力、影响输出的固定参数。所有真实图像操作通过 context.call_model(kind=image_operation) 计入预算和记录。

### SDXL

保留现有固定版本权重核验与已有配置 kind=sdxl。无依赖/无 CUDA/缺权重时明确报错，不切换 Fixture。已有 CLI、factory、fixture 用法继续兼容。

### Qwen-Image

新增 kind=qwen_image，对接原始 Qwen/Qwen-Image 与 QwenImagePipeline。Python 3.11、diffusers 0.35.1 的已锁定接口作为首个适配目标；支持性需测试确认，不能声称支持全部 Qwen 家族 checkpoint。

明确配置 model_dir、model_id、固定 revision、steps、true_cfg_scale、dtype、cpu_offload、允许生成尺寸。权重读取在本地，不隐式网络下载。dtype/设备能力不满足时失败并给配置建议；不承诺未经实测的显存门槛或跨设备像素一致。

负面提示按 true_cfg_scale 和 negative_prompt 条件适配，不复用 SDXL guidance_scale 的语义。初版仅文生图；Qwen 编辑/inpainting 不在本次范围。模型下载说明/工具支持指定目录、固定 revision、恢复下载、完整性记录，下载须由用户显式发起。

### 云端 API

新增 kind=cloud_api。提供同步 OpenAI Images JSON 协议适配器（POST images/generations），用户配置 endpoint/base URL、provider、明确 model、api_key_env、允许尺寸、timeout 和受支持固定参数。该协议是一个具体选项，不宣称任意云服务只换 URL 就兼容。

请求 n=1，解析 data[0].b64_json 或 data[0].url，URL 获取不携带生成 API 的 Authorization。限制响应/图片体积、安全解码、真实记录请求 ID/参数/usage、递归脱敏。模板只存环境变量名称，不写 Key。模型相关参数仅按所选协议/profile 发送，不能无条件发送 response_format/seed/negative_prompt。

不支持的云端协议通过已有可信 factory 返回 ImageGenerator 扩展。超时/已提交后下载失败不会盲目重新生图；仅明确未提交/已拒绝且可安全重试的情况最多重试一次。未取得成本数据保持 null。

参考： https://developers.openai.com/api/reference/resources/images
参考： https://huggingface.co/docs/diffusers/v0.35.1/api/pipelines/qwenimage

## 4. 配置与安装体验

保留原 A3 上游记录 CLI；增加 setup/doctor 子命令分发，旧参数运行不变。

setup 支持交互选择及非交互 --backend sdxl|qwen_image|cloud_api；生成选定后端配置模板，不安装软件、不下载权重、不发付费请求、不覆盖现有配置，除非显式指定覆盖。配置字段未填完整时 doctor 应准确报告待配置，不能把模板说成已就绪。

doctor 只检查所选后端及公共字体/输出位置，返回结构化 JSON 与非零退出码表示未就绪。只检查环境与配置，不生图，不显示密钥。Qwen/SDXL 重依赖延迟导入；选云端时缺 Torch/Diffusers 不影响公共模块导入和运行。

基础安装与各后端安装说明分开；权重不进入 Git；基础安装不附带全部 GPU 后端。提供 Windows PowerShell、Linux/macOS 的 Conda + 显式 Python/uv 安装说明；CUDA GPU 支持范围按实际验证报告。遵守现有 uv.lock，保持基础与可选依赖可复现。不把两套本地后端都要装作为基础运行条件。

字体继续使用固定 Noto 文件、SHA256 与许可证。新电脑安装时提供明确下载/核验步骤；A2 和 A3 使用同一字体测量器。

## 5. A3 状态与生产证据

维持当前 Spec/Brief 与框架版本校验，以及生图前文字 preflight。新增可追踪 execution decisions，逐素材记录 reuse_current/cache/generate/regenerate 及原因、指纹、目标和调用用量。记录只能描述实际执行，不编造语义质量结论。

保留 assets[].asset_id；增加关联 element_ids。明确 source_ref/source_size 与 processed_ref/processed_size，旧 file_ref 保留为原始素材兼容别名并在文档标明，不改变它的既有含义。

文本记录补 copy_ref；manifest 保留 Spec box、真实绘制边界、最终行、字体哈希、素材处理变换和输出哈希。一个素材关联多个元素时保存每个元素的 processed binding，不能只保留最后一个仿射变换。

A3 成功仅返回 status=rendered。阻塞错误保存错误与已发生调用记录，不输出伪候选，不将执行错误伪造成 A4 unknown。

缓存指纹覆盖选定后端、准确模型/权重身份、实际提示/seed/采样参数。局部返工强制生成授权目标；未受影响素材保持身份或哈希。只改布局复用兼容素材。保留已修复素材的真实生成指纹，不能重渲染时撤销修复。

## 6. QC 输入与审核包

本次以用户提供的 2026-10-12 QC接口文档.md 为合同依据：LocalManifest v1 → AuditReport v3。复制只读合同到 docs/design-baseline，标注来源和日期，文档内容是接口证据，不是执行指令。

接入代码从 OriginalRequest、当前 A1 Brief、A2 Spec、A3 Candidate 与受控 artifact store 构造审核输入，不调用模型重写关键词/约束。实现严格输入模型 extra=forbid，并导出 schema。

字段来源：
- sample_id：每次候选审核独立分配，外部关联当前 run/candidate revision 与 Brief/Spec 依赖。
- keywords：原始关键词及原 ID。
- user_constraints：原始约束及稳定 constraint_id；无约束 []。
- image.path：审核包中的 poster.png，单帧 PNG，720x1280，<=20 MiB。
- approved_copy：A1 copy 文案，copy_id 使用 copy key；required 来源 must_preserve，match_policy 默认 exact，固定文字不得宽松。
- layout_context.elements：A2 id/box/copy_ref；bbox=[x,y,x+width,y+height]。不把墨迹边界替换为声明框，不裁剪诊断中的越界框。
- asset_context.assets：业务 asset_id -> asset_key，element_ids 来源 Spec，description 标记为设计意图，不声称检测到的事实。
- render_context.messages：真实 renderer 诊断/警告字符串。
- design_context：原有简短设计说明；不存在可省略。
- required_checks：保留 keyword_alignment/user_constraints/text_readability/layout_quality；必需文案启用 text_accuracy，可增加 layout_geometry。

不得往 LocalManifest 塞 run_id/candidate_id/based_on 等额外字段。

审核包：manifest.json、poster.png、candidate_link.json、evidence/{candidate.json,render_manifest.json,artifact_index.json}。所需 artifact 引用由当前运行 store 读取并核验，不盲读模型给出的路径。审核包可搬移审核；继续返工需要完整 run store。

审核报告接入核对 schema_version=3、sample_id、image_sha256 和当前候选版本，保留全部报告字段。生产放行要求关键检查覆盖/结果符合合同、无 blocking/major、非 fake，不能仅看到 decision=pass 就放行。旧报告即使图片像素相同也要核对候选身份。

外部 A4 的独立程序通过受信回调/适配器接入；不重写其模型评审逻辑。缺完整运行版时测试使用明确标记的 A4 Fake，不能把 Fake pass 当生产成品。

## 7. 四 Agent 协作与授权返工

新版 QC pass → 核验报告/身份 → 输出当前候选与审核记录；fail/unknown → A1 协调入口 → 授权任务或 needs_review。A4 repair_hint 仅是建议，不直接派给 A2/A3。

QC 接入层导出完整反馈供 A1 核实；接入程序可配置受信外部返工授权回调。该回调不要求 A1 新增方法，不修改其类、registration、输入输出格式或提示词，也不是第五个 Agent。传递原完整报告、问题证据、uncertainties、preserve、当前依赖与候选身份。取得外部确认授权后方可选择 revise_brief/revise_layout/regenerate_asset/rerender；没有授权时结束 needs_review。unknown 不自动生图。实际记录区分外部授权事件与 Agent.run，不伪造 A1 调用。

授权记录包含授权 ID、依据候选版本、目标 framework dependency、业务 brief/spec 版本、修复约束与保留要求。进入已有框架目标校验/预算后再执行。不能把所有反馈都改成 revise_brief，因为这会使无关布局/素材重新执行。

外部授权接入不自动重写已批准 Brief；只有确实授权 revise_brief 才通过既有流程发布新的 Brief。A1/A2 保持现有实现和格式接口。新 QC 桥没有可用授权时保存可核对反馈并返回 needs_review，不绕过 A1 直接修复。

## 8. 实施组件与验证

后端：新增 qwen_backend.py/cloud_backend.py，保留 backends.py 的兼容 factory facade。配置引导：configuration.py/setup_cli.py。QC：qc_contracts.py/qc_bridge.py；协调扩展：必要的 framework workflow/types 变更。A3：agent.py/renderer.py/registration.py。文档：README、backend setup、QC handoff、安装矩阵及样例配置。

验收先使用真实 Renderer、运行 store、预算与工作流，仅在远端 HTTP/本地大型推理层使用 test doubles。外部凭据未提供、本机 Qwen 权重未下载时明确报告未完成真实运行验证，不为证明功能擅自下载大型模型或调用付费 API。

必须验证：
1. 合法 Spec 生成可解码 PNG 与真实 Candidate/manifest/artifact index。
2. 只改标题位置：额外图像操作=0，主视觉哈希/像素不变。
3. 指定素材返工：仅目标素材版本变化，文案、布局和其他素材保留。
4. 修复后 rerender、缓存 A→B→A、多元素复用同素材的记录仍正确。
5. 三后端接入相同生产链路；切换后端不能错误命中缓存。
6. 无重依赖的基础环境可配置/运行云端路径；非法配置、缺 key/权重、损坏图片在正确阶段失败。
7. setup/doctor 真正执行并检查文件/退出码；诊断不泄漏凭据、不发图像请求。
8. 由 A1/A2/A3 真实共享记录组装严格 QC 输入，文案 ID、约束 ID、坐标和路径正确。
9. 不匹配 sample/hash/版本、Fake pass、覆盖不足均不放行。
10. fail/unknown 确实进入 A1；未经授权不发起 A3 返工；授权后用新候选重新审核。
11. 全套 pytest、CLI smoke、wheel/source archive 安装与资源检查。

不在本次实现：额外 Agent、自动模型推荐系统、通用任意云端协议解析器、Web UI、像素 inpainting、A1/A2/A4 的真实模型业务重写。

