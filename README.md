# astrbot-plugin-startggbot

AstrBot 插件：查询 [start.gg](https://www.start.gg) 对阵并支持自助报分。

> [!NOTE]
> 本仓库为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件。安装后请在 WebUI 中配置 `startgg_api_token`，勿将真实 Token 提交到仓库。

## Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档（中文）](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)
- [start.gg API 文档](https://developer.start.gg/docs/sending-requests/)
- [Report Set 示例](https://developer.start.gg/docs/examples/mutations/report-set/)

## 功能

- 多赛事动态绑定与会话内赛事切换
- 管理员「我的赛事」：查询 API 账号在 start.gg 创建或管理的赛事及 bot 简码
- 查询我在哪 / 查询 [用户名]（最近一场对局；已结束附带比分）
- 管理员通过 start.gg 链接绑定赛事
- 查询指定 set 详情
- 报分开关（管理员手动开启/关闭）
- 自助报分（含 `gameData` 局分上报）；管理员可按选手名代报
- 默认映射：聊天平台 `userId` 作为 start.gg `entrantId`；支持手动 `entrantId` 绑定覆盖
- 报分审计日志
- **@ 机器人触发指令**（群聊）；私聊可直接发送指令文本

## 安装

1. 将本仓库克隆到 AstrBot 的 `data/plugins/` 目录，或通过 AstrBot 插件市场安装（若已上架）。
2. 在 AstrBot WebUI 打开本插件配置，填写 `startgg_api_token` 与 `admin_qq_ids`。
3. 重启或重载插件后，在群内 @ 机器人发送 `帮助` 查看指令。

旧版可将 `config.example.json` 复制为 `config.json` 填写（`config.json` 已加入 `.gitignore`，不会进入版本库）。

## 配置

| 配置项 | 说明 |
|--------|------|
| `startgg_api_token` | start.gg API Token |
| `admin_qq_ids` | 管理员 QQ 号列表 |
| `default_tournament` | 默认赛事简码（可留空） |
| `report_enabled_default` | 报分默认开关 |
| `report_switch_scope` | 报分开关作用域 |
| `report_cooldown_sec` | 报分冷却秒数 |

赛事通过聊天指令绑定，无需写入配置文件。

## 指令

群聊请 **@机器人** 后发送指令；私聊可直接发送指令文本。

- `@机器人 帮助`
- `@机器人 赛事列表`
- `@机器人 我的赛事`（管理员）
- `@机器人 绑定赛事 [简码] <eventId\|链接> [phaseGroupId]`
- `@机器人 绑定赛事链接 [简码] <start.gg链接> [phaseGroupId]`
- `@机器人 绑定赛事全参 [简码] <名称> <eventId> [phaseGroupId]`
- `@机器人 解绑赛事 <简码>`
- `@机器人 赛事详情 <简码>`
- `@机器人 切换 <赛事简码>`
- `@机器人 绑定 <entrantId>`
- `@机器人 我在哪`
- `@机器人 查询 [用户名]`
- `@机器人 set <setId>`
- `@机器人 报分状态 [赛事简码]`
- `@机器人 报分开关 <on\|off> [赛事简码]`
- `@机器人 报分 <比分> <我\|对手> [setId]`
- `@机器人 报分 <选手名> <比分> <我\|对手> [setId]`（管理员代报）
- `@机器人 添加管理员 @用户`

示例：

```
@机器人 帮助
@机器人 查询 PlayerName
@机器人 绑定赛事链接 https://www.start.gg/tournament/xxx/event/yyy
@机器人 报分 2-1 我
@机器人 报分 2-1 我 12345678
```

## 数据文件

插件运行后在插件目录下自动创建 `data/`（已 gitignore）：

- `bindings.json`：手动绑定
- `tournaments.json`：动态绑定赛事
- `report_switch.json`：报分开关
- `admin_qq_ids.json`：动态管理员
- `audit_log.jsonl`：审计日志

## 注意事项

- `reportBracketSet` 需 token 具备目标赛事报分权限；报分通过 `gameData` 同步局分（如 `2-1` 展开为 3 局记录）。
- 群聊中未 @ 机器人的消息不会被本插件拦截。
- 管理员来源：WebUI `admin_qq_ids`、动态管理员列表、群聊群主/群管。

## License

AGPL-3.0
