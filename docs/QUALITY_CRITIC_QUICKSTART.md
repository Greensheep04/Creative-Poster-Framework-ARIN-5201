# A4 快速使用

当前 A4 进度、后续开发顺序与接入资料清单见 [进度与交接](A4_PROGRESS_AND_NEXT_STEPS.md)。

字段定义与 A1 接入约定见 [A4 输入输出接口](QUALITY_CRITIC_TEAM_CONTRACT.md)。本分支按源码运行，不依赖未上传的本地测试或评测数据。

## 1. 准备环境和输入

使用 Python **3.11** 环境，从仓库根目录安装 A4 所需库：

```bash
python -m pip install "pydantic>=2,<3" "pillow>=10,<13" "httpx>=0.28,<0.29"
```

按接口文档保存自己的 `a4_input.json`，并准备同一候选的 720×1280 PNG/JPEG。图片相对路径以输入 JSON 所在目录为准；原始要求来自 A1、当前布局来自 A2、成品来自 A3，由接入程序汇总。

## 2. 配置与运行

在本地保存 `provider.json`，替换服务地址和视觉模型 ID；API key 通过 `A4_API_KEY` 环境变量提供，不写入配置。

```json
{
  "schema_version": 1,
  "profile": "compatible",
  "base_url": "https://YOUR-API-HOST/v1",
  "model": "YOUR-VISION-MODEL-ID",
  "api_key_env": "A4_API_KEY",
  "response_format": "prompt_json",
  "thinking_mode": "omit",
  "max_output_tokens": 1600,
  "timeout_seconds": 30.0
}
```

这是通用视觉 Chat Completions 配置示例，须替换为实际服务支持的地址和模型；地址必须为 HTTPS，且不包含 `/chat/completions`。服务如有专用参数，可使用源码内的 `models.example.json` 模板与对应 profile。本例为单模型配置，不加 `--model`。

设好环境变量后，从仓库根目录执行：

```bash
PYTHONPATH=src python -m poster_critic.cli \
  --provider chat --config provider.json \
  --input a4_input.json --out .scratch/a4/report.json
```

输出路径必须是新路径；再次运行时换一个报告名称。该命令会实际请求配置的模型。

## 3. 读取结果与接入 A1

- 查看 `report.json.dispatch.json`：这是最后写入的分流完成凭据。退出码 `0` 表示命令完成，不等于审核通过。
- `status=released`：真实审核通过，已输出 `report.json.poster.png/jpg`。
- `status=awaiting_a1`：fail/unknown；读取 `report.json.a1.json`，内含完整报告、原始输入上下文、输入指纹及候选快照路径。unknown 包含对象、疑点、缺失证据和处理建议。
- `status=test_only`：Fake 的 pass，仅用于程序联调，不发布生产成品。Fake 运行需自行提供匹配的观察 JSON，不能依赖本分支不存在的 tests/fixtures。

命令行写出反馈不表示 A1 已处理。要直接调用组员 A1，使用 `poster_critic.dispatch.audit_and_dispatch`，把真实的 **async** A1 接收函数传给 `on_feedback`；成品发布函数传给 `on_release`。参数与示例见接口文档。

默认共享工作流没有改成自动调用真实 A1；组员负责连接 A1 函数和当前 Brief/Spec 版本。旧报告可读取，但缺少或不匹配输入指纹时不能自动放行或派发。
