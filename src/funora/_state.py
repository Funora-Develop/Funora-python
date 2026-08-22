"""Сохранение состояния клиента между запусками.

Единственная задача файла - пережить перезапуск. Спецификация требует этого
прямо: кэш гашения повторов только в памяти означает, что после любого
перезапуска повторно приходит всё, что успело прийти до него. Для обработчика,
выдающего товар, это выданный дважды товар при каждом перезапуске процесса.

Три решения, каждое против своего вида беды.

Запись атомарна. Файл собирается рядом и переименовывается поверх, а не
дописывается на месте. Процесс, убитый посреди записи, оставил бы обрезанный
файл, и следующий запуск не смог бы его прочитать - то есть перезапуск в самый
неудачный момент отменял бы всю защиту, ради которой файл заведён.

Чужой формат не читается молча. Файл, записанный другой версией формата или
другим семейством адаптера, даёт ошибку, а не пустое состояние. Молчаливый старт
с нуля здесь неотличим от штатной работы и приводит к повторной обработке всего,
что уже обработано.

Отсутствие файла ошибкой не является. Первый запуск - штатное событие, и
требовать файл значило бы требовать его создать вручную.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .contract import ADAPTER_FAMILY as _ADAPTER_FAMILY
from .contract import CANONICAL_FORM_VERSION
from .errors import StateSchemaIncompatibleError

__all__ = ["StateFile", "STATE_FORMAT"]

#: Версия формата файла состояния.
#:
#: Меняется при любом изменении состава сохраняемого. Прочитать файл чужой
#: версии нельзя: неизвестно, что означают его поля.
#:
#: v2 добавила курсор. Файл v1 хранил только гашение повторов, и старт по нему
#: ушёл бы в холодный старт, молча съев всё, что изменилось за простой. Отказ
#: здесь честнее: пользователь удалит файл сам и будет знать, чем это грозит.
#:
#: v3 сменила не состав, а СМЫСЛ хранимого. События о самом наблюдении -
#: watch.primed и snapshot.incomplete - строились вручную: идентификатор
#: человекочитаемой строкой, ключ упорядочивания «account:...» мимо нормативной
#: таблицы. Теперь оба строятся общим путём, и прежние отпечатки в гашении
#: повторов не совпадут ни с чем.
#:
#: Молча принять такой файл значило бы выдать приветствие и жалобу на неполноту
#: повторно - по разу за срок гашения. Не смертельно, но необъяснимо со стороны
#: пользователя: он увидит «наблюдение началось» у работающего месяц клиента.
STATE_FORMAT: Final[str] = "funora-state-v3"

#: Семейство адаптера, к которому относится состояние.
#:
#: Состояние, снятое с другой площадки, бессмысленно здесь целиком: совпадение
#: идентификаторов было бы случайным, а последствия - молчаливым гашением чужих
#: событий.
ADAPTER_FAMILY: Final[str] = _ADAPTER_FAMILY


@dataclass(frozen=True, slots=True)
class StateFile:
    """Файл состояния клиента.

    Args:
        path (Path): Путь файла.
    """

    path: Path

    def load(self) -> dict[str, Any]:
        """Читает состояние.

        Returns:
            dict[str, Any]: Сохранённое состояние. Пустой словарь, если файла
            нет: первый запуск - штатное событие.

        Raises:
            StateSchemaIncompatibleError: Если файл записан другой версией
                формата либо другим семейством адаптера. Молчаливый старт с
                нуля здесь неотличим от штатной работы и приводит к повторной
                обработке всего, что уже обработано.
        """
        if not self.path.is_file():
            return {}

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StateSchemaIncompatibleError(
                f"файл состояния {self.path} не читается: {type(exc).__name__}. "
                "Удалите его вручную, если готовы к повторной обработке всего, "
                "что уже обработано"
            ) from exc

        if not isinstance(raw, dict):
            raise StateSchemaIncompatibleError(
                f"файл состояния {self.path} имеет неожиданное устройство"
            )

        stored_format = raw.get("format")
        if stored_format != STATE_FORMAT:
            raise StateSchemaIncompatibleError(
                f"файл состояния {self.path} записан форматом {stored_format!r}, "
                f"ожидался {STATE_FORMAT!r}"
            )

        stored_family = raw.get("adapter_family")
        if stored_family != ADAPTER_FAMILY:
            raise StateSchemaIncompatibleError(
                f"файл состояния {self.path} снят с семейства {stored_family!r}, "
                f"ожидалось {ADAPTER_FAMILY!r}"
            )

        stored_canonical = raw.get("canonical_form_version")
        if stored_canonical is not None and stored_canonical != CANONICAL_FORM_VERSION:
            raise StateSchemaIncompatibleError(
                f"файл состояния {self.path} записан канонической формой "
                f"{stored_canonical!r}, ожидалась {CANONICAL_FORM_VERSION!r}. "
                "Сохранённые отпечатки собраны по другим правилам и не совпадут "
                "ни с чем"
            )

        payload = raw.get("payload")
        return payload if isinstance(payload, dict) else {}

    def save(self, payload: dict[str, Any]) -> None:
        """Записывает состояние.

        Запись атомарна: файл собирается рядом и переименовывается поверх.
        Дописывание на месте оставило бы обрезанный файл при убийстве процесса
        посреди записи, и следующий запуск не смог бы его прочитать - то есть
        перезапуск в самый неудачный момент отменил бы всю защиту.

        Args:
            payload (dict[str, Any]): Сохраняемое состояние.

        Returns:
            None
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")

        body = json.dumps(
            {
                "format": STATE_FORMAT,
                "adapter_family": ADAPTER_FAMILY,
                # Версия канонической формы записывается вместе с остальным.
                # Она меняется отдельно от версии спецификации: одна и та же
                # модель может сериализоваться по-новому, и это ломает
                # сохранённые отпечатки. Файл, не помнящий её, нельзя проверить
                # на пригодность - можно только надеяться.
                "canonical_form_version": CANONICAL_FORM_VERSION,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        temporary.write_text(body, encoding="utf-8", newline="\n")
        os.replace(temporary, self.path)
