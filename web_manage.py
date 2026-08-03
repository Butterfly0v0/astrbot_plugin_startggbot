"""WebUI 管理面板 API（赛事绑定 / QQ-start.gg 账号关联）。"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from main import StartGGMatchPlugin

PLUGIN_ROUTE = "astrbot_plugin_startggbot"


def register_web_apis(plugin: "StartGGMatchPlugin") -> None:
    if not hasattr(plugin.context, "register_web_api"):
        return
    routes = [
        ("tournaments", plugin.web_list_tournaments, ["GET"], "List bound tournaments"),
        ("tournaments/bind", plugin.web_bind_tournament, ["POST"], "Bind tournament"),
        ("tournaments/unbind", plugin.web_unbind_tournament, ["POST"], "Unbind tournament"),
        ("tournaments/active", plugin.web_set_active_tournament, ["POST"], "Set active tournament"),
        ("account-links", plugin.web_list_account_links, ["GET"], "List account links"),
        ("account-links", plugin.web_add_account_link, ["POST"], "Add account link"),
        ("account-links/remove", plugin.web_delete_account_link_post, ["POST"], "Remove account link"),
        ("account-links/export", plugin.web_export_account_links, ["GET"], "Export account links"),
    ]
    for path, handler, methods, desc in routes:
        plugin.context.register_web_api(
            f"/{PLUGIN_ROUTE}/{path}",
            handler,
            methods,
            desc,
        )
    plugin.context.register_web_api(
        f"/{PLUGIN_ROUTE}/account-links/<qq_id>",
        plugin.web_delete_account_link,
        ["DELETE"],
        "Delete account link",
    )


def normalize_startgg_user_code(raw: str) -> Tuple[Optional[str], str]:
    text = (raw or "").strip()
    if not text:
        return None, "start.gg 唯一代码不能为空。"

    if "start.gg" in text or text.startswith("http"):
        match = re.search(r"user/([^/?#]+)", text, re.I)
        if match:
            return f"user/{match.group(1)}", ""
        return None, "无法从链接中解析 start.gg 用户代码。"

    text = text.strip("/")
    if text.lower().startswith("user/"):
        slug = text
    else:
        slug = f"user/{text.split('/')[-1]}"
    if not re.fullmatch(r"user/[A-Za-z0-9_-]+", slug):
        return None, "start.gg 唯一代码格式无效，示例：1c97bdae 或 user/1c97bdae"
    return slug, ""


def format_account_links_txt(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "暂无已关联账号。\n"
    lines = [
        "QQ号\tstart.gg唯一代码\tstart.gg用户ID\tstart.gg选手ID\t选手名\t关联时间",
    ]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    str(row.get("qq_id", "")),
                    str(row.get("startgg_code", "")),
                    str(row.get("startgg_user_id", "")),
                    str(row.get("startgg_player_id", "") or ""),
                    str(row.get("gamer_tag", "") or ""),
                    str(row.get("linked_at_text", "")),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def format_account_links_csv(rows: List[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "qq_id",
            "startgg_code",
            "startgg_user_id",
            "startgg_player_id",
            "gamer_tag",
            "linked_at",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("qq_id", ""),
                row.get("startgg_code", ""),
                row.get("startgg_user_id", ""),
                row.get("startgg_player_id", ""),
                row.get("gamer_tag", ""),
                row.get("linked_at", ""),
            ]
        )
    return output.getvalue()


def now_ts() -> int:
    return int(time.time())
