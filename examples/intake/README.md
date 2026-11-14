# Intake v1 数学问题验收

问题为 `sqrt(2)` 的无理性，材料为 [既有证明笔记](old-proof.md)。这是经典数学问题的
完整准备流程示例，不是用户私人研究成果，也没有在本流程中验证该证明。

`acceptance/` 保留本次实际 CLI 运行的原件、请求、原始模型答复及两版工件。
两份答复由当前 Codex 会话中的同一个核心模型阅读准备请求后撰写，经 `intake accept`
完成结构与逐行出处校验。这里没有启动嵌套 Codex 进程；模型身份按外部声明记录，
没有将它标为 CodexCLIAdapter 真实服务验收。

首轮请求：沿用互素正整数反证法，整理目标并保留奇偶性缺口，不新增条件或升级证据。
演示用自然语言修订：把奇偶性步骤明确为对所有整数 n 的待证明引理，分别用于 p、q，
保留原目标和反证法设定，后续先采用人工核对。此修订是验收输入，不声称用户确认了数学结论。

- [第一版说明](acceptance/versions/v0001-I-e88fc53abe5d41c7aad590a16de76cef/review.md)
- [第二版说明与变更](acceptance/versions/v0002-I-0562e7e8817e4f139073685518d7a78d/review.md)
- [第二版结构化草案](acceptance/versions/v0002-I-0562e7e8817e4f139073685518d7a78d/draft.yaml)
- [第二版数学陈述](acceptance/versions/v0002-I-0562e7e8817e4f139073685518d7a78d/statement.tex)

`response-v1.json` 和 `response-v2.json` 为本次请求的可阅读答复副本，来源 ID 绑定此次归档，
不适用于任意新请求。新问题需导出新的请求，并让模型基于新来源生成答复。
生产实现不会返回这些示例内容。

验收核对：目标、原有反证法假设和旧论证保持一致；第二版只改变奇偶性缺口和验收方式，
删除已由修订解决的验收方式问题。奇偶性引理没有进入假设列表，整体仍为 DRAFT_UNVERIFIED。
自动复核见 `tests/test_intake.py::test_saved_mathematical_example_retains_exact_sources_and_no_new_assumptions`。
