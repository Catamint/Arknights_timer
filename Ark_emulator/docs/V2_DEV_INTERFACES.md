# V2 并行开发接口

本文件冻结本轮并行开发的最小接口。共享类型在 `ark_sim/contracts/models.py`，由主 agent 维护；各子任务只编辑分配目录，接口变化先通知主 agent。

## 内核接口

`Session(quantum=1/30, seed=0, phase_order=None, reaction_budget=10000)`，业务规则不进入内核。

- `time` 为当前整数逻辑时间；`quantum` 为一个时间单位的秒数。
- `world.create(definition_id, components, tags=(), alias=None)` 返回本场实体 ID。
- `world.get(entity_id)` 返回包含 `id/definition_id/tags/components` 的只读实体视图。
- `world.entities()` 返回按 ID 稳定排序的只读视图；`world.resolve(alias_or_id)` 解析身份。
- `world.snapshot()` 与 `world.restore(data)` 导出恢复 JSON 数据。
- `session.register_handler(kind, callable)`；回调为 `(session, payload)`，只由执行者调用。
- `session.add_system(callable, phase=0)`；每个逻辑时间调用 `(session)`。
- `session.schedule(kind, payload, at, phase=0, priority=0)` 返回任务 ID，过去时间拒绝。
- `session.cancel(task_id)`；`session.advance(n)` 处理 `[time,time+n)`，完成后时间等于终点。
- `session.commit(intents)` 原子提交通用变更意图；`emit(type,payload,cause=None)` 记录本地 ID 事件。
- `session.random.sample(stream)` 为命名随机数流采样并记录序号。
- `session.snapshot()/checkpoint()/restore(checkpoint)` 不序列化回调，只序列化状态。

Intent 操作包括 `create/set/delete/emit/schedule/cancel`。create 的 data 为 definition_id/components/tags/alias；set 的 path 相对 components；schedule 的 data 为 kind/payload/at/phase/priority；emit 的 data 为 type/payload/cause。一次提交中任一意图无效时，world、事件与任务队列均不发生部分变化。

## 规则接口

`RuleRuntime(rules, bindings=None, catalog=None, numeric_profile=None, providers=None)`。

- `evaluate(calculation_id, inputs, scope=None, rule_id=None, context=None) -> EvaluationResult`。
- `evaluate_ref(rule_id, inputs, context=None) -> EvaluationResult`。
- `validate_rule(definition)` 在运行前检查表达式或图。
- 提供器注册为名称到可调用对象或声明对象；调用签名 `(inputs, params, context)`，结果必须符合输出类型。
- scope 的顺序为 scenario、source/target/owner（按 catalog.owner 选择）、component、attribute_or_resource、ability、effect、invocation，各层为 calculation_id 到 rule_id 的映射。
- 表达式至少支持设计样例、算术、比较、条件、布尔和 max/min/abs；不能使用 Python eval。
- 计算图格式为 implementation.nodes 的列表，节点含 id 与 expression 或 rule 和 inputs，output 是读取 nodes 的表达式；显式依赖检查与循环拒绝。

## 编译接口

`Compiler.compile(scenario, packages=None, ruleset=None, overrides=None) -> SimulationProgram`。scenario 可为完整包的 scenarioDraft、场景 dict 或 ID；packages 可为 dict、JSON 路径或目录。

- definitions 按 ID 保存原始定义，规则也保留在 rules 中；最终只包含传递依赖。
- ruleset 至少含 id/bindings/quantum/numeric_profile/system_order；领域使用这些配置。
- 初始实体字段 definition/instanceAlias/position/facing；scene 可含 waves、map、commands。
- 内置预设由 content 下的独立数据定义提供：preset/ark_standard、ruleset/ark_standard、policy/ark_ground_deploy、policy/ark_lifecycle、behavior/player_combat，以及样例所需基础规则。预设是内容，不进入内核。
- 局部规则覆盖、继承、缺失引用与环必须在编译时解释。

## 主 agent 集成范围

主 agent 负责 `ark_sim/domains`、公开 Engine、CLI、合成场景与 0-1 内容转换、集成测试、文档和最终评审。阶段一代码不得进入新版执行路径。计算接口清单中尚未实现的必需能力必须明确报错。
