# active_reply

按「关键词」和「发送者」规则，以可配置概率主动回复群聊消息的 AstrBot 插件。
回复内容由 LLM 生成；判定与回复流程参照 AstrBot 内置群聊上下文插件的 active_reply 功能实现
（[`astrbot/builtin_stars/astrbot`](https://github.com/AstrBotDevs/AstrBot/tree/main/astrbot/builtin_stars/astrbot)）。

## 功能

- **关键词规则**：群聊消息包含某关键词（不区分大小写）时，按该规则的概率触发回复。
- **发送者规则**：消息发送者的 ID / 昵称匹配时按该规则的概率触发回复。`sender_id` 与 `nickname` 至少填一个，填写的字段需全部匹配才命中。
- **兜底概率**：未命中任何规则时，使用全局兜底概率（默认 0，即不回复）。
- 多条规则同时命中时取**最高概率**，掷一次骰子。
- 仅在**群聊**生效；@机器人、唤醒词消息走默认回复链路，**指令消息**（如 `/help`）不触发，避免双重回复。
- **读空气（read-the-room）**：概率规则决定是否触发；触发后在同一次 LLM 回复调用中，
  插件注入一条静态提醒，LLM 依据该回复天然携带的会话上下文判断当前是否适合插话，
  不适合时调用拦截工具 `active_reply_stay_silent` 拒绝本次回复（不产生额外 LLM 调用，
  且该工具只注入主动回复这一轮，不影响正常对话）。

## 配置

安装插件后在 WebUI「插件 → 配置」中设置：

| 配置项 | 说明 |
|---|---|
| `enable` | 总开关 |
| `default_possibility` | 兜底概率（0-1），未命中任何规则时使用 |
| `keyword_rules` | 关键词规则列表：`keyword` + `possibility`（0-1） |
| `sender_rules` | 发送者规则列表：`sender_id`（选填）+ `nickname`（选填）+ `possibility`（0-1） |
| `read_the_room_enable` | 读空气拦截开关（默认开） |

发送者 ID 可在群聊中发送 `/sid` 查看。

回复链路与内置 active_reply 相同：需要已配置 LLM 提供商，且当前会话存在对话
（平台设置的「会话隔离(unique_session)」需保持关闭，并用 `/new` 创建会话）。

## 与内置 active_reply 的关系

AstrBot 内置「群聊上下文」插件同样提供 active_reply（按全局概率回复）。两者同时开启时，
同一条群聊消息可能各触发一次回复，建议二选一。

## 开发

```bash
# 在 AstrBot 仓库根目录运行测试
TZ=Asia/Shanghai uv run pytest data/plugins/astrbot_plugin_active_reply/tests
```
