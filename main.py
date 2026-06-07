import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


STARTGG_ENDPOINT = "https://api.start.gg/gql/alpha"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
BINDINGS_FILE = os.path.join(DATA_DIR, "bindings.json")
REPORT_SWITCH_FILE = os.path.join(DATA_DIR, "report_switch.json")
TOURNAMENTS_FILE = os.path.join(DATA_DIR, "tournaments.json")
ADMIN_QQ_FILE = os.path.join(DATA_DIR, "admin_qq_ids.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit_log.jsonl")

# 按长度降序匹配，避免「绑定赛事」误匹配「绑定赛事全参」
MENTION_COMMANDS = [
    "绑定赛事全参",
    "绑定赛事链接",
    "绑定赛事",
    "添加管理员",
    "解绑赛事",
    "赛事详情",
    "我的赛事",
    "赛事列表",
    "报分开关",
    "报分状态",
    "我在哪",
    "帮助",
    "help",
    "切换",
    "绑定",
    "报分",
    "查询",
    "set",
]


def _parse_startgg_link(text: str) -> Optional[Dict[str, Any]]:
    """解析 start.gg 链接、slug 或纯数字 eventId。"""
    raw = text.strip()
    if not raw:
        return None
    if raw.isdigit():
        return {"type": "id", "event_id": int(raw)}

    slug = raw
    if "start.gg" in raw or raw.startswith("http"):
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        slug = parsed.path.strip("/")
        if parsed.query:
            slug = slug.split("?")[0]

    if slug.startswith("tournament/") and "/event/" in slug:
        return {"type": "slug", "slug": slug}

    match = re.search(r"tournament/([^/]+)/event/([^/?#]+)", slug)
    if match:
        return {
            "type": "slug",
            "slug": f"tournament/{match.group(1)}/event/{match.group(2)}",
        }

    match_id = re.search(r"(?:^|/)event/(\d+)", slug)
    if match_id:
        return {"type": "id", "event_id": int(match_id.group(1))}

    return None


def _now_ts() -> int:
    return int(time.time())


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _gql_id(value: Any) -> str:
    """start.gg GraphQL ID 类型统一传字符串。"""
    if value is None:
        return ""
    return str(value)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _read_json(path: str, default_value: Any) -> Any:
    if not os.path.exists(path):
        return default_value
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_value


def _write_json(path: str, payload: Any) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _append_jsonl(path: str, payload: Dict[str, Any]) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


class StartGGClient:
    def __init__(self, token: str):
        self.token = token

    async def execute(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        def _req() -> Dict[str, Any]:
            body = json.dumps(
                {"query": query, "variables": variables}, ensure_ascii=False
            ).encode("utf-8")
            req = urllib.request.Request(
                STARTGG_ENDPOINT,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8")
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8")
                except Exception:
                    detail = str(e)
                raise RuntimeError(f"start.gg HTTP错误: {e.code} {detail}") from e
            except Exception as e:
                raise RuntimeError(f"start.gg请求失败: {e}") from e

        return await asyncio.to_thread(_req)

    @staticmethod
    def graphql_errors(resp: Dict[str, Any]) -> List[str]:
        errors = resp.get("errors") or []
        messages: List[str] = []
        for err in errors:
            if isinstance(err, dict):
                msg = str(err.get("message") or "").strip()
                if msg:
                    messages.append(msg)
            elif err:
                messages.append(str(err))
        return messages


@dataclass
class TournamentConfig:
    code: str
    name: str
    event_id: int
    phase_group_id: Optional[int] = None


@dataclass
class EventBindingInfo:
    event_id: int
    event_name: str
    tournament_name: str


class GGStore:
    def __init__(self) -> None:
        _ensure_dir(DATA_DIR)

    def get_bindings(self) -> Dict[str, Dict[str, Any]]:
        return _read_json(BINDINGS_FILE, {})

    def set_binding(self, key: str, value: Dict[str, Any]) -> None:
        data = self.get_bindings()
        data[key] = value
        _write_json(BINDINGS_FILE, data)

    def get_report_switch(self) -> Dict[str, Any]:
        return _read_json(
            REPORT_SWITCH_FILE,
            {"globalEnabled": False, "tournamentEnabledMap": {}, "updatedAt": 0},
        )

    def save_report_switch(self, payload: Dict[str, Any]) -> None:
        _write_json(REPORT_SWITCH_FILE, payload)

    def get_tournaments(self) -> Dict[str, Dict[str, Any]]:
        return _read_json(TOURNAMENTS_FILE, {})

    def save_tournaments(self, payload: Dict[str, Dict[str, Any]]) -> None:
        _write_json(TOURNAMENTS_FILE, payload)

    def append_audit(self, payload: Dict[str, Any]) -> None:
        _append_jsonl(AUDIT_FILE, payload)

    def get_dynamic_admin_qq_ids(self) -> List[str]:
        data = _read_json(ADMIN_QQ_FILE, {"qq_ids": []})
        raw = data.get("qq_ids", [])
        if not isinstance(raw, list):
            return []
        return [str(x).strip() for x in raw if str(x).strip()]

    def add_dynamic_admin_qq_ids(self, qq_ids: List[str], operator: str) -> List[str]:
        data = _read_json(ADMIN_QQ_FILE, {"qq_ids": []})
        existing = set(self.get_dynamic_admin_qq_ids())
        added: List[str] = []
        for qq in qq_ids:
            q = str(qq).strip()
            if not q or q in existing:
                continue
            existing.add(q)
            added.append(q)
        if added:
            data["qq_ids"] = sorted(existing)
            data["updatedAt"] = _now_ts()
            data["updatedBy"] = operator
            _write_json(ADMIN_QQ_FILE, data)
        return added


@register("astrbot_plugin_startggbot", "Butterfly0v0", "start.gg对阵查询与自助报分", "0.1.2")
class StartGGMatchPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config or AstrBotConfig({})
        self.store = GGStore()
        self.report_cooldown: Dict[str, int] = {}
        self.session_tournament: Dict[str, str] = {}

        cfg = self._merge_config_sources()
        self.token = str(cfg.get("startgg_api_token", "") or "")
        self.binding_mode = cfg.get(
            "binding_mode", "platform_user_id_as_entrant_id"
        )
        self.report_enabled_default = bool(cfg.get("report_enabled_default", False))
        self.report_switch_scope = cfg.get("report_switch_scope", "per_tournament")
        self.report_cooldown_sec = int(cfg.get("report_cooldown_sec", 15))
        self.tournaments = self._load_tournaments_from_sources(
            cfg.get("tournaments", [])
        )
        self.default_tournament = str(cfg.get("default_tournament", "") or "")
        admin_raw = cfg.get("admin_qq_ids")
        if admin_raw is None:
            admin_raw = cfg.get("private_admin_qq_ids", [])
        self.config_admin_qq_ids = self._parse_qq_id_list(admin_raw)
        self.dynamic_admin_qq_ids: set = set()
        self._reload_dynamic_admin_qq_ids()

    def _load_legacy_config(self) -> Dict[str, Any]:
        possible_files = [
            os.path.join(os.path.dirname(__file__), "config.json"),
            os.path.join(os.path.dirname(__file__), "config.example.json"),
        ]
        for p in possible_files:
            if os.path.exists(p):
                return _read_json(p, {})
        return {}

    def _merge_config_sources(self) -> Dict[str, Any]:
        """优先 WebUI 配置（_conf_schema），兼容旧版 config.json。"""
        legacy = self._load_legacy_config()
        merged: Dict[str, Any] = {
            "startgg_api_token": legacy.get("startgg", {}).get("apiToken", ""),
            "default_tournament": legacy.get("defaultTournament", ""),
            "binding_mode": legacy.get("bindingMode", "platform_user_id_as_entrant_id"),
            "report_enabled_default": legacy.get("reportEnabledDefault", False),
            "report_switch_scope": legacy.get("reportSwitchScope", "per_tournament"),
            "report_cooldown_sec": legacy.get("reportCooldownSec", 15),
            "admin_qq_ids": legacy.get("admin_qq_ids", legacy.get("private_admin_qq_ids", [])),
            "tournaments": legacy.get("tournaments", []),
        }
        if self.config:
            for key in (
                "startgg_api_token",
                "default_tournament",
                "binding_mode",
                "report_enabled_default",
                "report_switch_scope",
                "report_cooldown_sec",
                "admin_qq_ids",
            ):
                if key in self.config:
                    value = self.config.get(key)
                    if key == "startgg_api_token" and (value is None or value == ""):
                        continue
                    merged[key] = value
        return merged

    def _parse_qq_id_list(self, raw: Any) -> set:
        result: set = set()
        if not isinstance(raw, list):
            return result
        for item in raw:
            if isinstance(item, str) and item.strip():
                result.add(item.strip())
            elif isinstance(item, dict):
                qq = item.get("qq") or item.get("qq_id") or item.get("id")
                if qq is not None and str(qq).strip():
                    result.add(str(qq).strip())
            elif item is not None:
                text = str(item).strip()
                if text:
                    result.add(text)
        return result

    def _reload_dynamic_admin_qq_ids(self) -> None:
        self.dynamic_admin_qq_ids = set(self.store.get_dynamic_admin_qq_ids())

    def _all_plugin_admin_qq_ids(self) -> set:
        return self.config_admin_qq_ids | self.dynamic_admin_qq_ids

    def _is_plugin_config_admin(self, event: AstrMessageEvent) -> bool:
        return self._sender_id(event) in self._all_plugin_admin_qq_ids()

    def _extract_at_qq_ids(self, event: AstrMessageEvent) -> List[str]:
        qq_ids: List[str] = []
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is None:
            return qq_ids
        for seg in getattr(msg_obj, "message", []) or []:
            if isinstance(seg, Comp.At):
                qq = (
                    getattr(seg, "qq", None)
                    or getattr(seg, "id", None)
                    or getattr(seg, "user_id", None)
                )
                if qq is not None:
                    qq_ids.append(str(qq))
        return qq_ids

    def _load_tournaments(self, raw: List[Dict[str, Any]]) -> Dict[str, TournamentConfig]:
        result: Dict[str, TournamentConfig] = {}
        for item in raw:
            code = str(item.get("code", "")).strip()
            if not code:
                continue
            event_id = _safe_int(item.get("eventId"))
            if not event_id:
                continue
            result[code] = TournamentConfig(
                code=code,
                name=str(item.get("name", code)),
                event_id=event_id,
                phase_group_id=_safe_int(item.get("phaseGroupId")),
            )
        return result

    def _load_tournaments_from_sources(
        self, config_tournaments: List[Dict[str, Any]]
    ) -> Dict[str, TournamentConfig]:
        # 优先使用 bot 动态绑定赛事；为空时回退配置文件，兼容旧版本。
        dynamic_raw = list((self.store.get_tournaments() or {}).values())
        if dynamic_raw:
            return self._load_tournaments(dynamic_raw)
        return self._load_tournaments(config_tournaments)

    def _save_tournaments(self) -> None:
        payload: Dict[str, Dict[str, Any]] = {}
        for code, t in self.tournaments.items():
            payload[code] = {
                "code": t.code,
                "name": t.name,
                "eventId": t.event_id,
                "phaseGroupId": t.phase_group_id,
            }
        self.store.save_tournaments(payload)

    def _sync_tournaments_from_store(self) -> None:
        """从 data/tournaments.json 刷新内存，避免绑定后其他逻辑仍用旧列表。"""
        dynamic_raw = list((self.store.get_tournaments() or {}).values())
        if dynamic_raw:
            self.tournaments = self._load_tournaments(dynamic_raw)

    def _sender_id(self, event: AstrMessageEvent) -> str:
        for attr in ("get_sender_id", "sender_id", "user_id"):
            value = getattr(event, attr, None)
            if callable(value):
                try:
                    return str(value())
                except Exception:
                    continue
            if value is not None:
                return str(value)
        sender = getattr(event, "sender", None)
        if sender:
            uid = getattr(sender, "user_id", None) or getattr(sender, "id", None)
            if uid is not None:
                return str(uid)
        return "unknown_user"

    def _group_id(self, event: AstrMessageEvent) -> Optional[str]:
        getter = getattr(event, "get_group_id", None)
        if callable(getter):
            try:
                gid = getter()
                if gid:
                    return str(gid)
            except Exception:
                pass
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is not None:
            for attr in ("group_id", "chat_id"):
                gid = getattr(msg_obj, attr, None)
                if gid:
                    return str(gid)
        for attr in ("group_id", "chat_id"):
            gid = getattr(event, attr, None)
            if gid:
                return str(gid)
        return None

    def _session_key(self, event: AstrMessageEvent) -> str:
        gid = self._group_id(event)
        if gid and self._is_group_message(event):
            return f"group:{gid}"
        for attr in ("conversation_id", "channel_id"):
            value = getattr(event, attr, None)
            if value:
                return f"channel:{value}"
        return f"private:{self._sender_id(event)}"

    def _collect_session_keys(self, event: AstrMessageEvent) -> List[str]:
        """收集可能用于会话赛事切换的键，兼容旧版 session_id 存储。"""
        keys: List[str] = []
        seen: set = set()

        def add(key: str) -> None:
            k = key.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)

        add(self._session_key(event))
        if self._is_group_message(event):
            gid = self._group_id(event)
            if gid:
                add(gid)
                add(f"group:{gid}")
            for attr in ("session_id", "conversation_id", "group_id", "channel_id"):
                value = getattr(event, attr, None)
                if value:
                    add(str(value))
        return keys

    def _set_session_tournament(self, event: AstrMessageEvent, code: str) -> None:
        for key in self._collect_session_keys(event):
            self.session_tournament[key] = code

    def _normalize_group_role(self, role: Any) -> str:
        if role is None:
            return ""
        return str(role).strip().lower()

    def _is_group_privileged_member(self, event: AstrMessageEvent) -> bool:
        """群聊中的群主或管理员（含 OneBot role=owner/admin）。"""
        privileged_roles = {
            "admin",
            "owner",
            "superuser",
            "operator",
            "群主",
            "管理员",
            "group_owner",
            "group_admin",
        }

        for attr in ("is_admin", "is_operator", "is_owner"):
            value = getattr(event, attr, None)
            if callable(value):
                try:
                    if bool(value()):
                        return True
                except Exception:
                    continue
            elif value is True:
                return True

        for attr in ("sender_role", "role"):
            if self._normalize_group_role(getattr(event, attr, None)) in privileged_roles:
                return True

        sender = getattr(event, "sender", None)
        if sender is not None:
            if self._normalize_group_role(getattr(sender, "role", None)) in privileged_roles:
                return True

        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is not None:
            msg_sender = getattr(msg_obj, "sender", None)
            if msg_sender is not None:
                if (
                    self._normalize_group_role(getattr(msg_sender, "role", None))
                    in privileged_roles
                ):
                    return True
                if getattr(msg_sender, "is_owner", False) or getattr(
                    msg_sender, "is_admin", False
                ):
                    return True

        return False

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        if self._is_plugin_config_admin(event):
            return True
        if self._is_group_message(event):
            return self._is_group_privileged_member(event)
        return False

    def _current_tournament_code(self, event: AstrMessageEvent) -> Optional[str]:
        self._sync_tournaments_from_store()
        for key in self._collect_session_keys(event):
            code = self.session_tournament.get(key)
            if code and code in self.tournaments:
                return code
        if self.default_tournament and self.default_tournament in self.tournaments:
            return self.default_tournament
        if self.tournaments:
            return next(iter(self.tournaments.keys()))
        return None

    def _resolve_entrant_id(self, event: AstrMessageEvent) -> Tuple[Optional[int], str]:
        sender = self._sender_id(event)
        key = f"default:{sender}"
        bindings = self.store.get_bindings()
        if key in bindings:
            entrant_id = _safe_int(bindings[key].get("entrantId"))
            if entrant_id:
                return entrant_id, "manual"

        if self.binding_mode == "platform_user_id_as_entrant_id":
            entrant_id = _safe_int(sender)
            if entrant_id:
                return entrant_id, "default_user_id"
        return None, "none"

    def _sender_display_name(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_sender_name", None)
        if callable(getter):
            try:
                name = str(getter()).strip()
                if name:
                    return name
            except Exception:
                pass
        sender = getattr(event, "sender", None)
        if sender is not None:
            for attr in ("nickname", "card", "display_name", "name"):
                val = getattr(sender, attr, None)
                if val and str(val).strip():
                    return str(val).strip()
        return ""

    def _entrant_display_names(self, node: Dict[str, Any]) -> List[str]:
        names: List[str] = []
        main_name = str(node.get("name") or "").strip()
        if main_name:
            names.append(main_name)
        for participant in node.get("participants") or []:
            tag = str((participant or {}).get("gamerTag") or "").strip()
            if tag:
                names.append(tag)
        return names

    def _name_matches_keyword(self, entrant_names: List[str], keyword: str) -> bool:
        kw = keyword.strip().lower()
        if not kw:
            return False
        for name in entrant_names:
            nl = name.lower()
            if nl == kw or kw in nl or nl in kw:
                return True
        return False

    async def _find_entrant_by_username(
        self,
        client: StartGGClient,
        tournament: TournamentConfig,
        username: str,
    ) -> Tuple[Optional[int], Optional[str], str]:
        username = username.strip()
        if not username:
            return None, None, "用户名为空。"

        matches: List[Tuple[int, str]] = []

        filter_query = """
        query FindEntrant($eventId: ID!, $name: String!) {
          event(id: $eventId) {
            entrants(query: { perPage: 32, page: 1, filter: { name: $name } }) {
              nodes {
                id
                name
                participants { gamerTag }
              }
            }
          }
        }
        """
        try:
            resp = await client.execute(
                filter_query,
                {"eventId": _gql_id(tournament.event_id), "name": username},
            )
        except Exception as e:
            return None, None, f"查询选手失败: {e}"

        nodes = (
            (((resp.get("data") or {}).get("event") or {}).get("entrants") or {}).get(
                "nodes"
            )
            or []
        )
        for node in nodes:
            names = self._entrant_display_names(node)
            if self._name_matches_keyword(names, username):
                entrant_id = _safe_int(node.get("id"))
                if entrant_id:
                    matches.append((entrant_id, names[0] if names else username))

        if not matches:
            list_query = """
            query ListEntrants($eventId: ID!, $page: Int!, $perPage: Int!) {
              event(id: $eventId) {
                entrants(query: { perPage: $perPage, page: $page }) {
                  nodes {
                    id
                    name
                    participants { gamerTag }
                  }
                }
              }
            }
            """
            for page in range(1, 6):
                try:
                    resp = await client.execute(
                        list_query,
                        {
                            "eventId": _gql_id(tournament.event_id),
                            "page": page,
                            "perPage": 100,
                        },
                    )
                except Exception as e:
                    return None, None, f"查询选手失败: {e}"
                page_nodes = (
                    (
                        ((resp.get("data") or {}).get("event") or {}).get("entrants")
                        or {}
                    ).get("nodes")
                    or []
                )
                if not page_nodes:
                    break
                for node in page_nodes:
                    names = self._entrant_display_names(node)
                    if self._name_matches_keyword(names, username):
                        entrant_id = _safe_int(node.get("id"))
                        if entrant_id and all(m[0] != entrant_id for m in matches):
                            matches.append((entrant_id, names[0] if names else username))

        if len(matches) == 1:
            return matches[0][0], matches[0][1], ""
        if len(matches) > 1:
            hints = ", ".join(name for _, name in matches[:5])
            return None, None, f"匹配到多名选手，请更精确：{hints}"
        return None, None, f"未在当前赛事中找到用户「{username}」。"

    async def _resolve_query_target(
        self, event: AstrMessageEvent, username: Optional[str] = None
    ):
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return
        tournament_code = self._current_tournament_code(event)
        if not tournament_code or tournament_code not in self.tournaments:
            yield event.plain_result("未找到可用赛事，请先由管理员绑定赛事并切换。")
            return

        keyword = (username or "").strip() or self._sender_display_name(event)
        if not keyword:
            yield event.plain_result(
                "无法获取你的用户名，请使用：@机器人 查询 <start.gg用户名>"
            )
            return

        client = StartGGClient(self.token)
        entrant_id, matched_name, err = await self._find_entrant_by_username(
            client, self.tournaments[tournament_code], keyword
        )
        if err:
            yield event.plain_result(err)
            return
        assert entrant_id is not None and matched_name is not None

        source = (
            f"本人({matched_name})"
            if keyword == self._sender_display_name(event)
            else f"指定用户({matched_name})"
        )
        async for result in self._query_entrant_match(event, entrant_id, source):
            yield result

    async def _resolve_report_entrant(
        self,
        event: AstrMessageEvent,
        client: StartGGClient,
        tournament: TournamentConfig,
        player_name: str = "",
    ) -> Tuple[Optional[int], str, str]:
        """解析报分选手：优先用户名（与查询一致），回退绑定/QQ 映射。"""
        if player_name.strip():
            entrant_id, matched_name, err = await self._find_entrant_by_username(
                client, tournament, player_name.strip()
            )
            if err:
                return None, "", err
            assert entrant_id is not None and matched_name is not None
            return entrant_id, f"指定选手({matched_name})", ""

        keyword = self._sender_display_name(event)
        if keyword:
            entrant_id, matched_name, err = await self._find_entrant_by_username(
                client, tournament, keyword
            )
            if entrant_id and matched_name:
                return entrant_id, f"本人({matched_name})", ""
            if err and "未在当前赛事中找到" not in err:
                return None, "", err

        entrant_id, source = self._resolve_entrant_id(event)
        if entrant_id:
            return entrant_id, source, ""
        return (
            None,
            "",
            "无法解析选手。请使用与 start.gg 一致的昵称报分，或 @机器人 绑定 <entrantId>。",
        )

    def _is_report_enabled(self, tournament_code: Optional[str]) -> bool:
        switch_data = self.store.get_report_switch()
        global_enabled = bool(switch_data.get("globalEnabled", self.report_enabled_default))
        if self.report_switch_scope == "global":
            return global_enabled
        if not tournament_code:
            return global_enabled
        mapping = switch_data.get("tournamentEnabledMap", {})
        if tournament_code in mapping:
            return bool(mapping[tournament_code])
        return global_enabled

    def _set_report_switch(
        self, enabled: bool, operator: str, tournament_code: Optional[str]
    ) -> None:
        payload = self.store.get_report_switch()
        payload.setdefault("tournamentEnabledMap", {})
        if self.report_switch_scope == "global" or not tournament_code:
            payload["globalEnabled"] = enabled
        else:
            payload["tournamentEnabledMap"][tournament_code] = enabled
        payload["updatedAt"] = _now_ts()
        payload["updatedBy"] = operator
        self.store.save_report_switch(payload)

    def _check_report_cooldown(self, sender_id: str, set_id: int) -> bool:
        key = f"{sender_id}:{set_id}"
        now = _now_ts()
        last = self.report_cooldown.get(key, 0)
        if now - last < self.report_cooldown_sec:
            return False
        self.report_cooldown[key] = now
        return True

    def _parse_score(self, score: str) -> Optional[Tuple[int, int]]:
        cleaned = score.strip().replace("：", ":").replace("-", ":")
        chunks = cleaned.split(":")
        if len(chunks) != 2:
            return None
        a = _safe_int(chunks[0])
        b = _safe_int(chunks[1])
        if a is None or b is None:
            return None
        if a < 0 or b < 0:
            return None
        if max(a, b) > 5:
            return None
        if a == 0 and b == 0:
            return None
        return a, b

    def _build_set_game_data(
        self, entrant_ids: List[int], entrant1_wins: int, entrant2_wins: int
    ) -> List[Dict[str, Any]]:
        """将局分（如 2-1）展开为 start.gg gameData（按 slot 顺序的 entrant1/entrant2）。"""
        game_data: List[Dict[str, Any]] = []
        game_num = 1
        for _ in range(entrant1_wins):
            game_data.append(
                {
                    "gameNum": game_num,
                    "winnerId": _gql_id(entrant_ids[0]),
                    "entrant1Score": 1,
                    "entrant2Score": 0,
                }
            )
            game_num += 1
        for _ in range(entrant2_wins):
            game_data.append(
                {
                    "gameNum": game_num,
                    "winnerId": _gql_id(entrant_ids[1]),
                    "entrant1Score": 0,
                    "entrant2Score": 1,
                }
            )
            game_num += 1
        return game_data

    def _parse_report_args(
        self, event: AstrMessageEvent, arg_str: str
    ) -> Tuple[Optional[Dict[str, str]], str]:
        """解析报分参数。比分格式为 <选手局数>-<对手局数>，胜者为较大一方。"""
        usage = (
            "用法：@机器人 报分 <选手局数>-<对手局数> [setId]\n"
            "管理员代报：@机器人 报分 <选手名> <选手局数>-<对手局数> [setId]"
        )
        parts = self._split_args(arg_str)
        if not parts:
            return None, usage

        score_idx: Optional[int] = None
        for i, part in enumerate(parts):
            if self._parse_score(part):
                score_idx = i
                break

        if score_idx is None:
            return None, "比分格式错误，示例：@机器人 报分 2-1"

        score = parts[score_idx]
        set_id = ""
        if len(parts) > score_idx + 1:
            tail = parts[score_idx + 1]
            if _safe_int(tail):
                set_id = tail
            else:
                return None, f"未知参数「{tail}」。{usage}"

        if score_idx == 0:
            return {
                "player_name": "",
                "score": score,
                "set_id": set_id,
            }, ""

        player_name = " ".join(parts[:score_idx]).strip()
        if not self._is_admin(event):
            return None, "非管理员不能以选手名代报。请使用：@机器人 报分 <选手局数>-<对手局数> [setId]"
        if not player_name:
            return None, usage

        return {
            "player_name": player_name,
            "score": score,
            "set_id": set_id,
        }, ""

    def _bot_self_id(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_self_id", None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                pass
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is not None:
            return str(getattr(msg_obj, "self_id", "") or "")
        return ""

    def _is_group_message(self, event: AstrMessageEvent) -> bool:
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is not None:
            group_id = getattr(msg_obj, "group_id", "") or ""
            if group_id:
                return True
        getter = getattr(event, "get_group_id", None)
        if callable(getter):
            try:
                return bool(getter())
            except Exception:
                pass
        return False

    def _is_at_bot(self, event: AstrMessageEvent) -> bool:
        if bool(getattr(event, "is_at_or_wake_command", False)):
            return True
        self_id = self._bot_self_id(event)
        if not self_id:
            return False
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is None:
            return False
        for seg in getattr(msg_obj, "message", []) or []:
            if isinstance(seg, Comp.At):
                at_id = str(
                    getattr(seg, "qq", "")
                    or getattr(seg, "id", "")
                    or getattr(seg, "user_id", "")
                    or ""
                )
                if at_id == self_id:
                    return True
        return False

    def _should_handle_mention_command(self, event: AstrMessageEvent) -> bool:
        text = self._extract_command_text(event)
        if self._is_group_message(event):
            # 群聊必须 @ 机器人
            return self._is_at_bot(event)
        # 私聊：仅拦截已知指令，避免影响普通对话
        if not text:
            return False
        cmd, _ = self._match_command(text)
        return cmd is not None

    def _extract_command_text(self, event: AstrMessageEvent) -> str:
        text = (getattr(event, "message_str", None) or "").strip()
        if text.startswith("/"):
            text = text.lstrip("/").strip()
        return text

    def _split_args(self, arg_str: str) -> List[str]:
        return [part for part in arg_str.strip().split() if part]

    def _match_command(self, text: str) -> Tuple[Optional[str], str]:
        normalized = text.strip()
        if not normalized:
            return None, ""
        for cmd in MENTION_COMMANDS:
            if normalized == cmd:
                return cmd, ""
            if normalized.startswith(cmd + " "):
                return cmd, normalized[len(cmd) :].strip()
        return None, normalized

    def _set_contains_entrant(
        self, slots: List[Dict[str, Any]], entrant_id: int
    ) -> bool:
        for slot in slots:
            entrant = slot.get("entrant") or {}
            if _safe_int(entrant.get("id")) == entrant_id:
                return True
        return False

    def _slot_score_value(self, slot: Dict[str, Any]) -> Optional[int]:
        score = ((slot.get("standing") or {}).get("stats") or {}).get("score") or {}
        return _safe_int(score.get("value"))

    async def _fetch_entrant_sets(
        self,
        client: StartGGClient,
        event_id: int,
        entrant_id: int,
        include_scores: bool = False,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """拉取某 event 下指定 entrant 的全部 sets。"""
        standing_fields = (
            """
                standing {
                  stats { score { value label } }
                }
            """
            if include_scores
            else ""
        )
        set_node_fields = f"""
                id
                fullRoundText
                state
                slots {{
                  entrant {{
                    id
                    name
                  }}
                  {standing_fields}
                }}
        """
        event_query = f"""
        query EventEntrantSets(
          $eventId: ID!
          $entrantId: ID!
          $page: Int!
          $perPage: Int!
        ) {{
          event(id: $eventId) {{
            sets(
              page: $page
              perPage: $perPage
              sortType: STANDARD
              filters: {{ entrantIds: [$entrantId] }}
            ) {{
              nodes {{
                {set_node_fields}
              }}
            }}
          }}
        }}
        """
        entrant_query = f"""
        query EntrantPaginatedSets($entrantId: ID!, $page: Int!, $perPage: Int!) {{
          entrant(id: $entrantId) {{
            paginatedSets(
              page: $page
              perPage: $perPage
              sortType: STANDARD
            ) {{
              nodes {{
                {set_node_fields}
              }}
            }}
          }}
        }}
        """

        async def _paginate(
            query: str, variables: Dict[str, Any], extract_nodes
        ) -> Tuple[List[Dict[str, Any]], str]:
            collected: List[Dict[str, Any]] = []
            last_error = ""
            for page in range(1, 11):
                resp = await client.execute(query, {**variables, "page": page, "perPage": 50})
                gql_errs = StartGGClient.graphql_errors(resp)
                if gql_errs:
                    last_error = "; ".join(gql_errs)
                nodes = extract_nodes(resp) or []
                if not nodes:
                    break
                collected.extend(nodes)
                if len(nodes) < 50:
                    break
            return collected, last_error

        nodes, err = await _paginate(
            event_query,
            {"eventId": _gql_id(event_id), "entrantId": _gql_id(entrant_id)},
            lambda resp: (
                (
                    (((resp.get("data") or {}).get("event") or {}).get("sets") or {}).get(
                        "nodes"
                    )
                )
            ),
        )
        if nodes:
            return nodes, ""

        fallback_nodes, fallback_err = await _paginate(
            entrant_query,
            {"entrantId": _gql_id(entrant_id)},
            lambda resp: (
                (
                    (
                        ((resp.get("data") or {}).get("entrant") or {}).get(
                            "paginatedSets"
                        )
                        or {}
                    ).get("nodes")
                )
            ),
        )
        return fallback_nodes, fallback_err or err

    def _pick_entrant_set(
        self, nodes: List[Dict[str, Any]], entrant_id: int, pick: str
    ) -> Optional[Dict[str, Any]]:
        matched = [
            n
            for n in nodes
            if self._set_contains_entrant(n.get("slots") or [], entrant_id)
        ]
        # 已按 entrantIds 过滤时，部分 set 的 slots 可能尚未写入 entrant 对象
        if not matched and nodes:
            matched = list(nodes)
        if not matched:
            return None
        if pick == "latest":
            return max(matched, key=lambda x: _safe_int(x.get("id")) or 0)
        active_only = [
            n for n in matched if (_safe_int(n.get("state")) or 0) in (1, 2)
        ]
        pool = active_only or matched
        pool.sort(
            key=lambda x: (
                -(_safe_int(x.get("state")) or 0),
                -(_safe_int(x.get("id")) or 0),
            )
        )
        return pool[0]

    def _slot_entrant_ids(self, slots: List[Dict[str, Any]]) -> List[int]:
        ids: List[int] = []
        for slot in slots:
            eid = _safe_int((slot.get("entrant") or {}).get("id"))
            if eid:
                ids.append(eid)
        return ids

    def _entrant_name_from_slots(
        self, slots: List[Dict[str, Any]], entrant_id: int
    ) -> str:
        for slot in slots:
            entrant = slot.get("entrant") or {}
            if _safe_int(entrant.get("id")) == entrant_id:
                return str(entrant.get("name") or "").strip()
        return ""

    def _is_set_reportable(self, node: Dict[str, Any], entrant_id: int) -> bool:
        """可报分：双方选手已就位且对局未结束（排除晋级后仅一人入位的等待局）。"""
        state = _safe_int(node.get("state")) or 0
        if state not in (1, 2):
            return False
        slots = node.get("slots") or []
        entrant_ids = self._slot_entrant_ids(slots)
        if entrant_id not in entrant_ids or len(entrant_ids) < 2:
            return False
        if bool(node.get("hasPlaceholder")):
            return False
        return True

    def _pick_reportable_set(
        self, nodes: List[Dict[str, Any]], entrant_id: int
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        if not nodes:
            return None, "empty"

        reportable = [n for n in nodes if self._is_set_reportable(n, entrant_id)]
        if reportable:
            reportable.sort(
                key=lambda x: (
                    0 if (_safe_int(x.get("state")) or 0) == 2 else 1,
                    -(_safe_int(x.get("id")) or 0),
                )
            )
            return reportable[0], ""

        latest = self._pick_entrant_set(nodes, entrant_id, pick="latest")
        if latest:
            latest_state = _safe_int(latest.get("state")) or 0
            latest_id = _safe_int(latest.get("id")) or 0
            slot_ids = self._slot_entrant_ids(latest.get("slots") or [])
            if latest_state in (1, 2) and len(slot_ids) < 2:
                return (
                    None,
                    f"waiting:{latest_id}:{latest.get('fullRoundText') or ''}",
                )
            if latest_state == 3:
                return None, f"completed:{latest_id}"

        return None, "none"

    def _parse_entrant_set_result(
        self, target: Dict[str, Any], entrant_id: int
    ) -> Dict[str, Any]:
        slots = target.get("slots") or []
        opponent = "未知对手"
        my_score: Optional[int] = None
        opp_score: Optional[int] = None
        for slot in slots:
            entrant = slot.get("entrant") or {}
            eid = _safe_int(entrant.get("id"))
            score = self._slot_score_value(slot)
            if eid == entrant_id:
                my_score = score
            elif eid:
                if entrant.get("name"):
                    opponent = str(entrant.get("name"))
                opp_score = score

        state = _safe_int(target.get("state")) or 0
        result: Dict[str, Any] = {
            "ok": True,
            "setId": _safe_int(target.get("id")),
            "round": target.get("fullRoundText") or "未知轮次",
            "state": state,
            "opponent": opponent,
        }
        if state == 3 and my_score is not None and opp_score is not None:
            result["scoreText"] = f"{my_score} - {opp_score}"
        return result

    async def _query_entrant_latest_set(
        self, client: StartGGClient, tournament: TournamentConfig, entrant_id: int
    ) -> Dict[str, Any]:
        nodes, fetch_err = await self._fetch_entrant_sets(
            client, tournament.event_id, entrant_id, include_scores=True
        )
        target = self._pick_entrant_set(nodes, entrant_id, pick="latest")
        if not target:
            msg = (
                f"当前赛事(eventId={tournament.event_id})没有检索到你的对局，"
                "请确认已切换至正确赛事。"
            )
            if fetch_err:
                msg += f"\nAPI: {fetch_err}"
            elif not nodes:
                msg += "\n该选手在 bracket 中暂无已生成对局（可能尚未放签或轮空）。"
            return {"ok": False, "msg": msg}
        return self._parse_entrant_set_result(target, entrant_id)

    async def _query_reportable_set(
        self, client: StartGGClient, tournament: TournamentConfig, entrant_id: int
    ) -> Dict[str, Any]:
        nodes, fetch_err = await self._fetch_entrant_sets(
            client, tournament.event_id, entrant_id, include_scores=False
        )
        target, hint = self._pick_reportable_set(nodes, entrant_id)
        if not target:
            msg = (
                f"当前赛事(eventId={tournament.event_id})没有检索到可报分的对局，"
                "请确认已切换至正确赛事。"
            )
            if fetch_err:
                msg += f"\nAPI: {fetch_err}"
            elif not nodes:
                msg += "\n该选手在 bracket 中暂无已生成对局（可能尚未放签或轮空）。"
            elif hint.startswith("waiting:"):
                parts = hint.split(":", 2)
                set_id = parts[1] if len(parts) > 1 else ""
                round_text = parts[2] if len(parts) > 2 else ""
                msg += (
                    f"\n你最近一场为等待对手/轮空的晋级位"
                    f"（#{set_id} {round_text}），请对已打完且双方到位的对局报分；"
                    f"若查询里能看到上一场 setId，请使用：@机器人 报分 <选手局数>-<对手局数> <setId>"
                )
            elif hint.startswith("completed:"):
                set_id = hint.split(":", 1)[-1]
                msg += (
                    f"\n最近一场对局（#{set_id}）在 start.gg 上已标记为结束；"
                    "补报请显式填写 setId。"
                )
            return {"ok": False, "msg": msg}
        return self._parse_entrant_set_result(target, entrant_id)

    async def _report_set(
        self,
        client: StartGGClient,
        set_id: int,
        winner_id: int,
        game_data: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if game_data:
            mutation = """
            mutation reportSet(
              $setId: ID!
              $winnerId: ID!
              $gameData: [BracketSetGameDataInput]
            ) {
              reportBracketSet(
                setId: $setId
                winnerId: $winnerId
                gameData: $gameData
              ) {
                id
                state
              }
            }
            """
            variables: Dict[str, Any] = {
                "setId": _gql_id(set_id),
                "winnerId": _gql_id(winner_id),
                "gameData": game_data,
            }
        else:
            mutation = """
            mutation reportSet($setId: ID!, $winnerId: ID!) {
              reportBracketSet(setId: $setId, winnerId: $winnerId) {
                id
                state
              }
            }
            """
            variables = {
                "setId": _gql_id(set_id),
                "winnerId": _gql_id(winner_id),
            }
        return await client.execute(mutation, variables)

    async def _query_set_detail(self, client: StartGGClient, set_id: int) -> Dict[str, Any]:
        query = """
        query SetDetail($setId: ID!) {
          set(id: $setId) {
            id
            state
            fullRoundText
            winnerId
            hasPlaceholder
            slots {
              entrant { id name }
              standing {
                stats { score { value label } }
              }
            }
          }
        }
        """
        return await client.execute(query, {"setId": _gql_id(set_id)})

    async def _query_event_info(
        self, client: StartGGClient, event_id: int
    ) -> Optional[EventBindingInfo]:
        query = """
        query EventInfo($eventId: ID!) {
          event(id: $eventId) {
            id
            name
            tournament {
              id
              name
            }
          }
        }
        """
        resp = await client.execute(query, {"eventId": _gql_id(event_id)})
        event_data = ((resp.get("data") or {}).get("event")) or {}
        eid = _safe_int(event_data.get("id"))
        if not eid:
            return None
        event_name = str(event_data.get("name") or "").strip() or "未命名项目"
        tournament = event_data.get("tournament") or {}
        tournament_name = str(tournament.get("name") or "").strip() or event_name
        return EventBindingInfo(
            event_id=eid,
            event_name=event_name,
            tournament_name=tournament_name,
        )

    async def _query_event_by_slug(
        self, client: StartGGClient, slug: str
    ) -> Optional[EventBindingInfo]:
        query = """
        query EventBySlug($slug: String) {
          event(slug: $slug) {
            id
            name
            tournament {
              id
              name
            }
          }
        }
        """
        resp = await client.execute(query, {"slug": slug})
        event_data = ((resp.get("data") or {}).get("event")) or {}
        eid = _safe_int(event_data.get("id"))
        if not eid:
            return None
        event_name = str(event_data.get("name") or "").strip() or slug
        tournament = event_data.get("tournament") or {}
        tournament_name = str(tournament.get("name") or "").strip() or event_name
        return EventBindingInfo(
            event_id=eid,
            event_name=event_name,
            tournament_name=tournament_name,
        )

    async def _resolve_event_from_reference(
        self, client: StartGGClient, reference: str
    ) -> Tuple[Optional[EventBindingInfo], str]:
        parsed = _parse_startgg_link(reference)
        if not parsed:
            return None, "无法识别 start.gg 链接、slug 或 eventId。"

        if parsed["type"] == "id":
            try:
                info = await self._query_event_info(client, parsed["event_id"])
            except Exception as e:
                return None, f"查询 event 失败: {e}"
            if not info:
                return None, "未查询到该 eventId 对应赛事。"
            return info, ""

        slug = parsed["slug"]
        try:
            info = await self._query_event_by_slug(client, slug)
        except Exception as e:
            return None, f"通过链接解析 event 失败: {e}"
        if not info:
            return None, "链接无效或无权访问该 event。"
        return info, ""

    def _display_binding_name(self, info: EventBindingInfo) -> str:
        if info.tournament_name and info.tournament_name != info.event_name:
            return f"{info.tournament_name} · {info.event_name}"
        return info.event_name

    def _ensure_tournament_code(self, code: Optional[str], tournament_name: str) -> str:
        """未提供简码时，使用 start.gg tournament 名称作为简码；重名时自动追加后缀。"""
        if code and code.strip():
            return code.strip()
        base = (tournament_name or "").strip() or "event"
        sanitized = re.sub(r"\s+", "_", base).strip("_")
        if not sanitized:
            sanitized = "event"
        candidate = sanitized
        suffix = 2
        while candidate in self.tournaments:
            candidate = f"{sanitized}_{suffix}"
            suffix += 1
        return candidate

    def _save_tournament_binding(
        self,
        event: AstrMessageEvent,
        code: str,
        name: str,
        event_id: int,
        phase_group_id: Optional[int],
        audit_kind: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.tournaments[code] = TournamentConfig(
            code=code,
            name=name.strip() or code,
            event_id=event_id,
            phase_group_id=phase_group_id,
        )
        self._save_tournaments()
        payload: Dict[str, Any] = {
            "kind": audit_kind,
            "operator": self._sender_id(event),
            "code": code,
            "name": name,
            "eventId": event_id,
            "phaseGroupId": phase_group_id,
            "at": _now_ts(),
        }
        if extra:
            payload.update(extra)
        self.store.append_audit(payload)

    async def _query_entrant_match(
        self, event: AstrMessageEvent, entrant_id: int, source: str
    ):
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return
        tournament_code = self._current_tournament_code(event)
        if not tournament_code or tournament_code not in self.tournaments:
            yield event.plain_result("未找到可用赛事，请先由管理员绑定赛事并切换。")
            return

        client = StartGGClient(self.token)
        try:
            result = await self._query_entrant_latest_set(
                client, self.tournaments[tournament_code], entrant_id
            )
        except Exception as e:
            yield event.plain_result(f"查询失败: {e}")
            return

        if not result.get("ok"):
            yield event.plain_result(result.get("msg", "查询失败"))
            return

        state_map = {1: "未开始", 2: "进行中", 3: "已结束"}
        msg = (
            f"赛事: {tournament_code}\n"
            f"查询对象: {source}\n"
            f"对局: #{result['setId']} {result['round']}\n"
            f"对手: {result['opponent']}\n"
            f"状态: {state_map.get(result['state'], str(result['state']))}"
        )
        if result.get("scoreText"):
            msg += f"\n比分: {result['scoreText']}"
        yield event.plain_result(msg)

    def _merge_tournament_nodes(
        self, user_id: int, *sources: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        merged: Dict[int, Dict[str, Any]] = {}
        for nodes in sources:
            for tour in nodes or []:
                tid = _safe_int(tour.get("id"))
                if not tid:
                    continue
                owner_id = _safe_int(((tour.get("owner") or {}).get("id")))
                if tid not in merged:
                    merged[tid] = dict(tour)
                    merged[tid]["_roles"] = set()
                else:
                    existing_events = {
                        _safe_int(e.get("id"))
                        for e in (merged[tid].get("events") or [])
                        if _safe_int(e.get("id"))
                    }
                    for ev in tour.get("events") or []:
                        eid = _safe_int(ev.get("id"))
                        if eid and eid not in existing_events:
                            merged[tid].setdefault("events", []).append(ev)
                if owner_id == user_id:
                    merged[tid]["_roles"].add("创建")
                else:
                    merged[tid]["_roles"].add("管理")

        result = list(merged.values())
        for tour in result:
            roles = tour.pop("_roles", set())
            if not roles:
                roles = {"管理"}
            tour["_role_label"] = "/".join(sorted(roles))
        result.sort(key=lambda x: str(x.get("name") or ""))
        return result

    async def _query_managed_tournaments(self, client: StartGGClient) -> Dict[str, Any]:
        """查询 API 账号创建或管理的赛事（合并 currentUser.tournaments 与 owner 列表）。"""
        user_resp = await client.execute(
            """
            query CurrentUserManagedTournaments($perPage: Int!) {
              currentUser {
                id
                slug
                tournaments(query: { perPage: $perPage, page: 1 }) {
                  nodes {
                    id
                    name
                    slug
                    owner { id }
                    events { id name }
                  }
                }
              }
            }
            """,
            {"perPage": 50},
        )
        current_user = ((user_resp.get("data") or {}).get("currentUser")) or {}
        user_id = _safe_int(current_user.get("id"))
        if not user_id:
            return {"ok": False, "msg": "无法获取当前 API 账号信息，请检查 token 是否有效。"}

        managed_nodes = (
            ((current_user.get("tournaments") or {}).get("nodes")) or []
        )

        owner_resp = await client.execute(
            """
            query TournamentsByOwner($ownerId: ID!, $perPage: Int!) {
              tournaments(query: {
                perPage: $perPage
                filter: { ownerId: $ownerId }
              }) {
                nodes {
                  id
                  name
                  slug
                  owner { id }
                  events { id name }
                }
              }
            }
            """,
            {"ownerId": user_id, "perPage": 50},
        )
        owned_nodes = (
            (((owner_resp.get("data") or {}).get("tournaments") or {}).get("nodes")) or []
        )

        nodes = self._merge_tournament_nodes(user_id, managed_nodes, owned_nodes)
        return {
            "ok": True,
            "userId": user_id,
            "userSlug": current_user.get("slug"),
            "tournaments": nodes,
        }

    def _event_id_to_local_code(self) -> Dict[int, str]:
        mapping: Dict[int, str] = {}
        for code, cfg in self.tournaments.items():
            mapping[cfg.event_id] = code
        return mapping

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def on_mention_command(self, event: AstrMessageEvent):
        """@机器人后执行 start.gg 指令（群聊）；私聊可直接发指令"""
        if not self._should_handle_mention_command(event):
            return

        text = self._extract_command_text(event)
        if not text:
            if self._is_group_message(event) and self._is_at_bot(event):
                async for result in self._handle_help(event, ""):
                    yield result
                event.stop_event()
            return

        cmd, arg_str = self._match_command(text)
        if not cmd:
            yield event.plain_result("未知指令，请发送：@机器人 帮助")
            event.stop_event()
            return

        handlers = {
            "帮助": self._handle_help,
            "help": self._handle_help,
            "赛事列表": self._handle_tournament_list,
            "我的赛事": self._handle_my_owned_tournaments,
            "切换": self._handle_switch_tournament,
            "绑定赛事全参": self._handle_bind_tournament_full,
            "绑定赛事链接": self._handle_bind_tournament_link,
            "绑定赛事": self._handle_bind_tournament_auto,
            "解绑赛事": self._handle_unbind_tournament,
            "赛事详情": self._handle_tournament_detail,
            "绑定": self._handle_bind_player,
            "我在哪": self._handle_my_match,
            "查询": self._handle_query_match,
            "set": self._handle_set_detail,
            "报分状态": self._handle_report_status,
            "报分开关": self._handle_report_switch,
            "报分": self._handle_report,
            "添加管理员": self._handle_add_admin,
        }
        handler = handlers.get(cmd)
        if not handler:
            yield event.plain_result("未知指令，请发送：@机器人 帮助")
            event.stop_event()
            return

        async for result in handler(event, arg_str):
            yield result
        event.stop_event()

    async def _handle_help(self, event: AstrMessageEvent, _args: str):
        msg = (
            "用法（群聊请 @ 我）：\n"
            "@机器人 帮助\n"
            "@机器人 赛事列表\n"
            "@机器人 我的赛事（管理员，查询 API 账号创建/管理的赛事与简码）\n"
            "@机器人 绑定赛事 [简码] <eventId|链接> [phaseGroupId]（省略简码时用 tournament 名）\n"
            "@机器人 绑定赛事链接 [简码] <start.gg链接> [phaseGroupId]\n"
            "@机器人 绑定赛事全参 [简码] <名称> <eventId> [phaseGroupId]\n"
            "@机器人 解绑赛事 <简码>\n"
            "@机器人 赛事详情 <简码>\n"
            "@机器人 切换 <赛事简码>\n"
            "@机器人 绑定 <entrantId>\n"
            "@机器人 我在哪\n"
            "@机器人 查询 [用户名]（最近一场对局；已结束显示比分）\n"
            "@机器人 set <setId>\n"
            "@机器人 报分 <选手局数>-<对手局数> [setId]（前者为报分选手）\n"
            "@机器人 报分 <选手名> <选手局数>-<对手局数> [setId]（管理员代报）\n"
            "@机器人 报分开关 <on|off> [赛事简码]\n"
            "@机器人 报分状态 [赛事简码]\n"
            "@机器人 添加管理员 @用户（管理员，写入动态管理员列表）"
        )
        yield event.plain_result(msg)

    async def _handle_add_admin(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可添加管理员。")
            return

        targets = self._extract_at_qq_ids(event)
        if not targets:
            parts = self._split_args(args)
            targets = [p for p in parts if p.isdigit()]

        if not targets:
            yield event.plain_result(
                "请 @ 要添加的用户。\n"
                "示例：@机器人 添加管理员 @某人\n"
                "也可：@机器人 添加管理员 123456789"
            )
            return

        operator = self._sender_id(event)
        to_add: List[str] = []
        skipped: List[str] = []
        for qq in targets:
            if qq in self.config_admin_qq_ids:
                skipped.append(f"{qq}(已在插件配置中)")
                continue
            if qq in self.dynamic_admin_qq_ids:
                skipped.append(f"{qq}(已是管理员)")
                continue
            to_add.append(qq)

        added = self.store.add_dynamic_admin_qq_ids(to_add, operator)
        if added:
            self._reload_dynamic_admin_qq_ids()
            self.store.append_audit(
                {
                    "kind": "add_admin",
                    "operator": operator,
                    "added": added,
                    "at": _now_ts(),
                }
            )

        lines = []
        if added:
            lines.append(f"已添加管理员：{', '.join(added)}")
        if skipped:
            lines.append(f"已跳过：{', '.join(skipped)}")
        if not added and not skipped:
            lines.append("没有可添加的 QQ 号。")
        yield event.plain_result("\n".join(lines))

    async def _handle_tournament_list(self, event: AstrMessageEvent, _args: str):
        if not self.tournaments:
            yield event.plain_result("未绑定赛事，请管理员先执行：@机器人 绑定赛事 ...")
            return
        current = self._current_tournament_code(event)
        lines = ["已配置赛事:"]
        for code, t in self.tournaments.items():
            mark = " (当前)" if code == current else ""
            lines.append(f"- {code}: {t.name}{mark}")
        yield event.plain_result("\n".join(lines))

    async def _handle_my_owned_tournaments(self, event: AstrMessageEvent, _args: str):
        """管理员：查询当前 start.gg API 账号创建/管理的赛事及 bot 简码"""
        if not self._is_admin(event):
            yield event.plain_result("仅管理员或群主可查询我的赛事。")
            return
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return

        client = StartGGClient(self.token)
        try:
            result = await self._query_managed_tournaments(client)
        except Exception as e:
            yield event.plain_result(f"查询失败: {e}")
            return

        if not result.get("ok"):
            yield event.plain_result(result.get("msg", "查询失败"))
            return

        nodes = result.get("tournaments") or []
        if not nodes:
            yield event.plain_result(
                "当前 API 账号下没有查询到可管理的赛事。"
                "请确认 token 具备 tournament.manager 等权限。"
            )
            return

        event_to_code = self._event_id_to_local_code()
        lines = [
            "【我的赛事】（start.gg API 账号创建/管理）",
            f"账号 ID: {result.get('userId')}",
        ]
        user_slug = result.get("userSlug")
        if user_slug:
            lines.append(f"账号 slug: {user_slug}")

        for idx, tour in enumerate(nodes, start=1):
            tour_name = tour.get("name") or "未命名赛事"
            tour_id = tour.get("id")
            role_label = tour.get("_role_label") or "管理"
            lines.append(
                f"\n{idx}. {tour_name} (tournamentId: {tour_id}, 角色: {role_label})"
            )

            events = tour.get("events") or []
            if not events:
                lines.append("   简码: 未绑定（该赛事下无 event 或无法读取）")
                continue

            for ev in events:
                ev_id = _safe_int(ev.get("id"))
                ev_name = ev.get("name") or "未命名项目"
                code = event_to_code.get(ev_id) if ev_id else None
                if code:
                    lines.append(f"   - {ev_name} (eventId: {ev_id}) → 简码: {code}")
                else:
                    lines.append(f"   - {ev_name} (eventId: {ev_id}) → 简码: 未绑定")

        yield event.plain_result("\n".join(lines))

    async def _handle_switch_tournament(self, event: AstrMessageEvent, args: str):
        parts = self._split_args(args)
        if not parts:
            yield event.plain_result("用法：@机器人 切换 <赛事简码>")
            return
        code = parts[0].strip()
        self._sync_tournaments_from_store()
        if code not in self.tournaments:
            yield event.plain_result(f"未知赛事简码: {code}")
            return
        self._set_session_tournament(event, code)
        t = self.tournaments[code]
        yield event.plain_result(
            f"已切换到赛事: {code} ({t.name})\neventId: {t.event_id}"
        )

    def _is_link_or_event_ref(self, text: str) -> bool:
        return bool(_parse_startgg_link(text)) or text.startswith("http") or "start.gg" in text

    def _parse_bind_link_args(
        self, parts: List[str]
    ) -> Tuple[Optional[str], str, Optional[int], str]:
        """解析绑定链接参数，返回 (简码, 链接, phaseGroupId, 错误信息)。"""
        if not parts:
            return None, "", None, "缺少 start.gg 链接。"

        pgid: Optional[int] = None
        body = parts[:]
        if len(body) >= 2 and body[-1].isdigit():
            candidate_link = (
                " ".join(body[1:-1]).strip()
                if body[0] and not self._is_link_or_event_ref(body[0])
                else " ".join(body[:-1]).strip()
            )
            if self._is_link_or_event_ref(candidate_link):
                pgid = _safe_int(body[-1])
                body = body[:-1]

        if self._is_link_or_event_ref(body[0]):
            link = " ".join(body).strip()
            return None, link, pgid, ""

        if len(body) < 2:
            return (
                body[0].strip() or None,
                "",
                pgid,
                "请提供 start.gg 链接。",
            )
        code = body[0].strip() or None
        link = " ".join(body[1:]).strip()
        if not self._is_link_or_event_ref(link):
            return code, "", pgid, "第二个参数应为 start.gg 链接。"
        return code, link, pgid, ""

    async def _handle_bind_tournament_full(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可绑定赛事。")
            return
        parts = self._split_args(args)
        if len(parts) < 2:
            yield event.plain_result(
                "用法：@机器人 绑定赛事全参 [简码] <名称> <eventId> [phaseGroupId]"
            )
            return
        if len(parts) >= 3:
            code_raw, name, event_id = parts[0], parts[1], parts[2]
            phase_group_id = parts[3] if len(parts) > 3 else ""
        else:
            code_raw, name, event_id = "", parts[0], parts[1]
            phase_group_id = parts[2] if len(parts) > 2 else ""
        eid = _safe_int(event_id)
        if not eid:
            yield event.plain_result("eventId 必须是数字。")
            return
        pgid = _safe_int(phase_group_id)
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return
        client = StartGGClient(self.token)
        try:
            info = await self._query_event_info(client, eid)
        except Exception as e:
            yield event.plain_result(f"查询赛事信息失败: {e}")
            return
        if not info:
            yield event.plain_result("未查询到该 event 信息。")
            return
        display_name = name.strip() or self._display_binding_name(info)
        code = self._ensure_tournament_code(code_raw or None, info.tournament_name)
        self._save_tournament_binding(
            event, code, display_name, eid, pgid, "bind_tournament", None
        )
        yield event.plain_result(
            f"赛事绑定成功: {code} ({display_name})\neventId={eid}"
            + (f"\nphaseGroupId={pgid}" if pgid else "")
        )

    async def _handle_bind_tournament_link(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可绑定赛事。")
            return
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return
        parts = self._split_args(args)
        code_raw, link, pgid, parse_err = self._parse_bind_link_args(parts)
        if parse_err:
            yield event.plain_result(
                parse_err
                + "\n用法：@机器人 绑定赛事链接 [简码] <start.gg链接> [phaseGroupId]"
            )
            return

        client = StartGGClient(self.token)
        info, err = await self._resolve_event_from_reference(client, link)
        if err:
            yield event.plain_result(err)
            return
        assert info is not None

        display_name = self._display_binding_name(info)
        code = self._ensure_tournament_code(code_raw, info.tournament_name)
        self._save_tournament_binding(
            event,
            code,
            display_name,
            info.event_id,
            pgid,
            "bind_tournament_link",
            {"link": link},
        )
        yield event.plain_result(
            f"赛事绑定成功: {code} ({display_name})\neventId={info.event_id}"
            + (f"\nphaseGroupId={pgid}" if pgid else "")
            + f"\n来源链接: {link}"
        )

    async def _handle_bind_tournament_auto(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可绑定赛事。")
            return
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return
        parts = self._split_args(args)
        if len(parts) < 1:
            yield event.plain_result(
                "用法：@机器人 绑定赛事 [简码] <eventId|链接> [phaseGroupId]"
            )
            return

        code_raw: Optional[str] = None
        pgid: Optional[int] = None
        if len(parts) == 1:
            reference = parts[0]
        elif self._is_link_or_event_ref(parts[0]):
            reference = " ".join(parts).strip()
        else:
            code_raw = parts[0].strip() or None
            reference = parts[1]
            if len(parts) > 2:
                pgid = _safe_int(parts[2])

        client = StartGGClient(self.token)

        if _parse_startgg_link(reference):
            info, err = await self._resolve_event_from_reference(client, reference)
            if err:
                yield event.plain_result(err)
                return
            assert info is not None
            audit_kind = "bind_tournament_link"
            extra = {"link": reference}
        else:
            eid = _safe_int(reference)
            if not eid:
                yield event.plain_result("eventId 必须是数字，或提供 start.gg 赛事链接。")
                return
            try:
                info = await self._query_event_info(client, eid)
            except Exception as e:
                yield event.plain_result(f"拉取赛事信息失败: {e}")
                return
            if not info:
                yield event.plain_result(
                    "未查询到该 eventId 对应赛事，请检查 eventId 或 token 权限。"
                )
                return
            audit_kind = "bind_tournament_auto"
            extra = None

        display_name = self._display_binding_name(info)
        code = self._ensure_tournament_code(code_raw, info.tournament_name)
        self._save_tournament_binding(
            event, code, display_name, info.event_id, pgid, audit_kind, extra
        )
        yield event.plain_result(
            f"赛事绑定成功: {code} ({display_name})\neventId={info.event_id}"
            + (f"\nphaseGroupId={pgid}" if pgid else "")
        )

    async def _handle_unbind_tournament(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可解绑赛事。")
            return
        parts = self._split_args(args)
        if not parts:
            yield event.plain_result("用法：@机器人 解绑赛事 <简码>")
            return
        code = parts[0].strip()
        if code not in self.tournaments:
            yield event.plain_result(f"赛事不存在: {code}")
            return
        self.tournaments.pop(code, None)
        self._save_tournaments()
        session = self._session_key(event)
        if self.session_tournament.get(session) == code:
            self.session_tournament.pop(session, None)
        self.store.append_audit(
            {
                "kind": "unbind_tournament",
                "operator": self._sender_id(event),
                "code": code,
                "at": _now_ts(),
            }
        )
        yield event.plain_result(f"已解绑赛事: {code}")

    async def _handle_tournament_detail(self, event: AstrMessageEvent, args: str):
        parts = self._split_args(args)
        if not parts:
            yield event.plain_result("用法：@机器人 赛事详情 <简码>")
            return
        code = parts[0].strip()
        t = self.tournaments.get(code)
        if not t:
            yield event.plain_result(f"赛事不存在: {code}")
            return
        yield event.plain_result(
            f"赛事: {t.code} ({t.name})\neventId: {t.event_id}\nphaseGroupId: {t.phase_group_id}"
        )

    async def _handle_bind_player(self, event: AstrMessageEvent, args: str):
        parts = self._split_args(args)
        if not parts:
            yield event.plain_result("用法：@机器人 绑定 <entrantId>")
            return
        target = parts[0]
        entrant_id = _safe_int(target)
        if not entrant_id:
            yield event.plain_result(
                "当前版本优先使用 entrantId 绑定。示例：@机器人 绑定 1234567"
            )
            return

        sender = self._sender_id(event)
        key = f"default:{sender}"
        self.store.set_binding(
            key,
            {
                "entrantId": entrant_id,
                "rawInput": target,
                "updatedAt": _now_ts(),
            },
        )
        yield event.plain_result(
            f"绑定成功。当前账号将使用 entrantId={entrant_id}（优先级高于默认 userId 映射）。"
        )

    async def _handle_my_match(self, event: AstrMessageEvent, _args: str):
        """查询我的对局（等同 查询，按用户名匹配）"""
        async for result in self._resolve_query_target(event, None):
            yield result

    async def _handle_query_match(self, event: AstrMessageEvent, args: str):
        """查询指定用户名的最近一场对局；未提供时按本人聊天昵称匹配"""
        username = " ".join(self._split_args(args)).strip()
        async for result in self._resolve_query_target(event, username or None):
            yield result

    async def _handle_set_detail(self, event: AstrMessageEvent, args: str):
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return
        parts = self._split_args(args)
        if not parts:
            yield event.plain_result("用法：@机器人 set <setId>")
            return
        sid = _safe_int(parts[0])
        if not sid:
            yield event.plain_result("setId 必须是数字。")
            return

        client = StartGGClient(self.token)
        try:
            resp = await self._query_set_detail(client, sid)
        except Exception as e:
            yield event.plain_result(f"查询失败: {e}")
            return
        set_data = ((resp.get("data") or {}).get("set")) or {}
        if not set_data:
            yield event.plain_result("未找到该 set。")
            return

        state_map = {1: "未开始", 2: "进行中", 3: "已结束"}
        lines = [
            f"Set #{set_data.get('id')}",
            f"轮次: {set_data.get('fullRoundText') or '未知'}",
            f"状态: {state_map.get(_safe_int(set_data.get('state')), '未知')}",
        ]
        for idx, slot in enumerate(set_data.get("slots") or [], start=1):
            entrant = slot.get("entrant") or {}
            score = (
                (((slot.get("standing") or {}).get("stats") or {}).get("score") or {}).get(
                    "value"
                )
            )
            lines.append(f"{idx}. {entrant.get('name', '未知')} ({entrant.get('id')}) 分数:{score}")
        yield event.plain_result("\n".join(lines))

    async def _handle_report_status(self, event: AstrMessageEvent, args: str):
        parts = self._split_args(args)
        code = parts[0] if parts else ""
        code = code.strip() or self._current_tournament_code(event) or ""
        enabled = self._is_report_enabled(code)
        scope = "全局" if self.report_switch_scope == "global" else f"赛事({code or '未指定'})"
        yield event.plain_result(
            f"报分状态: {'开启' if enabled else '关闭'}\n作用域: {scope}"
        )

    async def _handle_report_switch(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("仅管理员可操作报分开关。")
            return
        parts = self._split_args(args)
        if not parts:
            yield event.plain_result("用法：@机器人 报分开关 <on|off> [赛事简码]")
            return
        switch = parts[0].strip().lower()
        if switch not in {"on", "off"}:
            yield event.plain_result("参数错误，请使用：@机器人 报分开关 <on|off> [赛事简码]")
            return
        t_code = parts[1].strip() if len(parts) > 1 else ""
        t_code = t_code or self._current_tournament_code(event) or ""
        if self.report_switch_scope != "global" and t_code and t_code not in self.tournaments:
            yield event.plain_result(f"未知赛事简码: {t_code}")
            return
        self._set_report_switch(
            enabled=(switch == "on"),
            operator=self._sender_id(event),
            tournament_code=t_code,
        )
        self.store.append_audit(
            {
                "kind": "report_switch",
                "enabled": switch == "on",
                "operator": self._sender_id(event),
                "tournament": t_code,
                "at": _now_ts(),
            }
        )
        yield event.plain_result("报分开关已更新。")

    async def _handle_report(self, event: AstrMessageEvent, args: str):
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return

        report_args, err = self._parse_report_args(event, args)
        if err:
            yield event.plain_result(err)
            return
        assert report_args is not None

        score = report_args["score"]
        set_id = report_args["set_id"]
        player_name = report_args["player_name"]
        proxy_report = bool(player_name)

        tournament_code = self._current_tournament_code(event)
        if not self._is_report_enabled(tournament_code):
            yield event.plain_result(
                "当前报分功能未开启，请管理员执行：@机器人 报分开关 on "
                + (tournament_code or "")
            )
            return

        parsed_score = self._parse_score(score)
        if not parsed_score:
            yield event.plain_result("比分格式错误，示例：@机器人 报分 2-1")
            return
        my_wins, opp_wins = parsed_score
        if my_wins == opp_wins:
            yield event.plain_result("比分不能平局，请填写如 2-1、2-0。")
            return

        sender = self._sender_id(event)
        sid = _safe_int(set_id)
        client = StartGGClient(self.token)
        t_code = tournament_code
        if not t_code or t_code not in self.tournaments:
            yield event.plain_result("未找到可用赛事，请先由管理员绑定赛事并切换。")
            return

        entrant_id, source, resolve_err = await self._resolve_report_entrant(
            event,
            client,
            self.tournaments[t_code],
            player_name=player_name if proxy_report else "",
        )
        if resolve_err:
            yield event.plain_result(resolve_err)
            return
        assert entrant_id is not None
        display_player = None
        if proxy_report:
            display_player = source.replace("指定选手(", "").rstrip(")") or player_name

        target_set = sid
        if not target_set:
            try:
                my_set = await self._query_reportable_set(
                    client, self.tournaments[t_code], entrant_id
                )
            except Exception as e:
                yield event.plain_result(f"查询对局失败: {e}")
                return
            if not my_set.get("ok"):
                yield event.plain_result(my_set.get("msg", "未找到可报分对局"))
                return
            target_set = my_set.get("setId")
        if not target_set:
            yield event.plain_result("无法确定 setId，请在命令末尾显式传入 setId。")
            return

        if not proxy_report and not self._check_report_cooldown(sender, target_set):
            yield event.plain_result("你操作太快了，请稍后重试。")
            return

        try:
            set_detail_resp = await self._query_set_detail(client, target_set)
        except Exception as e:
            yield event.plain_result(f"查询 set 失败: {e}")
            return
        set_data = ((set_detail_resp.get("data") or {}).get("set")) or {}
        slots = set_data.get("slots") or []
        entrant_ids = self._slot_entrant_ids(slots)
        if len(entrant_ids) < 2:
            yield event.plain_result(
                "当前 set 仅有一方选手（等待对手/轮空位），无法报分。"
                "请对已结束的对局报分，或在命令末尾填写正确的 setId。"
            )
            return
        if bool(set_data.get("hasPlaceholder")):
            yield event.plain_result("当前 set 含占位符，请等待对手就位后再报分。")
            return
        set_state = _safe_int(set_data.get("state")) or 0
        if set_state == 3 and set_data.get("winnerId"):
            yield event.plain_result(
                "该 set 在 start.gg 上已结束并已有胜者；若需更正请联系管理员。"
            )
            return
        if entrant_id not in entrant_ids:
            yield event.plain_result("你不是该 set 的参赛选手，拒绝报分。")
            return

        my_idx = entrant_ids.index(entrant_id)
        opp_idx = 1 if my_idx == 0 else 0
        if my_wins > opp_wins:
            winner_id = entrant_ids[my_idx]
            winner_side = "player"
        else:
            winner_id = entrant_ids[opp_idx]
            winner_side = "opponent"

        if my_idx == 0:
            entrant1_wins, entrant2_wins = my_wins, opp_wins
        else:
            entrant1_wins, entrant2_wins = opp_wins, my_wins

        game_data = self._build_set_game_data(entrant_ids, entrant1_wins, entrant2_wins)

        try:
            report_resp = await self._report_set(
                client, target_set, winner_id, game_data=game_data
            )
            gql_errs = StartGGClient.graphql_errors(report_resp)
            if gql_errs:
                raise RuntimeError("; ".join(gql_errs))
            audit_base = {
                "kind": "report_score",
                "sender": sender,
                "entrantId": entrant_id,
                "entrantSource": source,
                "tournament": t_code,
                "setId": target_set,
                "scoreInput": score,
                "winnerSide": winner_side,
                "winnerId": winner_id,
                "gameData": game_data,
                "proxyReport": proxy_report,
                "at": _now_ts(),
            }
            if proxy_report:
                audit_base["proxyPlayerName"] = display_player
            self.store.append_audit({**audit_base, "response": report_resp})
        except Exception as e:
            audit_fail = {
                "kind": "report_score",
                "sender": sender,
                "entrantId": entrant_id,
                "entrantSource": source,
                "tournament": t_code,
                "setId": target_set,
                "scoreInput": score,
                "winnerSide": winner_side,
                "proxyReport": proxy_report,
                "status": "failed",
                "error": str(e),
                "at": _now_ts(),
            }
            if proxy_report:
                audit_fail["proxyPlayerName"] = display_player
            self.store.append_audit(audit_fail)
            yield event.plain_result(f"报分失败: {e}")
            return

        winner_tag = self._entrant_name_from_slots(slots, winner_id) or "未知选手"
        yield event.plain_result(f"报分成功，胜者为{winner_tag}")
