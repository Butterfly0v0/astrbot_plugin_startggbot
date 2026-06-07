# Changelog

本文件记录各版本的改动。版本号与 `metadata.yaml` 保持一致。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v0.1.2] - 2026-06-07

### Changed

- 报分成功回复简化为 `报分成功，胜者为XXX`，XXX 为胜者 gamerTag

## [v0.1.1] - 2026-06-07

### Changed

- 报分指令移除 `我/对手` 参数；比分格式为 `<报分选手局数>-<对手局数>`，数字更大的一方自动为胜者
- 平局（如 `1-1`）将被拒绝
- 更新 README 报分说明与示例

### Fixed

- 修复管理员代报时「比分与胜者不一致」的误用场景（原需同时填写矛盾的比分与胜者）

## [v0.1.0] - 2026-06-01

### Added

- 初始发布：start.gg 对阵查询与自助报分 AstrBot 插件
- 多赛事动态绑定、解绑、切换与会话内当前赛事记忆
- `@机器人` 触发指令（群聊需 @；私聊仅拦截已知指令）
- 查询我在哪 / 查询 [用户名]：按 gamerTag 匹配选手，显示最近一场对局；已结束附带比分
- 管理员绑定赛事（eventId、start.gg 链接、全参）；省略简码时使用 tournament 名称
- 管理员「我的赛事」：列出 API 账号创建或管理的赛事及 bot 简码
- `set <setId>` 查询对局详情
- 报分开关（管理员 `on/off`）与报分状态查询
- 自助报分：通过 `gameData` 将局分（如 `2-1`）上报至 start.gg
- 管理员按选手名代报
- 选手身份：默认 QQ `userId` 映射 entrantId；支持 `绑定 <entrantId>` 覆盖
- 报分按聊天昵称匹配选手（与查询一致）；支持指定 `setId` 补报
- 排除晋级后仅一方入位的等待局，避免误报分
- 报分冷却、审计日志（`data/audit_log.jsonl`）
- WebUI 配置（`_conf_schema.json`）：`startgg_api_token`、`admin_qq_ids` 等
- 动态管理员（`添加管理员 @用户`）；群聊群主/群管亦具备管理员权限

### Fixed

- 多赛事切换后会话键不一致导致始终查询第一个绑定赛事的问题
- 对局查询由错误的 `entrant.sets` 改为 `event.sets(filters: entrantIds)`，修复切换赛事后查不到对局

[Unreleased]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/compare/v0.1.2...HEAD
[v0.1.2]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/compare/v0.1.1...v0.1.2
[v0.1.1]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/compare/v0.1.0...v0.1.1
[v0.1.0]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/releases/tag/v0.1.0
