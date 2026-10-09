# Agent 2：Layout Planner（美术与版式规划）

> 版本：v0.1｜日期：2026-09-22｜性质：课程项目设计提案，尚未实现或实测。


[返回主文档](00_PROJECT_OVERVIEW.md) · [上一阶段：Creative Director](01_AGENT_CREATIVE_DIRECTOR.md) · [下一阶段：Visual Producer](03_AGENT_VISUAL_PRODUCER.md)

## 1. 角色定位

把 CreativeBrief 转成**可以被程序执行的 PosterSpec**，明确元素、图层、坐标、尺寸、字体、颜色与素材需求。它回答“意图如何落在一张静态画布上”，不是写视频分镜，也不是再写一段含糊的生图提示词。

A1 决定主旨与正式文案；A2 决定视觉层级与精确呈现；A3 按规格生成素材并渲染。A2 可以提出“标题过长需要改写”，但不能自行更改 A1 发布的文案。

本文的场景描述结构与工具接口是本项目设计。借鉴 PosterLLaVA 的结构化布局思想，不意味着直接复制其模型、训练数据或输出格式。[R3]

## 2. 职责边界

| 必须负责 | 不负责 |
|---|---|
| 决定观看顺序、元素层级、留白与构图 | 替换原始关键词或重新发明项目主题 |
| 指定最终像素坐标、字号、字体与颜色 | 实际调用生图模型与素材重生成 |
| 为主视觉生成可执行的素材需求 | 在程序失败时偷偷更换正式文案 |
| 将每个关键词映射到布局元素/视觉属性 | 仅凭 Spec 声称真实图片已体现关键词 |
| 使用真实字体测量检查文字适配 | 依赖模型估计“这段字大概能放下” |
| 处理被授权的布局修复并保持其他内容 | 执行任意 LLM 生成的 HTML、SVG 脚本或 Python |

字号与标题位置属于 A2；标题文字属于 A1；图片内容与实际生成参数由 A3 在 Spec 限制内执行。渲染器不得偷偷缩字或裁掉文字来掩盖错误。

## 3. 内部架构

```text
CreativeBrief + 画布/字体/渲染能力
        |
        v
版式选择 + 视觉层级规划（LLM 决策）
        |
        v
元素列表 / 素材计划 / 配色与字体 → PosterSpec 草稿
        |
        v
measure_text_block + check_layout_geometry + validate_poster_spec
        |---- 可自动修复的格式问题：程序处理并留痕
        |---- 排版决策问题：有限回到 A2
        |---- 必须改文案：请求 A1，不自行删字
        v
PosterSpec vM → A3
```

初版可选择三个受控模板：标题在上且主视觉有边距的 `framed_hero`、全幅主视觉的 `full_bleed`、上下分区的 `split_stack`。模板是本项目拟实现的布局约束集合，不是额外 Agent。每次只输出一个可执行方案，多版式 best-of-N 留作扩展实验。

## 4. 工具组成

| 工具 | 输入 / 输出 | 作用与约束 |
|---|---|---|
| `list_layout_templates` | 渲染能力 → 模板、支持元素、布局约束 | 只选择 renderer 真正支持的模板 |
| `get_font_registry` | 环境配置 → 字体 key、真实字重、覆盖字符集 | 不让 LLM 编造一个机器上不存在的字体 |
| `measure_text_block` | 文本、字体、字号、行距、宽度 → 行数与真实 ink bounds | A2 与 A3 使用同一字体文件和测量引擎 [T3] |
| `check_layout_geometry` | Spec → 越界、非预期重叠、留白约束问题 | 区分有意叠放与违规遮挡 |
| `validate_poster_spec` | Brief + Spec → Schema、ID、文案、资产引用问题 | 包括跨字段检查，不仅校验 JSON 类型 |
| `check_planned_keyword_coverage` | 请求 + Brief + Spec → 规划覆盖结果 | 只能确认“计划安排了”，不能证明最终画面有效 |

这些工具不需要独立 LLM。程序只验证确定的属性；美学与语义呈现仍需 A4 在看到实际图片后评估。

## 5. 输入契约

| 必需输入 | 说明 |
|---|---|
| 原始 `KeywordRequest` 的只读引用 | 防止 A2 只记住扩写后内容而忘记原始约束 |
| 当前 `CreativeBrief` | 包括正式文案、关键词解释、主视觉要求和假设 |
| `canvas` 与 `render_capabilities` | 720 × 1280、支持的元素类型与渲染限制 |
| `font_registry` 与模板目录 | 仅允许使用已安装、已测量的字体和真实模板 |

返工时还需旧版 Spec、候选图片引用及 Router 验证过的 RepairPlan。版本不匹配就拒绝应用，不能将对旧图的修改无条件套到新图上。

## 6. 输出模板：PosterSpec

下方 JSON 是**跨文档一致的说明性输出模板**，不是已运行结果；其中图片路径、检查结论和用量均为演示值。工具名称是本项目拟实现的接口，不是声称第三方库已经提供同名 API。


```json
{
  "schema_version": "0.1.0",
  "run_id": "run_demo_001",
  "spec_version": 1,
  "based_on": {
    "brief_version": 1
  },
  "canvas": {
    "width": 720,
    "height": 1280,
    "color_mode": "RGB"
  },
  "template_id": "framed_hero_v1",
  "background": {
    "color": "#F6FAFB"
  },
  "design_tokens": {
    "palette": [
      "#F6FAFB",
      "#123C55",
      "#3D7893"
    ],
    "font_family_key": "noto_sans_sc",
    "safe_margin_px": 36
  },
  "asset_plan": [
    {
      "asset_id": "hero_image",
      "kind": "generated_image",
      "prompt": "平静蓝色海面与开阔天空，连续自然的海平线，简洁摄影感，柔和光线，无人物，无文字，无标志。",
      "negative_prompt": "暴风雨，密集人群，文字，标志，重影海平线",
      "target_size": {
        "width": 648,
        "height": 850
      },
      "subject_safe_region_norm": [
        0.05,
        0.1,
        0.9,
        0.8
      ],
      "required_keyword_ids": [
        "k1",
        "k2",
        "k3"
      ]
    }
  ],
  "elements": [
    {
      "id": "hero",
      "type": "image",
      "z": 1,
      "box": {
        "x": 36,
        "y": 260,
        "width": 648,
        "height": 850
      },
      "asset_id": "hero_image",
      "fit": "cover",
      "focus_point_norm": [
        0.5,
        0.5
      ]
    },
    {
      "id": "title",
      "type": "text",
      "z": 2,
      "box": {
        "x": 36,
        "y": 64,
        "width": 648,
        "height": 152
      },
      "copy_ref": "title",
      "text": "静海之蓝",
      "font_family_key": "noto_sans_sc",
      "font_weight": 700,
      "font_size_px": 72,
      "line_height_px": 90,
      "max_lines": 1,
      "align": "center",
      "color": "#123C55"
    },
    {
      "id": "subtitle",
      "type": "text",
      "z": 3,
      "box": {
        "x": 36,
        "y": 1160,
        "width": 648,
        "height": 56
      },
      "copy_ref": "subtitle",
      "text": "留一点宁静给自己",
      "font_family_key": "noto_sans_sc",
      "font_weight": 400,
      "font_size_px": 28,
      "line_height_px": 38,
      "max_lines": 1,
      "align": "center",
      "color": "#123C55"
    }
  ],
  "keyword_element_bindings": [
    {
      "keyword_id": "k1",
      "element_ids": [
        "hero"
      ]
    },
    {
      "keyword_id": "k2",
      "element_ids": [
        "hero"
      ],
      "layout_evidence": "低密度布局与充分留白"
    },
    {
      "keyword_id": "k3",
      "element_ids": [
        "hero",
        "title"
      ],
      "layout_evidence": "蓝色系配色"
    }
  ],
  "allowed_overlaps": [],
  "render_policy": {
    "text_overflow": "error",
    "missing_font": "error",
    "output_format": "PNG"
  }
}
```


### 6.1 坐标、图层与引用规则

`box` 使用最终画布像素的 `x/y/width/height`，左上为原点。所有长宽为正整数，默认不得越界。需要出血或图层叠放的模板必须显式声明支持，不允许隐式超出画布。

`z` 为绘制层级，按从小到大渲染；`background` 在元素之前绘制。每个 `id` 和 `asset_id` 在相应集合中唯一。`image` 元素引用 `asset_plan` 中存在的素材，`text` 元素引用 Brief 中存在的 `copy_ref`。

模板中 `subject_safe_region_norm` 是 `[x, y, width, height]`，取值范围在 0–1 内，是对素材主体区域的**生成目标**，不是已检测到的真实主体框。`focus_point_norm` 是裁剪中心意图。它们不能作为“主体一定没被裁掉”的证据。

`asset_plan.target_size` 是最终处理后素材需要适配的目标区域，不等于保证生图 API 原生支持该分辨率。A3 必须通过能力适配与明确的缩放/裁剪流程完成。

### 6.2 核心字段解释

| 字段 | 负责的事 | 重要约束 |
|---|---|---|
| `based_on.brief_version` | 绑定语义来源 | 不可消费过期 Brief |
| `design_tokens` | 精确配色、字体 key 与安全边距 | A1 的配色方向在这里落成具体值 |
| `asset_plan` | 视觉素材内容、目标比例与必须体现的关键词 | 主视觉通常不含正式文字，正式文案由程序绘制 |
| `elements` | 实际可渲染对象 | 初版限制为 image/text 与少量受控装饰，不扩成通用网页语言 |
| `keyword_element_bindings` | 关键词如何进入可见元素或布局属性 | 原始 ID 全覆盖；不是最终通过证明 |
| `allowed_overlaps` | 明确有意叠放的元素关系 | 不得用“允许重叠”掩盖文字不可读 |
| `render_policy` | 文字溢出、缺字/缺字体的处理规则 | 默认报错，不静默缩字、删字或换字体 |

如果只输入关键词，A1 生成的标题与副标题是下游统一文案。在受控文字实验中，由实验输入锁定的文案也通过同一契约传递，A2 仍不能自行改写。

## 7. 实现思路

### 7.1 先约束设计空间，再开放自由布局

初版选少数模板，每个模板规定主视觉槽位、标题/副标题区域、可接受文字长度、最小间距和允许的叠放关系。模型负责选择模板并调整允许的参数，而不是任意创造几十个组件。

这会限制表现力，因此主实验需要和整图生成对比美学；不能把“模板容易校验”包装成“模板必然更好看”。稳定后再增加自由布局、文字压图与局部渐变等能力。

### 7.2 测量文字，而不是凭经验猜

对选定字体、字重、字号、行距和边框调用 `measure_text_block`。测量工具需要显式换行，并使用与最终绘制相同的引擎；Pillow 不会因为提供一个宽度就自动完成所有排版决策。[T3]

文字放不下时，按顺序尝试：在模板允许范围内增大文本框；调整布局；在最小字号规则内减小字号或换行。仍无法放下才请求 A1 缩写生成文案。用户锁定文案不能以缩写替代。

建议初版将正文/副标题最小字号设为 24 px 作为可配置起点，并在真实 720 × 1280 输出上测试。这不是课程要求或普适可读性标准。标题的字号由层级与模板决定，不能无限缩小以消灭 overflow。

### 7.3 分离文案、布局和图片内容

`elements[].text` 必须与对应 Brief 文案一致，renderer 只负责画出来。`asset_plan.prompt` 描述场景、主体、风格和留白，不让模型承担指定标题的文字绘制。

负面提示只能是生成建议。若选定后端不支持独立 `negative_prompt`，A3 需要记录实际采用的适配方式，不得假装该参数已生效。素材仍可能出现伪文字，需要 A4 检查。

### 7.4 规划与真实图像可能冲突

A2 先规划，A3 后生成，因此生成素材未必天然匹配留白或主体位置。处理路径是：先按 Spec 生成和裁剪；若 A4 发现实际主体被文字遮挡或裁切，再有依据地要求 A2 调整布局或 A3 重生成素材。

不要在 A2 阶段仅凭预期就宣布图文融合成功。后期可以增加低成本素材预览驱动的布局调整，但它会增加反馈轮次，应单独记录成本。

## 8. 布局返工协议

A2 接收的返工任务必须包含 `expected_versions`、问题 ID、目标元素、允许动作与需要保留的内容。A2 根据建议生成新的完整 Spec，程序验证后递增 `spec_version`。A4 的建议不是对 Spec 的直接写权限。

一个布局补丁的说明性模板如下；它是内部受限操作，不是开放式任意 JSON 路径修改器：

```json
{
  "schema_version": "0.1.0",
  "run_id": "run_demo_001",
  "expected_versions": {
    "brief_version": 1,
    "spec_version": 1
  },
  "owner": "layout_planner",
  "changes": [
    {
      "target_type": "element",
      "target_id": "title",
      "field": "box.y",
      "old_value": 64,
      "new_value": 84
    }
  ],
  "preserve_element_ids": [
    "hero",
    "subtitle"
  ],
  "preserve_copy": true,
  "reason": "说明性示例：调整标题顶部留白；应用后仍需重新测量和审核。"
}
```


程序需要确认元素存在、字段可改、值在允许范围内、文案未被修改，并再次测量所有受影响文字。任何不在 allowlist 中的字段变化均拒绝。

标题位置、颜色或字号改变时，应复用未受影响主视觉，只让 A3 重新渲染。如果改变了 `asset_plan` 的语义或风格，受影响素材的缓存必须失效。不要把 Spec 的任何变化都等同于“重生所有图片”。

## 9. 建议提示词骨架

```text
你是 Layout Planner。将给定 CreativeBrief 转成 renderer 支持的 PosterSpec。
原始关键词与正式文案不可自行修改。
只使用给定模板、元素类型、字体 key 和允许字段。
所有最终位置必须是 720×1280 画布上的明确像素值。
为每个关键词安排可追溯的视觉元素或布局证据。
文字必须调用测量工具验证；不能只凭语言推测是否放得下。
正式文案由 text 元素绘制，不要求生图模型把它画进背景。
如必须改文案，返回约束问题并请求 A1，不擅自缩写。
返工只修改授权元素与样式，保留其他内容。
只输出符合 Schema 的 Spec 或明确的约束错误，不输出任意执行代码。
```

## 10. 测试与验收

| 测试场景 | 预期结果 |
|---|---|
| 正常 Brief 与示例模板 | 输出完整 Spec，所有引用与坐标合法 |
| 修改 `elements[].text` 与 Brief 不一致 | 跨字段校验拒绝 |
| 文字框过小 | 在真实测量阶段发现；不静默截断 |
| 字体 key 不存在或字形不覆盖中文 | 返回明确环境/字体错误 |
| 非预期重叠 | 几何检查发现；有意叠放仍需后续可读性审核 |
| 素材 ID 不存在 | 生成之前失败，避免无意义图像调用 |
| Brief 有关键词但 Spec 无对应安排 | 规划覆盖检查失败 |
| 调整标题 y 坐标 | Spec 更新；素材语义不变；后续不强制重生图片 |
| 修复来自过期版本 | 拒绝应用，保留当前产物 |
| 模型输出 HTML/script/路径逃逸 | 不是合法 Spec，拒绝执行 |

本阶段的验收重点是**规格可执行、测量可验证、修改可归因**，而不是仅凭一段美术描述看起来很专业。

## 11. 参考论文与项目

核对日期：2026-09-22；未在本环境复现。

| ID | 来源 | 对 A2 的参考价值与边界 |
|---|---|---|
| R3 | [PosterLLaVA 论文](https://arxiv.org/abs/2406.02884) / [项目](https://github.com/posterllava/PosterLLaVA) | 重点看结构化 JSON 表达多模态布局。借表示思想即可，不要求训练其布局模型；部分 demo/evaluation 在 README 中仍是计划发布状态 |
| R2 | [PosterGen 论文](https://arxiv.org/abs/2508.17188) / [项目](https://github.com/Y-Research-SBU/PosterGen) | 参考内容组织、空间布局、色彩和字体分工；本项目为满足四角色边界，将这些相关设计职责合在 A2 |
| R4 | [PosterMELD 论文](https://arxiv.org/abs/2608.02218) / [项目](https://github.com/Shannon4Science/PosterMELD) | 参考模板容量与结构优先的思路；本项目是 720×1280 创意海报，不直接使用学术版面容量规则 |
| T3 | [Pillow ImageDraw](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html) | 阅读 `textbbox`、`multiline_textbbox` 与文字绘制；自行实现一致的换行策略 |
| T2 | [Pydantic Models](https://docs.pydantic.dev/latest/concepts/models/) | 用类型和自定义校验落实字段与引用约束 |

阅读时区分“论文方法值得借鉴”“仓库某组件已发布”和“该组件已在我们的环境跑通”。它们是三件不同的事。
