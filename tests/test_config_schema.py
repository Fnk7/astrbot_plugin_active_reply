"""配置 schema 加载测试：读空气配置项的默认值。"""

import json
import os
import tempfile
from pathlib import Path

from astrbot.core.config.astrbot_config import AstrBotConfig

SCHEMA_PATH = Path(__file__).parent.parent / "_conf_schema.json"


def _load_config(tmp_path):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    config_path = os.path.join(tmp_path, "astrbot_plugin_active_reply_config.json")
    return AstrBotConfig(config_path=config_path, schema=schema)


def test_read_the_room_enable_default(tmp_path):
    cfg = _load_config(tmp_path)
    assert cfg["read_the_room_enable"] is True


def test_stat_config_keys_removed(tmp_path):
    # SPEC.md 第 5 节：统计配置项已废弃移除，schema 不得再定义
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "read_the_room_window_minutes" not in schema
    assert "read_the_room_history_cnt" not in schema
