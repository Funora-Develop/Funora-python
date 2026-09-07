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


def _envelope(kind: str, owner: str, position: str) -> dict[str, str | int]:
    if kind not in _KINDS:
        raise ValueError("cursor kind")
    if not isinstance(owner, str) or not owner or len(owner) > MAX_CURSOR_BYTES:
        raise ValueError("cursor owner")
    if not isinstance(position, str) or not position.strip() or len(position) > MAX_CURSOR_BYTES:
        raise ValueError("cursor position")
    return {
        "version": CURSOR_FORMAT_VERSION,
        "canonical_form_version": CANONICAL_FORM_VERSION,
        "adapter_family": ADAPTER_FAMILY,
        "kind": kind,
        "owner": _pack(owner.encode("utf-8")),
        "position": _pack(position.encode("utf-8")),
    }


def encode_cursor(kind: str, owner: str, position: str) -> str:
    """Сохраняет позицию без нормализации её байтов."""
    try:
        token = _pack(canonical_dumps(_envelope(kind, owner, position)).encode("utf-8"))
        if len(token) > MAX_CURSOR_BYTES:
            raise ValueError("cursor too large")
        return token
    except (TypeError, ValueError, ValidationError):
        raise CursorIncompatibleError(
            "позиция не помещается в поддерживаемый формат курсора"
        ) from None


def decode_cursor(token: str, *, kind: str) -> tuple[str, str]:
    """Проверяет формат до сети и возвращает владельца и исходную позицию."""
    try:
        raw = _unpack(token)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != _KEYS:
            raise ValueError("cursor envelope")
        if (
            type(payload["version"]) is not int
            or payload["version"] != CURSOR_FORMAT_VERSION
            or type(payload["canonical_form_version"]) is not int
            or payload["canonical_form_version"] != CANONICAL_FORM_VERSION
            or payload["adapter_family"] != ADAPTER_FAMILY
            or payload["kind"] != kind
        ):
            raise ValueError("cursor incompatible")
        owner = _unpack(payload["owner"]).decode("utf-8")
        position = _unpack(payload["position"]).decode("utf-8")
        # Сверка отвергает дубликаты ключей, лишние пробелы и альтернативные
        # кодировки: один курсор имеет одно представление между реализациями.
        if canonical_dumps(_envelope(kind, owner, position)).encode("utf-8") != raw:
            raise ValueError("non-canonical envelope")
        return owner, position
    except (TypeError, ValueError, RecursionError, ValidationError):
        raise CursorIncompatibleError(
            "курсор повреждён либо относится к другому формату или операции"
        ) from None
