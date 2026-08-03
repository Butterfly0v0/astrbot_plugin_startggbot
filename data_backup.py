"""插件运行时数据的手动备份与恢复。"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

BACKUP_ID_PATTERN = re.compile(r"^\d{8}-\d{6}$")
MANIFEST_FILE = "manifest.json"


def backups_root(data_dir: str) -> str:
    return os.path.join(data_dir, "backups")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def validate_backup_id(backup_id: str) -> bool:
    return bool(BACKUP_ID_PATTERN.fullmatch(str(backup_id or "").strip()))


def backup_dir_for_id(data_dir: str, backup_id: str) -> str:
    if not validate_backup_id(backup_id):
        raise ValueError("无效的备份 ID。")
    return os.path.join(backups_root(data_dir), backup_id)


def create_backup(
    data_dir: str,
    file_names: Tuple[str, ...],
    *,
    operator: str,
    plugin_version: str,
) -> Dict[str, Any]:
    backup_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = backup_dir_for_id(data_dir, backup_id)
    _ensure_dir(target_dir)

    copied: List[str] = []
    for name in file_names:
        src = os.path.join(data_dir, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(target_dir, name)
        _ensure_dir(os.path.dirname(dst))
        shutil.copy2(src, dst)
        copied.append(name)

    manifest = {
        "backup_id": backup_id,
        "created_at": int(datetime.now().timestamp()),
        "operator": operator,
        "plugin_version": plugin_version,
        "data_dir": data_dir,
        "files": copied,
    }
    with open(os.path.join(target_dir, MANIFEST_FILE), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {
        "id": backup_id,
        "files": copied,
        "file_count": len(copied),
        "created_at": manifest["created_at"],
    }


def list_backups(data_dir: str) -> List[Dict[str, Any]]:
    root = backups_root(data_dir)
    if not os.path.isdir(root):
        return []
    rows: List[Dict[str, Any]] = []
    for name in os.listdir(root):
        if not validate_backup_id(name):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        manifest_path = os.path.join(path, MANIFEST_FILE)
        manifest: Dict[str, Any] = {}
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                manifest = {}
        created_at = manifest.get("created_at")
        rows.append(
            {
                "id": name,
                "created_at": created_at,
                "operator": manifest.get("operator") or "",
                "plugin_version": manifest.get("plugin_version") or "",
                "file_count": len(manifest.get("files") or []),
            }
        )
    rows.sort(key=lambda x: x.get("id") or "", reverse=True)
    return rows


def restore_backup(data_dir: str, backup_id: str, file_names: Tuple[str, ...]) -> Tuple[List[str], str]:
    source_dir = backup_dir_for_id(data_dir, backup_id)
    if not os.path.isdir(source_dir):
        return [], f"备份不存在: {backup_id}"

    restored: List[str] = []
    for name in file_names:
        src = os.path.join(source_dir, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(data_dir, name)
        _ensure_dir(os.path.dirname(dst))
        shutil.copy2(src, dst)
        restored.append(name)

    if not restored:
        manifest_path = os.path.join(source_dir, MANIFEST_FILE)
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                for name in manifest.get("files") or []:
                    src = os.path.join(source_dir, name)
                    if not os.path.exists(src):
                        continue
                    dst = os.path.join(data_dir, name)
                    _ensure_dir(os.path.dirname(dst))
                    shutil.copy2(src, dst)
                    restored.append(name)
            except Exception:
                pass

    if not restored:
        return [], "备份中没有可恢复的数据文件。"
    return restored, ""


def build_backup_zip(data_dir: str, backup_id: str) -> Tuple[Optional[str], str]:
    source_dir = backup_dir_for_id(data_dir, backup_id)
    if not os.path.isdir(source_dir):
        return None, f"备份不存在: {backup_id}"

    zip_dir = os.path.join(backups_root(data_dir), "_downloads")
    _ensure_dir(zip_dir)
    zip_path = os.path.join(zip_dir, f"startggbot-backup-{backup_id}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(source_dir):
            for filename in files:
                full_path = os.path.join(root, filename)
                arcname = os.path.relpath(full_path, source_dir)
                zf.write(full_path, arcname)
    return zip_path, ""


def delete_backup(data_dir: str, backup_id: str) -> Tuple[bool, str]:
    source_dir = backup_dir_for_id(data_dir, backup_id)
    if not os.path.isdir(source_dir):
        return False, f"备份不存在: {backup_id}"

    shutil.rmtree(source_dir)
    zip_path = os.path.join(
        backups_root(data_dir),
        "_downloads",
        f"startggbot-backup-{backup_id}.zip",
    )
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    return True, ""
