# 本地 GPU 与人工案例运行指南

本扩展让人工准备的关键词、CreativeBrief、PosterSpec 通过真实框架记录运行 A3。A1/A2 是数据发布器，不调用语言模型；不运行真实 A4。最终 `rendered` 和返工探针的 `needs_review` 均等待人工视觉审核。

## 环境与模型

代码：D:/Projects/Creative-Poster-Framework-ARIN-5201；解释器：D:/Dev/conda-envs/creative-poster/python.exe。GPU 可选依赖在 uv.lock 中锁定，Windows 使用官方 CUDA 12.8 PyTorch 索引。没有向 base 或系统 Python 安装，没有创建 .venv。

```powershell
Set-Location 'D:\Projects\Creative-Poster-Framework-ARIN-5201'
powershell -NoProfile -File scripts/setup-local-gpu.ps1
$env:POSTER_SDXL_MODEL_DIR='D:\Models\sdxl-base-1.0'
```

SDXL Base 固定提交为 `462165984030d82259a11f4367a4eed129e94a7b`。下载官方 FP16 组件、许可与配置，保存 SHA256 清单。生成阶段只读本地权重、禁止隐式网络下载；缺文件、哈希不符和 CUDA 不可用都明确失败，不替换成离线图。

网络不稳定时可分别恢复下载，下载完再运行 setup-local-gpu：

```powershell
& 'D:\Dev\conda-envs\creative-poster\python.exe' scripts/fetch-torch-wheel.py --directory 'D:\Dev\uv-cache\local-wheels' --direct
& 'D:\Dev\conda-envs\creative-poster\python.exe' scripts/fetch-sdxl.py --model-dir $env:POSTER_SDXL_MODEL_DIR --direct
```

`--direct` 仅作用于这个进程，不修改系统代理。模型下载可选 `--mirror https://hf-mirror.com` 获取公开文件，权重哈希仍取自官方固定提交。模型不是无限制许可：随权重保存官方 CreativeML Open RAIL++-M 许可。

## 跑一组或多组词

```powershell
& 'D:\Dev\conda-envs\creative-poster\python.exe' -m poster_visual_producer.cases `
  --case examples/visual/cases/forest.json `
  --backend-config examples/visual/backend-sdxl.json `
  --root runs/my-forest-test --fonts examples/visual/fonts.json --font-base . --seed 42

& 'D:\Dev\conda-envs\creative-poster\python.exe' examples/visual/local_acceptance.py --exercise-repairs
```

案例中 name 是输出子目录；keywords 的数量应为 2–5；Brief 的 keyword_bindings 顺序对应词条顺序，内部 ID 在运行时映射为框架真实 ID。修改关键词时同时修改 Brief 解释、正式文案、视觉要求及 Spec 素材提示/配色。不能只改词条而保留旧主题。

海洋、森林、城市案例是人工开发样例，不是独立测试集。英文素材 prompt 用于减少模型语言理解的干扰；中文正式文案仍由 Pillow 使用真实 Noto 字体绘制。

每组保存 poster.png、hero_image-source.png、candidate.json、render_manifest.json、summary.json，以及 A1/A2/A3 运行记录。不同实验建议使用不同 --root，防止预览文件被新运行覆盖。失败案例保存 summary.json 和已经产生的框架记录。

`backend-sdxl.json` 可以修改 steps、guidance_scale、cpu_offload。生成尺寸从 768x1024/1024x1024/1024x768 选择，再按 Spec 等比裁切。模型版本、步数、引导强度、dtype 和 offload 设置都进入缓存指纹。需要时设 cpu_offload=true 降低显存压力；速度与输出质量需实测。

## 外部 API 替换接口

保持同样的案例命令，只将 --backend-config 换成应用自己的 JSON：

```json
{"kind":"factory","factory":"your_application.image_backend:create_generator","options":{"model":"exact-model-version","api_key_env":"POSTER_IMAGE_API_KEY"}}
```

应用实现 `create_generator(options) -> ImageGenerator`，返回现有 CallableImageGenerator，内部 callback 调 SDK 并返回 GeneratedImage(bytes, request_id, usage, provenance)。凭据只从 api_key_env 指定的环境变量读取。明确声明支持尺寸、seed、negative_prompt；把会影响输出的固定采样参数列入 capabilities.generation_parameters。外部工厂来自可信配置，不接受模型决定模块名称。

原 A3 单角色 CLI 同样新增 --backend-config 和 --seed，仍兼容 --fixture 与 --generator-factory。

## 验证边界

真实素材生成、文字与布局执行、尺寸、文件完整性、缓存和指定素材返工可独立验收。自动主题规划与真实视觉审核仍由组员 A1/A2/A4 实现。工程返工探针使用显式标记的模拟审核触发路由，最后返回 unknown/needs_review，不能当成美学审核通过。

本地推理 API 调用费用记为 0，不代表电力、下载、设备成本为 0。固定 seed 只在相同软件/硬件条件下帮助复现，不承诺跨 GPU 逐像素一致。

实际执行证据和未完成项见 local-gpu-progress.md。
