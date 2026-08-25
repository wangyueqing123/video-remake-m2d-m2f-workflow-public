# R6.32 物理与因果一致性合同

本版本只修复 P1→P4→P5 的机器可验证语义，不改变既有生产路径、宫格数量、调用次数、预算或人工审批纪律。

## 1. 物理道具规格

核心路线存在可变道具时，P1 `visual_state_contract` 必须使用 `R6.32-VISUAL-STATE-CONTRACT-1.0`，并声明 `physical_prop_specs`。每个道具必须有：

- 唯一 `entity_id`；
- 明确 `count_unit`，例如 `DISH`、`PIECE`、`BALL`；
- 唯一且可直接绘制的 `visual_signature`。

`project_identity.props`、`stable_prop_descriptions` 与 `physical_prop_specs[].visual_signature` 必须完全一致。P4 中每个可变道具的 `count_unit` 和 `visual_signature` 必须逐字继承 P1。任何缺失、重复或差异都在 P4 前阻断。

P5 必须输出“数量严格为 N UNIT”和固定可视外观，不得把 `count=1` 编译为“一份”。

## 2. 条件先于结果

当内容或动作合同含有明确时序条件，P4 宫格必须声明 `causal_proofs`：

```json
{
  "proof_id": "LOOK_THEN_REWARD",
  "condition_cell": 3,
  "condition_visual": "狗先抬头看主人",
  "result_cell": 4,
  "result_visual": "奖励饼干递到狗嘴前"
}
```

`condition_cell` 必须小于 `result_cell`，两段可视短语必须真实存在于对应格的 `visual_statement`。P5 把该顺序编译为不可提前的因果锁，并在提交前重新计算审计指纹。

## 3. 兼容与成本

- M2-D、M2-F 共用本门禁。
- 已验收旧项目继续由原版本解释，不原地修改。
- 不新增阶段、格子、参考图、上传、视频任务或自动重试。
- 失败只返回 P1/P4 修正，不得用额外 ImageGen 调用掩盖上游矛盾。
