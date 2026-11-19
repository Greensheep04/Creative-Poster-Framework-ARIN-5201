# 本地 GPU 实现与验证：已完成

代码与项目运行文件：D:/Projects/Creative-Poster-Framework-ARIN-5201。
独立 Conda：D:/Dev/conda-envs/creative-poster；PyTorch 未安装到 base 或系统 Python。
模型：D:/Models/sdxl-base-1.0。固定提交 462165984030d82259a11f4367a4eed129e94a7b，20 个组件均核验。

| 阶段 | 结果 | 证据 |
|---|---|---|
| 后端与环境 | CUDA GPU 张量运算通过；torch 2.8.0+cu128 | .scratch/verification/cuda-smoke.json |
| 人工 Brief/Spec | 三主题入口、版本/文案/关键词检查通过 | tests/visual/test_cases.py；runs/preset-input-check |
| 独立审查 | 5 项 Important 全部修复；6 个回归 RED→GREEN | docs/local-gpu-review.md |
| 真实 GPU 生图 | 海洋、森林、城市，720×1280 RGB PNG | runs/local-gpu-preview/contact-sheet.png |
| 标题移动 | 0 次额外生图；主视觉像素不变 | runs/local-gpu-preview/revise_layout/probe-report.json |
| 指定素材返工 | 1 次额外生图；标题像素不变 | runs/local-gpu-preview/regenerate_asset/probe-report.json |
| 独立 A3 CLI | 从真实 A2 导出记录成功执行 SDXL | runs/local-gpu-preview/forest/a3-cli-record.json |
| 完整回归 | 215 passed，1 Windows symlink 权限 skip | .scratch/verification/local-final-suite.txt |

实测：首张 73.480s（含模型验证/加载），森林 10.886s、城市 10.889s（已经预热）。30 步、guidance 6、seed 42、768×1024 原始素材；PyTorch peak allocated 显存约 9.53 GiB。本机数据，不代表其他机器或主题的性能。

真实生成共 7 次图像操作：3 主题各 1 次，标题返工探针 1 次，素材返工探针 2 次，独立 A3 CLI 1 次。未调用外部付费 API。

候选保持 rendered；工程返工最终 needs_review。A1/A2 是人工数据发布器，A4 是显式标记的工程探针。实际视觉观察：海洋素材出现边框；城市素材仍有招牌文字和人物。未把这些样张宣称为通过 A4 美学/语义审核，详见 runs/local-gpu-preview/visual-observations.json。

外部 API 工厂接口与 SDK 回调协议已保留，真实外部供应商/凭据仍未配置；选定后只需补对应回调。运行方法见 docs/local-gpu.md。

## 执行历史

# Local GPU execution ledger
Plan: docs/superpowers/plans/2026-10-09-local-gpu.md
Base: ed13c5c
Stage 1: in progress. Hardware: RTX 3080 Ti Laptop, 16 GiB VRAM; no torch/diffusers installed in project environment at start.
Pre-flight: Task 1 ImageGenerator interface -> Task 2 case runner; runner supplies generated keyword IDs and preserves source versions; no schema change required.
Pre-flight: Task 1 backend parameters -> A3 cache; include steps/guidance/dtype/offload plus pinned weights identity.

Stage 2: case/config/CLI tests 14 passed. Full regression: 203 passed, 1 skipped.
Ruling: export must supply the explicit CUDA index; project tool.uv.sources is not automatically consumed by uv pip install requirements.
Ruling: read usage from persisted post-run session, not SingleAgentResult input metadata.
Network: proxy connection refused during initial torch download, then connectivity restored. Partial model files were rejected; implement bounded HTTP resume with full SHA256 checks.

Engineering regression: 207 passed, 1 Windows symlink skip (31.12s). Preset CLI: ocean/forest/city rendered with explicitly offline fixtures. Real GPU samples pending weight/runtime download; no real-quality conclusion yet.

Independent review: 5 Important, no Critical; all fixed in one pass. Six regressions observed RED then GREEN. Extra real-A3 cache parameter check added. GPU samples still pending actual downloads.

Final engineering regression after review: 214 passed, 1 skip (31.88s). Source archive: 95 members, every member hash matches. Wheel build succeeded. Real CUDA and image verification queued after verified downloads; no GPU success claim yet.
