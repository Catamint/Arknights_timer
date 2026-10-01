# V1 历史实现与 V2 开发边界

自 2026-10-02 起，战斗模拟器后续开发统一基于 **V2 `ark_sim`**。
V1 已标记为历史实现，仅用于离线数据提取和代码、行为样本参考。
本文件中的版本名称只指本项目的战斗仿真运行时，与 MuMu 模拟器、内存工具协议或服务器 API 版本无关。

## 历史目录

| 目录或入口 | 历史用途 |
|---|---|
| `ark_emulator/` | 原引擎、阶段一原型、解析辅助代码及提取的 JSON 数据 |
| `ark_emulator/core`、`content`、`adapters`、`levels` | 阶段一的模块化实验和固定 0-1 提取包 |
| `ark_emulator/validation` | V1 与阶段一的模型、回放及旧回归记录 |
| `run_web.py`、旧 LiveServer、网页与 AgentEnv | 查阅旧接口和交互行为；新网页与 AI 接入 V2 |
| `examples/`、`tests/` | 历史示例、快照和回归样本，不作为 V2 验收 |
| `docs/archive`、旧机制与审计文档 | 设计演进、机制线索、失败复现和待校准资料 |
| `tools/build_level_pack.py` | 离线提取旧数据与解析结果，生成固定 JSON 转换输入 |
| `tools/scan_unhandled.py` | 旧扫描方法与结果，仅作历史参考 |

目录原样保留以便追溯，旧 Python 接口与入口可用于复查历史样本。
这不表示 V1 仍是当前模拟器底座，也不表示历史覆盖数字已经获得客户端逐帧验证。

## 数据提取边界

离线工具可以读取 V1 的 `data_*.json`、固定关卡包和解析辅助代码。
输出须明确保留来源摘要、提取版本、固定养成配置、推测值和待校准状态。
当前 V2 的 0-1 包位于 [packages/ark_content/level_main_00_01.json](../packages/ark_content/level_main_00_01.json)。
`ark_sim/adapters/imports/ark_level.py` 读取固定 JSON 并转换内容，不执行旧 Python 战斗引擎。

V2 运行时不导入、继承或委托 V1 的战斗、地图、波次、属性、伤害、技能、Buff 或 RNG 实现。
缺失的数值契约、内容或原语在 V2 中补齐，编译器明确报错，不自动回退到历史路径。

## 后续开发与验收

新增功能、数值算法、干员、技能、Buff、状态机与关卡内容使用 V2；新网页、编辑器、AI 和其他消费端
使用 `Compiler` / `Engine` / `Simulation`。历史测试和扫描数字不代表 V2 当前能力。

入口见 [项目 README](../README.md)、[V2 实现与验收](V2_IMPLEMENTATION.md)、
[V2 作者指南](V2_AUTHORING.md) 和 [开发约定](../AGENTS.md)。
V2 底座测试运行 `python -m pytest tests_v2 -q`，首关模型验证使用 `tools/verify_v2_baseline.py`。
模型通过与真实客户端逐帧对照分别记录。
