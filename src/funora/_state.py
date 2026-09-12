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

from dataclasses import dataclass
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any, Final

from ._canonical import canonical_dumps
from ._fileio import atomic_write, file_lock
from ._json import load_json
from .contract import ADAPTER_FAMILY as _ADAPTER_FAMILY
from .contract import CANONICAL_FORM_VERSION
from .errors import CursorIncompatibleError, StateSchemaIncompatibleError

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
#:
#: v4: метки гашения повторов перестали быть монотонными секундами и стали
#: моментами от эпохи. Прежние файлы принять нельзя: дробное показание
#: секундомера, прочитанное как момент от эпохи, попадает в тысяча девятьсот
#: семидесятый - и весь кэш гашения молча выбрасывается по сроку. Это ровно
#: то, что спецификация запрещает прямо: молчаливое чтение с начала порождает
#: повторную обработку всего, что уже обработано.
#: v5 хранит непринятую партию до обработчиков. v4 без незавершённых попыток
#: читается без сброса; v4 с попытками не содержит самих событий для повтора.
#: v6 различает личный курсор и набор снимков рынка. v5 читается без сброса.
STATE_FORMAT: Final[str] = "funora-state-v6"

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
        path (str | Path): Путь файла. Строка принимается наравне с Path и
            приводится к нему здесь.
    """

    path: Path

    def __post_init__(self) -> None:
        """Закрепляет общий путь файла и блокировок, сохраняя рабочие ссылки."""
        path = Path(self.path)
        try:
            try:
                resolved = path.resolve(strict=True)
            except FileNotFoundError:
                # Новый файл допустим, но битая ссылка в любом компоненте
                # пути не должна создавать пустой журнал в другом месте.
                for component in (path, *path.parents):
                    if component.is_symlink():
                        component.resolve(strict=True)
                resolved = path.resolve()
            object.__setattr__(self, "path", resolved)
            self._exists()
        except (OSError, RuntimeError) as exc:
            raise StateSchemaIncompatibleError(
                f"путь файла состояния {path} недоступен: {type(exc).__name__}"
            ) from exc

    def _exists(self) -> bool:
        """Отличает первый запуск от недоступного или специального файла."""
        for component in (self.path, *self.path.parents):
            try:
                mode = component.stat().st_mode
            except OSError as exc:
                if isinstance(exc, FileNotFoundError) and not component.is_symlink():
                    continue
                raise StateSchemaIncompatibleError(
                    f"путь состояния {self.path} недоступен: {type(exc).__name__}"
                ) from exc
            if component != self.path and S_ISDIR(mode):
                return False
            if component == self.path and S_ISREG(mode):
                return True
            raise StateSchemaIncompatibleError(
                f"неверный тип файла или родительского каталога состояния {self.path}"
            )
        raise StateSchemaIncompatibleError(f"путь состояния {self.path} недоступен")

    def load(self) -> dict[str, Any]:
        """Читает состояние.

        Returns:
            dict[str, Any]: Сохранённое состояние. Пустой словарь, если файла
            нет: первый запуск - штатное событие.

        Raises:
            StateSchemaIncompatibleError: Если файл не читается вовсе либо
                записан другой версией схемы файла.
            CursorIncompatibleError: Если сохранённая позиция снята с другого
                семейства адаптера либо собрана другой канонической формой.
                Молчаливый старт с нуля здесь неотличим от штатной работы и
                приводит к повторной обработке всего, что уже обработано.
        """
        try:
            if not self._exists():
                return {}
            raw = load_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
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
        if stored_format not in (STATE_FORMAT, "funora-state-v5", "funora-state-v4"):
            raise StateSchemaIncompatibleError(
                f"файл состояния {self.path} записан форматом {stored_format!r}, "
                f"ожидался {STATE_FORMAT!r}"
            )

        # Семейство адаптера и каноническая форма - про КУРСОР, а версия схемы
        # файла выше - про файл. Спецификация делит их прямо: 1801 говорит
        # «курсор принадлежит другой версии формата или другому семейству
        # адаптера», 1802 - «версия схемы сохранённого состояния не
        # поддерживается». Обе ветки ниже подпадают под первое и возбуждали
        # второе.
        #
        # Разница не в номере. Она в том, что делать: чужая схема файла лечится
        # выходом новой версии SDK, чужое семейство - никогда. Курсор, снятый с
        # другой площадки, не станет совместимым от обновления.
        stored_family = raw.get("adapter_family")
        if stored_family != ADAPTER_FAMILY:
            raise CursorIncompatibleError(
                f"файл состояния {self.path} снят с семейства {stored_family!r}, "
                f"ожидалось {ADAPTER_FAMILY!r}. Сохранённая позиция принадлежит "
                "другой площадке и совместимой не станет"
            )

        stored_canonical = raw.get("canonical_form_version")
        if stored_canonical is not None and stored_canonical != CANONICAL_FORM_VERSION:
            raise CursorIncompatibleError(
                f"файл состояния {self.path} записан канонической формой "
                f"{stored_canonical!r}, ожидалась {CANONICAL_FORM_VERSION!r}. "
                "Сохранённые отпечатки собраны по другим правилам и не совпадут "
                "ни с чем"
            )

        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise StateSchemaIncompatibleError(
                f"файл состояния {self.path}: payload обязан быть объектом; "
                "начать с пустым журналом значило бы забыть уже выполненные действия"
            )
        if stored_format == "funora-state-v4" and payload.get("attempts"):
            raise CursorIncompatibleError(
                "файл v4 содержит незавершённые попытки без сохранённых событий; "
                "завершите их прежней версией SDK перед обновлением. "
                "Восстановить исходную партию по новому снимку невозможно"
            )
        if (
            "watch_owner" in payload
            and not {
                "watch_pending",
                "watch_greeted",
                "cursor",
            }
            <= payload.keys()
        ):
            raise StateSchemaIncompatibleError("в состоянии watch отсутствует журнал или курсор")
        if payload.get("attempts") and payload.get("watch_pending") is None:
            raise StateSchemaIncompatibleError("попытки watch сохранены без непринятой партии")
        return payload

    def update(self, patch: dict[str, Any]) -> None:
        """Правит часть состояния, не трогая остального.

        Нужен затем, что состояние пишут ДВОЕ и в разное время. Цикл наблюдения
        сохраняет курсоры и гашение раз в шаг; ограничитель исходящих обязан
        сохраниться сразу после отправки, иначе перезапуск между отправкой и
        концом шага теряет её из реестра - а реестр для того и заведён, чтобы
        часовая квота не обнулялась перезапуском.

        Прямая запись целиком тут не годится: сохранив один только реестр, мы
        затёрли бы курсоры, и перезапуск ушёл бы в холодный старт.

        Слияние поверхностное, по ключам верхнего уровня. Глубокого не нужно:
        разделы состояния независимы, и владелец у каждого один.

        Args:
            patch (dict[str, Any]): Ключи верхнего уровня, которые надо заменить.

        Returns:
            None

        Raises:
            StateSchemaIncompatibleError: Если существующий файл не читается.
            CursorIncompatibleError: Если он снят с другого семейства адаптера.
        """
        self._exists()
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            current = self.load()
            current.update(patch)
            self._save(current)

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
        self._exists()
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            self._save(payload)

    def _save(self, payload: dict[str, Any]) -> None:
        self._exists()
        body = canonical_dumps(
            {
                "format": STATE_FORMAT,
                "adapter_family": ADAPTER_FAMILY,
                "canonical_form_version": CANONICAL_FORM_VERSION,
                "payload": payload,
            }
        )
        atomic_write(self.path, body)
