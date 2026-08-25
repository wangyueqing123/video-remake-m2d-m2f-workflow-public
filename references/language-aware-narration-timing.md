# R6.36 语言感知旁白计时

P3 只能使用明确支持交付语言的计时模型。禁止把中文逐字符公式用于英文，也禁止把英文逐词公式用于中文。

- 中文：`CJK_CHARACTER`，继承已确认的“真人播客女”1.3 倍字符公式。
- 英文：`WHITESPACE_WORD`，使用按 180 WPM 基准并折算到 1.3 倍的逐词规划估值。
- 其它语言：没有已登记模型时在 P3 阻断，不能猜测。

英文公式只用于视频生成前规划，必须标记 `PROVISIONAL_180_WPM_AT_1X_SCALED_TO_1P3`。无论中文或英文，P9 导出前仍须用最终剪映 TTS 或最终音频写入 `MEASURED_FINAL_VOICE`；实测造成动作跨度变化时返回 P4，而不是逐句改变语速。

R6.36 的 `NARRATION_PLAN.json` 必须包含：

- `status=LANGUAGE_AWARE_VOICE_ESTIMATE`；
- `delivery_language` 与 P2 内容合同一致；
- `timing_model.language_code`、`unit_mode`、`formula_status` 与声音档案一致；
- 每段使用 `spoken_unit_count`，不再用含义模糊的 `spoken_character_count`。

分句和分段重建全文时也必须服从语言模型：英文分句/分段之间补一个标准空格，中文连续拼接且不增加空格。校验器必须同时验证“分句→分段”和“分段→全文”两层重建，禁止英文词边界被静默删除。
