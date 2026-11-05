# 组员接入指南

本指南面向负责一个海报角色或业务工具的组员。每个成员实现自己的 Agent、模型连接和业务校验器，再由工作流注册表连接角色。

## Layout Planner（A2）

`poster_framework.layout_planner` 把 `poster.creative-brief@0.1.0` 转成 `poster.spec@0.1.0`。字段与 `docs/schemas/PosterSpec.json`（A3 分支）一致，因此 A3 不需要为接收规格声明 `ContentAdapter`。旧的 `poster.creative-brief@1` 仍被接受。

```python
from poster_framework import AgentSet, PosterWorkflow, ToolRegistry
from poster_framework.layout_planner import registration, register_layout_tools

tools = register_layout_tools(ToolRegistry())
registrations = [
    creative_director_registration,
    registration(),
    visual_producer_registration,
    quality_critic_registration,
]
workflow = PosterWorkflow(AgentSet(registrations), root="runs", tool_registry=tools)
```

### 规划的确定性步骤

1. **读文案，不改文案。** `copy.title`（或 `copy.headline`）必需，`copy.subtitle` 为空时视为没有副标题；`elements[].text` 必须与 brief 文案逐字一致。
2. **校验原始关键词。** brief 若带 `keywords` / `keyword_texts`，其文本必须与 `original_request.keywords` 相同（顺序可不同，ID 始终来自原始请求）。
3. **选模板。** 默认 `framed_hero_v1`；可用 brief 的 `template_id`、请求约束 `template_id` 或 `LayoutPlannerAgent(template_id=...)` 固定。模板只提供受控槽位，不提供任意几何。
4. **翻译配色。** 从 brief 的 `palette.colors` / `palette_direction` 等字段提取 `#RRGGBB`，生成 `design_tokens.palette`；浅色模板取最亮的候选，深色模板取最暗的候选，文字颜色优先使用调色板中对比度 ≥ 4.5 的颜色，否则回退黑白。
5. **真实测量。** 每个文字槽位从模板最大字号向下试到最小字号，用 Pillow 实际渲染 ink bounds，同时检查宽、高和行数预算。放不下就抛 `TextOverflowError`，绝不截断、绝不悄悄缩到最小字号以下。
6. **绑定关键词。** 每个原始关键词至少绑定 `hero`；关键词文本出现在标题/副标题时追加对应元素，并写入 `layout_evidence`（优先使用 brief 的 `keyword_bindings` 解释）。
7. **自检后发布。** 依次执行 `validate_poster_spec()`、`check_layout_geometry()`、`check_planned_keyword_coverage()`；任一阻塞项都会让本阶段失败并返回明确错误码，而不是产出一个“看起来能通过”的规格。

### 工具

`register_layout_tools(registry)` 注册以下工具，只授予需要的角色：

| 工具 | 可用角色 | 作用 |
|---|---|---|
| `layout.list_templates` | layout_planner | 列出模板槽位、字号范围、行数上限与有意叠放 |
| `layout.get_font_registry` | layout_planner, visual_producer | 返回真实字体文件、字重、哈希与搜索路径 |
| `layout.measure_text_block` | layout_planner, visual_producer | 真实测量换行、ink bounds 与是否放得下 |
| `layout.check_geometry` | layout_planner | 越界、溢出、缺字、安全边距与未声明重叠 |
| `layout.check_keyword_coverage` | layout_planner | 每个原始关键词是否已安排到已知元素 |
| `layout.validate_spec` | layout_planner, visual_producer | 结构化校验，不抛异常、返回 `ok`/`error` |

`layout.check_keyword_coverage` 通过 `context.original_request` 取得关键词，因此必须由 Agent 通过 `context.tools.call()` 调用，不能凭空传入别的请求。

### 字体与环境

默认查找 Windows（微软雅黑、黑体、宋体）、Linux（Noto CJK、文泉驿）和 macOS（PingFang）的 CJK 字体。需要固定字体时使用环境变量：

- `POSTER_FONT_REGISTRY`：JSON 文件，显式映射 family key → 字重 → 字体文件：
  ```json
  {"families": {"noto_sans_sc": {"400": {"path": "fonts/Regular.ttf", "index": 0}, "700": "fonts/Bold.ttf"}}}
  ```
- `POSTER_FONT_DIR`：目录，按 `noto-sans-sc-bold.ttf` 这类“族名-字重”文件名注册。
- `POSTER_FONT_FAMILY`：A2 默认请求的 family key（默认 `noto_sans_sc`）；也可用 `LayoutPlannerAgent(font_family_key=...)` 或 `plan_poster_spec(..., font_family_key=...)` 显式指定。

字体文件或字重不存在时抛 `MissingFontError`，不会用别的字重冒充。字形覆盖使用渲染对比（与 U+10FFFD 的 `.notdef` 掩码相同即视为缺字），缺字会以 `MISSING_GLYPH` 报告；这是启发式判断，字幕/生僻字仍应人工抽查。

### 返工：`revise_layout`

框架把 `metadata.revision_action` 与 `revision_target` 传给 A2。A2 从运行记忆中按 `revision_target` 找回上一版 spec，然后：

- **有 `brief["layout_patch"]`**：按白名单字段（`box.x/y/width/height`、`font_size_px`、`line_height_px`、`max_lines`、`align`、`color`、`z`）修改元素，拒绝改 `text`/`copy_ref`，拒绝 `old_value` 不匹配的过期补丁，重新测量受影响文字并递增 `spec_version`。
- **没有补丁且模板未被固定**：轮换到下一个模板重新规划，文案保持不变。
- **没有补丁但模板被固定**：直接报错，避免“同一版式原样重发却假装修好了”。

```json
{
  "changes": [
    {"target_type": "element", "target_id": "title", "field": "box.y", "old_value": 64, "new_value": 84}
  ],
  "preserve_element_ids": ["hero", "subtitle"],
  "preserve_copy": true
}
```

### 直接调用

不经工作流时也可以使用纯函数，便于单测和离线调试：

```python
from poster_framework import OriginalRequest, plan_poster_spec

request = OriginalRequest.from_keywords("demo", ["海洋", "宁静", "蓝色"])
spec = plan_poster_spec(
    {"copy": {"title": "静海之蓝"}, "palette": {"colors": ["#0B2A4C", "#A0D8E8"]}},
    request=request,
    template_id="framed_hero_v1",
)
```

独立示例：`uv run python examples/layout_planner.py --root .scratch/layout-planner`（加 `--template`、`--title`、`--no-subtitle`）。

### A2 本地测试

A2 的测试集中在 `tests/layout/`，不依赖其他角色的 Fake 实现：

```bash
uv run pytest tests/layout -q
```

| 文件 | 覆盖内容 |
|---|---|
| `test_layout_contract.py` | 必填字段、坐标/颜色/引用校验；与 A3 的 `PosterSpec.json` 做约束级比对 |
| `test_layout_fonts.py` | 字体解析、缺失字重报错、CJK/拉丁换行、溢出判定、缺字检测、对比度 |
| `test_layout_templates.py` | 三模板槽位在画布与安全边距内、叠放声明合法、字号自适应、超长文案报错 |
| `test_layout_checks.py` | 越界、真实溢出、安全边距、未声明重叠、关键词覆盖（缺失/未验证/未知 ID） |
| `test_layout_planner.py` | Agent 端到端、模板固定、配色、关键词绑定、`revise_layout` 补丁与版本递增 |
| `test_layout_tools.py` | 工具注册、角色权限、参数严格校验、工具与库函数结果一致 |
| `test_layout_workflow.py` | 真实 `PosterWorkflow` 四角色串联，A4 发起 `revise_layout` 后重新规划并放行 |

常用筛选：

```bash
uv run pytest tests/layout/test_layout_checks.py -q
uv run pytest tests/layout -q -k "overflow or margin"
uv run pytest tests/layout -q -x          # 第一个失败就停
```

与 A3 的契约比对默认在找不到共享 schema 时跳过。合并前可指定 A3 分支的文件：

```powershell
New-Item -ItemType Directory -Force .scratch | Out-Null
git show origin/feature/visual-producer:docs/schemas/PosterSpec.json |
  Set-Content -Encoding UTF8 .scratch/PosterSpec.json
uv run pytest tests/layout/test_layout_contract.py -q
# 或直接用环境变量指向已有文件（不涉及编码问题）
$env:POSTER_SPEC_SCHEMA = "C:\path\to\PosterSpec.json"
```

> `-Encoding UTF8` 在 Windows PowerShell 5.1 与 PowerShell 7 都可用（5.1 会写 BOM）；测试按 `utf-8-sig` 读取，因此有无 BOM 都能通过。

> 若在非 ASCII 路径（例如中文用户名）下出现 `ModuleNotFoundError: poster_framework`，这是 Python 3.11 以本地编码读取 editable `.pth` 的已知限制。A2 测试与示例已自带 `src` 引导；其余测试可在运行前设 `PYTHONPATH=src`。

## 1. 定义内容格式与 Agent

为输入和输出声明明确格式名称及业务版本。`AgentInput` 与 `AgentOutput` 的 `content` 使用 JSON 数据；格式版本代表业务字段约定，框架会另行记录公共接口版本、文件修订号和哈希。

```python
BRIEF_FORMAT = ContentFormat(name="poster.brief", version="1")

class BriefAgent:
    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        request = context.original_request
        content = {
            "headline": request.keywords[0].text,
            "tone": "calm",
        }
        return AgentOutput(content=content, content_format=BRIEF_FORMAT)
```

Agent 可读取 `context.original_request`、`context.metadata`、`context.files`、`context.memory` 和 `context.tools`。调用模型服务时，将 SDK 调用放入 `context.call_model()` 的 `invoke` 函数；模型返回之后再校验并转换为所需 `AgentOutput`。

## 2. 注册角色和业务检查

```python
registration = AgentRegistration(
    role=AgentRole.creative_director,
    instance_factory=lambda config: BriefAgent(),
    input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
    output_formats=frozenset({BRIEF_FORMAT}),
    output_validator=validate_brief,
)
```

业务检查函数接收 JSON 内容。发现字段缺失、类型错误或业务限制不满足时，应抛出异常；框架会在输入转换后和 Agent 输出后执行已注册的检查函数。

```python
def validate_brief(content: JSONValue) -> None:
    if not isinstance(content, dict):
        raise ValueError("brief must be a JSON object")
    if not isinstance(content.get("headline"), str) or not content["headline"].strip():
        raise ValueError("brief headline must be non-empty")
```

格式升级通过 `ContentAdapter` 描述源格式、目标格式及转换函数。目标 Agent 只声明它能接收的格式；单角色上游入口会加载导出结果并在输入边界执行适配器。不要手动填写上游 `VersionDependency`、文件 SHA-256 或框架分配的 artifact 身份。

## 3. 完整工作流和单角色开发

将注册实例放入 `AgentSet`，再创建 `PosterWorkflow`。正式应用保留默认 `ExecutionMode.real`：

```python
workflow = PosterWorkflow(
    AgentSet(registrations),
    root=run_root,
    tool_registry=tool_registry,
)
result = await workflow.run(
    ["green tea", "caffeine-free"],
    constraints={"tone": "calm"},
)
```

组员可使用 `run_single_agent_from_request()` 检查自己的 Agent，或使用 `run_single_agent_from_upstream()` 读取前一个 Agent 已输出的运行记录。后者保留原始 `run_id`，由框架读取上游输出、文件和依赖身份；提供单独的 `record_path` 可保留上游与下游两份记录。可直接运行 [`examples/single_role.py`](../examples/single_role.py) 与 [`examples/format_upgrade.py`](../examples/format_upgrade.py)。

### 混合 Agent 测试

完整工作流允许在同一个 `AgentSet` 中放入真实 Agent 与成员自编的确定性测试 Agent。为每个 `AgentRole` 提供一份独立 `AgentRegistration`；将要接入真实 SDK 的注册项与本地 Fake 注册项放入同一集合，再交给 `PosterWorkflow`。成员编写的 Fake Agent 应实现普通 `Agent.run()`，通过业务校验并返回该角色声明的 `AgentOutput`。

组合前检查格式连接：上游 Agent 的 `output_formats` 需要被下游 Agent 的 `input_formats` 接受；内容结构或版本不同，则为相应输入或输出声明 `ContentAdapter`。框架会在 Agent 调用前检查完整格式路径。单角色入口适合验证某个 Agent 本身；混合 `PosterWorkflow` 适合验证真实成员与 Fake 成员之间的数据传递、格式适配、文件检查和审核路由。

图片候选使用 `ExecutionMode.real`，Fake `visual_producer` 也应生成符合声明类型和尺寸的可解码图片，才能在这个模式下执行图片检查。只有 Fake `visual_producer` 写入框架专用 JSON 候选时才使用 `ExecutionMode.fake`；该设置不会拦截其他真实 Agent 内部的 SDK 或网络调用。若要保证测试不访问外部服务，所有在该次运行中会被调用的 Agent 都应使用本地确定性实现。

[`examples/revision_actions.py`](../examples/revision_actions.py) 展示四个独立 Agent 和逐角色注册，可按成员的实现替换其中一份注册。样例校验器要求 brief 和 visual 有非空 `title`、brief 的 `keyword_texts` 为字符串列表、layout 和 visual 的 `columns` 为正整数；`revision_validator` 要求 brief 返工保留所有原始关键词，layout 返工保留已批准标题。它们是该样例的业务规则，不是框架要求所有海报使用这些字段。内置 `FakePosterModel` 适用于整套四角色演示：它共享 `_calls` 顺序记录，且 `quality_critic` 读取专用 JSON 候选，不能直接作为可任意替换的单角色 Fake 集合。

## 4. 注册并调用工具

为每个工具提供命名空间名称、用途、参数注解、返回类型注解和可用角色。Agent 收到输入后，可把 `context.tools.available_tools()` 转换成其模型 SDK 所需的工具定义格式；模型选中工具后，将名称与参数交给 `context.tools.call()`：

```python
def repeat_label(label: str, repeat: int = 2) -> str:
    return " ".join([label] * repeat)

tool_registry.register(
    "layout.repeat_label",
    repeat_label,
    description="Repeat a layout label.",
    allowed_roles={AgentRole.layout_planner},
)
```

目录依照当前运行的注册快照筛选角色权限；修改原始 `ToolRegistry` 不会改变已经开始的运行。工具参数、返回值和调用预算由框架检查。工具函数若包含模型请求，应接收 `context: AgentContext` 并通过 `context.call_model()` 发起请求。

工具接入的完整工作流样例见 [`examples/tool_catalog.py`](../examples/tool_catalog.py)。样例使用 `FakePosterModel` 只为快速检查工作流工具入口；把真实模型接入时，应在自己的 Agent 中将 Schema 和工具响应映射到所用供应商 SDK。

## 5. A4 审核和返工

`quality_critic` 需要针对当前候选填写 `ReviewRecord`。提交前检查以下内容：

- `candidate_id` 与 `candidate_revision` 指向当前候选文件。
- `keyword_results` 覆盖全部原始关键词，且每个关键词编号只出现一次。
- `decision` 与关键词状态一致；要求返工时提供成对的 `action` 和 `target`。
- `reviewer_identity` 可留空，框架会补入当前 `quality_critic` 调用身份；如自行提供，该值必须与当前调用身份一致。

候选身份不匹配会抛出 `AgentIdentityMismatchError`，重复关键词编号会在 `ReviewRecord` 校验时抛出验证异常，关键词覆盖不全会产生 `needs_review`。

返工 Agent 依据 `context.metadata.revision_action` 与 `context.metadata.revision_target` 读取当前公开目标，并通过 `context.memory.context()` 获取最近两条返工历史和未解决问题：

```python
memory = context.memory.context()
unresolved_issues = memory["unresolved_issues"]
if context.metadata.revision_action is not None:
    latest = memory["revision_history"][-1]
    changes = latest["changes"]
    if changes["action"] != context.metadata.revision_action.value:
        raise ValueError("public revision history does not match the current action")
```

### 角色笔记与版本依据

创建角色笔记时，将其依据的当前产物编号和修订号传给 `based_on`。以下示例为版式 Agent 保存针对当前 brief 的决定，并通过公开记忆入口读取笔记：

```python
artifacts = context.memory.context()["artifacts"]
brief = next(item for item in artifacts if item["name"] == "brief")
reference = brief["reference"]
note_id = "layout-rule-for-current-brief"
context.memory.add_note(
    note_id=note_id,
    content={"layout_rule": "keep the title readable"},
    based_on={reference["artifact_id"]: reference["revision"]},
)

note_reference = context.memory.note_reference(note_id)
note = context.memory.read_note(note_reference)
current_notes = context.memory.context()["notes"]
```

后续调用可从 `current_notes` 找到当前有效笔记，再用 `note_reference()` 和 `read_note()` 读取完整内容。上游 brief 更新后，旧修订号不再匹配当前版本，旧笔记会退出 `context()` 的默认结果；运行记忆仍保存原笔记，可按编号创建引用并读取历史内容。

`context.metadata.revision_target` 提供框架生成的目标版本。若需按编号读取历史，可以调用 `context.memory.read_history(context.memory.history_reference(revision_id))`。未指定 `RunBudget` 时，工作流默认允许最多 4 轮返工；首次候选审核不计入返工轮数，因此最多审核 5 个候选。默认图像操作和 Critic 视觉调用额度各为 5 次，足以覆盖初始候选和 4 轮返工。调用级预算或 `max_revision_rounds` 用尽时，工作流会停止后续调用并返回 `needs_review`；其余未处理的调用错误会保存失败记录后重新抛出。

可运行的四角色首次运行与返工样例见 [`examples/four_role_walkthrough.py`](../examples/four_role_walkthrough.py)。该入口验证四个角色的数据传递、审核候选身份、关键词覆盖、公开返工说明和每种预算情形。

A1/A2 的定向返工样例使用完整 `PosterWorkflow` 路由，实际检查新候选和 A4 审核：

```bash
uv run python examples/revision_actions.py --action revise_brief --root .scratch/revise-brief
uv run python examples/revision_actions.py --action revise_layout --root .scratch/revise-layout
```

`revise_brief` 会让 A1 根据原始需求更新 brief，再运行 A2、A3 和 A4。`revise_layout` 会让 A2 更新版式，再运行 A3 和 A4，同时保留已批准标题。该样例使用默认 `real` 模式和 Pillow 生成的 PNG，不调用外部模型服务。

## 7. 外部 SDK 会话与故障处理

外部 SDK 客户端可能保留对话历史或请求状态。按运行身份隔离可变会话数据，并在每次 Agent 调用时从框架输入和记忆构造请求；避免跨用户或跨 `run_id` 复用带有历史消息的会话。将 SDK 的失败保留为明确错误，记录供应商和模型标识，并让应用边界决定是否重新提交完整工作流。

接口版本不兼容、格式适配缺失、业务校验失败、越权文件、错误工具参数等未处理异常会使工作流保存失败状态和异常类别，再将异常交给调用方。Agent 调用期间若 `BudgetExceededError` 向工作流边界传播，工作流会停止后续调用并返回 `needs_review`，同时保留停止原因；预算终止不会写成调用异常失败。不要把未处理异常转换成看似有效的 `AgentOutput`。

## 8. 本地检查

```bash
uv sync --locked --all-groups
uv run pytest
uv run python examples/single_role.py --root .scratch/single-role
uv run python examples/format_upgrade.py --root .scratch/format-upgrade
uv run python examples/tool_catalog.py --root .scratch/tool-catalog
uv run python examples/revision_actions.py --action revise_brief --root .scratch/revise-brief
uv run python examples/revision_actions.py --action revise_layout --root .scratch/revise-layout
```

四角色 Fake 演示使用业务检查样例入口，默认等待时间为 15 秒：

```bash
uv run python examples/four_role_walkthrough.py --root .scratch/four-role-walkthrough
```

