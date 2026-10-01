# Ark 模拟器当前架构入口

当前采用全量重建底层抽象的新版设计，详见 [Ark 通用模拟底座新版架构](ARCHITECTURE_V2.md)。该设计提供自定义干员、技能、Buff 和状态图，以及可替换的表达式、计算图、算法提供器和规则集。

- [当前需求](REQUIREMENTS.md)
- [新版完整架构](ARCHITECTURE_V2.md)
- [自定义创作与公式样例](CUSTOM_CONTENT_V2.md)
- [73 项计算与策略契约](V2_RULE_CATALOG.json)
- [完整内容包设计样例](v2_examples/custom_guard.json)

新版独立运行时已实现于 `ark_sim`，后续开发统一基于 V2。使用方式见 [V2 实现](V2_IMPLEMENTATION.md) 和 [创作指南](V2_AUTHORING.md)。V1 与阶段一 `ark_emulator` 仅用于离线数据提取和代码、行为样本参考，边界见 [V1 历史说明](V1_HISTORY.md)。

此前架构文档保留在 [阶段一架构存档](archive/ARCHITECTURE_STAGE1.md)，用于解释已有实现与设计变化。
