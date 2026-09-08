# -*- coding: utf-8 -*-
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from backend.desktop_app import (
    BEGINNER_GUIDE_FALLBACK, CoachWindow, load_beginner_guide,
)


def test_beginner_guide_covers_first_run_workflow():
    guide = load_beginner_guide()
    assert guide.startswith('# 明日方舟游戏数据显示工具：新手使用教程')
    for expected in ('选择 ADB', '启用自动寻址', '开始扫描', '扫描随机数',
                     '常见问题'):
        assert expected in guide


def test_beginner_guide_has_readable_packaging_fallback(tmp_path: Path):
    missing = tmp_path / 'docs' / 'missing.md'
    assert load_beginner_guide(missing) == BEGINNER_GUIDE_FALLBACK.strip()


def test_beginner_guide_button_is_immediately_left_of_adb_settings():
    app = QApplication.instance() or QApplication([])
    start_methods = (
        '_start_hook_server', '_start_websocket_api', '_start_workers',
        '_start_timers',
    )
    patches = [patch.object(CoachWindow, name, lambda self: None)
               for name in start_methods]
    for item in patches:
        item.start()
    window = None
    try:
        window = CoachWindow()
        assert window.btn_beginner_guide.text() == '新手教程'
        guide_index = window._title_actions_row.indexOf(
            window.btn_beginner_guide)
        adb_index = window._title_actions_row.indexOf(window.btn_select_adb)
        assert guide_index + 1 == adb_index
    finally:
        if window is not None:
            window._mini_hotkey_timer.stop()
            window._theme_timer.stop()
            window.deleteLater()
        for item in reversed(patches):
            item.stop()
        app.processEvents()
