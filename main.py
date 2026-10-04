"""active_reply 插件：按关键词 / 发送者规则的概率主动回复群聊消息。

判定与回复流程参照 AstrBot 内置群聊上下文插件的 active_reply 功能
（astrbot/builtin_stars/astrbot/group_chat_context.py），在此基础上支持：
- 关键词规则：消息包含关键词（不区分大小写）时按该规则的概率回复；
- 发送者规则：消息发送者的 ID / 昵称匹配时按该规则的概率回复；
- 兜底概率：未命中任何规则时使用；
- 多条规则同时命中时取最高概率，掷一次骰子；
- 读空气（read-the-room）：回复由概率规则决定是否触发，触发后在同一次 LLM
  调用中，LLM 依据该回复天然携带的会话上下文判断"现在是否该插话"，并通过调用
  拦截工具拒绝本次回复（不产生额外 LLM 调用）。
"""

import random
import traceback
from typing import Any

from astrbot.api import AstrBotConfig, FunctionTool, ToolSet, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.message.message_event_result import MessageChain

DEFAULT_POSSIBILITY = 0.0

STAY_SILENT_TOOL_NAME = "active_reply_stay_silent"
"""读空气拦截工具名。"""

READ_THE_ROOM_REMINDER = (
    "<system_reminder>\n"
    "你正在群聊中主动回复。请先根据会话上下文判断现在是否适合插话：\n"
    "- 如果你最近已经说过话、当前话题不需要你插话，或群聊氛围不适合发言，"
    f"请调用 {STAY_SILENT_TOOL_NAME} 工具拒绝本次回复；\n"
    "- 调用该工具后不要再输出任何内容。\n"
    "</system_reminder>"
)
"""主动回复轮注入给 LLM 的读空气静态提醒（无统计、不复制会话内容）。"""


def _clamp_possibility(value: Any) -> float:
    """把概率值钳制到 [0, 1]，无法解析的值按 0 处理。"""
    try:
        possibility = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(possibility, 0.0), 1.0)


def matched_possibilities(
    cfg: dict,
    message_str: str,
    sender_id: str,
    sender_name: str,
) -> list[float]:
    """返回所有命中规则的概率列表（已钳制到 [0, 1]）。

    Args:
        cfg: 插件配置（AstrBotConfig，dict 子类）。
        message_str: 消息纯文本（管道中已被 strip）。
        sender_id: 消息发送者 ID。
        sender_name: 消息发送者昵称。

    Returns:
        命中规则的概率列表；无命中时为空列表。
    """
    matched: list[float] = []

    for rule in cfg.get("keyword_rules") or []:
        if not isinstance(rule, dict):
            continue
        keyword = str(rule.get("keyword", "") or "").strip()
        if not keyword:
            continue
        if keyword.lower() in message_str.lower():
            matched.append(
                _clamp_possibility(rule.get("possibility", DEFAULT_POSSIBILITY))
            )

    for rule in cfg.get("sender_rules") or []:
        if not isinstance(rule, dict):
            continue
        rule_sender_id = str(rule.get("sender_id", "") or "").strip()
        rule_nickname = str(rule.get("nickname", "") or "").strip()
        if not rule_sender_id and not rule_nickname:
            continue
        if rule_sender_id and rule_sender_id != str(sender_id):
            continue
        if rule_nickname and rule_nickname != str(sender_name):
            continue
        matched.append(_clamp_possibility(rule.get("possibility", DEFAULT_POSSIBILITY)))

    return matched


def should_trigger(
    cfg: dict,
    *,
    message_type: MessageType = MessageType.GROUP_MESSAGE,
    is_at_or_wake_command: bool = False,
    has_command_match: bool = False,
    message_str: str = "",
    sender_id: str = "",
    sender_name: str = "",
    rng: Any = None,
) -> bool:
    """判定本次消息是否触发主动回复（纯函数，便于测试）。

    Args:
        cfg: 插件配置（AstrBotConfig，dict 子类）。
        message_type: 消息类型，仅群聊消息可能触发。
        is_at_or_wake_command: 是否为 @机器人/唤醒词消息，这类消息走默认回复链路。
        has_command_match: 是否有指令 handler 命中（handlers_parsed_params 非空），
            跳过以避免与指令回复双重回复。
        message_str: 消息纯文本。
        sender_id: 消息发送者 ID。
        sender_name: 消息发送者昵称。
        rng: 随机函数，默认 random.random，测试可注入固定值。

    Returns:
        是否触发主动回复。
    """
    if not cfg.get("enable", False):
        return False
    if message_type != MessageType.GROUP_MESSAGE:
        return False
    if is_at_or_wake_command:
        return False
    if has_command_match:
        return False

    matched = matched_possibilities(cfg, message_str, sender_id, sender_name)
    if matched:
        possibility = max(matched)
    else:
        possibility = _clamp_possibility(
            cfg.get("default_possibility", DEFAULT_POSSIBILITY)
        )
    roll = random.random if rng is None else rng
    return roll() < possibility


def build_stay_silent_tool(handler: Any) -> FunctionTool:
    """构建读空气拦截工具（仅注入主动回复那一轮，不全局注册）。"""
    return FunctionTool(
        name=STAY_SILENT_TOOL_NAME,
        description=(
            "当你不应该在此时插入群聊时调用此工具拒绝本次主动回复。"
            "例如：你最近已经说过话（过于频繁）、当前话题不需要你插话、"
            "或者群聊氛围不适合发言。调用后请不要再输出任何内容。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "拒绝本次回复的原因（可选）",
                }
            },
        },
        handler=handler,
    )


class ActiveReply(Star):
    """按关键词/发送者规则概率主动回复群聊消息的插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}

    async def _stay_silent_tool(self, event: AstrMessageEvent, reason: str = "") -> str:
        """读空气拦截工具：拒绝本次主动回复。"""
        logger.info(f"active_reply 读空气拦截本次回复，原因: {reason or '（未说明）'}")
        event.set_extra("_active_reply_intercepted", True)
        event.stop_event()
        return "已拦截本次主动回复。请立即停止输出任何内容，不要发送回复。"

    def _active_reply_tool_set(self) -> ToolSet | None:
        """读空气开启时返回注入本轮的工具集，否则返回 None。"""
        if not self.config.get("read_the_room_enable", True):
            return None
        return ToolSet(tools=[build_stay_silent_tool(self._stay_silent_tool)])

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

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response) -> None:
        """主动回复轮结束：复位标记；若被读空气拦截则清空回复内容兜底。"""
        try:
            if not event.get_extra("_active_reply_turn", False):
                return
            if event.get_extra("_active_reply_intercepted", False):
                response.completion_text = ""
                if response.result_chain is None:
                    response.result_chain = MessageChain()
                else:
                    response.result_chain.chain = []
            event.set_extra("_active_reply_turn", False)
            event.set_extra("_active_reply_intercepted", False)
        except Exception:
            logger.error(traceback.format_exc())
            logger.error("active_reply 清空被拦截回复失败")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """对群聊消息按配置概率触发 LLM 主动回复。"""
        try:
            has_command_match = bool(event.get_extra("handlers_parsed_params", {}))
            if not should_trigger(
                self.config,
                message_type=event.get_message_type(),
                is_at_or_wake_command=event.is_at_or_wake_command,
                has_command_match=has_command_match,
                message_str=event.message_str or "",
                sender_id=event.get_sender_id() or "",
                sender_name=event.get_sender_name() or "",
            ):
                return
        except Exception:
            logger.error(traceback.format_exc())
            logger.error("active_reply 判定失败")
            return

        # 概率命中，进入 LLM 主动回复流程（与内置 active_reply 一致）。
        # 全程捕获异常：handler 抛出的异常会让管道 stop_event，影响其他插件。
        try:
            provider = await self.context.get_using_provider_async(
                event.unified_msg_origin
            )
            if not provider:
                logger.error("未找到任何 LLM 提供商。请先配置。无法主动回复")
                return

            session_curr_cid = (
                await self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin,
                )
            )
            if not session_curr_cid:
                logger.error(
                    "当前未处于对话状态，无法主动回复，请确保 平台设置->会话隔离(unique_session) 未开启，并使用 /new 创建一个会话。",
                )
                return

            conv = await self.context.conversation_manager.get_conversation(
                event.unified_msg_origin,
                session_curr_cid,
            )
            if not conv:
                logger.error("未找到对话，无法主动回复")
                return

            image_urls = []
            for comp in event.get_messages():
                if isinstance(comp, Image):
                    try:
                        image_urls.append(await comp.convert_to_file_path())
                    except Exception:
                        logger.exception("主动回复处理图片失败")

            # 标记本轮为主动回复轮：on_llm_request 据此注入读空气提醒，
            # on_llm_response 据此执行兜底拦截。
            event.set_extra("_active_reply_turn", True)

            yield event.request_llm(
                prompt=event.message_str,
                session_id=event.session_id,
                image_urls=image_urls,
                conversation=conv,
                tool_set=self._active_reply_tool_set(),
            )
        except Exception:
            logger.error(traceback.format_exc())
            logger.error("主动回复失败")
