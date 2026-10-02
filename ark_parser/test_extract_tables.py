"""Tests for exact exportRaw table-name extraction."""

import struct

from extract_tables import find_tables, scan_cab_file


def _write_export_raw(path, table_id, signature_prefix=b"8"):
    name = table_id.encode("ascii")
    path.write_bytes(struct.pack("<I", len(name)) + name + signature_prefix + b"\0" * 5000)


def test_signature_hex_byte_is_not_appended_to_table_id(tmp_path):
    path = tmp_path / "activity_table.dat"
    _write_export_raw(path, "activity_table721b1b", b"8")
    assert scan_cab_file(path) == "activity_table721b1b"


def test_exact_name_supports_non_hex_signature_prefix(tmp_path):
    path = tmp_path / "enemy_database.dat"
    _write_export_raw(path, "enemy_databasea5b667", b"\xcb")
    assert scan_cab_file(path) == "enemy_databasea5b667"


def test_unity_serialized_cab_with_embedded_table_name_is_rejected(tmp_path):
    path = tmp_path / "CAB-enemy_databasea5b667"
    path.write_bytes(b"\0" * 32 + b"enemy_databasea5b667" + b"\0" * 5000)
    assert scan_cab_file(path) is None


def test_flat_base_and_hot_exports_choose_hot_and_ignore_invalid_cab(tmp_path):
    anon = tmp_path / "anon"
    legacy = anon / "9dfb.bin_unpacked"
    base = anon / "base_20261001"
    hot = anon / "zz_hot_20261001"
    legacy.mkdir(parents=True)
    base.mkdir()
    hot.mkdir()
    (legacy / "CAB-old").write_bytes(
        b"\0" * 32 + b"enemy_databasea5b667" + b"\0" * 5000)
    _write_export_raw(base / "enemy_databasea5b667.dat",
                      "enemy_databasea5b667")
    _write_export_raw(hot / "enemy_databasea5b667.dat",
                      "enemy_databasea5b667")
    with (base / "enemy_databasea5b667.dat").open("ab") as f:
        f.write(b"base")
    with (hot / "enemy_databasea5b667.dat").open("ab") as f:
        f.write(b"hot!")

    found = find_tables(anon)

    assert found["enemy_databasea5b667"] == hot / "enemy_databasea5b667.dat"
