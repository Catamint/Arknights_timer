# examples — V1 历史示例

这些脚本和快照仅用于查阅旧实现行为。新增示例使用 V2 `ark_sim`；当前运行入口见
[项目 README](../README.md)，首关验收脚本为 `tools/verify_v2_baseline.py`。

- `demo_main_01-01.py` — 加载 1-01 并自动部署一个编队跑完。
- `run_sim.py` — 启动 LiveServer（`--level`/`--port`），浏览器实时看战场。
- `agent_greedy.py` / `agent_play.py` / `bot.py` — AI 打图示例
  （GreedyDefender / BeamAgent）。
- `squad_demo.json` / `squad_cheap.json` / `custom_enemies_demo.json` —
  编队与自定义敌人配置样例。
- `snapshot_demo.json` / `benchmark_report.json` — 快照与基准输出示例。
