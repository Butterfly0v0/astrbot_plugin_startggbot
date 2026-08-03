"""AstrBot Web API 兼容层：优先 astrbot.api.web，否则回退 Quart。"""

from __future__ import annotations

import importlib
import os
from typing import Any, Optional

WEB_API_BACKEND = "none"
_IMPORT_ERROR = ""


def _load_astrbot_web_api() -> bool:
    global WEB_API_BACKEND, _IMPORT_ERROR
    try:
        web_mod = importlib.import_module("astrbot.api.web")
    except ImportError as exc:
        _IMPORT_ERROR = str(exc)
        return False

    global request, json_response, error_response, file_response
    request = web_mod.request
    json_response = web_mod.json_response
    error_response = web_mod.error_response
    file_response = web_mod.file_response
    WEB_API_BACKEND = "astrbot"
    return True


if not _load_astrbot_web_api():
    try:
        from quart import jsonify, request as _quart_request, send_file
    except ImportError as exc:
        jsonify = None
        _quart_request = None
        send_file = None
        if not _IMPORT_ERROR:
            _IMPORT_ERROR = str(exc)
    else:
        WEB_API_BACKEND = "quart"

    class _QueryProxy:
        def __init__(self, args: Any) -> None:
            self._args = args

        def get(self, key: str, default: Any = None, type: Any = None) -> Any:
            if self._args is None:
                return default
            val = self._args.get(key, default)
            if type is not None and val is not None and val != default:
                try:
                    return type(val)
                except (TypeError, ValueError):
                    return default
            return val

    class _RequestCompat:
        @property
        def username(self) -> Optional[str]:
            if _quart_request is None:
                return None
            return getattr(_quart_request, "username", None)

        @property
        def query(self) -> _QueryProxy:
            if _quart_request is None:
                return _QueryProxy(None)
            return _QueryProxy(getattr(_quart_request, "args", None))

        async def json(self, default: Any = None) -> Any:
            if _quart_request is None:
                return default if default is not None else {}
            data = await _quart_request.get_json(silent=True)
            if isinstance(data, dict):
                return data
            return default if default is not None else {}

    if WEB_API_BACKEND == "quart":
        request = _RequestCompat()

        def json_response(data: Any, status_code: int = 200):
            return jsonify(data), status_code

        def error_response(message: str, status_code: int = 400):
            return jsonify({"status": "error", "message": message}), status_code

        async def file_response(
            path: str, filename: Optional[str] = None, content_type: Optional[str] = None
        ):
            if send_file is None:
                raise RuntimeError("Quart send_file 不可用，无法导出文件。")
            return await send_file(
                path,
                mimetype=content_type,
                as_attachment=True,
                attachment_filename=filename or os.path.basename(path),
            )
    else:
        class _UnavailableRequest:
            @property
            def username(self) -> None:
                return None

            @property
            def query(self) -> _QueryProxy:
                return _QueryProxy(None)

            async def json(self, default: Any = None) -> Any:
                return default if default is not None else {}

        request = _UnavailableRequest()

        def json_response(data: Any, status_code: int = 200):
            return data

        def error_response(message: str, status_code: int = 400):
            return {"status": "error", "message": message}, status_code

        async def file_response(
            path: str, filename: Optional[str] = None, content_type: Optional[str] = None
        ):
            raise RuntimeError("Web API 不可用，无法导出文件。")


def web_api_supported() -> bool:
    return WEB_API_BACKEND in {"astrbot", "quart"}


def web_api_backend_name() -> str:
    return WEB_API_BACKEND


def web_api_import_error() -> str:
    return _IMPORT_ERROR

