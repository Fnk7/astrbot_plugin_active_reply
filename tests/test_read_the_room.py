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
