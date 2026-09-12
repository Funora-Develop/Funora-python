"""Переносимая позиция пагинации, привязанная к операции и её владельцу.

Это формат хранения, а не подпись или разрешение доступа. Проверка сессии
по-прежнему принадлежит транспорту. Сырые значения кодируются отдельно:
каноническая нормализация JSON не должна менять непрозрачный токен сервера.
"""

from __future__ import annotations

import base64
import json

from ._canonical import canonical_dumps
from .contract import (
    ACCEPTED_CURSOR_FORMAT_VERSIONS,
    ADAPTER_FAMILY,
    CANONICAL_FORM_VERSION,
    CURSOR_FORMAT_VERSION,
    MAX_CURSOR_BYTES,
)
from .errors import CursorIncompatibleError, ValidationError

_KINDS = {"chats.history_before", "reviews.get"}
_KEYS = {"version", "canonical_form_version", "adapter_family", "kind", "owner", "position"}


def _pack(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unpack(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > MAX_CURSOR_BYTES:
        raise ValueError("cursor size or type")
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _pack(raw) != value:
        raise ValueError("non-canonical base64url")
    return raw


def _envelope(
    kind: str,
    owner: str,
    position: str,
    scope: str | None,
    *,
    version: int = CURSOR_FORMAT_VERSION,
) -> dict[str, str | int | None]:
    if kind not in _KINDS:
        raise ValueError("cursor kind")
    if not isinstance(owner, str) or not owner or len(owner) > MAX_CURSOR_BYTES:
        raise ValueError("cursor owner")
    if not isinstance(position, str) or not position.strip() or len(position) > MAX_CURSOR_BYTES:
        raise ValueError("cursor position")
    if scope is not None and (
        not isinstance(scope, str) or not scope or len(scope) > MAX_CURSOR_BYTES
    ):
        raise ValueError("cursor scope")
    envelope: dict[str, str | int | None] = {
        "version": version,
        "canonical_form_version": CANONICAL_FORM_VERSION,
        "adapter_family": ADAPTER_FAMILY,
        "kind": kind,
        "owner": _pack(owner.encode("utf-8")),
        "position": _pack(position.encode("utf-8")),
    }
    if version == 2:
        envelope["scope"] = None if scope is None else _pack(scope.encode("utf-8"))
    return envelope


def encode_cursor(kind: str, owner: str, position: str, *, scope: str | None = None) -> str:
    """Сохраняет позицию без нормализации её байтов."""
    try:
        token = _pack(canonical_dumps(_envelope(kind, owner, position, scope)).encode("utf-8"))
        if len(token) > MAX_CURSOR_BYTES:
            raise ValueError("cursor too large")
        return token
    except (TypeError, ValueError, ValidationError):
        raise CursorIncompatibleError(
            "позиция не помещается в поддерживаемый формат курсора"
        ) from None


def decode_cursor(token: str, *, kind: str) -> tuple[str, str, str | None]:
    """Возвращает владельца, позицию и область выборки; v1 читается без области."""
    try:
        raw = _unpack(token)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("cursor envelope")
        version = payload.get("version")
        if (
            type(version) is not int
            or version not in ACCEPTED_CURSOR_FORMAT_VERSIONS
            or set(payload) != (_KEYS | {"scope"} if version == 2 else _KEYS)
            or type(payload["canonical_form_version"]) is not int
            or payload["canonical_form_version"] != CANONICAL_FORM_VERSION
            or payload["adapter_family"] != ADAPTER_FAMILY
            or payload["kind"] != kind
        ):
            raise ValueError("cursor incompatible")
        owner = _unpack(payload["owner"]).decode("utf-8")
        position = _unpack(payload["position"]).decode("utf-8")
        scope = payload.get("scope")
        if scope is not None:
            scope = _unpack(scope).decode("utf-8")
        # Сверка отвергает дубликаты ключей, лишние пробелы и альтернативные
        # кодировки: один курсор имеет одно представление между реализациями.
        if (
            canonical_dumps(_envelope(kind, owner, position, scope, version=version)).encode(
                "utf-8"
            )
            != raw
        ):
            raise ValueError("non-canonical envelope")
        return owner, position, scope
    except (TypeError, ValueError, RecursionError, ValidationError):
        raise CursorIncompatibleError(
            "курсор повреждён либо относится к другому формату или операции"
        ) from None
