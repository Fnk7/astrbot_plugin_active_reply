# active_reply 插件设计规范

- 日期：2026-10-04
- 状态：待评审
- 范围：`data/plugins/astrbot_plugin_active_reply/`，不修改任何 AstrBot 源码

## 1. 目标

群聊机器人按概率主动回复，且"会读空气"：

1. **概率触发**：关键词规则、发送者（ID/昵称）规则、兜底概率决定"是否触发回复"。
2. **读空气拦截**：触发后、生成回复的**同一次 LLM 调用**中，LLM 依据**该回复天然携带的会话上下文**判断当前是否适合插话；不适合则调用拦截工具拒绝本次回复。拦截只作用于自己，不产生额外 LLM 调用。

## 2. 上下文来源（本次修订的核心）

- 主动回复请求 `event.request_llm(conversation=conv)` 已携带会话历史：核心在
  `build_main_agent` 中把 `conversation.history` 载入 `req.contexts`
  （`astrbot/core/astr_main_agent.py:1452-1466, 1665-1667`），agent 据此构建交给
  LLM 的对话上下文。会话历史包含机器人自己的发言（assistant 消息，含主 agent 的
  @ 回复与主动回复）。
- **读空气判断的素材就是这份会话上下文本身**：LLM 直接看得到自己最近说了什么、
  说了多少，插件不另建消息记录，也不做任何统计注入。

## 3. 触发流程（不变量）

1. 仅群聊消息；`@机器人`/唤醒词/指令命中（`handlers_parsed_params` 非空）不触发。
2. 概率判定：命中规则取最高概率，未命中用 `default_possibility`，掷一次骰子；
   概率钳制 [0,1]。
3. 触发后：取 provider → 取当前会话（`get_curr_conversation_id` / `get_conversation`，
   与内置 active_reply 相同）→ 提取图片 → 设置 `_active_reply_turn` 标记 →
   `yield event.request_llm(..., tool_set=拦截工具集)`。
4. handler 全程 try/except（handler 抛异常会使管道 `stop_event`，影响其他插件）。

## 4. 读空气拦截（单次 LLM 调用 + 工具）

1. **拦截工具**：`FunctionTool(name="active_reply_stay_silent", handler=…)`，
   **仅注入主动回复这一轮**（经 `request_llm(tool_set=…)`，不全局注册，正常对话不受影响）。
   行为：记日志（含 reason）→ `event.set_extra("_active_reply_intercepted", True)` →
   `event.stop_event()`（阻止该回复写入历史）→ 返回"停止输出"指令字符串。
2. **上下文注入**：`@filter.on_llm_request()` 钩子，当 `_active_reply_turn` 标记存在时，
   向 `req.extra_user_content_parts` 追加一条**静态**系统提醒（读空气指引 + 工具调用
   说明）。不含任何统计数字、不复制会话内容——会话内容与机器人自己的发言 LLM 在
   对话上下文中本就能看到。
3. **兜底拦截**：`@filter.on_llm_response()` 钩子，若拦截标记存在，清空
   `response.completion_text` / `result_chain`（钩子在发送前触发且与最终结果同一对象引用，
   `tool_loop_agent_runner.py:180-202, 1564-1565`），保证被拦截时不发送任何内容。
4. 依据的机制全部为 AstrBot 既有插件 API：`FunctionTool(handler=…)` 执行时自动注入
   `event`（`astr_agent_tool_exec.py:690-692, 793`）；`req.func_tool` 会 merge 进该轮
   工具集（`astr_main_agent.py:631-634`）；`on_llm_request` 钩子在 agent 运行前触发
   （`agent_sub_stages/internal.py:303`）。

## 5. 配置（`_conf_schema.json`）

| 配置项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enable` | bool | false | 总开关 |
| `default_possibility` | float | 0 | 兜底概率（未命中规则时） |
| `keyword_rules` | template_list | [] | 条目：`keyword` + `possibility` |
| `sender_rules` | template_list | [] | 条目：`sender_id`(选填) + `nickname`(选填) + `possibility`，填写的字段需全部匹配 |
| `read_the_room_enable` | bool | true | 读空气拦截开关 |

已废弃移除：`read_the_room_history_cnt`、`read_the_room_window_minutes`
（v1 读空气实现中的平行内存上下文）、`read_the_room_context_cnt`
（会话历史统计窗口，无需统计，LLM 直接看会话上下文）。

## 6. 已移除的组件

- `RoomContextTracker`（内存消息记录 + 主动回复时间戳）：上下文素材改为会话历史，删除。
- `on_message` 中的群聊消息记录逻辑：删除。

## 7. 边界与已知限制

1. 读空气判断完全由 LLM 依据可见的会话上下文完成，插件不提供任何统计；
   "一段时间内话太多"的判断质量取决于 LLM 对会话历史的理解。
2. 拦截依赖 LLM 遵守工具约定（调用后停止输出）；`on_llm_response` 兜底可完全堵住
   非流式路径，流式路径若 LLM 先输出文字再调用工具，已发出的文字无法撤回。
3. 无跨事件状态（仅事件级标记），重启无影响。
4. 当 LLM 提供商开启"输出工具调用信息"（provider 设置 `show_tool_use_status`，默认关闭）时，
   框架会在工具 handler 执行前先向群聊发送一条工具调用状态消息；拦截工具调用本身会显示为
   一条消息，插件无法阻止（框架行为）。

## 8. 测试计划

- `on_llm_request`：标记存在且开关开启时注入静态提醒；标记缺失/开关关闭时不动；
  `extra_user_content_parts` 为 None 时初始化。
- `on_llm_response`：拦截标记 → 清空；无标记 → 不动。
- 拦截工具：标记 + stop_event + 返回指令字符串。
- `on_message`：tool_set 注入断言、`_active_reply_turn` 标记断言；概率判定回归不变。
- schema：默认值断言（`read_the_room_enable == True`）。

## 9. 变更记录

- v1：概率规则插件（已实现、已测试、已上线运行）。
- v2：读空气拦截（已实现，但上下文来源错误——自建 `RoomContextTracker` 平行记录
  群聊消息）。本规范即 v2 的修订：上下文来源改为回复请求天然携带的会话历史。
