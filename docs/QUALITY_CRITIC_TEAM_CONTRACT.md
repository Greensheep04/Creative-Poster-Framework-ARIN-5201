# A4 审核接口：输入与输出

更新：2026-10-12。输入为 **LocalManifest v1**，输出为 **AuditReport v3**。

A1、A2、A3 分别产出各自的信息；A3 完成最终候选后，由接入程序汇总输入交给 A4。`pass` 自动放行并输出当前候选；只有 `fail/unknown` 返回 A1。**A4 的分流、回调和 unknown 诊断已实现；接入方负责组装输入、连接组员的真实 A1 函数及框架版本。**

## 1. 输入

接入程序从共享上下文读取 A1 的原始要求与正式文案、A2 的当前布局、A3 的最终图片与素材/渲染记录，组装一份 JSON 和对应的成品图片。汇总由普通代码完成；原始关键词和硬约束直接读取运行初始记录，不能在下游传递时改写。

| 字段 | 必填 | 提供内容 |
|---|---|---|
| `sample_id` | 是 | 本次候选审核的唯一标识，由接入程序分配 |
| `keywords` | 是 | A1 保留的原始 2–5 个关键词，每项 `{keyword_id, text}`，ID 不重复 |
| `user_constraints` | 是 | 原始用户约束，每项 `{constraint_id, name, value}`，ID 不重复；无约束填 `[]` |
| `image.path` | 是 | A3 的最终成品图片路径，相对路径以输入 JSON 所在目录为准 |
| `approved_copy` | 否，建议提供 | A1 确认的正式文案，每项 `{copy_id, text, required, match_policy}` |
| `layout_context.elements` | 否，建议提供 | A2 当前采用的布局，每项 `{element_id, bbox_xyxy, copy_id}` |
| `asset_context.assets` | 否 | A3 的素材信息，每项 `{asset_key, element_ids, description}` |
| `design_context` | 否 | A1/A2 的简短设计说明，字符串 |
| `render_context.messages` | 否 | Renderer 的诊断信息，字符串数组 |
| `required_checks` | 否 | 需要执行的检查名称，默认四项基础检查 |
| `schema_version` | 否 | 默认整数 `1` |

嵌套字段：若提供 `layout_context` 或 `asset_context`，其中 `elements` 或 `assets` 必须存在（可为 `[]`），不能只传 `{}`。

- 文案：`copy_id/text` 必填且 ID 不重复；`required` 默认 `true`，`match_policy` 默认 `exact`，也支持允许排版差异的 `typographic`。用户固定文字用 `exact`。
- 布局：`element_id/bbox_xyxy` 必填且 ID 不重复；`copy_id` 默认 `null`，非空时应引用正式文案 ID。
- 素材：`asset_key` 必填且不重复；`element_ids` 默认 `[]`，`description` 默认 `null`。渲染信息 `messages` 默认 `[]`。
- `user_constraints.value` 可为 JSON 值；`name=required_text` 时须为非空字符串，A4 会增加 `copy_id=constraint:<constraint_id>` 的必需文案。

格式要求：

- 图片：单帧 PNG/JPEG，**720×1280**，不超过 **20 MiB**；A4 自行计算图片 hash。
- 坐标：最终画布像素 `[x0,y0,x1,y1]`，左上角为原点；不是 `[x,y,width,height]`。越界值如实保留给 A4 检查。
- 四项基础检查为 `keyword_alignment`、`user_constraints`、`text_readability`、`layout_quality`，不能删除；可增加 `text_accuracy`、`layout_geometry`。必需文案会自动启用 `text_accuracy`。
- 可选上下文可省略或填 `null`；`approved_copy=null` 表示未提供，`[]` 表示没有列出的文案。当前只检查声明框，不测量实际文字边界。
- JSON 禁止多余字段。`run_id/candidate_id`、Brief/Spec 版本由接入程序另存并关联；组装时核对图片、文案和布局属于同一候选版本，不能直接把这些版本字段塞入此输入。

示例输入：

```json
{
  "schema_version": 1,
  "sample_id": "team-demo-r1",
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

## 2. 输出与处理

| 字段 | 内容 |
|---|---|
| `schema_version` | 整数 `3` |
| `sample_id`、`image_sha256` | 对应本次输入标识和实际图片 hash |
| `input_fingerprint` | 绑定关键词、约束、文案、布局及图片；新报告自动提供，旧报告缺失此字段时可读取但不可自动分流 |
| `decision` | `pass`：通过；`fail`：已确认问题；`unknown`：证据不足 |
| `keyword_checks` | 每项 `{keyword_id, status, evidence}` |
| `constraint_checks` | 每项 `{constraint_id, status, evidence}` |
| `text_checks` | 每项 `{copy_id, observed_text, status, readability}`；无法辨认时 `observed_text=null` |
| `coverage` | 六项检查的覆盖情况，每项 `{check_id, required, state, status, reason}` |
| `issues` | 问题位置、严重程度、证据与修改建议，字段见下方 |
| `uncertainties` | 不确定项，字符串数组；说明检查对象、大致疑点、缺失证据及建议核实方向 |
| `repair_hint` | 可为 `null`；非空时包含 `{stage, element_id, asset_key, reason, preserve}` |

`issues[]`：`code` 为问题类型；`severity` 为 `blocking/major/minor`；`source` 为 `program/model`；`evidence` 是具体证据；`suggestion` 是修改建议；位置 `element_id/bbox_xyxy` 可为 `null`。

检查 `status/readability` 使用 `pass/fail/unknown`；`coverage.state` 使用 `assessed/not_assessed/unknown`，`status` 在未评估时可为 `null`。`repair_hint.stage` 类型允许 `brief/layout/asset/render`，**当前程序仅对证据完整、无 blocking 问题的明确布局几何错误生成 `layout` 提示**。

报告还带 `model/prompt_version/execution_mode/calls/vision_calls` 调用记录。接入程序在放行或派发反馈前核对当前候选及依赖版本；A1 只接收 fail/unknown，按上表字段处理。

| `decision` | 去向与动作 |
|---|---|
| `pass` | 生产审核的关键检查通过且报告匹配当前候选后，程序自动输出成品及审核记录，不再交 A1 决策；Fake 的 pass 仅用于联调 |
| `fail` | 把已确认的问题、证据、建议和保留要求交给 A1，由 A1 决定修改任务 |
| `unknown` | 暂不放行；把不确定对象、大致疑点、缺失证据和建议核实方向交给 A1 |

示例输出为**核心字段节选**，省略其他检查、其他问题和调用记录，不能按完整 `AuditReportV3` 解析。示例对应固定 Fake 观察，不是真实模型评价。

```json
{
  "schema_version": 3,
  "sample_id": "team-demo-r1",
  "image_sha256": "df5e18d68d9bb28d77b1fa858fe50f11a17f2f6fc2f3058a6f7e97ff8d073ce2",
  "decision": "fail",
  "issues": [
    {
      "code": "text_low_contrast",
      "severity": "major",
      "element_id": "title",
      "bbox_xyxy": [40, 90, 680, 180],
      "source": "model",
      "evidence": "标题与背景对比不足。",
      "suggestion": "检查标题配色和实际渲染结果。"
    }
  ],
  "uncertainties": [],
  "repair_hint": null
}
```

`unknown` 不只返回一个状态词。代码利用 `keyword_checks/constraint_checks/text_checks/coverage` 定位未完成的检查，并在 `uncertainties` 中自动补充“对象 → 观察到的疑点 → 缺少什么依据 → 建议下一步”；不能确定位置时保留 `null`，不猜测修复根因。

例如不能辨认标题时，说明“标题文字无法可靠转录，暂不能判断文字内容是否正确；建议核对实际文字参数并取得可读证据后重审”，不能直接认定“标题有错字”。若模型调用失败，说明未取得有效观察及已记录的错误原因，不凭空猜测画面瑕疵；未产出审核报告的执行错误按错误记录处理，不伪造 unknown 报告。

以下 unknown 示例为固定 Fake 观察及程序自动补充的核心字段节选，省略其他检查和诊断：

```json
{
  "decision": "unknown",
  "text_checks": [
    {"copy_id": "title", "observed_text": null, "status": "unknown", "readability": "unknown"}
  ],
  "uncertainties": [
    "检查对象：copy_id=title / text_accuracy、text_readability；疑点：文字无法可靠转录，尚不能判断文字准确性、正常观看时的可读性；缺失证据：能够核对当前成品文字准确性、正常观看时的可读性的可靠文字观察；建议：A1 核对 A2/A3 的文字参数与当前实际渲染，取得可读证据后重新审核，再判断是否需要改文案或渲染"
  ],
  "repair_hint": null
}
```

A1 对 unknown 或失败伴随关键证据不足时先核实；确认根因后再安排修改，预算不足或仍无法判断时记录原因并结束。已知 blocking/major 问题即使来自可选检查也会导致 `fail`。`repair_hint=null` 不表示无需修改，也不授权整图重生；A4 不直接执行 A2/A3 的修改。

调用入口：`audit` 是当前 `AuditInputV1`，`engine` 是已配置 Provider/Executor 的 `AuditEngine`。两个函数由接入方提供，参数都是 `DispatchResult`：通过回调从 `result.audit.image.data` 取得已审核的图片字节；A1 回调读取 `result.report` 和原始上下文 `result.audit`。

```python
from poster_critic.dispatch import audit_and_dispatch

result = await audit_and_dispatch(
    audit, engine,
    on_release=publish_poster,
    on_feedback=a1_receive_feedback,
)
```

`DispatchResult` 的封装版本为 `1`，报告仍为 `3`；包含 `route`（`release/a1/test_only`）、`action`、`recipient`、`handler_status`、`report`、`audit`、`input_fingerprint`。回调执行成功才返回 `handler_status=completed`；失败抛出异常，不重试副作用。Fake pass 为 `test_only`，不调用交付或 A1 回调。

现有 CLI 自动产生以下文件（假设 `--out report.json`）：

| 文件 | 含义 |
|---|---|
| `report.json`、`report.json.usage.json` | 审核报告与实际计量 |
| `report.json.dispatch.json` | 最后写入的分流完成凭据，含结果状态、路径、输入指纹；不能只凭退出码认定已交付 |
| `report.json.poster.png/jpg` | 仅生产 pass 生成的已审核成品 |
| `report.json.a1.json`、`report.json.candidate.png/jpg` | fail/unknown 的完整反馈与候选快照；状态为 `awaiting_a1`，等待组员 A1 消费 |

A1 文件包含 `recipient=creative_director`、`status=awaiting_a1`、`candidate_path`、`input_fingerprint`、`audit` 和 `report`；JSON 不内嵌图片 bytes，读取候选快照获取图片。所有路径均不覆盖已有文件。CLI 写出反馈不代表真实 A1 已执行；直接接 A1 函数时使用上面的回调。
