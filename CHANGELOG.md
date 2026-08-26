# Changelog

本文件记录各版本的改动。版本号与 `metadata.yaml` 保持一致。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v0.2.10] - 2026-08-26

### Added

- 绑定赛事时持久化 start.gg `slug`（写入 `tournaments.json`），供 TOP8 等插件通过赛事简码复用链接

### Changed

- 赛事列表 Web API 返回 `slug` / `url` 字段

## [v0.2.9] - 2026-06-08

### Fixed

- 查询选手名时优先精确匹配，避免短 ID（如 `ay`）被子串较长的同名选手（如 `ayase`）误判为多人匹配

## [v0.2.8] - 2026-06-08

### Changed

- 导出/下载完成后提示文件名与保存位置（系统默认下载目录）

### Fixed

- 修复 AstrBot 插件页 iframe 未启用 `allow-modals` 导致 `window.confirm` 无效的问题，改为页面内自定义确认对话框

## [v0.2.7] - 2026-06-08

### Added

- 数据备份列表新增「删除」按钮（二次确认后删除备份目录与缓存 ZIP）

### Fixed

- 修复部分 AstrBot 版本不支持 `bridge.download` 导致导出/下载报错的问题，改为通过 JSON 内容接口 + 浏览器本地下载

## [v0.2.6] - 2026-06-08

### Added

- 管理页「数据备份」：手动创建备份、查看历史、下载 ZIP、从备份恢复（适用于 AstrBot 或插件大版本更新前）
- Web API：`data/backups`、`data/backups/create`、`data/backups/download`、`data/backups/restore`

## [v0.2.5] - 2026-06-08

### Added

- 管理页新增「切换当前赛事」下拉框与「设为当前赛事」按钮

### Changed

- 运行时数据迁移至 AstrBot `data/plugin_data/astrbot_plugin_startggbot/`，插件更新后保留赛事绑定与账号关联；首次启动自动从旧版插件目录 `data/` 迁移

## [v0.2.4] - 2026-06-08

### Added

- 账号关联管理页新增「前缀」列；关联时从 start.gg 选手档案读取 `prefix` 与 `gamerTag`
- 管理页「更新选手信息」按钮：重新拉取关联账号当前的前缀与选手名

## [v0.2.3] - 2026-06-08

### Added

- 聊天指令 `@机器人 解除关联`：用户自行解除 QQ 与 start.gg 账号绑定

### Changed

- 「我在哪」及省略用户名的「查询」：已关联 start.gg 账号的用户按关联身份匹配，未关联用户仍按群名片/昵称匹配（与报分逻辑一致）

## [v0.2.2] - 2026-06-08

### Fixed

- 修复 AstrBot 包路径加载插件时找不到 `web_compat` / `web_manage` 的问题（改为基于 `__file__` 显式加载同目录模块）

## [v0.2.1] - 2026-06-08

### Fixed

- 修复插件加载失败：不再在 import 阶段硬依赖 `astrbot.api.web`；v0.2.0 在部分环境下会直接报 `No module named 'astrbot.api.web'`
- 启动时记录 WebUI API 后端类型，便于排查 AstrBot 本体是否包含 `astrbot.api.web`

## [v0.2.0] - 2026-06-08

### Added

- WebUI 插件 Page「赛事与账号管理」（`pages/manage`）：展示已绑定赛事列表，支持绑定、解绑与切换当前赛事
- QQ ↔ start.gg 账号关联：可通过 start.gg 唯一代码（或用户链接）关联；配置页可查看 QQ 号、唯一代码、start.gg 用户 ID，并导出 TXT / CSV / JSON
- 聊天指令 `@机器人 关联 <唯一代码或链接>`：用户自助绑定 start.gg 账号
- 全局当前赛事（`data/settings.json`）：WebUI 切换的赛事在会话未单独切换时作为默认赛事

### Changed

- 报分：已关联 start.gg 账号的用户按关联身份（选手 ID / gamerTag）报分，不再依赖群名片或昵称；未关联用户保持原有匹配方式

## [v0.1.3] - 2026-06-08

### Changed

- 查询 / 我在哪：群聊优先使用群名片（`card`）匹配选手，与 QQ 昵称区分
- 查询回复：`查询对象` 直接显示选手名；`对局` 仅显示轮次，不再附带 setId
- 帮助 / help：群聊以合并转发（`Nodes`）发送指令说明；私聊仍为纯文本
- 报分指令：命令与参数之间可无空格或多空格（如 `报分2-1`、`报分  2-1`）

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

[Unreleased]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/compare/v0.1.3...HEAD
[v0.1.3]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/compare/v0.1.2...v0.1.3
[v0.1.2]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/compare/v0.1.1...v0.1.2
[v0.1.1]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/compare/v0.1.0...v0.1.1
[v0.1.0]: https://github.com/Butterfly0v0/astrbot_plugin_startggbot/releases/tag/v0.1.0
