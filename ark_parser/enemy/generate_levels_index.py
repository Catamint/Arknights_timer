#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the raw-level ID index consumed by build_sim_bundle.py.

The simulator asset index is the coverage denominator: it includes extracted
level assets whether or not this checkout has parsed JSON for them. Parsed
level_data_index entries are added only when their JSON file exists locally.
"""

import argparse
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_ASSET_INDEX = (
    SCRIPT_DIR.parents[1]
    / "Ark_emulator"
    / "ark_emulator"
    / "data_level_assets_index.json"
)


def generate_index(asset_index_path, parsed_index_path, levels_dir):
    with Path(asset_index_path).open(encoding="utf-8") as stream:
        asset_index = json.load(stream)
    if not isinstance(asset_index, dict):
        raise ValueError("asset index must be a JSON object mapping IDs to paths")

    level_ids = {
        level_id for level_id in asset_index
        if isinstance(level_id, str)
        and level_id.startswith("level_")
        and level_id != "level_script_table91d938"
    }

    with Path(parsed_index_path).open(encoding="utf-8") as stream:
        parsed_index = json.load(stream)
    if not isinstance(parsed_index, list):
        raise ValueError("parsed level index must be a JSON array")

    levels_dir = Path(levels_dir)
    for entry in parsed_index:
        if not isinstance(entry, dict):
            continue
        level_id = entry.get("levelId")
        if (not isinstance(level_id, str)
                or not level_id.startswith("level_")
                or level_id == "level_script_table91d938"):
            continue
        if (levels_dir / f"{level_id}.json").is_file():
            level_ids.add(level_id)

    return [{"name": level_id} for level_id in sorted(level_ids)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-index", type=Path, default=DEFAULT_ASSET_INDEX,
                        help="raw level asset ID-to-path index")
    parser.add_argument("--parsed-index", type=Path,
                        default=DATA_DIR / "level_data_index.json",
                        help="index emitted by extract_level_data.py")
    parser.add_argument("--levels-dir", type=Path, default=DATA_DIR / "levels")
    parser.add_argument("--out", type=Path, default=DATA_DIR / "levels_index.json")
    args = parser.parse_args()

    index = generate_index(args.asset_index, args.parsed_index, args.levels_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as stream:
        json.dump(index, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")

    print(f"indexed {len(index)} level IDs -> {args.out}")


if __name__ == "__main__":
    main()