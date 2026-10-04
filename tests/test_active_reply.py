"""active_reply 插件单元测试：规则匹配、触发判定与 handler 行为。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import main
from astrbot.api.platform import MessageType

# ============================================================
# matched_possibilities：规则命中收集
# ============================================================


def test_no_match_returns_empty():
    cfg = {"keyword_rules": [], "sender_rules": []}
    assert main.matched_possibilities(cfg, "hello", "123", "Alice") == []


def test_keyword_hit_chinese():
    cfg = {"keyword_rules": [{"keyword": "天气", "possibility": 0.5}]}
    assert main.matched_possibilities(cfg, "今天天气不错", "", "") == [0.5]
    assert main.matched_possibilities(cfg, "今天吃什么", "", "") == []


def test_keyword_hit_case_insensitive():
    cfg = {"keyword_rules": [{"keyword": "Hello", "possibility": 0.3}]}
    assert main.matched_possibilities(cfg, "say HELLO to me", "", "") == [0.3]
    assert main.matched_possibilities(cfg, "say hi", "", "") == []


def test_multiple_keyword_rules_all_collected():
    cfg = {
        "keyword_rules": [
            {"keyword": "a", "possibility": 0.2},
            {"keyword": "b", "possibility": 0.8},
        ]
    }
    assert main.matched_possibilities(cfg, "a and b", "", "") == [0.2, 0.8]


def test_blank_keyword_ignored():
    cfg = {"keyword_rules": [{"keyword": "  ", "possibility": 0.9}]}
    assert main.matched_possibilities(cfg, "anything", "", "") == []


def test_sender_id_match():
    cfg = {"sender_rules": [{"sender_id": "123", "nickname": "", "possibility": 0.4}]}
    assert main.matched_possibilities(cfg, "", "123", "Alice") == [0.4]
    assert main.matched_possibilities(cfg, "", "456", "Alice") == []


def test_sender_id_numeric_config_still_matches():
    # 用户可能在配置里填数字，比较时统一转字符串
    cfg = {"sender_rules": [{"sender_id": 123, "nickname": "", "possibility": 0.4}]}
    assert main.matched_possibilities(cfg, "", "123", "Alice") == [0.4]


def test_sender_nickname_match():
    cfg = {"sender_rules": [{"sender_id": "", "nickname": "Alice", "possibility": 0.6}]}
    assert main.matched_possibilities(cfg, "", "123", "Alice") == [0.6]
    assert main.matched_possibilities(cfg, "", "123", "Bob") == []


def test_sender_both_fields_must_match():
    cfg = {
        "sender_rules": [
            {"sender_id": "123", "nickname": "Alice", "possibility": 0.7}
        ]
    }
    assert main.matched_possibilities(cfg, "", "123", "Alice") == [0.7]
    assert main.matched_possibilities(cfg, "", "123", "Bob") == []
    assert main.matched_possibilities(cfg, "", "456", "Alice") == []


def test_blank_sender_rule_ignored():
    cfg = {"sender_rules": [{"sender_id": "", "nickname": "", "possibility": 0.9}]}
    assert main.matched_possibilities(cfg, "", "123", "Alice") == []


def test_possibility_clamped():
    cfg = {"keyword_rules": [{"keyword": "x", "possibility": 2.0}]}
    assert main.matched_possibilities(cfg, "x", "", "") == [1.0]
    cfg = {"keyword_rules": [{"keyword": "x", "possibility": -0.5}]}
    assert main.matched_possibilities(cfg, "x", "", "") == [0.0]
    cfg = {"keyword_rules": [{"keyword": "x", "possibility": "not-a-number"}]}
    assert main.matched_possibilities(cfg, "x", "", "") == [0.0]


def test_malformed_rule_entries_ignored():
    cfg = {
        "keyword_rules": [None, "str", {"possibility": 0.5}],
        "sender_rules": [None, ["list"]],
    }
    assert main.matched_possibilities(cfg, "x", "1", "n") == []


# ============================================================
# should_trigger：整体判定
# ============================================================


def test_disabled():
    cfg = {"enable": False, "keyword_rules": [{"keyword": "x", "possibility": 1.0}]}
    assert not main.should_trigger(cfg, message_str="x", rng=lambda: 0.0)


def test_non_group_message_skipped():
    cfg = {"enable": True}
    assert not main.should_trigger(cfg, message_type=MessageType.FRIEND_MESSAGE)


def test_at_or_wake_skipped():
    cfg = {"enable": True, "keyword_rules": [{"keyword": "x", "possibility": 1.0}]}
    assert not main.should_trigger(
        cfg, is_at_or_wake_command=True, message_str="x", rng=lambda: 0.0
    )


def test_command_match_skipped():
    cfg = {"enable": True, "keyword_rules": [{"keyword": "x", "possibility": 1.0}]}
    assert not main.should_trigger(
        cfg, has_command_match=True, message_str="x", rng=lambda: 0.0
    )


def test_matched_rules_take_max_possibility():
    cfg = {
        "enable": True,
        "keyword_rules": [
            {"keyword": "a", "possibility": 0.1},
            {"keyword": "b", "possibility": 0.9},
        ],
        "sender_rules": [],
    }
    assert main.should_trigger(cfg, message_str="a b", rng=lambda: 0.5)
    assert not main.should_trigger(cfg, message_str="a b", rng=lambda: 0.95)


def test_rule_hit_below_rng_fails():
    cfg = {"enable": True, "keyword_rules": [{"keyword": "a", "possibility": 0.4}]}
    assert not main.should_trigger(cfg, message_str="a", rng=lambda: 0.5)


def test_default_possibility_fallback_when_no_rule_hit():
    cfg = {"enable": True, "default_possibility": 0.6}
    assert main.should_trigger(cfg, rng=lambda: 0.5)
    assert not main.should_trigger(cfg, rng=lambda: 0.7)


def test_default_possibility_zero_never():
    cfg = {"enable": True}
    assert not main.should_trigger(cfg, rng=lambda: 0.0)


def test_possibility_one_always():
    cfg = {"enable": True, "keyword_rules": [{"keyword": "a", "possibility": 1.0}]}
    assert main.should_trigger(cfg, message_str="a", rng=lambda: 0.999)


def test_empty_config_safe():
    assert not main.should_trigger({})


# ============================================================
# handler 行为
# ============================================================


def _make_event(**overrides):
    event = MagicMock()
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.is_at_or_wake_command = False
    event.get_extra.return_value = {}
    event.message_str = "hello"
    event.session_id = "session-1"
    event.unified_msg_origin = "test_platform:group:123"
    event.get_sender_id.return_value = "u1"
    event.get_sender_name.return_value = "Alice"
    event.get_messages.return_value = []
    event.request_llm.return_value = "provider-request"
    for key, value in overrides.items():
        setattr(event, key, value)
    return event


def _make_plugin(cfg, context):
    # 跳过 Star.__init__，仅驱动 handler 逻辑
    plugin = main.ActiveReply.__new__(main.ActiveReply)
    plugin.config = cfg
    plugin.context = context
    return plugin


def _collect(agen):
    """同步驱动 async generator 并收集 yield 结果。"""
    results = []

    async def _run():
        async for item in agen:
            results.append(item)

    asyncio.run(_run())
    return results


def test_handler_yields_nothing_when_disabled():
    context = MagicMock()
    event = _make_event()
    plugin = _make_plugin({"enable": False}, context)
    assert _collect(plugin.on_message(event)) == []
    context.get_using_provider_async.assert_not_called()


def test_handler_yields_nothing_on_private_message():
    context = MagicMock()
    event = _make_event()
    event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
    plugin = _make_plugin(
        {"enable": True, "keyword_rules": [{"keyword": "hello", "possibility": 1.0}]},
        context,
    )
    assert _collect(plugin.on_message(event)) == []
    context.get_using_provider_async.assert_not_called()


def test_handler_skips_on_command_match():
    context = MagicMock()
    event = _make_event()
    # handlers_parsed_params 非空 ⟺ 有指令 handler 命中
    event.get_extra.return_value = {"some.command": {}}
    plugin = _make_plugin(
        {"enable": True, "keyword_rules": [{"keyword": "hello", "possibility": 1.0}]},
        context,
    )
    assert _collect(plugin.on_message(event)) == []
    context.get_using_provider_async.assert_not_called()


def test_handler_requests_llm_when_triggered():
    context = MagicMock()
    context.get_using_provider_async = AsyncMock(return_value=object())
    context.conversation_manager = MagicMock()
    context.conversation_manager.get_curr_conversation_id = AsyncMock(
        return_value="cid-1"
    )
    conv = object()
    context.conversation_manager.get_conversation = AsyncMock(return_value=conv)

    event = _make_event()
    plugin = _make_plugin(
        {"enable": True, "keyword_rules": [{"keyword": "hello", "possibility": 1.0}]},
        context,
    )
    results = _collect(plugin.on_message(event))
    assert results == ["provider-request"]
    event.request_llm.assert_called_once()
    kwargs = event.request_llm.call_args.kwargs
    assert kwargs["prompt"] == "hello"
    assert kwargs["session_id"] == "session-1"
    assert kwargs["conversation"] is conv
    assert kwargs["image_urls"] == []
    # 读空气：标记本轮为主动回复轮，并注入拦截工具
    event.set_extra.assert_any_call("_active_reply_turn", True)
    tool_set = kwargs["tool_set"]
    assert tool_set is not None
    assert tool_set.get_tool(main.STAY_SILENT_TOOL_NAME) is not None


def test_handler_no_provider_logs_and_skips():
    context = MagicMock()
    context.get_using_provider_async = AsyncMock(return_value=None)
    plugin = _make_plugin(
        {"enable": True, "keyword_rules": [{"keyword": "hello", "possibility": 1.0}]},
        context,
    )
    event = _make_event()
    assert _collect(plugin.on_message(event)) == []
    event.request_llm.assert_not_called()


def test_handler_no_conversation_logs_and_skips():
    context = MagicMock()
    context.get_using_provider_async = AsyncMock(return_value=object())
    context.conversation_manager = MagicMock()
    context.conversation_manager.get_curr_conversation_id = AsyncMock(
        return_value=None
    )
    plugin = _make_plugin(
        {"enable": True, "keyword_rules": [{"keyword": "hello", "possibility": 1.0}]},
        context,
    )
    event = _make_event()
    assert _collect(plugin.on_message(event)) == []
    event.request_llm.assert_not_called()


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
