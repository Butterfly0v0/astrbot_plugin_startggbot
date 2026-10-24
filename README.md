# astrbot-plugin-startggbot

AstrBot 插件：查询 [start.gg](https://www.start.gg) 对阵并支持自助报分。

> [!NOTE]
> 本仓库为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件。安装后请在 WebUI 中配置 `startgg_api_token`，勿将真实 Token 提交到仓库。
>
> **WebUI 管理页**需要 AstrBot **v4.26.8+**（含 `astrbot.api.web` 与 Plugin Pages）。请使用插件 **v0.2.1+**（v0.2.0 加载时会因 import 失败而无法启动）。
>
> 若仍报错，请在 **AstrBot 实际运行的 Python 环境**中执行诊断：
> `python -c "import importlib; importlib.import_module('astrbot.api.web'); print('ok')"`
> 若此处失败，说明 AstrBot 本体未更新完整，而非插件版本问题。

## Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档（中文）](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)
- [start.gg API 文档](https://developer.start.gg/docs/sending-requests/)
- [Report Set 示例](https://developer.start.gg/docs/examples/mutations/report-set/)

## 功能

- 多赛事动态绑定与会话内赛事切换
- 管理员「我的赛事」：查询 API 账号在 start.gg 创建或管理的赛事及 bot 简码
- 查询我在哪 / 查询 [用户名]（本人查询时：已关联 start.gg 账号按关联身份；未关联则群聊优先用群名片匹配）
- 管理员通过 start.gg 链接绑定赛事
- 查询指定 set 详情
- 报分开关（管理员手动开启/关闭）
- 自助报分（含 `gameData` 局分上报）；管理员可按选手名代报
- 报分：已关联 start.gg 账号的用户按关联身份报分；未关联则按群名片/昵称匹配（与「查询」一致），支持指定 `setId` 补报
- 群聊中 `帮助` / `help` 以合并转发发送指令说明（OneBot v11）
- 默认映射：聊天平台 `userId` 作为 start.gg `entrantId`；支持手动 `entrantId` 绑定覆盖
- 报分审计日志
- **WebUI 管理面板**（插件详情 → `manage`）：赛事绑定/解绑/切换、QQ-start.gg 账号关联与导出
- **@ 机器人触发指令**（群聊）；私聊可直接发送指令文本
- **与 [astrbot_plugin_top8](https://github.com/Butterfly0v0/astrbot_plugin_top8) 互通**：绑定赛事时写入 start.gg `slug`，TOP8 插件可用同一套赛事简码生成成绩图（需本插件 ≥ v0.2.10）

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
| `manage_page_hint` | WebUI 管理面板说明（只读） |

赛事可通过聊天指令或 WebUI「赛事与账号管理」页面绑定，无需写入配置文件。

### WebUI 管理面板

在 AstrBot 插件详情页打开 **manage / 赛事与账号管理**：

- 查看已绑定赛事，绑定新赛事（eventId 或 start.gg 链接），解绑，通过下拉框或列表切换当前赛事
- 管理 QQ 与 start.gg 账号关联（唯一代码、用户 ID），并导出 TXT / CSV / JSON
- **数据备份**：更新 AstrBot 或插件前手动备份，支持下载 ZIP 与从历史备份恢复

关联数据保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_startggbot/`（全局当前赛事为其中的 `settings.json`）。插件更新后会保留；若从旧版升级，首次启动会自动从插件目录内 `data/` 迁移。建议在重大更新前在管理页创建备份。

### 绑定 start.gg 账号

将 QQ 号与 start.gg 账号关联后，**「我在哪」「查询」（省略用户名）和报分** 会优先使用关联身份（按 start.gg 选手 ID / gamerTag 匹配），不再依赖群名片或 QQ 昵称。未关联的用户仍按原有方式（群名片/昵称）匹配。

#### 唯一代码是什么？

start.gg 个人主页 URL 中的用户标识，例如：

- 链接：`https://www.start.gg/user/1c97bdae`
- 唯一代码可填写：`1c97bdae` 或 `user/1c97bdae`（完整链接也可）

在 start.gg 网站打开你的个人主页，地址栏中 `user/` 后面的部分即为唯一代码。

#### 用户自助绑定（推荐）

群聊 @ 机器人，或私聊直接发送：

```
@机器人 关联 1c97bdae
@机器人 关联 user/1c97bdae
@机器人 关联 https://www.start.gg/user/1c97bdae
```

绑定成功后会回复 QQ 号、唯一代码、start.gg 用户 ID 与选手名。

#### 解除关联

```
@机器人 解除关联
```

解除后，「我在哪」「查询」「报分」将恢复为群名片/昵称匹配。

#### 管理员在 WebUI 绑定

插件详情页 → **manage / 赛事与账号管理** → 「QQ ↔ start.gg 账号关联」：

1. 填写 QQ 号与 start.gg 唯一代码（或链接）
2. 点击「添加关联」
3. 可在列表中查看、删除关联，或导出 TXT / CSV / JSON

#### 关联后如何报分

已关联用户直接报分即可，无需与群名片一致：

```
@机器人 报分 2-1
@机器人 报分 2-1 12345678   # 指定 setId 补报
```

若提示「在当前赛事中未找到对应选手」，请确认已切换至正确赛事，且该 start.gg 账号已在该赛事中报名。

## 指令

群聊请 **@机器人** 后发送指令；私聊可直接发送指令文本。

- `@机器人 帮助` / `@机器人 help`（群聊为合并转发）
- `@机器人 赛事列表`
- `@机器人 我的赛事`（管理员）
- `@机器人 绑定赛事 [简码] <eventId\|链接> [phaseGroupId]`
- `@机器人 绑定赛事链接 [简码] <start.gg链接> [phaseGroupId]`
- `@机器人 绑定赛事全参 [简码] <名称> <eventId> [phaseGroupId]`
- `@机器人 解绑赛事 <简码>`
- `@机器人 赛事详情 <简码>`
- `@机器人 切换 <赛事简码>`（同时写入全局当前赛事，管理页与 TOP8 会跟着更新）
- `@机器人 绑定 <entrantId>`
- `@机器人 我在哪`
- `@机器人 查询 [用户名]`（省略时群聊用群名片）
- `@机器人 set <setId>`
- `@机器人 报分状态 [赛事简码]`
- `@机器人 报分开关 <on\|off> [赛事简码]`
- `@机器人 报分 <选手局数>-<对手局数> [setId]`
- `@机器人 报分 <选手名> <选手局数>-<对手局数> [setId]`（管理员代报）
- `@机器人 添加管理员 @用户`
- `@机器人 关联 <start.gg唯一代码或链接>`
- `@机器人 解除关联`

### 查询回复格式

```
赛事: <简码>
查询对象: @查询者 <选手名>   # 群聊会 @ 发出查询的人；私聊无 @
对局: <轮次>
对手: <对手名>
直播场次: 是 / 否            # 该场是否已在 start.gg 标记到直播流
比分: 2 - 1                   # 仅已结束时显示
```

### 报分格式

- 比分写作 `<报分选手局数>-<对手局数>`，**数字更大的一方自动为胜者**
- 自助报分时，前者为你的局数；管理员代报时，前者为被代报选手的局数
- 指令与参数之间可无空格或多空格，如 `报分2-1`、`报分  2-1`
- 平局（如 `1-1`）会被拒绝
- 晋级后仅一方入位的等待局无法报分；可对已打完的对局报分，或在末尾附上 `setId`
- 报分成功后回复：`报分成功，胜者为XXX`（XXX 为胜者 gamerTag）
- 一场报分成功后的 3 分钟内（可配置 `report_next_set_guard_sec`），双方再不带 setId 报分时，如果自动选中的是另一场，会拒绝写入，避免比分落到还没打的下一场。命令里写了 setId 则仍按指定场次报分

示例：

```
@机器人 帮助
@机器人 查询
@机器人 查询 PlayerName
@机器人 绑定赛事链接 https://www.start.gg/tournament/xxx/event/yyy
@机器人 报分2-1           # 无空格亦可
@机器人 报分 0-2
@机器人 报分 gamera 2-0   # 管理员代报
@机器人 报分 2-1 12345678 # 指定 setId
@机器人 关联 1c97bdae      # 绑定 start.gg 账号
# 成功回复：报分成功，胜者为PlayerName
```

## 数据文件

插件运行后在 AstrBot 的 `data/plugin_data/astrbot_plugin_startggbot/` 下持久化（不在插件目录内，更新插件不会丢失）：

- `bindings.json`：手动绑定
- `tournaments.json`：动态绑定赛事（含 `code` / `name` / `eventId` / `phaseGroupId`；**v0.2.10+** 另存 `slug`，可供 [TOP8 插件](https://github.com/Butterfly0v0/astrbot_plugin_top8) 用简码生图）
- `account_links.json`：QQ ↔ start.gg 账号关联
- `settings.json`：全局当前赛事（`active_tournament_code`）。聊天「切换」和管理页「设为当前赛事」都会更新；TOP8 发送裸指令 `TOP8` 时读取此值
- `report_switch.json`：报分开关
- `admin_qq_ids.json`：动态管理员
- `audit_log.jsonl`：审计日志
- `backups/`：手动备份快照（含 `manifest.json` 与各数据文件副本）

### 与 TOP8 成绩图插件配合

1. 升级本插件至 **v0.2.10+**，安装 [astrbot_plugin_top8](https://github.com/Butterfly0v0/astrbot_plugin_top8)。
2. 用本插件绑定赛事（链接或 eventId）；新绑定会自动保存 `slug`。旧绑定可重新绑定一次，或由 TOP8 在首次用简码生图时自动补全。
3. 在 TOP8 中：`TOP8`（当前赛事）/ `TOP8 <简码>` / `TOP8赛事`。

两插件共享的是 AstrBot `plugin_data` 下的 JSON 文件，无需额外同步服务。

## 注意事项

- `reportBracketSet` 需 token 具备目标赛事报分权限；报分通过 `gameData` 同步局分（如 `2-1` 展开为 3 局记录）。
- 群聊中未 @ 机器人的消息不会被本插件拦截。
- 管理员来源：WebUI `admin_qq_ids`、动态管理员列表、群聊群主/群管。

## 更新日志

见 [CHANGELOG.md](./CHANGELOG.md)。

## License

AGPL-3.0
