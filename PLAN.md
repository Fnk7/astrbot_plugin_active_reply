# active_reply 读空气修订 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 SPEC.md 修订插件：移除 RoomContextTracker 与一切统计/计数，读空气改为"静态提醒注入 + 单次 LLM 调用内工具拦截"。

**Architecture:** 概率判定逻辑（规则匹配、掷骰子、handler 流程）保持不变。读空气由三部分组成：模块级常量 `READ_THE_ROOM_REMINDER`（静态文本）经 `on_llm_request` 钩子仅在主动回复轮注入 `req.extra_user_content_parts`；本轮专属工具 `active_reply_stay_silent` 经 `request_llm(tool_set=…)` 注入并拦截；`on_llm_response` 兜底清空被拦截回复。判断素材 = 回复请求天然携带的会话历史（LLM 直接可见），插件不做任何统计。

**Tech Stack:** Python 3 + AstrBot 插件 API（`Star`/`@filter`/`FunctionTool`/`ToolSet`/`AstrBotConfig`），pytest + unittest.mock，运行方式 `TZ=Asia/Shanghai uv run pytest`。

**Spec:** `data/plugins/astrbot_plugin_active_reply/SPEC.md`

## Global Constraints

- 仅修改 `data/plugins/astrbot_plugin_active_reply/` 下文件，不修改任何 AstrBot 源码。
- 仅群聊消息触发；`@机器人`/唤醒词/指令命中（`handlers_parsed_params` 非空）不触发。
- 概率钳制 [0,1]；多条规则命中取最高概率，掷一次骰子。
- handler（`on_message`/`on_llm_request`/`on_llm_response`/拦截工具）全程 try/except，异常只记日志，不得向管道抛出（handler 抛异常会使管道 `stop_event`）。
- 无跨事件状态：只允许事件级标记 `_active_reply_turn`、`_active_reply_intercepted`；读空气不得有任何统计、计数或插件端消息记录。
- 拦截工具只注入主动回复轮（经 `request_llm(tool_set=…)`），不全局注册。
- 配置项最终仅 5 个：`enable`、`default_possibility`、`keyword_rules`、`sender_rules`、`read_the_room_enable`。
- 测试命令（沙箱 TZ 必须为 Asia/Shanghai，否则 `astrbot.core` 导入失败）：
  `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests`
- git：`data/` 目录被仓库 `.gitignore`（第 23 行）整体忽略，插件文件不提交 git。本计划没有 commit 步骤；每个任务的完成标准是测试输出（RED 先失败、GREEN 全绿）。

## Review Focus

1. `req.extra_user_content_parts` 为 None 时，钩子必须初始化后再追加，否则静默丢提醒 → Task 1 `test_on_llm_request_initializes_none_parts`。
2. 用户已保存的旧配置可能残留 `read_the_room_window_minutes`/`read_the_room_history_cnt` 键：代码不得读取它们，注入内容必须仍是纯静态提醒 → Task 1 `test_on_llm_request_ignores_stale_stat_config`。
3. 拦截发生后必须复位 `_active_reply_turn`/`_active_reply_intercepted`，否则污染后续消息轮 → Task 1 `test_on_llm_response_resets_flags`。
4. LLM 调用拦截工具时可能不填 `reason`（FunctionTool 以无参调用 handler）：不得崩溃，仍返回拦截文案 → Task 1 `test_stay_silent_tool_without_reason`。
5. 规则条目缺少 `possibility` 键（用户只填了 keyword）：按 0 处理，永不触发 → Task 2 `test_rule_without_possibility_treated_as_zero`。

---

### Task 1: 读空气静态提醒（替换统计块）

**Files:**
- Modify: `data/plugins/astrbot_plugin_active_reply/tests/test_read_the_room.py`（整体重写）
- Modify: `data/plugins/astrbot_plugin_active_reply/main.py:14-18`（imports）、`:186-217`（删除 `build_read_the_room_block`）、`:263-285`（重写 `on_llm_request`）

**Interfaces:**
- Consumes: 既有符号 `STAY_SILENT_TOOL_NAME`（main.py:31）、`build_stay_silent_tool`、`ActiveReply._stay_silent_tool`、`TextPart`、`MessageChain`。
- Produces: `main.READ_THE_ROOM_REMINDER: str`（模块常量，静态提醒全文）；`main.build_read_the_room_block` 被删除；`ActiveReply.on_llm_request(event, req)` 只注入该常量，不读 tracker、不读任何统计配置。

- [ ] **Step 1: 重写测试文件（RED）**

用以下完整内容覆盖 `data/plugins/astrbot_plugin_active_reply/tests/test_read_the_room.py`：

```python
"""读空气拦截功能的单元测试：静态提醒注入、拦截工具、两个钩子。"""

import asyncio
from unittest.mock import MagicMock

import main


# ============================================================
# READ_THE_ROOM_REMINDER：静态提醒常量
# ============================================================


def test_reminder_is_static_and_mentions_tool():
    reminder = main.READ_THE_ROOM_REMINDER
    assert isinstance(reminder, str)
    assert main.STAY_SILENT_TOOL_NAME in reminder
    assert "会话上下文" in reminder


def test_stat_block_builder_removed():
    # SPEC.md 第 4.2 节：注入内容为静态提醒，统计块构建函数必须删除
    assert not hasattr(main, "build_read_the_room_block")


# ============================================================
# build_stay_silent_tool
# ============================================================


def _make_plugin():
    plugin = main.ActiveReply.__new__(main.ActiveReply)
    return plugin


def test_tool_schema_fields():
    plugin = _make_plugin()
    tool = main.build_stay_silent_tool(plugin._stay_silent_tool)
    assert tool.name == main.STAY_SILENT_TOOL_NAME
    assert "reason" in tool.parameters["properties"]
    assert tool.handler == plugin._stay_silent_tool


def test_stay_silent_tool_marks_and_stops_event():
    plugin = _make_plugin()
    event = MagicMock()

    async def _run():
        return await plugin._stay_silent_tool(event, reason="测试原因")

    result = asyncio.run(_run())
    event.set_extra.assert_called_with("_active_reply_intercepted", True)
    event.stop_event.assert_called_once()
    assert "已拦截" in result


def test_stay_silent_tool_without_reason():
    # LLM 可能不填 reason 参数（FunctionTool 以无参调用 handler）
    plugin = _make_plugin()
    event = MagicMock()

    async def _run():
        return await plugin._stay_silent_tool(event)

    result = asyncio.run(_run())
    event.set_extra.assert_called_with("_active_reply_intercepted", True)
    event.stop_event.assert_called_once()
    assert "已拦截" in result


# ============================================================
# on_llm_request 钩子：注入静态提醒
# ============================================================


def test_on_llm_request_injects_static_reminder_when_flagged():
    plugin = _make_plugin()
    plugin.config = {"read_the_room_enable": True}

    event = MagicMock()
    event.get_extra.return_value = True  # _active_reply_turn
    req = MagicMock()
    req.extra_user_content_parts = []

    asyncio.run(plugin.on_llm_request(event, req))
    assert len(req.extra_user_content_parts) == 1
    assert req.extra_user_content_parts[0].text == main.READ_THE_ROOM_REMINDER


def test_on_llm_request_ignores_stale_stat_config():
    # 用户已保存的旧配置可能残留已废弃的统计键：注入内容必须仍是纯静态提醒
    plugin = _make_plugin()
    plugin.config = {
        "read_the_room_enable": True,
        "read_the_room_window_minutes": 10,
        "read_the_room_history_cnt": 20,
    }

    event = MagicMock()
    event.get_extra.return_value = True
    req = MagicMock()
    req.extra_user_content_parts = []

    asyncio.run(plugin.on_llm_request(event, req))
    assert len(req.extra_user_content_parts) == 1
    assert req.extra_user_content_parts[0].text == main.READ_THE_ROOM_REMINDER


def test_on_llm_request_skips_when_not_flagged():
    plugin = _make_plugin()
    plugin.config = {"read_the_room_enable": True}

    event = MagicMock()
    event.get_extra.return_value = False
    req = MagicMock()
    req.extra_user_content_parts = []

    asyncio.run(plugin.on_llm_request(event, req))
    assert req.extra_user_content_parts == []


def test_on_llm_request_skips_when_disabled():
    plugin = _make_plugin()
    plugin.config = {"read_the_room_enable": False}

    event = MagicMock()
    event.get_extra.return_value = True
    req = MagicMock()
    req.extra_user_content_parts = []

    asyncio.run(plugin.on_llm_request(event, req))
    assert req.extra_user_content_parts == []


def test_on_llm_request_initializes_none_parts():
    plugin = _make_plugin()
    plugin.config = {"read_the_room_enable": True}

    event = MagicMock()
    event.get_extra.return_value = True
    req = MagicMock()
    req.extra_user_content_parts = None

    asyncio.run(plugin.on_llm_request(event, req))
    assert isinstance(req.extra_user_content_parts, list)
    assert len(req.extra_user_content_parts) == 1


# ============================================================
# on_llm_response 钩子：拦截后清空回复
# ============================================================


def _fake_response():
    resp = MagicMock()
    resp.result_chain = main.MessageChain()
    resp.result_chain.chain = [object()]
    resp.completion_text = "本不该说的话"
    return resp


def _flagged_event(turn=True, intercepted=True):
    event = MagicMock()
    event.get_extra.side_effect = (
        lambda key, default=False: {
            "_active_reply_turn": turn,
            "_active_reply_intercepted": intercepted,
        }.get(key, default)
    )
    return event


def test_on_llm_response_clears_when_intercepted():
    plugin = _make_plugin()
    event = _flagged_event()
    resp = _fake_response()

    asyncio.run(plugin.on_llm_response(event, resp))
    assert resp.completion_text == ""
    assert resp.result_chain.chain == []


def test_on_llm_response_resets_flags():
    # 拦截后必须复位事件标记，避免污染后续消息轮
    plugin = _make_plugin()
    event = _flagged_event()
    resp = _fake_response()

    asyncio.run(plugin.on_llm_response(event, resp))
    event.set_extra.assert_any_call("_active_reply_turn", False)
    event.set_extra.assert_any_call("_active_reply_intercepted", False)


def test_on_llm_response_untouched_when_not_intercepted():
    plugin = _make_plugin()
    event = _flagged_event(turn=True, intercepted=False)
    resp = _fake_response()

    asyncio.run(plugin.on_llm_response(event, resp))
    assert resp.completion_text == "本不该说的话"
    assert resp.result_chain.chain != []
```

- [ ] **Step 2: 运行测试验证 RED**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests/test_read_the_room.py -v`
Expected: **5 failed**，其余通过（本任务后全量为 44 passed, 5 failed）：
- `test_reminder_is_static_and_mentions_tool` — AttributeError: module 'main' has no attribute 'READ_THE_ROOM_REMINDER'
- `test_stat_block_builder_removed` — AssertionError（`build_read_the_room_block` 仍存在）
- `test_on_llm_request_injects_static_reminder_when_flagged` — AssertionError（现钩子读 `self.tracker` 抛 AttributeError 被 try/except 吞掉，无任何注入）
- `test_on_llm_request_ignores_stale_stat_config` — AssertionError（同上，无注入）
- `test_on_llm_request_initializes_none_parts` — AssertionError（同上，parts 仍为 None）

其余（工具、跳过、兜底）为既有正确行为，应通过。

- [ ] **Step 3: 最小实现（GREEN）**

三处修改 `main.py`：

(a) 删除 import（第 18 行，统计块构建函数使用的）：

```python
from datetime import datetime
```

(b) 在 `STAY_SILENT_TOOL_NAME` 常量块（第 31-32 行）之后新增常量：

```python
READ_THE_ROOM_REMINDER = (
    "<system_reminder>\n"
    "你正在群聊中主动回复。请先根据会话上下文判断现在是否适合插话：\n"
    "- 如果你最近已经说过话、当前话题不需要你插话，或群聊氛围不适合发言，"
    f"请调用 {STAY_SILENT_TOOL_NAME} 工具拒绝本次回复；\n"
    "- 调用该工具后不要再输出任何内容。\n"
    "</system_reminder>"
)
"""主动回复轮注入给 LLM 的读空气静态提醒（无统计、不复制会话内容）。"""
```

(c) 删除 `build_read_the_room_block` 整个函数（第 186-217 行）。

(d) 更新模块 docstring（第 9-11 行）中读空气的旧描述（"注入的最近群聊上下文"）为：

```python
- 读空气（read-the-room）：回复由概率规则决定是否触发，触发后在同一次 LLM
  调用中，LLM 依据该回复天然携带的会话上下文判断"现在是否该插话"，并通过调用
  拦截工具拒绝本次回复（不产生额外 LLM 调用）。
```

(e) 用以下实现替换 `on_llm_request` 方法体（第 263-285 行）：

```python
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        """主动回复轮注入静态读空气提醒。"""
        try:
            if not self.config.get("read_the_room_enable", True):
                return
            if not event.get_extra("_active_reply_turn", False):
                return
            if req.extra_user_content_parts is None:
                req.extra_user_content_parts = []
            req.extra_user_content_parts.append(TextPart(text=READ_THE_ROOM_REMINDER))
        except Exception:
            logger.error(traceback.format_exc())
            logger.error("active_reply 注入读空气提醒失败")
```

注意：本任务**保留** `RoomContextTracker`、`MAX_TRACKED_MESSAGES`、`_trim_to_cap` 与 `on_message` 中的记录调用（Task 2 移除），`import time` 仍被 `on_message` 使用（Task 2 移除）。

- [ ] **Step 4: 运行测试验证 GREEN**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests/test_read_the_room.py -v`
Expected: 13 passed。

- [ ] **Step 5: 全量测试验证（无 git 提交，data/ 被 .gitignore 忽略）**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests`
Expected: 49 passed（`test_room_tracker.py` 的 7 个测试此时仍通过——tracker 尚未删除）。

---

### Task 2: 删除 RoomContextTracker 与群聊消息记录

**Files:**
- Modify: `data/plugins/astrbot_plugin_active_reply/main.py:15,17`（imports）、`:34-35`、`:140-183`、`:245-253`、`:306-320`、`:371-375`
- Modify: `data/plugins/astrbot_plugin_active_reply/tests/test_active_reply.py`（`_make_plugin` + 2 个新测试）
- Delete: `data/plugins/astrbot_plugin_active_reply/tests/test_room_tracker.py`

**Interfaces:**
- Consumes: Task 1 的 `READ_THE_ROOM_REMINDER`（不变）。
- Produces: `main` 不再暴露 `RoomContextTracker`/`_trim_to_cap`/`MAX_TRACKED_MESSAGES`；`ActiveReply.__init__(context, config)` 不再创建 tracker；`on_message` 不记录任何消息/回复时间戳。

- [ ] **Step 1: 添加失败测试（RED）**

在 `data/plugins/astrbot_plugin_active_reply/tests/test_active_reply.py` 末尾追加：

```python
# ============================================================
# 统计组件移除
# ============================================================


def test_statistics_components_removed():
    # SPEC.md 第 6 节：RoomContextTracker 等统计组件必须删除
    for name in ("RoomContextTracker", "_trim_to_cap", "MAX_TRACKED_MESSAGES"):
        assert not hasattr(main, name), f"统计组件应已移除：{name}"


def test_rule_without_possibility_treated_as_zero():
    # 规则缺少 possibility 键时按 0 处理，永不触发
    cfg = {"keyword_rules": [{"keyword": "x"}]}
    assert main.matched_possibilities(cfg, "x", "", "") == [0.0]
```

- [ ] **Step 2: 运行测试验证 RED**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests/test_active_reply.py -v`
Expected: 1 failed（`test_statistics_components_removed`，AssertionError: 统计组件应已移除：RoomContextTracker），其余通过（`test_rule_without_possibility_treated_as_zero` 是防回归固定，当前行为已正确，应通过）。

- [ ] **Step 3: 最小实现（GREEN）**

(a) `main.py` 删除两行 import（第 15、17 行）：

```python
import time
from collections import defaultdict, deque
```

(b) 删除常量 `MAX_TRACKED_MESSAGES`（第 34-35 行，含注释）。

(c) 删除 `RoomContextTracker` 类（第 140-178 行）与 `_trim_to_cap` 函数（第 181-183 行）。

(d) `__init__`（第 245-248 行）改为：

```python
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
```

(e) `on_message` 删除记录块（第 310-317 行的 `if event.get_message_type() == ...` 记录块及其注释），该 try 块开头保留为：

```python
        try:
            has_command_match = bool(event.get_extra("handlers_parsed_params", {}))
```

(f) `on_message` 删除 `self.tracker.record_bot_reply(event.unified_msg_origin, time.time())` 一行（第 374 行），并把上方注释（第 371-372 行）更新为：

```python
            # 标记本轮为主动回复轮：on_llm_request 据此注入读空气提醒，
            # on_llm_response 据此执行兜底拦截。
```

(g) 删除文件 `data/plugins/astrbot_plugin_active_reply/tests/test_room_tracker.py`。

(h) `tests/test_active_reply.py` 的 `_make_plugin` 删除 tracker 行，改为：

```python
def _make_plugin(cfg, context):
    # 跳过 Star.__init__，仅驱动 handler 逻辑
    plugin = main.ActiveReply.__new__(main.ActiveReply)
    plugin.config = cfg
    plugin.context = context
    return plugin
```

- [ ] **Step 4: 运行测试验证 GREEN**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests -v`
Expected: 44 passed（删除了 `test_room_tracker.py` 的 7 个测试；`test_handler_requests_llm_when_triggered` 仍断言 `_active_reply_turn` 标记与 tool_set 注入，保持不变并应通过）。

- [ ] **Step 5: 全量测试验证（无 git 提交）**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests`
Expected: 44 passed。

---

### Task 3: schema 移除统计配置项

**Files:**
- Modify: `data/plugins/astrbot_plugin_active_reply/tests/test_config_schema.py`（重写）
- Modify: `data/plugins/astrbot_plugin_active_reply/_conf_schema.json:24-35`

**Interfaces:**
- Consumes: 无（不依赖 Task 1/2 的产物；main.py 在 Task 1 后已不读取这两个键）。
- Produces: schema 仅定义 5 个配置项；`read_the_room_enable` 默认 true 不变。

- [ ] **Step 1: 重写测试文件（RED）**

用以下完整内容覆盖 `data/plugins/astrbot_plugin_active_reply/tests/test_config_schema.py`：

```python
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
```

- [ ] **Step 2: 运行测试验证 RED**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests/test_config_schema.py -v`
Expected: 1 failed（`test_stat_config_keys_removed`，AssertionError），1 passed（`test_read_the_room_enable_default`）。

- [ ] **Step 3: 最小实现（GREEN）**

删除 `_conf_schema.json` 中的两个对象（第 24-35 行）：

```json
  "read_the_room_window_minutes": {
    "description": "读空气统计窗口（分钟）",
    "type": "int",
    "default": 10,
    "hint": "统计最近 N 分钟内机器人已主动回复的次数，作为 LLM 判断依据。"
  },
  "read_the_room_history_cnt": {
    "description": "读空气上下文条数",
    "type": "int",
    "default": 20,
    "hint": "注入给 LLM 判断的最近群聊消息条数。"
  },
```

同时更新 `read_the_room_enable` 的 hint（去掉"最近群聊上下文"表述，改为会话上下文）：

```json
    "hint": "开启后，主动回复触发时 LLM 会依据该回复天然携带的会话上下文判断当前是否适合插话；若认为不该插话，可调用拦截工具拒绝本次回复（同一轮 LLM 调用内完成，不产生额外调用）。"
```

- [ ] **Step 4: 运行测试验证 GREEN**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests/test_config_schema.py -v`
Expected: 2 passed。

- [ ] **Step 5: 全量测试验证（无 git 提交）**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests`
Expected: 45 passed。

---

### Task 4: README 更新

**Files:**
- Modify: `data/plugins/astrbot_plugin_active_reply/README.md`

**Interfaces:** 无代码接口。文档内容必须与 SPEC.md 第 4、5 节一致。

- [ ] **Step 1: 重写读空气段落**

将 README.md 第 14-17 行的读空气 bullet 替换为：

```markdown
- **读空气（read-the-room）**：概率规则决定是否触发；触发后在同一次 LLM 回复调用中，
  插件注入一条静态提醒，LLM 依据该回复天然携带的会话上下文判断当前是否适合插话，
  不适合时调用拦截工具 `active_reply_stay_silent` 拒绝本次回复（不产生额外 LLM 调用，
  且该工具只注入主动回复这一轮，不影响正常对话）。
```

- [ ] **Step 2: 删除配置表中的两个统计行**

删除 README.md 配置表中的这两行（第 30-31 行）：

```markdown
| `read_the_room_window_minutes` | 读空气统计窗口（分钟，默认 10） |
| `read_the_room_history_cnt` | 注入给 LLM 判断的最近群聊消息条数（默认 20） |
```

- [ ] **Step 3: 全文检查**

通读 README.md，确认不再出现 `read_the_room_window_minutes`、`read_the_room_history_cnt`、"统计"、"最近 N 分钟" 等已废弃表述。

- [ ] **Step 4: 全量测试验证（无 git 提交）**

Run: `TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests`
Expected: 45 passed（纯文档改动，测试结果应与 Task 3 一致）。

---

## 实现完成后的运维步骤（非任务，由会话执行者处理）

1. 停止正在运行的旧版插件实例：`TaskStop` 后台任务 `bh9p5equn`（当前运行的是修订前的 v2 代码）。
2. 重新启动 AstrBot：`cd data && TZ=Asia/Shanghai uv run python main.py`（后台运行）。
3. 从启动日志确认插件 `astrbot_plugin_active_reply` 加载成功、无 schema/导入错误。
