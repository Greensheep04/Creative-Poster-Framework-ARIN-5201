# 接口说明

本文对应 `poster_framework` 当前公开接口。代码中的类型注解与 Pydantic 校验是运行时边界；业务 `content` 可按成员自己的 JSON 格式扩展。

## Agent 接口

每个 Agent 实现异步 `run` 方法：

```python
async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
    ...
```

| 类型 | 公共字段 | 用途 |
|---|---|---|
| `AgentInput` | `content`、`content_format`、`metadata`、`provenance` | 当前业务内容、格式、框架元数据和可选来源 |
| `AgentOutput` | `content`、`content_format`、`artifacts`、`review` | Agent 输出的业务 JSON、格式、文件引用和可选审核 |
| `AgentContext` | `identity`、`metadata`、`original_request`、`tools`、`files`、`memory`、`call_model()`、`build_model_request()` | 当前运行提供的身份、服务接口、历史请求和预算记录 |

`AgentOutput` 始终包含四个公共字段。`content` 与 `content_format` 由业务定义；`artifacts` 用于提供框架登记过的文件引用；`review` 仅供 `quality_critic` 返回。框架会为审核补充当前审核身份，并核对审核角色。

## 原始需求与框架元数据

使用 `OriginalRequest.from_keywords(run_id, keywords, constraints=None)` 创建输入。框架生成 `request_id` 和各关键词的 `keyword_id`。`OriginalRequest` 还包含 `constraints` 与 `canvas`；当前画布固定为 720×1280。

`FrameworkMetadata` 由框架提供，包括 `interface_version`、`identity`、`original_request`、`version_dependencies`、`files`、`review_route`、`revision_action`、`revision_target`、`usage` 和 `budget`。业务 Agent 应读取这些信息；通常无需手动构造运行身份、文件修订号、文件哈希或上游依赖。

完整工作流 `PosterWorkflow.run(raw_keywords, *, constraints=None, execution_mode=ExecutionMode.real, budget=None)` 会生成 `run_id` 并创建运行会话。`PosterWorkflow` 构造参数还包括 `agent_set`、`root`、可选的 `tool_registry`、默认预算、`configuration` 和 `revision_validator`。

单角色入口签名：

```python
await run_single_agent_from_request(
    registration,
    original_request,
    root=run_root,
    execution_mode=ExecutionMode.real,
    record_path=None,
)

await run_single_agent_from_upstream(
    registration,
    source_record_path,
    root=run_root,
    record_path=None,
)
```

原始需求入口默认使用 `real` 模式。上游入口读取已保存的单角色输出与文件引用，建立依赖关系并把兼容输入提供给新角色；默认情况下，结果写回上游记录路径，若需保留两份记录，请显式提供 `record_path`。

## AgentRegistration

`AgentRegistration` 通过下列参数声明接入能力：

| 参数 | 说明 |
|---|---|
| `role` | `AgentRole` 中的角色 |
| `instance_factory` | 接收配置字典并创建 Agent 的函数 |
| `input_formats` | Agent 可直接处理的 `ContentFormat` 集合 |
| `output_formats` | Agent 可生成的 `ContentFormat` 集合 |
| `input_adapters`、`output_adapters` | 输入或输出格式转换器 |
| `input_validator`、`output_validator` | 业务 JSON 校验函数；发现错误时抛出异常 |
| `interface_versions` | Agent 接受的公共接口版本 |
| `config` | 经过 JSON 校验的 Agent 配置 |

`ContentAdapter` 包含 `adapter_id`、`source_format`、`target_format` 和 `transform`。`transform` 接收并返回 JSON 值。输入适配器的目标格式必须列入 `input_formats`；输出适配器的源格式必须列入 `output_formats`。格式转换记录由框架生成，并保存源内容、目标内容、格式和来源信息。

## A4 审核与返工

`ReviewRecord` 的决策字段包括 `decision`、`candidate_id`、`candidate_revision`、`reviewer_identity`、`action`、`target`、`keyword_results` 和 `details`。A4 应明确它审核的候选编号与修订号，并为 `original_request.keywords` 中每个关键词提供一个状态为 `pass`、`fail` 或 `unknown` 的 `KeywordReviewResult`。重复关键词编号会在 `ReviewRecord` 校验时抛出验证异常。候选编号或修订号与当前候选不符、或审核身份与当前调用不符时，工作流会抛出 `AgentIdentityMismatchError`；关键词覆盖不全时，工作流返回 `needs_review`。

返工审核使用 `action` 和 `target` 成对提供。支持的路由为 `revise_brief` 到 `creative_director`，`revise_layout` 到 `layout_planner`，`regenerate_asset` 与 `rerender` 到 `visual_producer`。`target` 是框架已有的 `VersionDependency`；业务 Agent 可从输入和元数据读取来源，不应手动创建文件修订号。审核通过且所有关键词状态均为 `pass` 时，工作流结束为 `passed`。返工轮数用尽、审核信息不足或审核结论仍需人工判断时，工作流正常返回 `needs_review`；调用级模型、图片或工具预算用尽时，工作流也会记录 `needs_review` 并停止后续调用。默认 `RunBudget` 允许最多 4 轮返工，首次候选审核不计入返工轮数，因此最多审核 5 个候选；默认图像操作和 Critic 视觉调用额度各为 5 次。调用方可以传入 `RunBudget` 自行设定额度。

返工 Agent 通过 `context.metadata.revision_action` 和 `context.metadata.revision_target` 读取当前公开返工目标。`context.memory.context()` 返回当前角色可见的 `original_request`、`artifacts`、当前有效 `notes`、`unresolved_issues`、最近两条 `revision_history` 和当前调用的 `private_messages`。`unresolved_issues` 保存未通过关键词编号；没有具体关键词时会记录通用审核问题。返工历史中的 `changes` 包含动作、目标、审核说明、候选身份与审核身份。需要读取完整历史对象时，可使用 `context.memory.history_reference(revision_id)` 和 `context.memory.read_history(reference)`。Agent 可通过 `context.memory.private_messages` 属性读取当前运行、角色和调用编号下的私有消息，通过 `context.memory.append_private_message(message)` 写入 JSON 消息。

```python
memory = context.memory.context()
if context.metadata.revision_action is not None:
    instruction = memory["revision_history"][-1]["changes"]
    if instruction["action"] != context.metadata.revision_action.value:
        raise ValueError("revision history does not match the current action")
    target = context.metadata.revision_target
```

## 工具和模型调用

`ToolRegistry.register(name, function, *, description, allowed_roles, input_model=None, output_type=None)` 注册一个带命名空间的工具。省略 `input_model` 时，框架按函数注解创建严格参数模型；返回注解用于校验工具结果。

Agent 调用工具的公开入口：

```python
descriptions = context.tools.available_tools()
result = await context.tools.call("layout.repeat_label", {"label": "grid"})
```

`available_tools()` 返回 `tuple[ToolDescription, ...]`。每项包含 `name`、`description` 和 `parameters` JSON Schema。结果按名称排序，只包含当前角色有权调用的工具，参数 Schema 会复制自本次运行的注册快照。调用仍会执行权限检查、参数和返回值校验，并记入运行预算与事件记录。

通过 `context.call_model(provider=..., model=..., request_data=..., parameters=..., invoke=..., kind="llm")` 记录 Agent 内部的模型请求。工具函数若需要调用模型，可以声明 `context: AgentContext` 参数并使用该方法；SDK 私有调用若绕开此入口，框架无法核算其中的模型次数与费用。

## 角色笔记

`context.memory.add_note(*, note_id, content, based_on, role=None)` 保存角色笔记。`based_on` 是产物编号到修订号的映射；省略 `role` 时，笔记归当前 Agent 角色所有。`context.memory.context()["notes"]` 只列出当前角色且依据版本仍有效的笔记。可用 `note_reference(note_id)` 创建本次运行内的引用，再用 `read_note(reference)` 读取完整笔记；上游修订后，旧笔记不再出现在默认上下文，历史记录仍保存在运行记忆中。

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
note = context.memory.read_note(context.memory.note_reference(note_id))
```

## 文件、记忆与错误

通过 `context.files` 写入文件，公开运行记录只接受当前 run 已登记的文件引用。`context.memory` 提供角色权限范围内的需求、审核问题、文件和 Agent 结果；`context.cache` 提供带生成指纹的缓存接口。

输入身份、接口版本、内容格式、业务校验、文件身份、工具参数或返回值检查失败时，框架会保存失败状态与异常类型，并重新抛出异常。若 `BudgetExceededError` 从 Agent 调用边界传播，完整工作流会停止后续调用并返回 `needs_review`，同时保存预算终止原因；返工轮数用尽也会返回 `needs_review`。调用方应在应用边界处理重新抛出的错误并保留诊断信息。
