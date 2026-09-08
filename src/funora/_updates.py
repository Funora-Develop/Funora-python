"""Разбор ответа канала обновлений.

ЧТО ЭТО ЗА КАНАЛ. Площадка держит его для собственной страницы: один POST с
подпиской, в ответ - изменения только тех объектов, на которые подписан. Ничего
не изменилось - объекты пусты.

ПОЧЕМУ ЭТО ВАЖНЕЕ ВСЕГО ОСТАЛЬНОГО. Наблюдение сегодня читает две полные
страницы на КАЖДОМ шаге, изменилось что-нибудь или нет. Канал отвечает малым
телом и молчит, когда молчать нечего.

Три факта, наблюдённые 30.08.2026 и записанные в spec/extraction/updates.yaml:

Метка - это КВИТАНЦИЯ «я видел до сюда», а не пропуск. Площадка принимает любую,
в том числе выдуманную, и отвечает всем, что изменилось с той поры. Свою метку
она возвращает в ответе; тот же опрос с ней даёт пустоту.

Подписка длиннее ДЕСЯТИ объектов обрезается МОЛЧА. Послано одиннадцать - пришло
десять, без единого признака, что один отброшен.

Счётчики приходят ЧИСЛАМИ, а не выводятся из разметки.

ЧТО ЗДЕСЬ НЕ РАЗБИРАЕТСЯ. Поле html внутри объектов - разметка переписки, и
разбирать её тем же кодом, что разбирает страницу, никто не проверял. Она
проходит насквозь как непрозрачная строка: разбор по догадке разошёлся бы с
площадкой молча.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Final

from .errors import ProtocolChangedError

__all__ = [
    "MAX_SUBSCRIPTION",
    "UpdatesAnswer",
    "ChannelObject",
    "build_subscription",
    "parse_updates_answer",
]

#: Сколько объектов площадка принимает в одной подписке.
#:
#: Наблюдено, а не взято у чужой реализации: послано одиннадцать узлов диалога,
#: вернулось десять. Чужая константа была поводом проверить, а не свидетельством.
#:
#: МОЛЧАЛИВОСТЬ ОБРЕЗКИ - главное в этом числе. Признака, что объект отброшен, в
#: ответе нет никакого. Подписавшийся на пятнадцать диалогов получит десять и не
#: узнает об этом ниоткуда: сообщения пяти покупателей не придут, и объяснить это
#: будет нечем.
MAX_SUBSCRIPTION: Final[int] = 10


@dataclass(frozen=True, slots=True)
class ChannelObject:
    """Один изменившийся объект из ответа канала.

    Attributes:
        type (str): Вид объекта, как его назвала площадка.
        id (str): Идентификатор объекта.
        tag (str): Непустая метка для следующего опроса.
        data (dict[str, Any]): Данные объекта как есть. Разметка внутри НЕ
            разбирается: она проходит насквозь непрозрачной строкой.
    """

    type: str
    id: str
    tag: str
    data: dict[str, Any] = field(default_factory=dict)

    def number(self, name: str) -> int | None:
        """Читает целое поле данных.

        Числа - единственное, что этот разбор берёт из данных объекта, и берёт
        он их без толкования: что означает счётчик, решает вызывающий.

        Возвращается None, а не ноль, когда поля нет. Ноль означал бы «счётчик
        равен нулю», а это другое утверждение.

        Аргументы:
            name (str): Имя поля.

        Возвращает:
            int | None: Значение либо None, если поля нет или оно не целое.
        """
        value = self.data.get(name)
        # Логическое исключается отдельно: в Python True - это единица, и
        # счётчик, оказавшийся булевым, прочитался бы числом молча.
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value


@dataclass(frozen=True, slots=True)
class UpdatesAnswer:
    """Ответ канала обновлений.

    Attributes:
        objects (tuple[ChannelObject, ...]): Изменившиеся объекты. Пусто -
            штатное состояние, а не признак поломки: канал молчит, когда молчать
            нечего.
        error (object | None): Поле ошибки без преобразования. Только None означает
            отсутствие отказа; форма непустого поля не интерпретируется.
        answered_action (bool): Был ли в запросе действие. Площадка отвечает
            объектом при опросе С ДЕЙСТВИЕМ и логическим - при опросе без него.
    """

    objects: tuple[ChannelObject, ...]
    error: object | None
    answered_action: bool

    @property
    def is_quiet(self) -> bool:
        """Говорит, что изменений не пришло.

        Returns:
            bool: True, если объектов нет.
        """
        return not self.objects

    def tags(self, known: dict[tuple[str, str], str] | None = None) -> dict[tuple[str, str], str]:
        """Собирает метки для следующего опроса, НАКАПЛИВАЯ прежние.

        Ключ - пара из вида и идентификатора, а не один вид: подписка держит по
        объекту на каждый диалог, и вид у них общий.

        НАКОПЛЕНИЕ - не удобство, а условие работоспособности. Ответ несёт
        только ИЗМЕНИВШИЕСЯ объекты: у молчавшего диалога метки в ответе нет
        вовсе. Собирая метки лишь из последнего ответа, опрос подставлял бы
        всем молчавшим метку «я ничего не видел» - и площадка отдавала бы их
        целиком заново на каждом шаге.

        То есть без накопления канал перестаёт быть дешевле страниц ровно в
        тот момент, когда становится нужен: при десяти диалогах и одном
        говорящем девять приезжали бы полностью каждый раз.

        Аргументы:
            known (dict[tuple[str, str], str] | None): Метки прошлых опросов.
                Пришедшие в этом ответе их вытесняют.

        Returns:
            dict[tuple[str, str], str]: Метки по объектам.
        """
        collected = dict(known or {})
        collected.update({(one.type, one.id): one.tag for one in self.objects if one.tag})
        return collected


def build_subscription(
    wanted: list[tuple[str, str]], tags: dict[tuple[str, str], str]
) -> list[list[dict[str, Any]]]:
    """Собирает подписку ПОРЦИЯМИ не длиннее предела.

    Порции считает вызывающий, а не площадка: она обрезает лишнее молча, и
    заметить обрезку в ответе нечем.

    Объект без известной метки получает выдуманную. Это не обход защиты, а
    значение «я ничего не видел»: площадка отвечает на него всем, что изменилось,
    - ровно так же, как отвечает странице при первом обращении.

    Аргументы:
        wanted (list[tuple[str, str]]): На что подписываться: вид и
            идентификатор.
        tags (dict[tuple[str, str], str]): Метки прошлого ответа.

    Возвращает:
        list[list[dict[str, Any]]]: Подписки порциями, каждая не длиннее
        предела.
    """
    objects = [
        {
            "type": kind,
            "id": entity,
            "tag": tags.get((kind, entity), UNSEEN_TAG),
            "data": False,
        }
        for kind, entity in wanted
    ]
    return [
        objects[at : at + MAX_SUBSCRIPTION] for at in range(0, len(objects), MAX_SUBSCRIPTION)
    ] or [[]]


#: Метка, означающая «я ничего не видел».
#:
#: Годится любая несуществующая: площадка отвечает на неё всем, что изменилось.
#: Наблюдено 30.08.2026 - подписка с этой самой строкой вернула оба объекта
#: целиком, а повторная с вернувшимися метками вернула пустоту.
UNSEEN_TAG: Final[str] = "0000000000"


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("повтор поля в ответе канала")
        result[name] = value
    return result


def _finite_number(raw: str) -> float:
    value = float(raw)
    if not isfinite(value):
        raise ValueError("неконечное число в ответе канала")
    return value


def load_runner_json(body: str) -> object:
    """Читает JSON канала без потери полей и неконечных чисел.

    Опрос и отправка делят один декодер. Ошибки ValueError/RecursionError
    вызывающий переводит в свой исход: откат к страницам либо unconfirmed.
    """
    return json.loads(
        body,
        object_pairs_hook=_unique_fields,
        parse_float=_finite_number,
        parse_constant=_finite_number,
    )


def parse_updates_answer(body: str) -> UpdatesAnswer:
    """Разбирает тело ответа канала.

    Декодер JSON общий с отправкой. Опрос дополнительно проверяет все объекты
    подписки, прежде чем вызывающий сможет подтвердить их метки.

    Аргументы:
        body (str): Тело ответа.

    Возвращает:
        UpdatesAnswer: Разобранный ответ.

    Raises:
        ProtocolChangedError: Если тело не разбирается либо устроено не так,
            как наблюдалось.
    """
    try:
        parsed = load_runner_json(body)
    except (ValueError, RecursionError) as exc:
        raise ProtocolChangedError(
            "ответ канала обновлений не разобрался как JSON. Канал отвечал JSON "
            "во всех наблюдениях; разбор чего-то иного означал бы, что мы "
            "приняли за канал не канал"
        ) from exc

    if not isinstance(parsed, dict):
        raise ProtocolChangedError(
            f"ответ канала обновлений - не объект, а {type(parsed).__name__}"
        )

    raw = parsed.get("objects")
    if not isinstance(raw, list):
        raise ProtocolChangedError(
            "в ответе канала обновлений нет перечня объектов. Пустой перечень - "
            "штатное состояние, а отсутствие поля означает другой ответ"
        )

    answer = parsed.get("response")
    # Объект при опросе С ДЕЙСТВИЕМ, логическое - без действия. Различие
    # наблюдено, и по нему же читается ошибка: у логического ошибке взяться
    # неоткуда.
    error = None
    if isinstance(answer, dict):
        if "error" not in answer:
            raise ProtocolChangedError("в ответе действия канала нет поля error")
        action = True
        error = answer["error"]
    elif isinstance(answer, bool):
        action = False
    else:
        raise ProtocolChangedError("поле response канала - не логическое и не объект действия")

    objects: list[ChannelObject] = []
    seen: set[tuple[str, str]] = set()
    for one in raw:
        if not isinstance(one, dict):
            raise ProtocolChangedError("элемент objects канала - не объект")
        kind = one.get("type")
        if not isinstance(kind, str) or not kind:
            raise ProtocolChangedError("у объекта канала нет непустого строкового type")
        entity = one.get("id")
        if not (type(entity) is int or isinstance(entity, str) and entity):
            raise ProtocolChangedError("id объекта канала - не целое число и не непустая строка")
        tag = one.get("tag")
        if not isinstance(tag, str) or not tag:
            raise ProtocolChangedError("у объекта канала нет непустой строковой tag")
        data = one.get("data")
        if not isinstance(data, dict):
            raise ProtocolChangedError("data объекта канала - не объект")
        key = (kind, str(entity))
        try:
            for value in (*key, tag):
                value.encode("utf-8")
        except UnicodeEncodeError:
            raise ProtocolChangedError(
                "ключ или метка канала содержит некорректный Unicode"
            ) from None
        if key in seen:
            raise ProtocolChangedError("в ответе канала повторяется объект подписки")
        seen.add(key)
        objects.append(
            ChannelObject(
                type=kind,
                id=key[1],
                tag=tag,
                data=data,
            )
        )

    return UpdatesAnswer(objects=tuple(objects), error=error, answered_action=action)
