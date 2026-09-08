# -*- coding: utf-8 -*-
"""Runtime enemy path table and public API contract tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from backend.app.enemy_ui import (
    ENEMY_COLUMN_DEFS,
    PATHING_COLUMN_KEYS,
    format_column_value,
    load_column_order,
    load_visible_columns,
    save_column_order,
    save_visible_columns,
)
from backend.app.services.websocket_api import (
    TOPIC_DEFAULTS,
    TOPIC_LIMITS,
    WebSocketApi,
    _Client,
)
from backend.desktop_app import CoachWindow, fail_closed_mismatched_enemy_pathing


def _pathing(**overrides):
    value = {
        "available": True,
        "sample_frame": 901,
        "consistent": True,
        "path_identity_stable": True,
        "cursor_kind": "primary",
        "temporarily_diverted": False,
        "intent_end": {"row": 5, "col": 9, "label": "F10"},
        "route": {
            "kind": "main", "index": 0, "ordinal": 1,
            "global_index": 0, "label": "主#1（A1→F10）",
            "matched_by": "route_object", "runtime_modified": False,
        },
        "next_waypoint": {"row": 2, "col": 7, "label": "C8"},
        "next_checkpoint": {
            "index": 2, "ordinal": 3, "type": 3,
            "type_name": "WAIT_CURRENT_FRAGMENT_TIME",
            "label": "等待当前片段至15.00秒", "target": None, "time": 15.0,
        },
        "checkpoint_countdown": {
            "seconds": 2.366667, "frames": 71, "exact": True,
            "source": "fragment_clock", "waiting": True,
        },
    }
    value.update(overrides)
    return value


class EnemyPathingColumnTests(unittest.TestCase):
    def test_five_columns_are_immediately_after_precise_position(self):
        keys = [column["key"] for column in ENEMY_COLUMN_DEFS]
        start = keys.index("precise_pos") + 1
        self.assertEqual(tuple(keys[start:start + 5]), PATHING_COLUMN_KEYS)
        self.assertTrue(all(
            ENEMY_COLUMN_DEFS[keys.index(key)]["default"]
            for key in PATHING_COLUMN_KEYS))

    def test_old_settings_are_migrated_once_then_respect_user_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(
                str(Path(temp_dir) / "settings.ini"), QSettings.Format.IniFormat)
            visible_key = "enemy_table/visible_columns"
            order_key = "enemy_table/column_order"
            settings.setValue(visible_key, "row,name,precise_pos,action_state")
            settings.setValue(order_key, "row,name,precise_pos,action_state")

            visible = load_visible_columns(settings, visible_key)
            order = load_column_order(
                settings, order_key,
                [column["key"] for column in ENEMY_COLUMN_DEFS])
            self.assertTrue(set(PATHING_COLUMN_KEYS).issubset(visible))
            insert_at = order.index("precise_pos") + 1
            self.assertEqual(tuple(order[insert_at:insert_at + 5]), PATHING_COLUMN_KEYS)

            # A later explicit hide/reorder must not be undone on restart.
            visible.remove("current_route")
            save_visible_columns(settings, visible_key, visible)
            custom_order = ["row", "current_route", "name"] + [
                key for key in order if key not in {"row", "current_route", "name"}]
            save_column_order(settings, order_key, custom_order)
            self.assertNotIn("current_route", load_visible_columns(
                settings, visible_key))
            self.assertEqual(load_column_order(
                settings, order_key,
                [column["key"] for column in ENEMY_COLUMN_DEFS])[:3],
                ["row", "current_route", "name"])

    def test_path_columns_format_current_snapshot_and_seconds_before_frames(self):
        enemy = SimpleNamespace(lifecycle="active", pathing=_pathing())
        decimals = {"default": 2, "checkpoint_countdown": 2}
        self.assertEqual(format_column_value(
            "intent_end", enemy, decimals), "F10")
        self.assertEqual(format_column_value(
            "current_route", enemy, decimals), "主#1（A1→F10）")
        self.assertEqual(format_column_value(
            "next_waypoint", enemy, decimals), "C8")
        self.assertEqual(format_column_value(
            "next_checkpoint", enemy, decimals), "等待当前片段至15.00秒")
        self.assertEqual(format_column_value(
            "checkpoint_countdown", enemy, decimals), "2.37秒 / 71帧")

        # The formatter is stateless: an invalid current frame replaces, never
        # carries forward, the valid label from the preceding call.
        enemy.pathing = _pathing(
            available=False, consistent=False,
            path_identity_stable=False, reason="path_changed_during_snapshot")
        for key in PATHING_COLUMN_KEYS:
            self.assertEqual(format_column_value(key, enemy, decimals), "同步重试")


class EnemyPathingApiTests(unittest.TestCase):
    def setUp(self):
        self.api = WebSocketApi(enabled=False, app_version="test")

    def tearDown(self):
        self.api.stop(timeout=0)

    def test_pathing_topic_rate_and_public_whitelist(self):
        self.assertEqual(TOPIC_DEFAULTS["enemy_pathing"], 30.0)
        self.assertEqual(TOPIC_LIMITS["enemy_pathing"], (1.0, 60.0))
        pathing = _pathing()
        pathing["cursor_ptr"] = "0x12345678"
        pathing["unknownInternal"] = "secret"
        pathing["route"]["route_addr"] = "0x87654321"
        pathing["next_checkpoint"]["data_ptr"] = "0x11112222"
        enemy = SimpleNamespace(
            roster_id=7, eid="enemy_100", name="测试敌人", pos_x=1.0,
            pos_y=2.0, hp=100.0, max_hp=100.0, alive=True,
            lifecycle="active", action={}, shield=0.0, abnormal_status=[],
            pathing=pathing,
        )

        self.api.publish_runtime({
            "frame_consistent": True, "fixed_frame": 901, "state": 2,
            "enemies": [enemy], "characters": [],
        })
        payload = self.api._snapshots["enemy_pathing"]
        public = payload["items"][0]["pathing"]
        self.assertEqual(payload["sampleFrame"], 901)
        self.assertTrue(payload["consistent"])
        self.assertEqual(public["sampleFrame"], 901)
        self.assertEqual(public["intentEnd"]["label"], "F10")
        self.assertEqual(public["route"]["globalIndex"], 0)
        self.assertEqual(public["nextCheckpoint"]["typeName"],
                         "WAIT_CURRENT_FRAGMENT_TIME")
        self.assertEqual(public["checkpointCountdown"]["frames"], 71)
        self.assertEqual(
            self.api._snapshots["enemies"]["items"][0]["pathing"], public)
        encoded = json.dumps(payload)
        self.assertNotIn("cursor_ptr", encoded)
        self.assertNotIn("route_addr", encoded)
        self.assertNotIn("data_ptr", encoded)
        self.assertNotIn("unknownInternal", encoded)
        self.assertNotIn("0x12345678", encoded)

    def test_inconsistent_frame_overwrites_path_topic_with_unavailable(self):
        enemy = SimpleNamespace(roster_id=8, pathing=_pathing(sample_frame=100))
        self.api.publish_runtime({
            "frame_consistent": True, "fixed_frame": 100,
            "enemies": [enemy], "characters": [],
        })
        self.assertTrue(self.api._snapshots[
            "enemy_pathing"]["items"][0]["pathing"]["available"])

        self.api.publish_runtime({
            "frame_consistent": False, "fixed_frame": 101,
            "enemies": [enemy], "characters": [],
        })
        payload = self.api._snapshots["enemy_pathing"]
        self.assertFalse(payload["consistent"])
        self.assertFalse(payload["items"][0]["pathing"]["available"])
        self.assertEqual(payload["items"][0]["pathing"]["reason"],
                         "frame_inconsistent")
        self.assertIsNone(payload["items"][0]["pathing"]["route"])
        cached_enemy = self.api._snapshots["enemies"]["items"][0]
        self.assertFalse(cached_enemy["pathing"]["available"])
        self.assertIsNone(cached_enemy["pathing"]["intentEnd"])

    def test_entity_path_from_another_logic_frame_is_rejected(self):
        enemy = SimpleNamespace(roster_id=9, pathing=_pathing(sample_frame=100))
        self.api.publish_runtime({
            "frame_consistent": True, "fixed_frame": 101,
            "enemies": [enemy], "characters": [],
        })
        public = self.api._snapshots[
            "enemy_pathing"]["items"][0]["pathing"]
        self.assertFalse(public["available"])
        self.assertEqual(public["reason"], "sample_frame_mismatch")
        self.assertIsNone(public["route"])
        self.assertEqual(
            self.api._snapshots["enemies"]["items"][0]["pathing"], public)

    def test_missing_enclosing_frame_is_rejected(self):
        enemy = SimpleNamespace(roster_id=11, pathing=_pathing(sample_frame=100))
        self.api.publish_runtime({
            "frame_consistent": True, "fixed_frame": None,
            "enemies": [enemy], "characters": [],
        })
        public = self.api._snapshots[
            "enemy_pathing"]["items"][0]["pathing"]
        self.assertFalse(public["available"])
        self.assertEqual(public["reason"], "sample_frame_missing")

    def test_desktop_invalidates_before_api_and_battle_cache_consumers(self):
        enemy = SimpleNamespace(pathing=_pathing(sample_frame=100))
        snapshot = {"ok": True, "fixed_frame": 101, "enemies": [enemy]}
        fail_closed_mismatched_enemy_pathing(snapshot)
        self.assertFalse(enemy.pathing["available"])
        self.assertIsNone(enemy.pathing["route"])
        self.assertEqual(enemy.pathing["reason"], "sample_frame_mismatch")

    def test_ui_invalidation_also_clears_backing_enemy_objects(self):
        enemy = SimpleNamespace(
            lifecycle="active", pathing=_pathing(sample_frame=100))
        holder = SimpleNamespace(_enemy_last=[enemy])
        CoachWindow._invalidate_enemy_pathing_cells(holder)
        self.assertFalse(enemy.pathing["available"])
        self.assertIsNone(enemy.pathing["route"])
        self.assertEqual(enemy.pathing["reason"], "frame_inconsistent")

    def test_path_invalidation_bypasses_normal_topic_rate_limit(self):
        client = _Client(websocket=None, kind="game")
        client.subscriptions = {
            "enemies": {"rateHz": 10.0},
            "enemy_pathing": {"rateHz": 30.0},
        }
        self.api._clients.add(client)
        enemy = SimpleNamespace(
            roster_id=10, lifecycle="active",
            pathing=_pathing(sample_frame=200),
        )

        self.api.publish_runtime({
            "frame_consistent": True, "fixed_frame": 200,
            "enemies": [enemy], "characters": [],
        })
        client.last_sent = {"enemies": 1.0, "enemy_pathing": 1.0}
        self.api.publish_runtime({
            "frame_consistent": False, "fixed_frame": 201,
            "enemies": [enemy], "characters": [],
        })

        self.assertNotIn("enemies", client.last_sent)
        self.assertNotIn("enemy_pathing", client.last_sent)


if __name__ == "__main__":
    unittest.main()
