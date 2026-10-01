# V2 内容创作与规则配置

本文对应实际的 `ark_sim` 编译器、规则运行时、Builder 和 CLI。明日方舟预设与内容包独立于时间、实体、调度和事务内核。作者通过定义与规则组合单位，无需添加角色专用 Python 类。

可运行样例为 [packages/custom/custom_guard.json](../packages/custom/custom_guard.json)。架构与需求见 [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md) 和 [REQUIREMENTS.md](REQUIREMENTS.md)。规则契约的运行版本随包保存于 `ark_sim/rules/contracts.json`；设计清单中的声明不等于所有游戏机制已实现。

## 先运行作者工具

在 `D:\Arknights\Arknights_timer\Ark_emulator` 执行：

```powershell
..\.venv\Scripts\python.exe -m ark_sim validate packages/custom/custom_guard.json
..\.venv\Scripts\python.exe -m ark_sim explain packages/custom/custom_guard.json --output dependencies.json
..\.venv\Scripts\python.exe -m ark_sim run packages/custom/custom_guard.json --seconds 1 --output snapshot.json --replay-output replay.json
..\.venv\Scripts\python.exe -m ark_sim replay packages/custom/custom_guard.json --record replay.json --output replayed.json

# 同一内容采用另一整条伤害管线
..\.venv\Scripts\python.exe -m ark_sim run packages/custom/custom_guard.json --ruleset ruleset/custom_balance --seconds 1 --output balanced.json

# 从固定 JSON 提取产物离线转换 0-1，不导入旧模拟运行时
..\.venv\Scripts\python.exe -m ark_sim validate --scenario ark-00-01
```

`validate` 检查结构、引用、传递依赖、继承、循环、计算规则、提供器和实际内容需要的计算接口。错误返回非零退出码，并显示定义 ID 或字段路径。`explain` 输出依赖关系、提供器版本、规则预设、场景和嵌套作用域。`preview` 给出试算值与计算轨迹。`run` 使用真实 Engine，输出快照，可同时导出回放。`replay` 必须提供 `--record`，按记录里的种子、时间、命令与身份重放，不接受额外的种子或时间参数。

公式试算可以用文件，避免终端的 JSON 引号差异：

```json
{"power": 100, "defense": 80, "resistance": 0, "damage_type": "physical"}
```

将上述内容保存为 `inputs.json` 后运行：

```powershell
..\.venv\Scripts\python.exe -m ark_sim preview packages/custom/custom_guard.json --rule rule/my_physical --inputs inputs.json
```

结果为 60。也可用 `--calculation damage.mitigation` 按规则绑定试算，并以 `--scope` 提供局部规则 JSON。`--package` 可重复指定额外包；`--scenario` 选择包内的具体场景。`--override damage.mitigation=rule/my_physical` 在场景作用域显式覆盖一个计算。`--ticks` 使用整数逻辑时间；`--seconds` 和额外指令的 `at_seconds` 通过所选 `time.quantize` 换算。

## 内容包与引用

内容包使用 `schemaVersion: 2`，可包含 `manifest`、`entities`、`abilities`、`buffs`、`selectors`、`behaviors`、`policies`、`rules`、`rulesets`、`scenarios` 或通用 `definitions`。单场景可以写在 `scenarioDraft` 中。

ID 必须是带命名空间的字符串，例如 `unit/my_guard`，没有官方干员白名单。字段名严格检查；描述性资料放入 `metadata`，算法参数放入 `parameters`。导入时重复定义同一 ID 会报冲突。需要复用时创建新 ID，以 `extends` 指定父定义；对象递归覆盖，数组整体替换。可用 `{"$delete": true}` 删除继承字段。

```json
{
  "id": "unit/my_guard_variant",
  "kind": "entity",
  "extends": "unit/my_guard",
  "components": {"attributes": {"base": {"atk": 180}}}
}
```

内容定义 ID 与运行实例身份分开。初始实体使用 `definition` 和 `instanceAlias`；技能命令的 `source` 引用实例别名。同一单位定义可实例化多次。部署命令使用 `entity` 或 `definition`，以 `alias` 命名创建的实例。

```json
{
  "id": "scenario/my_scene",
  "ruleset": "ruleset/ark_standard",
  "initialEntities": [
    {"definition": "unit/my_guard", "instanceAlias": "actor/guard", "position": {"row": 2, "col": 2}, "facing": "right"}
  ],
  "commands": [
    {"at_seconds": 0, "action": "activate_ability", "source": "actor/guard", "ability": "ability/my_burst"}
  ]
}
```

场景、编队 `roster`、绝对出生波次、初始实体和命令中的内容引用决定加载闭包。规则与提供器也进入闭包和版本锁。`dependencies` 声明额外必需定义；`dynamicReferences` 可以声明允许的 ID 集合，但当前运行时不执行动态引用解析。`metadata`、普通参数、表达式输入和事件载荷不会因恰巧含有 `rule` 等字段而被误当成引用。

场景命令和波次的 `at` 是绝对整数逻辑时间，能力时间线的 `at` 是相对施放起点的整数逻辑时间；均要求非负整数，并与 `at_seconds` 互斥。`at_seconds` 使用非负秒数。标准量化公式先按规则参数 `ratio_digits=12` 对秒数与 quantum 的比值舍入，再取上界，避免浮点表达把 6 个单位误算为 7 个；精度参数和整条 `time.quantize` 规则都能替换。

绝对波次一条只创建一个实例；需要多次出生时写多条明确时间的波次。当前不执行 `count != 1` 或非零 `interval_seconds`，编译时拒绝这些配置。路线使用内联 `route`，未实现的 `route_id` 解析会明确报错。

## 单位、属性与资源

单位由 `components` 组成。当前支持属性、资源、能力、部署、空间、行为、生命周期和 Buff 容器。属性名、资源名均由作者指定。`hp`、`sp`、`dp` 和数值角色只在明日方舟预设中声明。

初始 Buff 写作 `"buffs": {"initial": ["buff/my_boost"]}`，也可使用互斥别名 `buff_container`，两个组件不能同时出现。初始列表仅支持 Buff ID 字符串；引用必须存在且种类为 Buff。创建实例时以该实例为来源施加这些 Buff，初始修饰器会参与有效属性计算。

```json
{
  "attributes": {"base": {"atk": 100, "max_hp": 1200, "attack_interval": 1}},
  "resources": {
    "hp": {"initial": 1200, "capacity_attribute": "max_hp", "role": "health"},
    "energy": {"initial": 20, "capacity": 40, "recovery_rate": 2}
  },
  "abilities": ["ability/my_basic", "ability/my_burst"],
  "lifecycle": {"policy": "policy/ark_lifecycle"}
}
```

资源容量和边界由 `resource.capacity` 与 `resource.bounds` 执行，可通过 `capacity_rule`、`bounds_rule` 或资源的 `rules` 更换。资源没有隐含的“初始值就是上限”算法。不同单位和资源可采用不同容量、保留或拒绝策略。

连续恢复是默认驱动。周期恢复显式声明：

```json
{
  "initial": 0,
  "capacity": 40,
  "recovery_rate": 2,
  "recovery": {"mode": "periodic", "interval_seconds": 1},
  "parameters": {"pause_at_full": false, "freeze_while_cast": true, "freeze_cast_modes": ["manual"]}
}
```

周期按整数逻辑时间累计，达到间隔才调用恢复公式；冻结保留未完成周期。`recovery_rate` 表示每秒速率，默认公式读取 `inputs.parameters.rate`。自定义恢复规则可以计算降温、负向变化或依赖属性的变化。满值时继续执行规则；只有显式 `pause_at_full: true` 才暂停计时。明日方舟 SP 包声明只冻结 `manual`，普攻不会停掉自动回复。

成长使用 `growth` 或 `components.attributes.growth`：`{"atk": {"rule": "rule/my_growth", "level": 120, "parameters": {}}}`。`rule` 可省略，此时使用规则集、场景、拥有者、组件或该属性的 `attributes.growth` 绑定；显式 `rule` 则优先选择该实现。编译阶段会确认有可用绑定。规则输入为 `base`、`level` 和 `growth_parameters`，等级不受官方精英阶段限制。单属性规则可写在 `attribute_rules`，例如 `{"atk": {"attributes.effective": "rule/my_effective"}}`。

## 技能、Buff 与状态图

技能使用 `activation`、资源成本、可选 `selector` 和有序 `timeline`。激活模式支持 `manual`、`automatic_attack`、`on_deploy`、有明确事件的 `passive`。时间线可有限重复，并能组合伤害、治疗、资源变化、Buff、生成、发事件、直接坐标移动、状态转换和延迟效果。

```json
{
  "id": "ability/my_burst",
  "kind": "ability",
  "activation": {"mode": "manual", "costs": [{"resource": "energy", "amount": 20}]},
  "selector": "selector/my_front_area",
  "target_capture": "each_hit",
  "timeline": [
    {"at_seconds": 0, "effect": {"op": "apply_buff", "target": "source", "buff": "buff/my_attack_boost"}},
    {"at_seconds": 0.2, "repeat": {"count": 3, "interval_seconds": 0.2},
     "effect": {"op": "damage", "target": "selected", "damage_type": "physical",
       "rules": {"damage.base": "rule/my_skill_power", "damage.mitigation": "rule/my_physical"},
       "on_success": [{"op": "modify_resource", "target": "source", "resource": "energy", "delta": 2}]}}
  ]
}
```

完整样例包括对应的选择器、单位与规则。成本、时间线任务和施放状态原子启动；无法支付时不扣除部分资源。`read_mode` 可分别配置来源和目标属性的 `at_cast`、`at_launch`、`at_hit` 读取。被拒绝的伤害走 `on_failure`，不执行命中成功返还。

Buff 支持 refresh、independent、add、extend、max，持续、周期与层数分别由计算接口决定。修饰器使用 `attribute`、`layer`、`value` 与可选 `stacks`；单条修饰器的 `rule`、`operation` 暂不执行，编译时拒绝。替换属性算法应绑定 `attributes.modifier_layer` 或整个 `attributes.effective`。

Buff 和能力的事件订阅采用 `[{"event": "damage.accepted", "condition": "...", "effects": [...]}]`。条件是安全表达式，反应通过事件任务队列执行。Buff 周期采用 `interval_seconds` 与 `effects`。状态图采用 `states: {id: {on_enter: [], on_exit: []}}` 与 `transitions: [{from, to, condition, priority, effects}]`，可用 `condition_rule` 指向 `behavior.threshold`。同一时刻按优先级与定义顺序选择转换。

## 替换公式、整条管线与提供器

表达式读取契约中的 `inputs` 与规则 `parameters` 对应的 `params`，通过独立 AST 解释器执行。支持算术、比较、条件、布尔、字段、索引、列表、字典和受控数学函数；没有 Python `eval`、导入、任意方法调用或状态写入。

计算图节点使用 `expression`、固定 `rule` 或动态 `calculation` 三选一。固定 `rule` 锁定具体实现；`calculation` 保留来源、目标、能力、效果等规则作用域。节点结果是原始值，读作 `nodes.power`。字符串输入是表达式；字符串常量用 `{"literal": "physical"}`。

完整伤害管线返回 `accepted`、`amount`、`allocations` 和 `events`。默认图的属性读取由 `metadata.input_bindings` 声明：例如 `{"attack": {"entity": "source", "attribute_role": "attack"}}`。一个固定伤害管线没有该绑定时可以完全跳过攻击力和减伤读取。护盾等自定义资源可通过结算分配执行。

复杂算法注册独立提供器：

```python
def my_algorithm(inputs, params, context):
    return inputs["base"] * params["factor"]

providers = {"custom.algorithm": {"callable": my_algorithm, "version": "1.0.0"}}
program = Compiler(providers=providers).compile(my_package)
simulation = Engine.create(program, providers=providers)
```

如果内容同时使用明日方舟提供器，需要把所需预设提供器一起注册。提供器签名为 `(inputs, params, context)`，只读上下文可通过 `calculate()` 和 `invoke_provider()` 组合纯计算，没有 World 写入口。提供器可以声明参数 schema。动态子计算可在规则的 `metadata.calculation_dependencies` 声明；聚合提供器以 `parameters.aggregator.provider` 或显式 `provider_dependencies` 声明。

自定义契约可传给 `Compiler(catalog=...)`，编译结果保存类型清单，Engine 与公式试算读取同一清单。编译器检查实际内容需要的计算是否有合法默认或局部绑定；空世界不需要伤害、部署等无关计算。规则集必须声明或继承正的 `quantum`，不会给自定义规则集偷偷补上明日方舟频率。

## Python Builder 与公开运行接口

```python
from ark_sim import Compiler, Engine
from ark_sim.tools.authoring import PackageBuilder, EntityBuilder, AbilityBuilder

skill = (AbilityBuilder("ability/demo")
         .cost("energy", 5)
         .effect(0, {"op": "modify_resource", "target": "source", "resource": "energy", "delta": 2}))
unit = (EntityBuilder("unit/demo", tags=["player"])
        .attributes(atk=10, max_hp=100, attack_interval=1)
        .resource("hp", initial=100, capacity_attribute="max_hp", role="health")
        .resource("energy", initial=10, capacity=20, recovery_rate=1)
        .component("lifecycle", {"policy": "policy/ark_lifecycle"})
        .abilities("ability/demo"))
builder = (PackageBuilder("package/demo")
           .add(unit).add(skill)
           .scenario("scenario/demo", ruleset="ruleset/ark_standard",
                     initialEntities=[{"definition": "unit/demo", "instanceAlias": "actor/demo"}],
                     commands=[{"at_seconds": 0, "action": "activate_ability", "source": "actor/demo", "ability": "ability/demo"}]))
program = builder.compile()  # 与 Compiler().compile(builder.build()) 相同
simulation = Engine.create(program, seed=123)
simulation.advance(30)
snapshot = simulation.snapshot()
record = simulation.export_replay()
```

`Builder.build()` 生成普通 JSON 数据，`write()` 保存内容包。JSON、Builder、路径和目录共享同一个编译流程。`SimulationProgram` 递归只读；每场运行独立保存资源、Buff、施放、状态机、计时和随机流。检查点与回放锁定程序、规则、提供器、数值、随机算法和实现身份；修改实现后应重新编译并重新生成验证证据。

## 当前明确的边界

- 动态引用只支持依赖声明，执行解析尚未支持。
- 装备、天赋、完整养成流程、嵌套状态子图尚未实现；已知未实现字段不会静默通过。
- 自定义 Buff 叠层 policy、中断后退款或继续执行、基于力与重量的 `displace` 尚未支持。直接坐标的 `move` 已支持。
- 单条修饰器的 `rule` 和 `operation` 不支持；层公式和整体属性管线可以替换。
- 数值后端为 `float`/`float64`，可配置量化、精度和舍入；其他后端与 `declarative_graph` 实现明确报错。
- 战斗中的规则热切换未实现；编辑内容默认在下一场重新编译后生效。
- 0-1 导入与合成样例属于模型验证。真实客户端命中帧、移动与教程时序等仍需外部对照，不能把确定回放等同于全游戏准确模拟。

遇到新机制时，先检查能否由现有规则、图和效果组成。需要新底层能力时，增加明确的领域适配器、输入输出契约与独立预期，再把它开放给所有内容包。
