# 新版自定义内容与规则示例

本文说明新底座如何创作干员、技能、Buff 和计算规则。完整样例现可由新版 `ark_sim` 执行，不能交给阶段一运行时。实际字段与限制见 [V2_AUTHORING.md](V2_AUTHORING.md)。机器可读的原始样例见 [v2_examples/custom_guard.json](v2_examples/custom_guard.json)，规则契约见 [V2_RULE_CATALOG.json](V2_RULE_CATALOG.json)。

## 自定义干员

单位使用任意命名空间 ID，定义基础属性、资源、能力、部署规则与行为。成长、养成与天赋属于可选内容，没有精零或官方干员名单限制。

```json
{
  "id": "unit/my_guard",
  "kind": "entity",
  "tags": ["player", "ground", "melee"],
  "components": {
    "attributes": {"base": {"atk": 100, "def": 50, "max_hp": 1200, "attack_interval": 1.0}},
    "resources": {
      "hp": {"initial": 1200, "capacity_attribute": "max_hp"},
      "energy": {"initial": 20, "capacity": 40, "recovery_rule": "rule/my_energy_recovery"}
    },
    "abilities": ["ability/my_basic", "ability/my_burst"],
    "deployable": {"policy": "policy/ark_ground_deploy"},
    "behavior": {"machine": "behavior/player_combat"}
  }
}
```

`policy/ark_ground_deploy` 和 `behavior/player_combat` 由所选明日方舟预设提供。单位也可以引用自己的部署或状态规则。属性名 `atk` 与资源名 `energy` 是内容定义，内核不因某个 ID 自动附加特殊规则。

## 自定义技能

技能定义目标、激活方式、资源支付和有序时间线。下面的设计使用已有效果原语：先支付 20 能量，施加 5 秒增益，然后在 0.2 秒间隔内执行三次范围伤害，每次成功命中返还 2 能量。

```json
{
  "id": "ability/my_burst",
  "kind": "ability",
  "activation": {"mode": "manual", "costs": [{"resource": "energy", "amount": 20}]},
  "selector": "selector/my_front_area",
  "timeline": [
    {"at_seconds": 0, "effect": {"op": "apply_buff", "target": "source", "buff": "buff/my_attack_boost"}},
    {
      "at_seconds": 0.2,
      "repeat": {"count": 3, "interval_seconds": 0.2},
      "effect": {
        "op": "damage",
        "target": "selected",
        "damage_type": "physical",
        "rules": {"damage.base": "rule/my_skill_power", "damage.mitigation": "rule/my_physical"},
        "read_mode": {"source_attributes": "at_hit", "target_attributes": "at_hit"},
        "on_success": [{"op": "modify_resource", "target": "source", "resource": "energy", "delta": 2}]
      }
    }
  ]
}
```

目标范围、选择时机、成功命中的定义、是否允许尸体目标和重复任务的中断方式均需要在定义或预设中明确。样例将成功定义为一次未被取消且被接受的命中，返还量按每个被接受的目标命中计数；实际作者可以更换此规则。

该技能不绑定某个 Python 干员类。另一单位可以引用同一能力，也可以继承能力定义并只改能量成本、次数或范围。

完整样例给初始单位定义了实例别名 `actor/guard_1`，激活指令引用该实例。
内容 ID `unit/my_guard` 用于创建单位，同定义的多个实例仍拥有不同身份。
生命周期与初始 alive 状态由样例显式引用的预设策略提供。

## 自定义 Buff

```json
{
  "id": "buff/my_attack_boost",
  "kind": "buff",
  "duration_seconds": 5,
  "stacking": {"mode": "refresh", "identity": ["definition", "source", "target"]},
  "modifiers": [{"attribute": "atk", "layer": "direct_ratio", "value": 0.5}]
}
```

`direct_ratio` 在所选规则集中定义其叠加方法。如果另一个规则集规定百分比连乘，Buff 定义仍可复用；有效属性结果会由对应规则决定。周期损伤、资源回复和受击触发等行为通过事件与效果列表定义。

## 替换一个公式

```json
{
  "id": "rule/my_physical",
  "kind": "calculation_rule",
  "contract": "damage.mitigation",
  "implementation": {
    "type": "expression",
    "expression": "max(inputs.power - inputs.defense * params.defense_factor, inputs.power * params.minimum_ratio)"
  },
  "parameters": {"defense_factor": 0.5, "minimum_ratio": 0.01}
}
```

基础威力 100、目标防御 80 时，示例输出 60；把 `defense_factor` 改为 1 后输出 20。只有这一阶段采用该公式，后续护盾、生命写入和事件仍按伤害管线继续执行。若要直接替换整条结算，则绑定新的 `damage.pipeline` 提供器或计算图。

公式可以绑定到规则集、场景、来源或目标拥有的计算、能力或效果。相同 ID 的覆盖顺序与所有者由规则契约决定，冲突会在编译时显示。首次实现至少支持规则集级和能力效果级替换。

## 自定义资源与其他底层计算

样例的能量回复规则可定义为 `inputs.current + params.rate * inputs.delta_seconds`；默认每秒加 2。可以替换为随 HP 比例变化的表达式，也可以将回复策略换成 `on_damage_taken` 事件触发。

攻击间隔可以采用 `base_interval / speed_ratio`、分段函数或固定间隔；属性成长可以用线性、曲线或经验表；移动距离、部署费用、护盾分配、目标排序和终局判断均使用各自规则接口。复杂排序与寻路选择 Python 提供器，避免将算法强行塞入一条公式。

## 作者操作流程

1. 创建内容包并选择一个规则预设。
2. 添加单位定义，引用或组合能力、Buff 和状态图。
3. 根据需要添加表达式规则、计算图或算法提供器，并绑定作用域。
4. 用创作校验工具检查字段、类型、引用、循环依赖和能力支持。
5. 在合成场景试算，查看公式轨迹与状态变化。
6. 锁定内容与规则版本，再加入 0-1 或其他目标关卡验证。

CLI 已提供 `validate`、`explain`、`preview` 和 `run`，Python Builder 生成同样的内容定义；运行例子见 [实现说明](V2_IMPLEMENTATION.md)。

## 便捷性的验收例子

新增 `unit/my_guard`、组合上面的技能、改防御系数和能量回复后，内核目录应没有改动。更换复杂寻路提供器时，只新增提供器模块和配置绑定。一个行为确实需要新的底层能力时，扩展明确的领域契约与测试，然后所有内容包都可以使用它。
