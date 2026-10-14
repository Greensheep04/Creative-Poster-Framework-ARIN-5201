# 整份 CD → prompt → SDXL 整图海报，与 Pillow 合成对照

这个实验让本地 SDXL 生成整张海报，包括文字。输入来自原仓库的六份 CD：标题、非空标语、concept、visual_direction、keyword_mapping、style、must_include、must_avoid 一起编译为明确的自然语言 prompt。完整原文保留，缺失或空标语解释为只绘制标题；不使用先前精简的无文字主视觉 prompt。

对照图右侧是此前 `cd_poster_preview.py` 生成的完整 Pillow 海报。它的文字来自程序绘制、布局来自明确记录的临时人工布局。实验新结果左侧完全由 SDXL 生成，模型画出的文字保留原状，不覆盖、不修字。Pillow 在本实验中只负责读取图片、生成图外标签和缩略对比，不改变保存的原始模型结果。

## 实验条件

- 同一本地 SDXL base 1.0 固定权重 revision；30 步、guidance 6、seed 42。
- 新的 SDXL 整图原生生成 720×1280，与最终 Pillow 海报同尺寸。此前 Pillow 使用的主视觉原图是 768×1024，再由程序完成布局；此分辨率差异保留在报告中。
- 每份 CD 生成一次，共 6 次，不挑选多次生成中较好的一张，不自动重试。
- 对照结果是探索性样例，不是控制全部变量的模型排名：新旧 prompt、布局决策、原生素材分辨率和文字生成方式均存在差异。

## 完整 prompt 与长度处理

入口直接编译原始 CD 字段。将 headline 与 supporting_copy 放在引号内明确要求逐字绘制；其余字段按原文组成主题、构图、关键词表达、艺术媒介、情绪、配色、必需项和禁止项。负面提示没有加入通用的 `text, letters` 禁令。Qwen3.5 传统主题的原始 avoid 列表有 headline-only 歧义，源内容保留，并明确解释为保留必需标题、不要标语。

六份 prompt 长度为 215–263 个内容 token，超过本地 CLIP 单段上限。本实验使用显式分块编码：

1. 两个 tokenizer 都不截断完整 prompt，按每段 75 个内容 token 加 BOS/EOS，补至 77。
2. 正向和负向提示都补至相同分块数，本次为 3–4 段。保存每段内容 token 数，丢失 token 数为 0。
3. 两个编码器取与当前 Diffusers SDXL 相同的倒数第二层 hidden state，分别拼接各段上下文，再在特征维拼接两个编码器。
4. 第二编码器的 pooled projection 按各段内容 token 数加权平均。作为 `pooled_prompt_embeds` 与上下文一起输入 pipeline。

这是本实验定义的长 prompt 编码方法，不是声称 SDXL 原生单段接口支持任意长度。保留所有 token 并不保证模型理解全部语义，也不保证文字准确。Diffusers 官方接口支持传入预计算的 prompt/negative/pooled embeddings，见 [SDXL pipeline 文档](https://huggingface.co/docs/diffusers/api/pipelines/stable_diffusion/stable_diffusion_xl)。没有新增模型、LoRA 或在线服务依赖。

## 运行

在项目目录使用已安装的项目专用 Python 环境，设置 `POSTER_SDXL_MODEL_DIR` 指向已核验本地权重。

```powershell
python examples/visual/cd_direct_poster.py --reference-run runs/cd-posters-20261014 --dry-run --output runs/cd-direct-dry
python examples/visual/cd_direct_poster.py --reference-run runs/cd-posters-20261014 --output runs/cd-direct --seed 42
```

输出目录必须不存在。源 CD 哈希、对照 CD 身份、对照 PNG 哈希与尺寸在生成前核验。

每个样例目录包含：

- `direct.png`：SDXL 原生整图输出，原样保留文字。
- `pillow.png`：此前 Pillow 海报的逐字节副本。
- `comparison.png`：左侧直接生成、右侧 Pillow 合成。
- `original-cd.json`、`prompt.txt`、`negative-prompt.txt`：原始输入与实际完整提示。

总目录包含 `report.json`、`comparison-all.png`、`ocean-comparison.png`、`tradition-comparison.png`。报告记录完整 CD、提示、token 数、分块数、embedding shape、模型版本、参数、GPU 用量、计数与失败状态。

## 比较重点

观察标题是否逐字正确、标语是否完整、是否出现额外文字，及视觉隐喻、艺术风格、构图与文字融合。原生输出和程序绘制文字应分别评价，不能用图外标签冒充模型成功画出了标题。任何初步视觉观察都需要说明来源；不编造 OCR 或字符错误率指标。

工程测试使用受控 token/encoder 和图片后端验证完整 CD 保留、无 token 丢失、正负条件尺寸匹配、pooled 加权、来源漂移拒绝和原始图像不被替换文字。真实 GPU 生成独立运行。

## 原生短 prompt 对照

第一轮完整长 prompt 出现明显主体偏移，尤其传统主题被画成人头或机器人，因此追加了相同六份 CD 的原生单段对照。`cd-direct-native-prompts.json` 保留完整标题与非空标语，手工压缩视觉主体、关系、媒介、情绪和配色；它不是原始全部字段的逐字序列化。负面提示沿用相应 CD 禁止项，仍不禁用所有文字。

原生正向提示只有 45–55 个内容 token，加 BOS/EOS 后均未超过 77，直接使用 pipeline 的普通 prompt/negative_prompt 参数。这个模式不使用拼接 embedding。新增 6 次调用，两组共 12 次；两组都保留，未挑选或覆盖失败的视觉结果。

```powershell
python examples/visual/cd_direct_poster.py --reference-run runs/cd-posters-20261014 --encoding native --output runs/cd-direct-native --seed 42

# 从已保存原图重建两列与三列对比，新增模型调用为 0。
python examples/visual/cd_direct_poster.py --reference-run runs/cd-posters-20261014 --output runs/cd-direct-native --rebuild-comparisons --full-cd-run runs/cd-direct
```

三列依次为完整 CD 的长 prompt 分块结果、保留精确文案的原生短 prompt 结果、原先 Pillow 合成结果。各列的方法标签写在原图外；原生图没有用程序修字。

## 2026-10-14 实测结果

- 完整长 prompt：`runs/cd-direct-posters-20261014`，6 次图像调用，生成与初次模型加载累计 99.598 秒。
- 原生短 prompt：`runs/cd-direct-native-20261014`，6 次图像调用，累计 97.386 秒。
- 两组原图都是 720×1280 RGB PNG，峰值 allocated 显存约 10.02 GiB；CD 文案均完整包含在实际发送的 prompt 中，没有 token 被截断。
- 对照 Pillow 海报逐字节复制；所有 24 份直出/参考 PNG 的哈希均与报告一致。
- 三列对比在原生组的 `ocean-three-way.png` 和 `tradition-three-way.png`；完整观察见同目录的 `visual-observations.md`。

人工初步观察：完整长 prompt 的多份图像发生主体偏移；原生短 prompt 对主体更忠实，部分画面中的文字与图像更融合。但两组均未看到完整正确的指定标题；海洋组的长标语也没有完整正确呈现。原生海洋组有错字/伪文字，传统组主要遗漏标题。Pillow 合成保留了全部正式文案，但不能由此推断其图片语义或整体美学一定优于直接生成。

这些是人工看图观察，没有运行 OCR，也没有生成字符错误率。结果只对应本机固定 SDXL base 1.0、这六份 CD、这两种 prompt 编码和单个 seed。特别不能把分块长 prompt 的主题漂移当作 SDXL 原生短 prompt 的同等表现。
