# R6.26 作用域化强调色合同

## 目标

封闭单色画风必须同时做到两件事：拒绝普通全彩漂移，并允许故事真正需要的局部彩色物体。旧的“全图彩色像素不超过 3%”会误杀大红球等合法道具，因此不再作为 R6.26 的通过条件。

## P1 预检

- 扫描 `project_identity.person`、`animal`、`environment` 和 `props` 中明确出现的彩色词。
- 每种彩色词必须出现在 `project_identity.accent_colors` 中。
- 每条强调色必须同时写颜色和具体对象；“红色”不合格，“红色食品安全大球”合格。
- 固定创作档案的强调色是必需子集，项目定义的道具强调色可以追加，不能删除固定条目。
- 棕色和米色属于当前单色画风的禁止色，不得通过登记为强调色绕过。

机器入口：`scripts/accent_color_contract.py` 与 `scripts/validate_r62_job.py`。

## P5 编译证明

Prompt 必须原样包含全部作用域化强调色。编译审计在 `style_color_contract` 中记录：

- `declared_accents`；
- `declared_accents_compiled`；
- 封闭单色模式与机器 QC 是否启用。

缺少任一声明时不得建立 P6 调用包。

## P6 像素审计

`audit_r69_style_output.py` 从当前项目锁定的 JOB 读取强调色，不接受聊天记忆或命令行临时补色。它分别记录：

- `pct_declared_accent_chroma_gt_20`：与已声明颜色色相匹配的彩色面积；
- `pct_undeclared_chroma_gt_20`：其余彩色面积；
- `mean_chroma`：全图平均色度。

通过条件同时要求：未声明彩色面积不超过 0.75%，已声明强调色面积不超过 12%，平均色度不超过风格上限。首个宫格使用自身作为基线只用于尺寸与统计格式，不得绕过绝对上限。

像素色相不能证明颜色属于哪个语义对象。P6 人工 QC 仍须确认红色只在球上、蓝色只在口水兜上、粉色只在蝴蝶结上，并拒绝彩色皮肤、服装、墙面、地面、家具或光影。

## 迁移

已有封印、批准、提交或 P6 QC 的项目不得原地改 JOB。建立新项目修订，保留原输出、提交、QC 与成本证据；在新 JOB 中补齐作用域化强调色后，仅重新运行本地 P1、P5、P6 审计。没有新的人工封印时不得调用 ImageGen。

使用 `scripts/build_r626_named_accent_reaudit.py` 建立只读复审包。脚本从 P1 身份描述中确定性登记遗漏的“颜色 + 对象”，重编对应宫格 Prompt，并对既有输出执行新像素审计。输出包标记为 `NONEXECUTABLE_AUDIT_EVIDENCE`，不会继承历史审批，也不能创建供应商任务。
