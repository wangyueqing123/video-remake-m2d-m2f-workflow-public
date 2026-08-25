# R6.13 Production Final

R6.13 不改变 R6.12 的分析、场景规划、宫格、Prompt、视觉锚点、供应商调用或单段 QC。

发布层只有两个规则：

1. 单段视觉分继续以 80 分为可装配底线，任何硬视觉失败仍然阻断。
2. 最终母版的片段平均视觉分必须达到 85 分，技术检查、后期旁白替换和硬失败检查必须全部通过。

通过后运行：

```powershell
python scripts/finalize_r613_release.py --project-dir <project> --final-qc artifacts/P8/FINAL_MASTER_QC.json --release-name "Video Remake Workflow R6.13 Production Final" --commit
```

命令写入 `FINAL_RELEASE_CERTIFICATE_R613.json`，关闭供应商调用权限，并把项目状态改为 `COMPLETE`。剪映/CapCut 草稿导出保留为后续 `JIANying_CAPCUT_DRAFT_ADAPTER`，不与本版生成核心耦合。
