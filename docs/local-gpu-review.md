# 本地 GPU 扩展审查与修复

独立只读审查范围：ed13c5c..483a084。原结论 With fixes；无 Critical，5 项 Important。作者随后一次修复并验证，不宣称独立审查者再次批准。

1. 非 LFS 文件未校验官方来源：缓存及新下载配置/分词器文件都核对官方 size 与 Git blobId；权重核对 LFS SHA256。2 个回归先失败后通过。
2. --direct 元数据仍走代理：同一 transport 获取官方固定提交元数据及 payload。回归先失败后通过。
3. 下载后的 Torch wheel 不被安装流程复用：setup 在任何安装前核对官方 SHA256，随后用 --no-deps 安装本地 wheel，再按 uv.lock 补齐所有依赖。损坏缓存回归先失败后通过。
4. 模型清单漏配置/分词器：要求完整的 20 个固定组件，对每个 SHA256 重新核验后才加载 GPU。5 项虚假最小清单回归先失败后通过。
5. guidance<=1 会关闭 CFG、静默忽略独立 negative_prompt：本后端限制 guidance_scale>1，保持能力声明真实。回归先失败后通过。依据 Diffusers v0.35.1 官方 StableDiffusionXLPipeline do_classifier_free_guidance。

6 个新回归验证以上 5 类问题，另补实际 A3 执行的采样设置缓存失效检查。

Declined-to-judge rulings: A1/A2 自动规划和真实 A4 评审仍由组员实现；像素级 inpainting 为后期扩展；不承诺跨 GPU 逐像素一致。这些边界保留。真实 CUDA 和模型样张必须另外实测，不能用本次代码审查代替。
