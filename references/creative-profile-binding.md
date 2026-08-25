# D/F身份与画风绑定

`route_id` 决定内容权限，`creative_profile_binding` 决定人物/动物身份，`style_profile` 决定画面语言。三者在P1一起锁定，但不能互相替代。

## 默认身份

- M2-D默认使用 `DOG_HIGH_SHARE_HEAT_V1`。
- M2-F默认使用 `DOG_SOURCE_AUDIO_RESTYLE_V1`。
- 两者默认都是白色/奶油白小比熊、蓝色口水兜、粉色蝴蝶结和年轻男性主人；狗是第一主体。

## 项目自定义身份

用户明确要求换人、换狗、换环境或换道具时，选择 `PROJECT_DEFINED_DF_V1`，在P1完整登记人物、动物、环境、道具、强调色和视觉主次。不能只修改Prompt而继续使用固定身份档案。

## 画风

默认身份不强制黑白风。`style_profile` 可从画风注册表中独立选择；未选择时才回退到 `DOG_HIGH_SHARE_MONO_COMIC`。切换画风不自动改变身份，切换身份也不自动改变画风。

任何身份或画风改变都要求新项目修订，并使P3-P9失效。

