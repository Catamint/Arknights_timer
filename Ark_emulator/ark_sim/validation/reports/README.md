# V2 验收证据

2026-10-02 的最终结果：

- [v2_tests_20261002.json](v2_tests_20261002.json)：332 项 V2 测试全部通过，含源码 SHA-256 与执行命令；[原始日志](v2_tests_20261002.log)。
- [v2_baseline_20261002.json](v2_baseline_20261002.json)：两套自定义数值规则、0-1 完整清场、时间 300 检查点续跑与从头回放。状态与完整事件序列按精确摘要比较，无容差。
- [v2_baseline_20261002.replay.json](v2_baseline_20261002.replay.json)：首关的版本锁定命令记录。
- [custom_standard_20261002.json](custom_standard_20261002.json)、[custom_balance_20261002.json](custom_balance_20261002.json)：同一自定义内容在两套规则集下分别造成 850 与 60 伤害。
- [custom_standard_20261002.replay.json](custom_standard_20261002.replay.json) 与 [custom_standard_20261002.replayed.json](custom_standard_20261002.replayed.json)：真实 CLI 输入回放，与原快照逐字段比较没有差异。

`ark_00_01_20261002.performance.json`、`.profile` 和 `performance_after_20261002.json` 是重构过程中的性能快照，记录的是当时源码身份。后续属性缓存、事件读取和时间量化修正已继续改变实现。这些文件用于瓶颈分析，不代表最终首关通过状态。

所有通过结果属于模型验证。原生客户端逐帧对照仍待采集；规则接口存在或内部回放一致都不表示全游戏机制已经还原。修改内容或算法后应重新运行测试和 `tools/verify_v2_baseline.py`，并保存新版本证据。
