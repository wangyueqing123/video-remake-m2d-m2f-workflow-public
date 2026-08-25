# R6.39 关键帧时刻与 QC 合同

## 目的

宫格格子是静态关键帧，不是视频动作描述。P4 必须先决定该格是“状态帧”还是“接触瞬间帧”，P6 只能审核被明确声明为硬证明的内容。

## 每格三个字段

```json
{
  "keyframe_contract": {
    "snapshot_type": "STATE",
    "contact_pair": [],
    "support_proof_required": false
  }
}
```

- `snapshot_type=STATE`：只展示动作前态或后态，不要求重演前一步接触过程；`contact_pair` 必须为空。
- `snapshot_type=CONTACT`：必须定格真实接触瞬间；`contact_pair` 必须恰好列出两个接触对象。
- `support_proof_required=true`：只有支撑关系本身决定动作是否合理时使用，例如进笼入口、站上厕板、跨越障碍或爪部落点。
- 普通展示、表情、看向主人、道具已经放好等状态帧默认不要求完整四爪或完整支撑面，但仍禁止穿模、悬浮和不可能拓扑。

## 一致性门

- `CONTACT` 必须同时有 `transition_visible=true` 和 `critical_contact_visible=true`。
- `STATE` 必须同时为 false。
- `CONTACT` 必须使用 `interaction_phase=CONTACT`；`STATE` 不得沿用旧的 `interaction_phase=CONTACT`。
- `support_surfaces_visible` 必须与 `support_proof_required` 完全一致。
- 场景若声明决定性接触必须可见，至少有一个 `CONTACT` 格；这不授权其它格重复该接触。
- P5 必须把这三项编译成模型可读的一句话；不得用全局“所有格都必须看见四爪”覆盖单格范围。
- P6 的 `support_surface_checks` 只覆盖明确要求支撑证明的格子。
- P6 的 `critical_contact.required` 只由 `snapshot_type=CONTACT` 决定，不能由状态变化、常识或审核者自行升级。

## 示例

- 主人举盘展示两类零食：`STATE`，不要求四爪。
- 浅盘已经放到地毯上：`STATE`，不要求手仍接触盘。
- 手正在扣上笼门锁：`CONTACT`，接触对象为“主人手”和“门锁”。
- 幼犬前爪第一次踩到厕板：`CONTACT` 且 `support_proof_required=true`。

## 成本不变量

本合同不增加格数、宫格数、参考图、上传、视频任务或重试。它只减少错误硬拒绝，并让真正需要接触证明的格子更明确。
