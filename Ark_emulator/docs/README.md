# ArkSim 文档索引

当前运行与开发入口是独立 V2 包 `ark_sim`。单位、技能、Buff 与状态图由内容组合，数值和算法由可替换规则集执行。明日方舟预设接入同一底座；首个官方基线为 0-1，内容覆盖按关卡推进。

先阅读实际运行和作者指南，再查阅架构与需求。设计中的契约清单与历史实现覆盖记录都不能单独证明运行能力或游戏准确性。模型验证和真实客户端对照分别记录。

## 实际运行与内容创作

| 文档或内容 | 用途 |
|---|---|
| [项目 README](../README.md) | V2 主入口、CLI 编译、运行、回放和 Python API |
| [V2_IMPLEMENTATION.md](V2_IMPLEMENTATION.md) | 实际模块边界、运行接口、原子性、回放、调试轨迹与当前限制 |
| [V2_AUTHORING.md](V2_AUTHORING.md) | 实际内容字段、局部规则、周期恢复、技能、Buff、状态图、计算图、提供器、Builder 与 CLI |
| [可运行自定义内容包](../packages/custom/custom_guard.json) | 同一单位和技能采用标准或自定义规则集的合成场景 |
| [0-1 V2 内容包](../packages/ark_content/level_main_00_01.json) | 固定提取数据转换后的首关输入；验收和校准状态需结合证据 |
| [0-1 固定部署操作](../scenarios/level_main_00_01/commands.json) | 部署时序、位置、朝向和实例别名 |
| [运行时计算契约](../ark_sim/rules/contracts.json) | 随运行时保存的类型与计算接口；具体必需能力由编译预检判断 |
| [V2 模型验收记录](../ark_sim/validation/reports/v2_baseline_20261002.json) | 0-1 清场、检查点续跑、完整事件回放及两套自定义数值规则 |
| [V2 测试证据](../ark_sim/validation/reports/v2_tests_20261002.json) | 2026-10-02 的 332 项 V2 测试、运行日志与源码摘要 |

在模拟器目录运行：

```powershell
..\.venv\Scripts\python.exe -m ark_sim validate packages/custom/custom_guard.json
..\.venv\Scripts\python.exe -m ark_sim explain packages/custom/custom_guard.json --output dependencies.json
..\.venv\Scripts\python.exe -m ark_sim run packages/custom/custom_guard.json --seconds 1 --output snapshot.json --replay-output replay.json
..\.venv\Scripts\python.exe -m ark_sim replay packages/custom/custom_guard.json --record replay.json --output replayed.json
```

CLI 的实际子命令为 `validate`、`explain`、`preview`、`run`、`replay`。`--replay-output` 导出记录，`replay --record` 按锁定的程序身份、种子、时间与操作重放。Python API 同样提供 `ark_sim.tools.replay.replay()`。

## 当前需求与架构

| 文档 | 内容 |
|---|---|
| [REQUIREMENTS.md](REQUIREMENTS.md) | 自定义内容、可替换规则、全量底层重构与当前验收要求 |
| [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md) | 独立内核、通用领域、规则运行时、内容编译与扩展边界 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构入口与历史设计存档 |
| [LEVEL_00_01_PLAN.md](LEVEL_00_01_PLAN.md) | 0-1 基线、内容与操作范围、实施及验收计划 |
| [V2_RULE_CATALOG.json](V2_RULE_CATALOG.json) | 设计时的计算与策略清单；运行状态以当前实现为准 |
| [V2_DEV_INTERFACES.md](V2_DEV_INTERFACES.md) | 并行开发阶段的共享接口约定 |
| [CUSTOM_CONTENT_V2.md](CUSTOM_CONTENT_V2.md) | 初始创作设计说明；实际字段与用法以作者指南为准 |
| [初始内容设计样例](v2_examples/custom_guard.json) | 架构设计时的机器可读样例；可运行维护版位于 packages/custom |
| [REFERENCES.md](REFERENCES.md) | 用户提供的数据、工具与机制参考入口 |

## 阶段一与历史审计

V1 与阶段一已标记为历史实现，仅用于离线数据提取和代码、行为样本参考，后续开发统一使用 V2。
下列资料保留已有代码、行为样本、机制推测和历史回归记录。它们不属于新版运行路径，也不能作为当前 `ark_sim` 全游戏内容覆盖或真实数值还原的结论。

| 文档 | 内容 |
|---|---|
| [V1_HISTORY.md](V1_HISTORY.md) | V1 历史目录、数据提取边界与 V2 开发和验收约定 |
| [MODULAR_IMPLEMENTATION.md](MODULAR_IMPLEMENTATION.md) | 阶段一模块化原型的运行与测试说明 |
| [SIMULATION_COMPLETENESS_REVIEW.md](SIMULATION_COMPLETENESS_REVIEW.md) | 2026-10-01 旧代码审查、回归与完整模拟差距评估 |
| [review_evidence_20261001.json](review_evidence_20261001.json) | 当次审查的数量基线、最小复现和失败条目 |
| [MECHANICS.md](MECHANICS.md) | 旧实现的机制记录、证据标记和待实测项 |
| [DELIVERABLES_AUDIT.md](DELIVERABLES_AUDIT.md) | 历史需求与证据审计、改动记录 |
| [TEST_SCOPING.md](TEST_SCOPING.md) | 旧测试的定向回归策略 |

新底座验证运行 `python -m pytest tests_v2 -q`。新增内容应同时保存采用的包、规则、提供器和数值配置，并补独立预期；新增关卡只提升经过相应验收的组合。
