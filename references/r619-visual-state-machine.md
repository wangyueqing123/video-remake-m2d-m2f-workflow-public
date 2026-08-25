# R6.19 视觉状态机合同

## 根因

旧系统把三个不同问题混在一起：

- `beat_role` 表示一格对叙事有多重要；
- `interaction_phase` 表示身体或物体是否真正接触；
- `state_transition_contract` 表示可变道具是否在相邻格之间改变状态。

G03 的失败不是单纯出图随机性。旧 P4 一面写“第2格靠近盘子、尚未接触”，一面把它标为 `DECISIVE_ACTION` 和 `critical_contact_visible=true`；P1 又把“盘中始终有一块肉干”写进永久身份。P5 因而收到互相冲突的事实，并把叙事重点错误放大为物理接触。放宽 Prompt 长度只能让冲突完整进入模型，不能消除冲突。

## 唯一权威顺序

1. P1 只保存稳定身份；可变化的位置、数量、开关、入口、吞咽或消失状态禁止写入身份。
2. P4 `state_ledger` 是格间可变事实的唯一权威。
3. `state_transition_contract.changes` 必须等于相邻两格状态账本的机器差值，不能人工另写结论。
4. `interaction_phase` 独立声明 `BEFORE_CONTACT / CONTACT / AFTER_CONTACT / NOT_APPLICABLE`。
5. `critical_contact_visible` 必须严格等于 `interaction_phase == CONTACT`。
6. `beat_role` 只决定叙事重要性，不得推导接触或状态变化。
7. P5 只编译一份“可变物体状态表”，不得在身份或逐格散文中建立第二套状态事实。

## G03 正确状态示例

| 格 | 交互阶段 | 状态阶段 | 肉干状态 |
|---|---|---|---|
| 1 | BEFORE_CONTACT | HOLD | TREAT_IN_PLATE |
| 2 | BEFORE_CONTACT | HOLD | TREAT_IN_PLATE |
| 3 | CONTACT | TRANSFER | TREAT_IN_PLATE → TREAT_IN_MOUTH |
| 4 | AFTER_CONTACT | TERMINAL | TREAT_IN_MOUTH → SWALLOWED |

第2格即使是 `DECISIVE_ACTION`，也不能提前清空盘子；第3格是唯一允许取走肉干的格；第4格只完成吞咽终态。

## P5 前强制阻断

- `R619_STABLE_PROP_*_CONTAINS_MUTABLE_STATE`：可变事实混入永久身份。
- `R619_CONTACT_FLAG_CONTRADICTS_INTERACTION_PHASE`：未接触格被标为已有接触，或反之。
- `R619_STATE_TRANSITION_PHASE_MISMATCH`：状态阶段与真实相邻格差值不一致。
- `R619_STATE_TRANSITION_CHANGES_MISMATCH`：人工变化列表与状态账本不一致。
- `R619_TRANSFER_MUST_OCCUR_AT_CONTACT`：转移不在接触格发生。
- `R619_TERMINAL_CANNOT_PRECEDE_CONTACT`：终态出现在接触之前。
- `R619_CHANGING_PROP_NOT_DECLARED_MUTABLE`：发生变化的道具没有在 P1 声明为可变实体。

这些错误一律停止在 P4，不生成 P5 Prompt，不进入 ImageGen，也不消耗外部调用预算。原有“一次批准、一次提交、不自动重试、最多一次合并修正”的成本纪律保持不变。
