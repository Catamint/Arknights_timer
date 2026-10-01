# 模块化模拟器使用与扩展

本文记录阶段一原型的实现与运行方式。最新目标已调整为全量底层重构，架构依据
[通用底座新版设计](ARCHITECTURE_V2.md)。这里的固定计算与限定干员配置不满足新版
自定义目标，保留为数据和行为核对参考。

0-1 已接入独立模块化内核。运行时读取约 61 KB 的关卡包，加载本场敌人和所选编队的处理器，不导入旧版 battle、全量 Buff 引擎、技能实现或 DataStore。现有其他关卡通过兼容适配器保留，并在快照中标记 `backend=legacy`、`support.status=legacy_unverified`。

当前首关是模型实现与回归通过阶段，没有新增真实客户端对照证据。敌人 OnAttack、出生偏移分布、动画缩放、弹道速度和教程控制时序仍在支持配置中列为待校准。

## 运行首关

在 `Ark_emulator` 目录运行：

```powershell
..\.venv\Scripts\python.exe examples/run_modular.py
..\.venv\Scripts\python.exe run_web.py --no-browser
```

网页默认关卡现为 0-1，控制台目录仅展示当前模块化关卡与四名已迁移干员。仍可显式指定其他关卡运行旧实现，但其结果不提升为已验证。

```python
from ark_emulator import Simulator

sim = Simulator(
    level_id="level_main_00-01",
    backend="modular",
    seed=123,
    squad=[
        {"charId": "char_502_nblade", "level": 30},
        {"charId": "char_124_kroos", "level": 40},
    ],
)
sim.deploy("char_502_nblade", 3, 3, direction=1)
sim.run_ticks(180)
sim.deploy("char_124_kroos", 1, 3, direction=2)
snapshot = sim.run_ticks(10000)
record = sim.export_replay()
```

示例模型结果为 11 击杀、零漏怪、防卫点 20。`backend="auto"` 对已有编译包选择新内核，其他关卡选择旧适配器；`backend="modular"` 强制要求已迁移关卡与配置。新内核中的未知干员或机制会明确失败，不会转入旧实现。

## 当前养成与技能范围

| 干员 | 范围 | 已迁移行为 |
|---|---|---|
| 夜刀 | 精零，1 至 30 级 | 近战、阻挡、撤退、再部署、30 级再部署时间天赋 |
| 克洛丝 | 精零，1 至 40 级，技能 1 至 7 级 | 高台部署、远程弹道、攻击回复、自动二连射 |
| 芬 | 精零，1 至 40 级，技能 1 至 7 级 | 地面近战、自动回复、自动费用技能 |
| 芙蓉 | 精零，1 至 40 级，技能 1 至 7 级 | 高台医疗、伤员优先、治疗、手动治疗强化与属性 Buff |

潜能与信赖固定为零，不支持模组。精一的概率天赋、属性和范围未纳入当前配置。未知或越界配置在加载时报告具体内容路径。

首关敌人包含源石虫与士兵；士兵 DEF=30 的关卡覆盖已进入编译包，路线中的 3 秒 WAIT 检查点在运行时独立计时。

## 代码入口

| 边界 | 实际实现 |
|---|---|
| 内核调度与运行时状态 | `core/battle.py`，`core/entities.py`，`core/clock.py` |
| 命令校验与动作枚举 | `core/commands.py`，`Battle.execute`，`Battle.schedule` |
| 公式与状态更新 | `core/attributes.py`，`core/damage.py`，`core/blocking.py`，`core/movement.py` |
| 能力与 Buff 生命周期 | `core/abilities.py`，`core/buffs.py`，`core/projectiles.py` |
| 定义与按需加载 | `content/schemas.py`，`content/repository.py`，`content/dependency.py`，`content/registry.py` |
| 普通敌人行为族 | `content/behaviors/ground.py` |
| 具体技能效果 | `content/effects/attributes.py`，`content/effects/skills.py` |
| 对外接口 | `adapters/api.py`，`adapters/live_server.py`，`adapters/agent_env.py` |
| 旧实现兼容 | `adapters/legacy.py`，原有顶层模块仍保留 |
| 支持声明与数据 | `levels/profiles/level_main_00-01.json`，`levels/packs/level_main_00-01.json` |
| 回放与对照 | `validation/replay.py`，`validation/compare.py` |

已有独立的地图流场、波次调度和 RNG 工具被复用；未复用旧战斗编排与全量技能/Buff 执行器。运行时定义使用只读映射，实体、Buff、计时器与随机数属于各自战斗。深拷贝用于策略试算时，共享静态定义并复制运行时状态。

## 编译数据包

```powershell
..\.venv\Scripts\python.exe tools/build_level_pack.py
```

编译器目前限定 0-1，读取现有提取数据，合并敌人等级与关卡覆盖，保存两类敌人、四名干员的精零配置、精确范围和能力参数，以及输入文件摘要。大表只在这一离线步骤使用。

数据包内容摘要采用规范化 JSON，避免仅因换行与缩进差异使回放失效。回放同时校验内容摘要与 `mechanicsVersion`；修改规则后需要更新机制版本并重新验证相关样本。

## 新增敌人 Buff 或干员

1. 查明当前关卡实际引用的定义及其来源，生成有效属性和能力，列出传递依赖。普通攻击也要从基础 Prefab 解析。
2. 判断能否复用现有行为族与效果处理器。能复用时只增加内容数据；不能复用时在对应领域增加独立模块，并注册明确能力 ID。
3. 在依赖图中声明新定义与生成单位、Buff、技能效果等引用。新节点、特殊波次动作和检查点在支持前会被预检查拒绝。
4. 更新关卡支持配置，添加有限且明确的养成范围。不得仅因可读取数据就把全部角色配置加入支持。
5. 添加独立预期的机制测试与首关回放，随后进行真实游戏对照。支持记录保留未验证项。

当前 Buff 支持多来源独立属性修饰、同来源刷新、解除和精确到期事件；复杂异常、通用动作图与活动环境仍按后续关卡需求迁移。不要把已有处理器列表解释成全部 Buff 已迁移。

## 验证

```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_modular_core.py tests/test_modular_adapters.py -q
```

测试覆盖首关出怪和终局、士兵等待和覆盖、零漏怪清场、非法操作、并发首次初始化、暂停与单步、Buff 层与到期、技能/治疗、依赖失败、回放版本检查、快照隔离与 AI 接口。

`validation/fixtures/level_00_01_model_replay.json` 是模型回归样本，明确标记 `gameVerified=false`。真实样本应另存并说明客户端版本、同帧采样与字段容差，不能把模型自身生成的数据标成客户端观测。

原有全量测试仍作为兼容回归。改造前的已知基线为 762 项中 717 通过、45 失败；它包含缺失数据、旧场景和待核查行为。新模块测试与旧失败清单分别记录。

本次全量兼容回归收集了 792 项，747 通过、45 失败，失败条目与改造前完全一致。
最后一次定向验证覆盖 36 项模块化测试与 11 项旧网页适配器测试，47 项全部通过。
后续新增的接口与固定回放样本由该定向验证覆盖。详细结果见
[改造回归证据](../ark_emulator/validation/reports/refactor_20261001.json) 与
[全量日志](../ark_emulator/validation/reports/full_regression_20261001.log)。
