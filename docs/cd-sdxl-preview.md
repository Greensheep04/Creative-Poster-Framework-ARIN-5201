# CD → prompt → SDXL 主视觉预览

如需包含 CD 标题、标语与排版的完整海报，使用 [CD 完整海报入口](cd-poster-preview.md)。本页入口只负责生成供后续合成使用的主视觉素材。

这条独立测试链路读取 `examples/creative-director-samples` 中两个主题、三个 Qwen 模型的六份原始输出。它不依赖 A2，也不执行海报排版或 A4 审核。

`examples/visual/cd-preview-prompts.json` 保存逐样例人工精简的英文 prompt、negative prompt、CD 来源 SHA-256 和转换说明。入口不会自动改写任意新 CD 输出；新增 CD 样例时需要为它编写对应 prompt 并记录转换依据。原始 CD 中缺失的 supporting_copy、数组型 style 字段，以及不同描述间的语义冲突都会保留在报告中。

## 运行

在项目根目录、项目专用 Python 环境中执行。设置 `POSTER_SDXL_MODEL_DIR` 为本机已下载的 SDXL 目录。已有 `examples/visual/backend-sdxl.json` 配置固定权重 revision、30 步、guidance 6；本次统一使用 seed 42、768×1024。

```powershell
# 只读本地 tokenizer 并检查全部 prompt，不加载生图管线。
python examples/visual/cd_sdxl_preview.py --dry-run --output runs/cd-preview-dry

# 六个样例各生成一次，不自动重试。
python examples/visual/cd_sdxl_preview.py --output runs/cd-preview --seed 42
```

输出目录必须是新目录，避免覆盖已有实验。检查正向与负向提示在两个 SDXL tokenizer 中均未超过长度上限；超长时整个批次在生图前失败，不静默截断。源文件改变后 SHA-256 校验失败，需要人工复核 prompt 的对应关系。

每次成功运行保存六张 PNG、`contact-sheet.png` 和 `report.json`。对比图第一行为 ocean/plastic/hope，第二行为 tradition/technology/harmony；每行从左到右为 Qwen3-4B-Instruct-2507、Qwen3-1.7B、Qwen3.5-4B。图外标注 CD headline，图片内不绘制正式文案。

报告包含完整 CD 原文、来源提交与哈希、转换说明、实际 prompt、两个 tokenizer 的 token 数、seed、模型/权重/采样配置、耗时、显存用量、图像哈希和每次调用状态。失败时保留已完成图片与失败记录并退出，不伪装成功。

## 如何比较

观察每个关键词是否有可见表达、隐喻是否连贯、构图是否符合 CD、色彩与风格是否一致。相同 SDXL 配置和 seed 方便检查这些人工 prompt 的效果；它不构成严格的 CD 模型排名，因为 CD 原始采样配置未知，且 prompt 经过人工精简。

这次产物是主视觉素材预览，不是完整 PosterCandidate，也没有通过 QC。后续接入 A2 时应使用其正式 PosterSpec；不把本入口当作绕过正式输入契约的 A3 入口。

## 验证

```powershell
python -m pytest tests/visual/test_cd_sdxl_preview.py -q
python -m pytest -q
```

工程测试使用确定性图片后端覆盖来源漂移、超长正负提示、无生图 dry-run、可解码图像和对比图，以及生成失败留痕。真实 GPU 验证另行运行上述命令，不包含在 pytest 中。

Windows 上完整框架测试可能产生较长的运行引用路径。若临时目录位于较深的项目路径下，使用 `--basetemp D:/Projects/.cd-preview-tests` 等短目录；不需要改变系统长路径设置。

## 本次真实预览

2026-10-14 的六图结果在 `runs/cd-sdxl-20261014/`，其中 `contact-sheet.png` 为对比图，`report.json` 为完整运行证据，`visual-observations.md` 为初步视觉观察。六次调用成功，没有重试；正向提示含特殊 token 在内分别为 52、57、53、61、52、62 token，两个 tokenizer 的计数相同。生成及首次加载累计 83.365 秒。
