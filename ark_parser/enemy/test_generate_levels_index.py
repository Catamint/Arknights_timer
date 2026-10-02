import json

from generate_levels_index import generate_index


def test_generate_index_preserves_assets_and_adds_existing_parsed_levels(tmp_path):
    asset_index = tmp_path / "asset_index.json"
    parsed_index = tmp_path / "level_data_index.json"
    levels_dir = tmp_path / "levels"
    levels_dir.mkdir()
    asset_index.write_text(json.dumps({
        "level_main_01-01": "raw/main.bytes",
        "level_act_01": "raw/act.bytes",
        "not_a_level": "raw/other.bytes",
        "level_script_table91d938": "raw/table.bytes",
    }), encoding="utf-8")
    parsed_index.write_text(json.dumps([
        {"levelId": "level_main_01-01"},
        {"levelId": "level_official_extra"},
        {"levelId": "level_missing_file"},
        {"levelId": "level_script_table91d938"},
    ]), encoding="utf-8")
    (levels_dir / "level_official_extra.json").write_text("{}", encoding="utf-8")

    result = generate_index(asset_index, parsed_index, levels_dir)

    assert result == [
        {"name": "level_act_01"},
        {"name": "level_main_01-01"},
        {"name": "level_official_extra"},
    ]