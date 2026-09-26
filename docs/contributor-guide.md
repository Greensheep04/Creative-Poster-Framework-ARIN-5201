# 组员接入指南

本指南面向负责一个海报角色或业务工具的组员。每个成员实现自己的 Agent、模型连接和业务校验器，再由工作流注册表连接角色。

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
