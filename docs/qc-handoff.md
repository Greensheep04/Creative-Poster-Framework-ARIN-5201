# 新版 A4 QC 交接与授权返工

原接口基线保存在 [QC 接口文档](design-baseline/2026-10-12-QC接口文档.md)。A3 的 PosterCandidate 保持原格式，增加可选生产证据；A4 收到严格 LocalManifest v1，返回完整 AuditReport v3。适配层属于 A3/框架接入代码，A1/A2 的实现、registration、格式与提示词没有改变。

## 审核包

`qc_registration(audit, bundle_root=...)` 仍接收现有 CANDIDATE_FORMAT，从共享上下文读取初始 OriginalRequest、当前 CreativeBrief/PosterSpec 和已发布 Candidate，通过受控 ArtifactStore 验证引用。审核包可以搬移：

```text
manifest.json             # 仅 LocalManifest v1，image.path=poster.png
poster.png                # 720x1280 单帧 PNG，<=20 MiB
candidate_link.json       # 框架候选与依赖身份，不属于 A4 manifest
 evidence/
  candidate.json
  render_manifest.json
  artifact_index.json
```

manifest 中的关键词保留初始 ID/text；用户约束按原始 request identity+位置生成稳定 ID，值保持原 JSON。正式文案来自 Brief.copy，以 copy_ref 对应 copy_id；必需文案开启 text_accuracy。layout_context 记录 Spec 的 `[x,y,x+width,y+height]` 声明框，不拿实际文字边界替代，也不裁掉越界框；asset_key 是业务素材 ID，element_ids 明确映射关联元素。提示词仅标为设计意图，不冒充模型观察。null/空数组与不合法空对象按接口区别校验。

candidate_link 保存 run_id、candidate_id、候选/manifest 文件引用、业务版本及框架依赖。导出前验证当前 Brief/Spec、候选图、render manifest 和 issued index_ref；审核后再比较框架依赖与业务版本。相同像素的新候选仍不能使用旧报告。旧 A3 若缺 index_ref，需显式适配并取得受控引用，不能根据模型给出的路径读任意文件。

## 接入真实 A4

在应用的集成入口使用现有 A1/A2 注册对象，不修改它们：

```python
from poster_framework import AgentSet, PosterWorkflow
from poster_visual_producer.qc_bridge import qc_registration

async def audit(manifest_path, context):
    # 读取该包的 poster.png/manifest，调用组员 A4 的实际入口。
    # A4 适配器必须用 context.call_model 记录真实 LLM/视觉调用与用量。
    return await application_a4_adapter(manifest_path, context)

workflow = PosterWorkflow(
    AgentSet([existing_a1_registration, existing_a2_registration,
              existing_a3_registration,
              qc_registration(audit, bundle_root=review_root)]),
    root=artifact_root,
    repair_authorizer=confirmed_repair_adapter,  # 可省略；省略时 fail/unknown 停止
)
```

`application_a4_adapter`/`confirmed_repair_adapter` 是应用需要提供的接入函数，此示例不是本仓库内置的 A4 模型。A4 的 SDK、凭据、prompt_version 与图像能力由 A4 维护；A3 的图像 API Key 不自动作为 A4 Key。不能仅在报告中写 calls 就当作框架预算已记录；所有真实调用需通过收到的 context 执行和计量。模型错误保存安全 qc-error 证据并抛出，不伪造 unknown 报告。

完整报告包含身份/hash、decision、各关键词/约束/文案检查、六项 coverage、issues、uncertainties、repair_hint、model/prompt_version/execution_mode/calls/vision_calls。仅复制文档里的节选会被拒绝。transport 扩展字段保留在原始报告；重复 ID 和缺失核心字段不接受。

只有真实执行模式的 pass、覆盖齐全且所有必需检查成功、正式文案可读并有实际 observed_text、无 major/blocking 和未解决 uncertainty，才可放行。文字 exact/typographic 语义由独立 A4 判定，A3 不根据设计文字制造成功观察。工程 Fake pass 永远不生产放行。

## 外部授权，不直接执行 repair_hint

fail/unknown 保存完整 audit-report 和 review-feedback，然后交给受信的外部 `repair_authorizer(feedback, context)`。这个入口用于接入程序取得 A1 确认或人工确认，返回 RepairAuthorization 或 None；不会额外运行 A1 Agent.run，也不要求 A1 新增 coordinate_review。事件证据明确记为 external_authorization，不伪造 A1 模型调用。

feedback 包含原始报告、当前 candidate、OriginalRequest、brief_dependency、layout_dependency。授权应使用这些当前身份，而不是复制 repair_hint：

```python
from poster_framework import RepairAuthorization, RevisionAction, VersionDependency

async def confirmed_repair_adapter(feedback, context):
    confirmed = await application_confirmation(feedback, context)
    if not confirmed:
        return None
    candidate = feedback['current_candidate']
    source = next(a['source_ref'] for a in candidate['assets']
                  if a['asset_id'] == 'hero_image')
    return RepairAuthorization(
        authorization_id=confirmed.authorization_id,
        based_on_candidate=VersionDependency(
            artifact_id=candidate['candidate_id'],
            revision=candidate['candidate_ref']['revision']),
        action=RevisionAction.regenerate_asset,
        target=VersionDependency(artifact_id=source['artifact_id'], revision=source['revision']),
        expected_versions=candidate['based_on'],
        constraints=['只修正指定主素材，保留批准文案与版式'],
        preserve=['title', 'subtitle'],
    )
```

这段代码的 `application_confirmation` 必须返回真实确认；工程演示中的固定授权仅用于测试。授权约束必须由接入层核实，可利用现有 revision_validator 验证需由上游履行的 preserve 业务规则。A3 的 regenerate_asset 保持 Spec/文案/其他 source 引用；rerender 复用全部 source。preserve 名称必须是当前 element_id/copy_ref/业务 asset_id，不能同时保留和替换同一目标。对 revise_layout/revise_brief，外部接入者需要保证上游返回的新 Spec/Brief 满足获批的保留条件；现有 A1/A2 的业务执行能力保持原样。

| action | target 引用 | 执行与复用 |
|---|---|---|
| regenerate_asset | 当前指定素材 source_ref | 仅目标重新生成；其他素材、版式、正式文字保留 |
| rerender | 当前 candidate_ref | 全部素材复用，重新处理/渲染，无新生图 |
| revise_layout | feedback.layout_dependency | 按既有 A2 路由；A3 对未改变素材条件复用，重新布局 |
| revise_brief | feedback.brief_dependency | 按既有 A1→A2 路由；新数据仍通过原接口校验 |

框架先验证候选/目标/expected_versions，才执行返工；过期授权在生图前失败。未知审核原 decision 继续保存 unknown，授权不会把它篡改成 fail。没授权时停止 needs_review；hint 的 asset/layout/render 字样不会自动触发工具。预算、有限轮次、重复问题保护继续生效。每次新候选重新进入 QC，不因返工工具成功而宣称 passed。

## 可复现的课程协作证据

```bash
python examples/visual/qc_collaboration.py --root runs/qca --scenario all
```

四场景使用明确标记的工程 A1/A2 数据发布器、真实 A3/Pillow renderer、Fake A4 完整合同和受信 fixture 授权。生成真实 PNG、审核包、候选决策和授权证据，不发网络请求。acceptance.json 及每场景 summary.json 检查：

- 无授权 unknown：1 次素材生成、0 返工，保持 needs_review。
- 双素材局部返工：初始 2 次、目标增加 1 次，其他 source 与文字/版式不变。
- rerender：1 次初始生成、返工不增加图像调用。
- revise_layout：标题位置更新，素材哈希保持，仍只有 1 次生图。

所有场景额外证明旧报告不能绑定同像素的新候选，最终均停在 needs_review。它证明接口、决策、工具、状态和受限返工可以协作；真实主题质量、OCR/美学和四个实际模型协作需接入组员 Agent 后另验。
