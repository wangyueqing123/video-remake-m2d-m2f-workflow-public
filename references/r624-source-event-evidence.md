# R6.24 P2 源事件证据

只用于 `M2_F_SOURCE_AUDIO_RESTYLE`。不改变后续生产流程。

1. 用 `prepare_r62_source_evidence.py` 至少每秒抽帧；动作密集处缩短间隔。
2. 审阅 `FRAME_INDEX.json` 中全部帧，并检查事件边界前后帧。
3. 每次关键可见状态变化写一个事件：主体、对象、前状态、后状态、事件时间、前帧和后帧。
4. 前后帧必须来自同一个 Frame Index，使用项目相对路径和真实 SHA256。
5. 每个事件必须被一个且仅一个 `macro_scene.source_event_ids` 引用；对应场景写 `change_policy=TRANSITION`。没有变化的场景写 `HOLD`。
6. 若口播声称与源画面行为相反，使用 `CONTRADICTION_PRESERVED` 并写明反差；不得为了迎合文案删除源视频已经发生的动作。
7. 源大场景必须从 0 秒无缝覆盖到源视频终点。任何缺帧、漏事件、重复事件、哈希漂移或场景间隙都在 P3 前阻断。

该证据只授予动作语义权威，不授予源像素、构图、机位、切点或生成参考图权威。
