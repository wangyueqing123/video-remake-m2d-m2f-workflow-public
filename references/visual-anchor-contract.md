# R6.7 项目视觉锚点合同

## 目的

文字身份描述只能限定类别，不能证明多次独立生图中的人物、动物、环境和画风是同一个。只要全片要求跨段视觉一致，就把实际图片锚点作为 P5/P6 的硬输入和硬审核对象。

## 两类锚点

### PROJECT_VISUAL_ANCHOR

项目级不变量，至少覆盖：

- 人物脸型、发型、体型和服装；
- 动物品种、脸部、体型、毛色和饰物；
- 线条粗细、灰阶、阴影、人物比例和写实/动漫程度；
- 核心环境布局与固定道具。

锚点必须记录项目相对路径、SHA256、来源网格/设定图、建立方式和 QC。聊天上下文、最近图片、缓存路径或自然语言“同一个”不能充当锚点。

### PREVIOUS_SEGMENT_END_STATE

段间连续状态，来自上一张已通过 QC 宫格的最后一格确定性裁切。它控制下一段第一格的主体位置、姿态入口、道具数量/可见性和环境状态，不负责重新定义项目画风。

## 执行策略

### TEST_SEQUENTIAL_ANCHORED

用于测试和默认生产：

1. G01 按 Prompt 生成整张宫格并通过 P6；
2. 建立项目视觉锚点；
3. 确定性裁切 G01 末格；
4. G02 使用项目锚点、G01末格和自己的 Prompt；
5. 每段通过 P6 后再为下一段裁切末格。

优点是状态链最强，缺点是不能并行。

### PRODUCTION_BATCH_SHARED_ANCHOR

用于多个互不连续或硬切段：一次审批提交多个独立请求，每个请求使用不同 Prompt，但共享同一项目锚点。若相邻段为 `CONTINUOUS` 且需要前段生成结果作为入口，禁止批量并行，退回顺序策略。

`n` 参数只能生成同一 Prompt 的变体，不表示多份不同 Prompt。

### TEST_INCREMENTAL_UNANCHORED

只允许明确的探索测试，输出不得进入正式 P7/P8，不得声称全片身份连续。若项目要求同一人物/动物/风格，该策略不能通过 P6。

## 调用包角色

G02 以后的顺序锚定调用必须满足：

- `input_mode=ANCHORED_WHOLE_GRID`；
- 恰好一个 `PROJECT_VISUAL_ANCHOR`；
- 连续边界恰好一个 `PREVIOUS_SEGMENT_END_STATE`；
- 参考路径和角色数量一一对应；
- `include_recent_conversation_images=false`；
- 失败基线不得作为任何视觉参考。

模型/工具若无法同时接收这些参考图，调用前 `BLOCKED_P0`；不能删掉锚点继续。

## P6 审核

历史项目迁移后，G01 已消费提交与已通过 QC 不进入 R6.6 实时权限账本，只作为只读 `anchor_origin_historical_*` 证据。试生产门只有在 `PROJECT_VISUAL_ANCHOR` 重新校验通过且该只读 QC 明确为 G01/PASSED 时才视为满足；旧封印、旧批准和旧提交均不能复用。

每张宫格分别记录：

- `intra_grid_continuity`；
- `project_anchor_identity_match`；
- `project_anchor_style_match`；
- `project_anchor_environment_match`；
- `previous_segment_state_match`。

G01 是锚点源，可将后四项中的锚点/前段检查标记为 `NOT_APPLICABLE_ANCHOR_ORIGIN` 或 `NOT_APPLICABLE_VIDEO_START`。G02 以后要求 `PASSED`。人物脸型、年龄感、头身比例、五官、狗的体型饰物、线条/阴影或核心空间明显变化，均为跨宫格硬失败。

跨段漂移若是调用包缺少锚点或角色错误，分类为 `REFERENCE_ROLE`，必须回 P5；不能消耗 ImageGen 修正预算。调用包正确而模型仍漂移，才分类为 `MODEL_RENDERING`，允许一次整张合并修正。
