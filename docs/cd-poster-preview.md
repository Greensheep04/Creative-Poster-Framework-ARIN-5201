# CD → 完整海报：临时布局测试

目标是从六份完整 CD JSON 制作 720×1280 海报。与只看 SDXL 主视觉的入口不同，这个入口实际绘制 CD 的标题和标语，并把主体、媒介、情绪、配色方向与临时版式结合。

## 内容来源与职责

- CD 原始 JSON 保留不变，包括 concept、headline、supporting_copy、keyword_mapping、visual_direction、style、must_include、must_avoid。
- 标题和非空标语必须出现在最终画面中；换行只改变排版，不改词语或标点。空标语、缺失标语不补写。每个渲染前检查与最终 RenderManifest 核对都保持这一约定。
- 素材来自 `cd_sdxl_preview.py` 的真实本地 SDXL 运行；本次复用六张已生成图片。原图哈希与 CD 来源哈希必须同时匹配先前报告。
- `cd-poster-layouts.json` 显式保存六份临时布局。因为 A2 尚未提供输出，这些坐标、字号、标题分行、配色映射与装饰是测试阶段人工决定，不能当作 CD 已给出了完整坐标或 A2 已运行。
- 程序生成底板：保持比例放置 SDXL 图片，再绘制渐变、细线或标题光晕/阴影。底板里的光晕/阴影是正式文字的派生效果；清晰的正式文字仍由现有 `render_poster()` 按 PosterSpec 和真实字体绘制。底板、原图与文字渲染证据分别保存。

CD 的抽象风格字段会影响视觉方案，但测试不会宣称所有视觉语义已经通过审核。例如上一轮图像里的塑料材质、形态转换、传统与科技关系仍需观察；排版不会自动修复这些图像问题。

## 流程

```text
原始 CD JSON
  ├─ 主视觉需求 → 本地 SDXL 素材（前一阶段）
  └─ 标题、标语、风格 → 临时布局 → CreativeBrief + PosterSpec
                                ↓
                        素材适配、装饰和文字预检
                                ↓
                  现有 Pillow renderer → 完整海报 + 渲染清单
```

这个示例直接复用已有契约校验与 renderer，并非四角色完整工作流。真实 A2 接入后使用其正式 PosterSpec；不要把此处临时布局当作模型输出。

## 运行

在项目根目录用项目专用 Python 环境执行，先完成主视觉生成，再合成完整海报：

```powershell
python examples/visual/cd_sdxl_preview.py --output runs/cd-assets --seed 42
python examples/visual/cd_poster_preview.py --asset-run runs/cd-assets --output runs/cd-posters
```

第一步需要已安装的本地 SDXL 与 `POSTER_SDXL_MODEL_DIR` 配置。第二步不调用生图模型。两个输出目录都必须是新目录，避免覆盖记录。当前完整海报入口只适配这些已记录的六份 CD，不会静默替任意新 CD 决定布局。

输出：六张 `poster.png`、`contact-sheet.png`、`report.json`。每份海报目录还包含 `original-cd.json`、`creative-brief.json`、`poster-spec.json`、`temporary-layout.json`、`render_manifest.json`、`source.png` 与 `plate.png`。

## 本次结果

2026-10-14：输出位于 `runs/cd-posters-20261014`。六张 720×1280 RGB PNG 均已核验，标题和标语逐字保持 CD 原文，真实文字边界均在画布内；原素材哈希保持不变，新增图像调用为 0。三张海洋主题海报包含标题与标语，三张传统主题海报按原 CD 仅包含标题。光晕/阴影、渐变和装饰来源在报告的 asset_processing 与每份 temporary-layout 中记录。

```powershell
python -m pytest tests/visual/test_cd_poster_preview.py -q
python -m pytest -q --basetemp D:/Projects/.cd-poster-suite
```

工程测试检查完整 CD 文案落到正式文字元素、缺失标语不被补写、不得暗改标题、画布与真实绘制边界。视觉结果仍是候选预览，不把 `rendered` 当作 QC 通过。
