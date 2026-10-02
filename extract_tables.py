"""Extract Arknights data tables from unpacked AB files.

Usage:
    python extract_tables.py

Scans data/anon/ for CAB files and extracts known data tables to data/tables/.
Table identifiers are matched by prefix (hash suffix may change between game versions).
"""

import os
import re
import shutil
import struct
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ANON_DIR = SCRIPT_DIR / "data" / "anon"
TABLES_DIR = SCRIPT_DIR / "data" / "tables"

# Known table name prefixes (hash suffix changes between game versions)
TABLE_PREFIXES = [
    "character_table",
    "skill_table",
    "stage_table",
    "activity_table",
    "charword_table",
    "handbook_info_table",
    "uniequip_table",
    "battle_equip_table",
    "skin_table",
    "retro_table",
    "roguelike_topic_table",
    "sandbox_perm_table",
    "building_data",
    "enemy_handbook_table",
    "enemy_database",
    # 热更包中出现的补充表（PersistentData/Bundles/anon 解包）
    "item_table",
    "gacha_table",
    "medal_table",
    "story_table",
    "zone_table",
    "shop_client_table",
    "climb_tower_table",
    "arkvent_table",
    "battle_misc_table",
    "display_meta_table",
    "extra_battlelog_table",
    "hotupdate_meta_table",
]

TABLE_FULL_PATTERN = re.compile(
    rf"^({'|'.join(TABLE_PREFIXES)})[a-f0-9]{{4,}}$")


def scan_cab_file(filepath: Path) -> str | None:
    """Return the table ID from an exportRaw name-length header."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(8192)
        if len(header) >= 4:
            name_len = struct.unpack_from("<I", header, 0)[0]
            if 0 < name_len <= 256 and 4 + name_len <= len(header):
                table_id = header[4:4 + name_len].decode("ascii", errors="ignore")
                if TABLE_FULL_PATTERN.fullmatch(table_id):
                    return table_id
        return None
    except Exception:
        return None


def find_tables(anon_dir: Path) -> dict[str, Path]:
    """Find valid raw exports; later base/hot directories override earlier ones."""
    found = {}
    raw_suffixes = {".bin", ".dat", ".bytes"}

    for source in sorted(anon_dir.iterdir()):
        if source.is_dir() and source.name.endswith(".bin_unpacked"):
            candidates = source.glob("CAB-*")
        elif source.is_dir():
            candidates = (
                path for path in source.rglob("*")
                if path.is_file()
                and path.suffix.lower() in raw_suffixes
                and path.name.lower().startswith(tuple(TABLE_PREFIXES))
            )
        elif source.is_file():
            candidates = (source,)
        else:
            continue

        for candidate in sorted(candidates):
            if not candidate.is_file() or candidate.stat().st_size < 4 * 1024:
                continue
            table_id = scan_cab_file(candidate)
            if table_id:
                found[table_id] = candidate

    return found


def main():
    if not ANON_DIR.exists():
        print(f"Error: {ANON_DIR} not found")
        print("Please extract AB files using AssetStudio-Arknights first")
        return

    TABLES_DIR.mkdir(parents=True, exist_ok=True)

    # Clear old tables
    for old in TABLES_DIR.glob("*.bin"):
        old.unlink()

    print(f"Scanning {ANON_DIR} for data tables...\n")

    found = find_tables(ANON_DIR)

    # Copy found tables
    for table_id, src in sorted(found.items()):
        dst = TABLES_DIR / f"{table_id}.bin"
        shutil.copy2(src, dst)
        size_mb = src.stat().st_size / (1024 * 1024)
        print(f"  {table_id} ({size_mb:.1f} MB)")

    print(f"\nDone: Extracted {len(found)} tables to {TABLES_DIR}")


if __name__ == "__main__":
    main()
