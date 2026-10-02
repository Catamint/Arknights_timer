# enemy — 敌方数据解析

`extract_enemy_data.py` 提取敌人库/关卡/技能目录到 `data/`；
`00_overview.md`～`11_skill_prefab_catalog.md` 为完整逆向文档；
`build_sim_bundle.py` 汇总生成 `stage_sim_bundle.json`（模拟器关卡 bundle）。

生成 stage bundle 前运行：

```bash
python ark_parser/enemy/generate_levels_index.py
python ark_parser/enemy/build_sim_bundle.py
```

`generate_levels_index.py` 从 `Ark_emulator/ark_emulator/data_level_assets_index.json`
读取本地原始关卡资产 ID，并补入已有解析 JSON 对应的额外关卡，生成
`data/levels_index.json`（`[{"name": "level_..."}]`，供 bundle coverage 使用）。
