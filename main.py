import asyncio
import base64
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)


def _load_local_module(module_name: str):
    """AstrBot 以包路径加载 main 时，同目录模块需显式加载。"""
    module_path = os.path.join(_PLUGIN_DIR, f"{module_name}.py")
    if not os.path.isfile(module_path):
        raise ImportError(f"缺少插件模块文件: {module_path}")
    spec = importlib.util.spec_from_file_location(
        f"astrbot_plugin_startggbot.{module_name}",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件模块: {module_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_web_compat = _load_local_module("web_compat")
_web_manage = _load_local_module("web_manage")

error_response = _web_compat.error_response
file_response = _web_compat.file_response
json_response = _web_compat.json_response
request = _web_compat.request
web_api_backend_name = _web_compat.web_api_backend_name
web_api_import_error = _web_compat.web_api_import_error
web_api_supported = _web_compat.web_api_supported
format_account_links_csv = _web_manage.format_account_links_csv
format_account_links_txt = _web_manage.format_account_links_txt
normalize_startgg_user_code = _web_manage.normalize_startgg_user_code
register_web_apis = _web_manage.register_web_apis
_data_backup = _load_local_module("data_backup")
create_data_backup = _data_backup.create_backup
list_data_backups = _data_backup.list_backups
restore_data_backup = _data_backup.restore_backup
build_backup_zip = _data_backup.build_backup_zip
delete_data_backup = _data_backup.delete_backup

logger = logging.getLogger("astrbot_plugin_startggbot")

PLUGIN_NAME = "astrbot_plugin_startggbot"
LEGACY_DATA_DIR = os.path.join(_PLUGIN_DIR, "data")
_PERSISTENT_DATA_FILES = (
    "bindings.json",
    "tournaments.json",
    "account_links.json",
    "settings.json",
    "report_switch.json",
    "admin_qq_ids.json",
    "audit_log.jsonl",
)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _resolve_plugin_data_dir() -> str:
    """持久化到 AstrBot plugin_data，避免插件更新覆盖插件目录内 data/。"""
    persistent_dir = LEGACY_DATA_DIR
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        persistent_dir = os.path.join(get_astrbot_plugin_data_path(), PLUGIN_NAME)
    except ImportError:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            persistent_dir = os.path.join(
                get_astrbot_data_path(), "plugin_data", PLUGIN_NAME
            )
        except ImportError:
            logger.warning(
                "无法获取 AstrBot plugin_data 路径，回退到插件目录 data/。"
            )

    _ensure_dir(persistent_dir)
    if os.path.normpath(persistent_dir) != os.path.normpath(LEGACY_DATA_DIR):
        _migrate_legacy_data_dir(LEGACY_DATA_DIR, persistent_dir)
    return persistent_dir


def _migrate_legacy_data_dir(legacy_dir: str, new_dir: str) -> None:
    if not os.path.isdir(legacy_dir):
        return
    migrated: List[str] = []
    for name in _PERSISTENT_DATA_FILES:
        src = os.path.join(legacy_dir, name)
        dst = os.path.join(new_dir, name)
        if not os.path.exists(src):
            continue
        if os.path.exists(dst):
            if name.endswith(".json"):
                try:
                    with open(dst, "r", encoding="utf-8") as f:
                        current = json.load(f)
                    if current:
                        continue
                except Exception:
                    pass
            else:
                continue
        _ensure_dir(os.path.dirname(dst))
        shutil.copy2(src, dst)
        migrated.append(name)
    legacy_exports = os.path.join(legacy_dir, "exports")
    new_exports = os.path.join(new_dir, "exports")
    if os.path.isdir(legacy_exports) and not os.path.isdir(new_exports):
        shutil.copytree(legacy_exports, new_exports)
    if migrated:
        logger.info(
            "已从插件目录 data/ 迁移数据到 plugin_data：%s",
            ", ".join(migrated),
        )


STARTGG_ENDPOINT = "https://api.start.gg/gql/alpha"
DATA_DIR = _resolve_plugin_data_dir()
BINDINGS_FILE = os.path.join(DATA_DIR, "bindings.json")
REPORT_SWITCH_FILE = os.path.join(DATA_DIR, "report_switch.json")
TOURNAMENTS_FILE = os.path.join(DATA_DIR, "tournaments.json")
ADMIN_QQ_FILE = os.path.join(DATA_DIR, "admin_qq_ids.json")
AUDIT_FILE = os.path.join(DATA_DIR, "audit_log.jsonl")
ACCOUNT_LINKS_FILE = os.path.join(DATA_DIR, "account_links.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

# 按长度降序匹配，避免「绑定赛事」误匹配「绑定赛事全参」
MENTION_COMMANDS = [
    "绑定赛事全参",
    "绑定赛事链接",
    "绑定赛事",
    "添加管理员",
    "解除关联",
    "关联",
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

    def get_settings(self) -> Dict[str, Any]:
        return _read_json(SETTINGS_FILE, {})

    def save_settings(self, payload: Dict[str, Any]) -> None:
        _write_json(SETTINGS_FILE, payload)

    def get_account_links(self) -> Dict[str, Dict[str, Any]]:
        data = _read_json(ACCOUNT_LINKS_FILE, {"links": {}})
        links = data.get("links")
        if not isinstance(links, dict):
            return {}
        return links

    def save_account_links(self, links: Dict[str, Dict[str, Any]]) -> None:
        _write_json(ACCOUNT_LINKS_FILE, {"links": links})

    def set_account_link(self, qq_id: str, record: Dict[str, Any]) -> None:
        links = self.get_account_links()
        links[str(qq_id)] = record
        self.save_account_links(links)

    def delete_account_link(self, qq_id: str) -> bool:
        links = self.get_account_links()
        if qq_id not in links:
            return False
        links.pop(qq_id, None)
        self.save_account_links(links)
        return True


@register("astrbot_plugin_startggbot", "Butterfly0v0", "start.gg对阵查询与自助报分", "0.2.7")
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
        if web_api_supported() and hasattr(self.context, "register_web_api"):
            register_web_apis(self)
            logger.info(
                "WebUI 管理页 API 已注册（backend=%s）。",
                web_api_backend_name(),
            )
        elif hasattr(self.context, "register_web_api"):
            logger.warning(
                "WebUI 管理页 API 未启用（backend=%s，import=%s）。"
                "聊天指令仍可用；请确认 AstrBot 本体已更新并重载插件。",
                web_api_backend_name(),
                web_api_import_error() or "unknown",
            )

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

    def _global_active_tournament_code(self) -> Optional[str]:
        code = str((self.store.get_settings() or {}).get("active_tournament_code") or "").strip()
        if code and code in self.tournaments:
            return code
        return None

    def _set_global_active_tournament(self, code: str, operator: str) -> None:
        settings = self.store.get_settings()
        settings["active_tournament_code"] = code
        settings["updated_at"] = _now_ts()
        settings["updated_by"] = operator
        self.store.save_settings(settings)

    def _current_tournament_code(self, event: AstrMessageEvent) -> Optional[str]:
        self._sync_tournaments_from_store()
        for key in self._collect_session_keys(event):
            code = self.session_tournament.get(key)
            if code and code in self.tournaments:
                return code
        global_code = self._global_active_tournament_code()
        if global_code:
            return global_code
        if self.default_tournament and self.default_tournament in self.tournaments:
            return self.default_tournament
        if self.tournaments:
            return next(iter(self.tournaments.keys()))
        return None

    def _resolve_active_tournament_code(self) -> Optional[str]:
        """无聊天上下文时解析当前赛事（WebUI / 全局默认）。"""
        self._sync_tournaments_from_store()
        global_code = self._global_active_tournament_code()
        if global_code:
            return global_code
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

    def _sender_field(self, event: AstrMessageEvent, *attrs: str) -> str:
        sources: List[Any] = []
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is not None:
            sources.append(getattr(msg_obj, "sender", None))
        sources.append(getattr(event, "sender", None))

        for src in sources:
            if src is None:
                continue
            if isinstance(src, dict):
                for attr in attrs:
                    val = src.get(attr)
                    if val and str(val).strip():
                        return str(val).strip()
            else:
                for attr in attrs:
                    val = getattr(src, attr, None)
                    if val and str(val).strip():
                        return str(val).strip()
        return ""

    def _sender_query_name(self, event: AstrMessageEvent) -> str:
        """查询/我在哪：群聊优先群名片（card），与 QQ 昵称区分；私聊用昵称。"""
        if self._is_group_message(event):
            card = self._sender_field(event, "card", "group_card")
            if card:
                return card
        for attr in ("nickname", "display_name", "name"):
            name = self._sender_field(event, attr)
            if name:
                return name
        getter = getattr(event, "get_sender_name", None)
        if callable(getter):
            try:
                name = str(getter()).strip()
                if name:
                    return name
            except Exception:
                pass
        return ""

    def _sender_display_name(self, event: AstrMessageEvent) -> str:
        """通用展示名（报分等）：群聊仍优先群名片。"""
        return self._sender_query_name(event)

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

        client = StartGGClient(self.token)
        tournament = self.tournaments[tournament_code]
        explicit_name = (username or "").strip()
        if explicit_name:
            entrant_id, matched_name, err = await self._find_entrant_by_username(
                client, tournament, explicit_name
            )
        else:
            entrant_id, matched_name, err = await self._resolve_self_entrant(
                event, client, tournament, allow_entrant_id_fallback=False
            )
        if err:
            yield event.plain_result(err)
            return
        assert entrant_id is not None and matched_name is not None

        async for result in self._query_entrant_match(
            event, entrant_id, matched_name
        ):
            yield result

    def _get_sender_account_link(self, event: AstrMessageEvent) -> Optional[Dict[str, Any]]:
        sender = self._sender_id(event)
        return self.store.get_account_links().get(sender)

    async def _find_entrant_by_linked_account(
        self,
        client: StartGGClient,
        tournament: TournamentConfig,
        link: Dict[str, Any],
    ) -> Tuple[Optional[int], Optional[str], str]:
        player_id = _safe_int(link.get("startgg_player_id"))
        gamer_tag = str(link.get("gamer_tag") or "").strip()
        if not player_id and not gamer_tag:
            return (
                None,
                None,
                "已关联 start.gg 账号，但未记录选手 ID 或选手名，请重新执行「关联」。",
            )

        list_query = """
        query ListEntrants($eventId: ID!, $page: Int!, $perPage: Int!) {
          event(id: $eventId) {
            entrants(query: { perPage: $perPage, page: $page }) {
              nodes {
                id
                name
                participants {
                  gamerTag
                  player { id }
                }
              }
            }
          }
        }
        """
        matches: List[Tuple[int, str]] = []
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
                (((resp.get("data") or {}).get("event") or {}).get("entrants") or {}).get(
                    "nodes"
                )
                or []
            )
            if not page_nodes:
                break
            for node in page_nodes:
                entrant_id = _safe_int(node.get("id"))
                if not entrant_id:
                    continue
                matched = False
                display_name = str(node.get("name") or "").strip()
                for participant in node.get("participants") or []:
                    participant = participant or {}
                    p_player_id = _safe_int((participant.get("player") or {}).get("id"))
                    p_tag = str(participant.get("gamerTag") or "").strip()
                    if player_id and p_player_id == player_id:
                        matched = True
                        display_name = p_tag or display_name
                        break
                    if gamer_tag and p_tag.lower() == gamer_tag.lower():
                        matched = True
                        display_name = p_tag or display_name
                        break
                if matched and all(m[0] != entrant_id for m in matches):
                    matches.append((entrant_id, display_name or gamer_tag))

        if len(matches) == 1:
            return matches[0][0], matches[0][1], ""
        if len(matches) > 1:
            hints = ", ".join(name for _, name in matches[:5])
            return None, None, f"关联账号在当前赛事中匹配到多名选手，请联系管理员：{hints}"

        label = gamer_tag or str(link.get("startgg_code") or "关联账号")
        return (
            None,
            None,
            f"已关联 start.gg 账号「{label}」，但在当前赛事中未找到对应选手。"
            "请确认已切换至正确赛事，且该账号已报名参赛。",
        )

    async def _resolve_self_entrant(
        self,
        event: AstrMessageEvent,
        client: StartGGClient,
        tournament: TournamentConfig,
        *,
        allow_entrant_id_fallback: bool = False,
    ) -> Tuple[Optional[int], Optional[str], str]:
        """本人身份解析：已关联 start.gg 账号优先，否则群名片/昵称；关联失败不回退昵称。"""
        account_link = self._get_sender_account_link(event)
        if account_link:
            entrant_id, matched_name, err = await self._find_entrant_by_linked_account(
                client, tournament, account_link
            )
            if err:
                return None, None, err
            assert entrant_id is not None and matched_name is not None
            return entrant_id, matched_name, ""

        keyword = self._sender_query_name(event)
        if keyword:
            entrant_id, matched_name, err = await self._find_entrant_by_username(
                client, tournament, keyword
            )
            if entrant_id and matched_name:
                return entrant_id, matched_name, ""
            if err and "未在当前赛事中找到" not in err:
                return None, None, err

        if allow_entrant_id_fallback:
            entrant_id, _ = self._resolve_entrant_id(event)
            if entrant_id:
                return entrant_id, keyword or str(entrant_id), ""

        if not keyword:
            return (
                None,
                None,
                "无法获取你的群名片/昵称。请先 @机器人 关联 <start.gg唯一代码>，"
                "或使用：@机器人 查询 <start.gg用户名>",
            )
        return (
            None,
            None,
            f"未在当前赛事中找到用户「{keyword}」。"
            "可先 @机器人 关联 <start.gg唯一代码> 绑定账号。",
        )

    async def _resolve_report_entrant(
        self,
        event: AstrMessageEvent,
        client: StartGGClient,
        tournament: TournamentConfig,
        player_name: str = "",
    ) -> Tuple[Optional[int], str, str]:
        """解析报分选手：管理员代报按名称；本人报分优先已关联 start.gg 账号，否则按群名片/昵称匹配。"""
        if player_name.strip():
            entrant_id, matched_name, err = await self._find_entrant_by_username(
                client, tournament, player_name.strip()
            )
            if err:
                return None, "", err
            assert entrant_id is not None and matched_name is not None
            return entrant_id, f"指定选手({matched_name})", ""

        entrant_id, matched_name, err = await self._resolve_self_entrant(
            event, client, tournament, allow_entrant_id_fallback=True
        )
        if err:
            return None, "", err
        assert entrant_id is not None and matched_name is not None

        account_link = self._get_sender_account_link(event)
        if account_link:
            tag = str(account_link.get("gamer_tag") or matched_name)
            return entrant_id, f"关联账号({tag})", ""
        return entrant_id, f"本人({matched_name})", ""

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
            if not normalized.startswith(cmd):
                continue
            if len(normalized) == len(cmd):
                return cmd, ""
            # 命令后可无空格、单空格或多空格，如「报分2-1」「报分  2-1」
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
        operator: str,
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
            "operator": operator,
            "code": code,
            "name": name,
            "eventId": event_id,
            "phaseGroupId": phase_group_id,
            "at": _now_ts(),
        }
        if extra:
            payload.update(extra)
        self.store.append_audit(payload)

    def _unbind_tournament_core(self, code: str, operator: str) -> bool:
        if code not in self.tournaments:
            return False
        self.tournaments.pop(code, None)
        self._save_tournaments()
        settings = self.store.get_settings()
        if settings.get("active_tournament_code") == code:
            settings.pop("active_tournament_code", None)
            settings["updated_at"] = _now_ts()
            settings["updated_by"] = operator
            self.store.save_settings(settings)
        stale_keys = [k for k, v in self.session_tournament.items() if v == code]
        for key in stale_keys:
            self.session_tournament.pop(key, None)
        self.store.append_audit(
            {
                "kind": "unbind_tournament",
                "operator": operator,
                "code": code,
                "at": _now_ts(),
            }
        )
        return True

    @staticmethod
    def _format_ts_text(ts: Any) -> str:
        value = _safe_int(ts)
        if not value:
            return ""
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return str(ts)

    def _serialize_account_link(self, record: Dict[str, Any]) -> Dict[str, Any]:
        linked_at = record.get("linked_at")
        return {
            "qq_id": str(record.get("qq_id", "")),
            "startgg_code": str(record.get("startgg_code", "")),
            "startgg_user_id": str(record.get("startgg_user_id", "")),
            "startgg_player_id": str(record.get("startgg_player_id", "") or ""),
            "prefix": str(record.get("prefix", "") or ""),
            "gamer_tag": str(record.get("gamer_tag", "") or ""),
            "linked_at": linked_at,
            "linked_at_text": self._format_ts_text(linked_at),
            "linked_by": str(record.get("linked_by", "") or ""),
            "profile_updated_at": record.get("profile_updated_at"),
            "profile_updated_at_text": self._format_ts_text(record.get("profile_updated_at")),
        }

    def _build_account_link_record(
        self,
        qq_id: str,
        user_info: Dict[str, Any],
        operator: str,
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = _now_ts()
        base = existing or {}
        return {
            "qq_id": qq_id,
            "startgg_code": user_info["startgg_code"],
            "startgg_user_id": str(user_info["startgg_user_id"]),
            "startgg_player_id": (
                str(user_info["startgg_player_id"])
                if user_info.get("startgg_player_id")
                else ""
            ),
            "prefix": str(user_info.get("prefix") or "").strip(),
            "gamer_tag": str(user_info.get("gamer_tag") or "").strip(),
            "linked_at": base.get("linked_at") or now,
            "linked_by": base.get("linked_by") or operator,
            "profile_updated_at": now,
        }

    async def _refresh_account_link_profile(
        self, record: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        if not self.token:
            return None, "缺少 start.gg apiToken，请先配置。"
        slug = str(record.get("startgg_code") or "").strip()
        if not slug:
            return None, "缺少 start.gg 唯一代码，无法刷新选手信息。"
        client = StartGGClient(self.token)
        try:
            user_info, err = await self._query_startgg_user_by_slug(client, slug)
        except Exception as e:
            return None, f"查询 start.gg 用户失败: {e}"
        if err or not user_info:
            return None, err or "未找到 start.gg 用户。"
        qq_id = str(record.get("qq_id") or "")
        operator = str(record.get("linked_by") or "webui")
        return self._build_account_link_record(qq_id, user_info, operator, existing=record), ""

    def _list_account_link_rows(self) -> List[Dict[str, Any]]:
        rows = [
            self._serialize_account_link(record)
            for record in self.store.get_account_links().values()
        ]
        rows.sort(key=lambda x: (x.get("qq_id") or ""))
        return rows

    async def _query_startgg_user_by_slug(
        self, client: StartGGClient, slug: str
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        resp = await client.execute(
            """
            query UserBySlug($slug: String!) {
              user(slug: $slug) {
                id
                slug
                player {
                  id
                  prefix
                  gamerTag
                }
              }
            }
            """,
            {"slug": slug},
        )
        gql_errs = StartGGClient.graphql_errors(resp)
        if gql_errs:
            return None, "; ".join(gql_errs)
        user_data = ((resp.get("data") or {}).get("user")) or {}
        user_id = _safe_int(user_data.get("id"))
        if not user_id:
            return None, "未找到该 start.gg 用户，请检查唯一代码是否正确。"
        player = user_data.get("player") or {}
        return {
            "startgg_user_id": user_id,
            "startgg_code": str(user_data.get("slug") or slug),
            "startgg_player_id": _safe_int(player.get("id")),
            "prefix": str(player.get("prefix") or "").strip(),
            "gamer_tag": str(player.get("gamerTag") or "").strip(),
        }, ""

    async def _bind_tournament_from_reference(
        self,
        operator: str,
        reference: str,
        code_raw: Optional[str] = None,
        pgid: Optional[int] = None,
    ) -> Tuple[Optional[str], str]:
        if not self.token:
            return None, "缺少 start.gg apiToken，请先在插件配置中填写。"
        client = StartGGClient(self.token)
        if _parse_startgg_link(reference):
            info, err = await self._resolve_event_from_reference(client, reference)
            if err:
                return None, err
            assert info is not None
            audit_kind = "bind_tournament_link"
            extra: Optional[Dict[str, Any]] = {"link": reference}
        else:
            eid = _safe_int(reference)
            if not eid:
                return None, "reference 必须是 eventId 数字或 start.gg 赛事链接。"
            try:
                info = await self._query_event_info(client, eid)
            except Exception as e:
                return None, f"拉取赛事信息失败: {e}"
            if not info:
                return None, "未查询到该 eventId 对应赛事，请检查 eventId 或 token 权限。"
            audit_kind = "bind_tournament_auto"
            extra = None

        display_name = self._display_binding_name(info)
        code = self._ensure_tournament_code(code_raw, info.tournament_name)
        self._save_tournament_binding(
            operator,
            code,
            display_name,
            info.event_id,
            pgid,
            audit_kind,
            extra,
        )
        return code, ""

    async def web_list_tournaments(self):
        self._sync_tournaments_from_store()
        active = self._resolve_active_tournament_code()
        items = []
        for code, t in self.tournaments.items():
            items.append(
                {
                    "code": code,
                    "name": t.name,
                    "eventId": t.event_id,
                    "phaseGroupId": t.phase_group_id,
                    "isActive": code == active,
                }
            )
        items.sort(key=lambda x: x["code"])
        return json_response(
            {
                "activeCode": active,
                "defaultTournament": self.default_tournament,
                "items": items,
            }
        )

    async def web_bind_tournament(self):
        payload = await request.json(default={})
        reference = str(payload.get("reference") or "").strip()
        if not reference:
            return error_response("缺少 reference（eventId 或 start.gg 链接）。", status_code=400)
        code_raw = str(payload.get("code") or "").strip() or None
        pgid = _safe_int(payload.get("phaseGroupId"))
        operator = request.username or "webui"
        code, err = await self._bind_tournament_from_reference(
            operator, reference, code_raw, pgid
        )
        if err:
            return error_response(err, status_code=400)
        t = self.tournaments.get(code or "")
        return json_response(
            {
                "ok": True,
                "code": code,
                "name": t.name if t else code,
                "eventId": t.event_id if t else None,
                "phaseGroupId": t.phase_group_id if t else None,
            }
        )

    async def web_unbind_tournament(self):
        payload = await request.json(default={})
        code = str(payload.get("code") or "").strip()
        if not code:
            return error_response("缺少赛事简码 code。", status_code=400)
        self._sync_tournaments_from_store()
        operator = request.username or "webui"
        if not self._unbind_tournament_core(code, operator):
            return error_response(f"赛事不存在: {code}", status_code=404)
        return json_response({"ok": True, "code": code})

    async def web_set_active_tournament(self):
        payload = await request.json(default={})
        code = str(payload.get("code") or "").strip()
        if not code:
            return error_response("缺少赛事简码 code。", status_code=400)
        self._sync_tournaments_from_store()
        if code not in self.tournaments:
            return error_response(f"未知赛事简码: {code}", status_code=404)
        operator = request.username or "webui"
        self._set_global_active_tournament(code, operator)
        self.store.append_audit(
            {
                "kind": "switch_tournament_global",
                "operator": operator,
                "code": code,
                "at": _now_ts(),
            }
        )
        t = self.tournaments[code]
        return json_response(
            {
                "ok": True,
                "code": code,
                "name": t.name,
                "eventId": t.event_id,
            }
        )

    async def web_list_account_links(self):
        return json_response({"items": self._list_account_link_rows()})

    async def web_add_account_link(self):
        payload = await request.json(default={})
        qq_id = str(payload.get("qq_id") or "").strip()
        raw_code = str(payload.get("startgg_code") or payload.get("code") or "").strip()
        if not qq_id:
            return error_response("缺少 QQ 号 qq_id。", status_code=400)
        if not qq_id.isdigit():
            return error_response("QQ 号必须为数字。", status_code=400)
        slug, slug_err = normalize_startgg_user_code(raw_code)
        if slug_err:
            return error_response(slug_err, status_code=400)
        assert slug is not None
        if not self.token:
            return error_response("缺少 start.gg apiToken，请先在插件配置中填写。", status_code=400)
        client = StartGGClient(self.token)
        try:
            user_info, err = await self._query_startgg_user_by_slug(client, slug)
        except Exception as e:
            return error_response(f"查询 start.gg 用户失败: {e}", status_code=400)
        if err or not user_info:
            return error_response(err or "未找到 start.gg 用户。", status_code=400)
        operator = request.username or "webui"
        existing = self.store.get_account_links().get(qq_id)
        record = self._build_account_link_record(qq_id, user_info, operator, existing=existing)
        self.store.set_account_link(qq_id, record)
        self.store.append_audit(
            {
                "kind": "link_startgg_account",
                "operator": operator,
                "qq_id": qq_id,
                "startgg_code": record["startgg_code"],
                "startgg_user_id": record["startgg_user_id"],
                "prefix": record.get("prefix"),
                "gamer_tag": record.get("gamer_tag"),
                "at": _now_ts(),
            }
        )
        return json_response({"ok": True, "item": self._serialize_account_link(record)})

    async def web_refresh_account_link(self):
        payload = await request.json(default={})
        qq_id = str(payload.get("qq_id") or "").strip()
        if not qq_id:
            return error_response("缺少 QQ 号 qq_id。", status_code=400)
        record = self.store.get_account_links().get(qq_id)
        if not record:
            return error_response(f"未找到 QQ 号 {qq_id} 的关联记录。", status_code=404)
        updated, err = await self._refresh_account_link_profile(record)
        if err or not updated:
            return error_response(err or "刷新失败。", status_code=400)
        self.store.set_account_link(qq_id, updated)
        operator = request.username or "webui"
        self.store.append_audit(
            {
                "kind": "refresh_startgg_account_profile",
                "operator": operator,
                "qq_id": qq_id,
                "prefix": updated.get("prefix"),
                "gamer_tag": updated.get("gamer_tag"),
                "at": _now_ts(),
            }
        )
        return json_response({"ok": True, "item": self._serialize_account_link(updated)})

    async def web_delete_account_link(self, qq_id: str):
        return await self._delete_account_link_impl(qq_id)

    async def web_delete_account_link_post(self):
        payload = await request.json(default={})
        qq_id = str(payload.get("qq_id") or "").strip()
        return await self._delete_account_link_impl(qq_id)

    async def _delete_account_link_impl(self, qq_id: str):
        qq_id = str(qq_id or "").strip()
        if not qq_id:
            return error_response("缺少 QQ 号。", status_code=400)
        if not self.store.delete_account_link(qq_id):
            return error_response(f"未找到 QQ 号 {qq_id} 的关联记录。", status_code=404)
        operator = request.username or "webui"
        self.store.append_audit(
            {
                "kind": "unlink_startgg_account",
                "operator": operator,
                "qq_id": qq_id,
                "at": _now_ts(),
            }
        )
        return json_response({"ok": True, "qq_id": qq_id})

    def _build_account_links_export(self, fmt: str) -> Tuple[str, str, str]:
        rows = self._list_account_link_rows()
        if fmt == "json":
            content = json.dumps({"items": rows}, ensure_ascii=False, indent=2)
            filename = "startgg_account_links.json"
            content_type = "application/json; charset=utf-8"
        elif fmt == "csv":
            content = format_account_links_csv(rows)
            filename = "startgg_account_links.csv"
            content_type = "text/csv; charset=utf-8"
        else:
            content = format_account_links_txt(rows)
            filename = "startgg_account_links.txt"
            content_type = "text/plain; charset=utf-8"
        return content, filename, content_type

    async def web_export_account_links(self):
        fmt = str(request.query.get("format") or "txt").strip().lower()
        content, filename, content_type = self._build_account_links_export(fmt)
        export_dir = os.path.join(DATA_DIR, "exports")
        _ensure_dir(export_dir)
        export_path = os.path.join(export_dir, filename)
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(content)
        return file_response(export_path, filename=filename, content_type=content_type)

    async def web_export_account_links_content(self):
        fmt = str(request.query.get("format") or "txt").strip().lower()
        content, filename, content_type = self._build_account_links_export(fmt)
        return json_response(
            {
                "filename": filename,
                "content": content,
                "contentType": content_type,
            }
        )

    async def web_list_data_backups(self):
        rows = list_data_backups(DATA_DIR)
        for row in rows:
            row["created_at_text"] = self._format_ts_text(row.get("created_at"))
        return json_response({"items": rows, "dataDir": DATA_DIR})

    async def web_create_data_backup(self):
        operator = request.username or "webui"
        try:
            backup = create_data_backup(
                DATA_DIR,
                _PERSISTENT_DATA_FILES,
                operator=operator,
                plugin_version="0.2.7",
            )
        except ValueError as e:
            return error_response(str(e), status_code=400)
        backup["created_at_text"] = self._format_ts_text(backup.get("created_at"))
        self.store.append_audit(
            {
                "kind": "data_backup_create",
                "operator": operator,
                "backupId": backup.get("id"),
                "fileCount": backup.get("file_count"),
                "at": _now_ts(),
            }
        )
        return json_response({"ok": True, "backup": backup, "dataDir": DATA_DIR})

    async def web_restore_data_backup(self):
        payload = await request.json(default={})
        backup_id = str(payload.get("backup_id") or payload.get("id") or "").strip()
        if not backup_id:
            return error_response("缺少 backup_id。", status_code=400)
        try:
            restored, err = restore_data_backup(DATA_DIR, backup_id, _PERSISTENT_DATA_FILES)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        if err:
            return error_response(err, status_code=404)
        self._sync_tournaments_from_store()
        operator = request.username or "webui"
        self.store.append_audit(
            {
                "kind": "data_backup_restore",
                "operator": operator,
                "backupId": backup_id,
                "restored": restored,
                "at": _now_ts(),
            }
        )
        return json_response(
            {
                "ok": True,
                "backup_id": backup_id,
                "restored": restored,
            }
        )

    async def web_download_data_backup(self):
        backup_id = str(
            request.query.get("backup_id") or request.query.get("id") or ""
        ).strip()
        if not backup_id:
            return error_response("缺少 backup_id。", status_code=400)
        try:
            zip_path, err = build_backup_zip(DATA_DIR, backup_id)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        if err or not zip_path:
            return error_response(err or "备份不存在。", status_code=404)
        return file_response(
            zip_path,
            filename=f"startggbot-backup-{backup_id}.zip",
            content_type="application/zip",
        )

    async def web_download_data_backup_content(self):
        backup_id = str(
            request.query.get("backup_id") or request.query.get("id") or ""
        ).strip()
        if not backup_id:
            return error_response("缺少 backup_id。", status_code=400)
        try:
            zip_path, err = build_backup_zip(DATA_DIR, backup_id)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        if err or not zip_path:
            return error_response(err or "备份不存在。", status_code=404)
        with open(zip_path, "rb") as f:
            payload = base64.b64encode(f.read()).decode("ascii")
        filename = f"startggbot-backup-{backup_id}.zip"
        return json_response(
            {
                "filename": filename,
                "contentBase64": payload,
                "contentType": "application/zip",
            }
        )

    async def web_delete_data_backup(self):
        payload = await request.json(default={})
        backup_id = str(payload.get("backup_id") or payload.get("id") or "").strip()
        if not backup_id:
            return error_response("缺少 backup_id。", status_code=400)
        try:
            ok, err = delete_data_backup(DATA_DIR, backup_id)
        except ValueError as e:
            return error_response(str(e), status_code=400)
        if not ok:
            return error_response(err or "删除失败。", status_code=404)
        operator = request.username or "webui"
        self.store.append_audit(
            {
                "kind": "data_backup_delete",
                "operator": operator,
                "backupId": backup_id,
                "at": _now_ts(),
            }
        )
        return json_response({"ok": True, "backup_id": backup_id})

    async def _query_entrant_match(
        self, event: AstrMessageEvent, entrant_id: int, query_target: str
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
            f"查询对象: {query_target}\n"
            f"对局: {result['round']}\n"
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
            "关联": self._handle_link_startgg_account,
            "解除关联": self._handle_unlink_startgg_account,
        }
        handler = handlers.get(cmd)
        if not handler:
            yield event.plain_result("未知指令，请发送：@机器人 帮助")
            event.stop_event()
            return

        async for result in handler(event, arg_str):
            yield result
        event.stop_event()

    def _help_command_lines(self) -> List[str]:
        return [
            "start.gg 对阵查询与报分 · 指令帮助",
            "用法（群聊请 @ 我）",
            "@机器人 帮助 / help",
            "@机器人 赛事列表",
            "@机器人 我的赛事（管理员，查询 API 账号创建/管理的赛事与简码）",
            "@机器人 绑定赛事 [简码] <eventId|链接> [phaseGroupId]（省略简码时用 tournament 名）",
            "@机器人 绑定赛事链接 [简码] <start.gg链接> [phaseGroupId]",
            "@机器人 绑定赛事全参 [简码] <名称> <eventId> [phaseGroupId]",
            "@机器人 解绑赛事 <简码>",
            "@机器人 赛事详情 <简码>",
            "@机器人 切换 <赛事简码>",
            "@机器人 绑定 <entrantId>",
            "@机器人 我在哪（已关联 start.gg 账号时按关联身份查询；否则按群名片/昵称）",
            "@机器人 查询 [用户名]（省略时同上；指定用户名则按名称查询）",
            "@机器人 set <setId>",
            "@机器人 报分 <选手局数>-<对手局数> [setId]（已关联 start.gg 账号时按关联身份报分；否则按群名片/昵称匹配）",
            "@机器人 报分 <选手名> <选手局数>-<对手局数> [setId]（管理员代报）",
            "@机器人 报分开关 <on|off> [赛事简码]",
            "@机器人 报分状态 [赛事简码]",
            "@机器人 添加管理员 @用户（管理员，写入动态管理员列表）",
            "@机器人 关联 <start.gg唯一代码或链接>（绑定你的 QQ 与 start.gg 账号）",
            "@机器人 解除关联（解除你的 QQ 与 start.gg 账号绑定）",
        ]

    def _build_help_forward_nodes(self, event: AstrMessageEvent) -> Comp.Nodes:
        bot_uin = self._bot_self_id(event) or "0"
        bot_name = "start.gg 帮助"
        nodes = [
            Comp.Node(
                uin=bot_uin,
                name=bot_name,
                content=[Comp.Plain(line)],
            )
            for line in self._help_command_lines()
        ]
        return Comp.Nodes(nodes=nodes)

    async def _handle_help(self, event: AstrMessageEvent, _args: str):
        if self._is_group_message(event):
            yield event.chain_result([self._build_help_forward_nodes(event)])
            return
        yield event.plain_result("\n".join(self._help_command_lines()))

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
            self._sender_id(event), code, display_name, eid, pgid, "bind_tournament", None
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
            self._sender_id(event),
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
            self._sender_id(event),
            code,
            display_name,
            info.event_id,
            pgid,
            audit_kind,
            extra,
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
        self._sync_tournaments_from_store()
        if not self._unbind_tournament_core(code, self._sender_id(event)):
            yield event.plain_result(f"赛事不存在: {code}")
            return
        yield event.plain_result(f"已解绑赛事: {code}")

    async def _handle_link_startgg_account(self, event: AstrMessageEvent, args: str):
        parts = self._split_args(args)
        if not parts:
            yield event.plain_result(
                "用法：@机器人 关联 <start.gg唯一代码或链接>\n"
                "示例：@机器人 关联 1c97bdae\n"
                "或：@机器人 关联 https://www.start.gg/user/1c97bdae"
            )
            return
        raw_code = " ".join(parts).strip()
        slug, slug_err = normalize_startgg_user_code(raw_code)
        if slug_err:
            yield event.plain_result(slug_err)
            return
        assert slug is not None
        if not self.token:
            yield event.plain_result("缺少 start.gg apiToken，请先配置。")
            return
        client = StartGGClient(self.token)
        try:
            user_info, err = await self._query_startgg_user_by_slug(client, slug)
        except Exception as e:
            yield event.plain_result(f"查询 start.gg 用户失败: {e}")
            return
        if err or not user_info:
            yield event.plain_result(err or "未找到 start.gg 用户。")
            return
        qq_id = self._sender_id(event)
        existing = self.store.get_account_links().get(qq_id)
        record = self._build_account_link_record(qq_id, user_info, qq_id, existing=existing)
        self.store.set_account_link(qq_id, record)
        self.store.append_audit(
            {
                "kind": "link_startgg_account",
                "operator": qq_id,
                "qq_id": qq_id,
                "startgg_code": record["startgg_code"],
                "startgg_user_id": record["startgg_user_id"],
                "prefix": record.get("prefix"),
                "gamer_tag": record.get("gamer_tag"),
                "at": _now_ts(),
            }
        )
        gamer_tag = record["gamer_tag"] or "（未设置选手名）"
        prefix = record.get("prefix") or ""
        prefix_line = f"前缀: {prefix}\n" if prefix else ""
        yield event.plain_result(
            "start.gg 账号关联成功。\n"
            f"QQ: {qq_id}\n"
            f"唯一代码: {record['startgg_code']}\n"
            f"start.gg 用户 ID: {record['startgg_user_id']}\n"
            f"{prefix_line}"
            f"选手名: {gamer_tag}"
        )

    async def _handle_unlink_startgg_account(self, event: AstrMessageEvent, _args: str):
        qq_id = self._sender_id(event)
        link = self.store.get_account_links().get(qq_id)
        if not link:
            yield event.plain_result("你尚未关联 start.gg 账号。")
            return
        if not self.store.delete_account_link(qq_id):
            yield event.plain_result("解除关联失败，请稍后重试。")
            return
        self.store.append_audit(
            {
                "kind": "unlink_startgg_account_self",
                "operator": qq_id,
                "qq_id": qq_id,
                "startgg_code": link.get("startgg_code"),
                "startgg_user_id": link.get("startgg_user_id"),
                "at": _now_ts(),
            }
        )
        label = (
            str(link.get("gamer_tag") or "").strip()
            or str(link.get("startgg_code") or "").strip()
            or "关联账号"
        )
        yield event.plain_result(
            f"已解除 start.gg 账号关联（{label}）。\n"
            "「我在哪」「查询」「报分」将恢复为群名片/昵称匹配。"
        )

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
