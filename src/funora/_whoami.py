"""Чтение аккаунта, проверка сессии и профиль возможностей.

Три операции об одном: от чьего имени работает клиент и годна ли его сессия.

Имя модуля - _whoami, а не _identity: последнее занято сетевой идентичностью,
то есть парой «исходящий адрес и целевой хост». Разные вещи с похожими именами
стоят рядом, и путать их дорого: одно про то, кем нас видит площадка, другое про
то, откуда мы к ней приходим.

ЧИТАЕТСЯ СТРАНИЦА С ВИДЖЕТОМ ПЕРЕПИСКИ, и это не безразличная подробность.
Собственный идентификатор лежит в атрибуте data-user, а атрибут этот есть только
там, где виджет есть: на списке продаж и на странице баланса его нет вовсе. С
любой авторизованной страницы прочесть себя нельзя.

ИМЯ И ИДЕНТИФИКАТОР ЧИТАЮТСЯ ПО-РАЗНОМУ. Идентификатор - из атрибута, имя - из
текста в шапке, и узлов имени ДВА: настольное меню и мобильное. Значение в них
одно и то же, и разбор обязан их сверить: разошлись - разметка изменилась.

ПРОФИЛЬ ВОЗМОЖНОСТЕЙ СОБИРАЕТСЯ БЕЗ СЕТИ. Он отвечает на «что этот клиент умеет
прямо сейчас», а это уже известно из того, что наблюдалось. Неуспешная проба
даёт unknown, а не unsupported: отсутствие объявляется только по положительному
свидетельству, и неудача таковым не является.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._classify import ResponseClass, Verdict
from ._observed import Observed
from ._result import Defect, Severity
from ._secret import Secret
from .capabilities import Capability, CapabilityState
from .errors import ProtocolChangedError
from .extraction import SELECTORS

__all__ = [
    "Account",
    "AppData",
    "CapabilityProfile",
    "SessionHealth",
    "parse_account",
    "parse_app_data",
]

_OWN_ID: Final[str] = SELECTORS["session.identity.own_user_id"]
_OWN_NAME: Final[str] = SELECTORS["session.identity.own_username"]
_APP_DATA: Final[str] = SELECTORS["session.identity.app_data"]

#: Имя ключа защитного токена внутри объекта настроек страницы.
_CSRF_KEY: Final[str] = "csrf-token"


@dataclass(frozen=True, slots=True)
class Account:
    """Аккаунт, от имени которого работает клиент.

    Балансов здесь нет намеренно: они лежат на другой странице, и читать её ради
    профиля значило бы ходить на площадку дважды за одним ответом. Отсутствие
    поля означает «не наблюдали», а не «нулю равно».

    Attributes:
        user_id (Observed[str]): Собственный идентификатор из атрибута.
        username (Observed[str]): Собственное отображаемое имя.
        locale (Observed[str]): Метка языка страницы.
        observed_at (datetime): Момент наблюдения.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    user_id: Observed[str]
    username: Observed[str]
    locale: Observed[str]
    observed_at: datetime
    defects: tuple[Defect, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionHealth:
    """Пригодность сессии.

    Attributes:
        response_class (ResponseClass): Класс ответа площадки.
        is_usable (bool): Годится ли сессия для работы прямо сейчас.
        reason (str): Машиночитаемая причина решения классификатора.
        provisional (bool): Принято ли решение непроверенной сигнатурой.
        checked_at (datetime): Момент проверки.
        from_cache (bool): Отдан ли ответ из кэша, а не получен запросом.
    """

    response_class: ResponseClass
    is_usable: bool
    reason: str
    provisional: bool
    checked_at: datetime
    from_cache: bool

    @classmethod
    def of(cls, verdict: Verdict, checked_at: datetime, *, from_cache: bool) -> SessionHealth:
        """Собирает ответ из вердикта классификатора.

        Годной сессия считается РОВНО при классе ok. Прочие классы означают
        разное, но ни один из них не означает «можно работать»: правило «всё,
        кроме явного отказа, годится» однажды приняло бы страницу проверки за
        рабочую.

        Args:
            verdict (Verdict): Вердикт классификатора.
            checked_at (datetime): Момент проверки.
            from_cache (bool): Отдан ли ответ из кэша.

        Returns:
            SessionHealth: Пригодность сессии.
        """
        return cls(
            response_class=verdict.cls,
            is_usable=verdict.cls is ResponseClass.OK,
            reason=verdict.reason,
            provisional=verdict.provisional,
            checked_at=checked_at,
            from_cache=from_cache,
        )


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    """Состояние каждой возможности адаптера и аккаунта.

    Attributes:
        observed_at (datetime): Момент сборки профиля.
    """

    observed_at: datetime
    _states: dict[Capability, CapabilityState] = field(repr=False, default_factory=dict)

    def states(self) -> dict[Capability, CapabilityState]:
        """Возвращает состояние каждой возможности.

        Ключ объявлен ровно для КАЖДОЙ возможности контракта: профиль,
        умалчивающий о возможности, читался бы как «её нет», а это другой ответ.

        Returns:
            dict[Capability, CapabilityState]: Состояния по возможностям.
        """
        return dict(self._states)

    def state_of(self, capability: Capability) -> CapabilityState:
        """Возвращает состояние одной возможности.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Её состояние.

        Raises:
            KeyError: Если возможности нет в профиле. Такого быть не должно:
                профиль обязан называть каждую, и пробел здесь - дефект сборки,
                а не отсутствие возможности.
        """
        return self._states[capability]


def _text(node: Node | None, name: str) -> Observed[str]:
    """Извлекает текст узла как наблюдение.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    value = " ".join((node.text() or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _attribute(node: Node | None, name: str, field_name: str) -> Observed[str]:
    """Читает атрибут, различая три исхода.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя атрибута.
        field_name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{field_name}")
    attributes = node.attributes or {}
    if name not in attributes:
        return Observed.missing(f"attribute_absent:{field_name}")
    value = (attributes.get(name) or "").strip()
    return Observed.present(value) if value else Observed.empty("")


def _username(tree: HTMLParser) -> tuple[Observed[str], list[Defect]]:
    """Читает собственное имя и сверяет два его носителя.

    Узлов имени два - настольное меню и мобильное, - и значение в них одно и то
    же. Взять первый попавшийся значило бы повторить ошибку, на которой уже
    спотыкались разбор списка продаж и разбор отзывов.

    Args:
        tree (HTMLParser): Разобранная страница.

    Returns:
        tuple[Observed[str], list[Defect]]: Имя и перечень повреждений.
    """
    nodes = tree.css(_OWN_NAME)
    if not nodes:
        return Observed.missing(f"selector_no_match:{_OWN_NAME}"), []

    values = {" ".join((one.text() or "").split()) for one in nodes}
    if len(values) > 1:
        return (
            Observed.missing("username_carriers_disagree"),
            [
                Defect(
                    severity=Severity.PAGE,
                    code="username_carriers_disagree",
                    detail=(
                        f"узлы имени разошлись, прочитано {sorted(values)}. "
                        "Взять любое значило бы выбрать наугад"
                    ),
                    field_name="username",
                )
            ],
        )

    value = values.pop()
    return (Observed.present(value) if value else Observed.empty("")), []


def parse_account(html: str, observed_at: datetime) -> Account:
    """Разбирает страницу и собирает сведения о собственном аккаунте.

    Args:
        html (str): Тело страницы. Предполагается уже классифицированным как
            пригодное: состояние сессии здесь не проверяется.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        Account: Собственный идентификатор, имя и метка языка.

    Raises:
        ProtocolChangedError: Если на странице нет ни одного признака собственной
            личности. Пустой ответ вернуть нельзя: он неотличим от страницы,
            отданной не нам.
    """
    tree = HTMLParser(html)

    carrier = tree.css_first(_OWN_ID)
    if carrier is None and not tree.css(_OWN_NAME):
        raise ProtocolChangedError(
            f"на странице нет ни носителя собственного идентификатора ({_OWN_ID}), "
            f"ни собственного имени ({_OWN_NAME}). Пустой ответ вернуть нельзя: "
            "он неотличим от страницы, отданной не нам"
        )

    username, defects = _username(tree)

    if carrier is None:
        # Атрибут есть не на всякой странице: там, где виджета переписки нет,
        # нет и его. Это не поломка - это выбор страницы, и он записан
        # повреждением, чтобы вызывающий не принял пробел за отсутствие
        # идентификатора у аккаунта.
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="own_id_carrier_missing",
                detail=(
                    f"на странице нет носителя собственного идентификатора ({_OWN_ID}). "
                    "Он есть только там, где есть виджет переписки"
                ),
                field_name="user_id",
            )
        )

    root = tree.css_first("html")
    return Account(
        user_id=_attribute(carrier, "data-user", "user_id"),
        username=username,
        locale=_attribute(root, "lang", "locale"),
        observed_at=observed_at,
        defects=tuple(defects),
    )


@dataclass(frozen=True, slots=True)
class AppData:
    """Настройки страницы из атрибута data-app-data.

    ЗДЕСЬ ЛЕЖИТ ЗАЩИТНЫЙ ТОКЕН, без которого не собрать ни одного запроса
    записи. Отдаётся он обёрнутым в Secret: значение видно только по явному
    вызову reveal, и ни в repr, ни в журнал, ни в сообщение об ошибке не
    попадает. Строкой его не отдают нарочно - подстановка в f-строку случайна,
    вызов reveal виден при чтении кода.

    Идентификатор и метка языка лежат в том же объекте ВТОРЫМИ носителями: их
    же несут атрибут data-user и html[lang]. Сверка носителей - дело
    вызывающего разбора, здесь читается ровно то, что написано.

    Attributes:
        csrf_token (Secret | None): Защитный токен. None, если не прочитан.
        user_id (Observed[str]): Собственный идентификатор из объекта.
        locale (Observed[str]): Метка языка из объекта.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    csrf_token: Secret | None
    user_id: Observed[str]
    locale: Observed[str]
    defects: tuple[Defect, ...] = ()


def _json_field(data: dict[str, object], key: str, field_name: str) -> Observed[str]:
    """Читает строковое поле объекта настроек как наблюдение.

    Args:
        data (dict[str, object]): Разобранный объект настроек.
        key (str): Имя ключа в объекте.
        field_name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if key not in data:
        return Observed.missing(f"key_absent:{field_name}")
    value = data[key]
    if not isinstance(value, str):
        # Число здесь читалось бы как строка молча, и разница вышла бы наружу
        # только при сравнении с другим носителем.
        return Observed.missing(f"key_not_a_string:{field_name}")
    value = value.strip()
    return Observed.present(value) if value else Observed.empty("")


def parse_app_data(html: str) -> AppData:
    """Разбирает объект настроек страницы и достаёт защитный токен.

    НЕ ПАДАЕТ НИ НА ЧЁМ. Атрибута может не быть, он может не разобраться, в нём
    может не быть нужного ключа - и всё это разные обстоятельства с разными
    причинами. Отказ здесь превратил бы «токена нет» в «страница сломана», а это
    решается по-разному: первое ведёт к перезаходу, второе к разбирательству с
    разметкой.

    Отдельно стоит случай СТАРОГО СНИМКА. До формата скелета v8 атрибут
    маскировался целиком, одной подписью, и разобрать его как JSON нельзя. Это
    не поломка площадки, а возраст снимка, и причина названа отдельно.

    Args:
        html (str): Тело страницы.

    Returns:
        AppData: Токен, идентификатор и метка языка из объекта настроек.
    """
    tree = HTMLParser(html)
    carrier = tree.css_first(_APP_DATA)
    if carrier is None:
        return AppData(
            csrf_token=None,
            user_id=Observed.missing("app_data_carrier_missing"),
            locale=Observed.missing("app_data_carrier_missing"),
            defects=(
                Defect(
                    severity=Severity.PAGE,
                    code="app_data_carrier_missing",
                    detail=f"на странице нет носителя настроек ({_APP_DATA})",
                    field_name="csrf_token",
                ),
            ),
        )

    raw = (carrier.attributes or {}).get("data-app-data") or ""
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        parsed = None

    if not isinstance(parsed, dict):
        masked = raw.startswith("T") and ":" in raw
        code = "app_data_masked_by_skeleton" if masked else "app_data_not_json"
        detail = (
            "атрибут настроек замаскирован скелетом целиком: снимок сделан "
            "форматом старше v8, и ключей объекта в нём нет"
            if masked
            else "атрибут настроек не разбирается как объект JSON"
        )
        return AppData(
            csrf_token=None,
            user_id=Observed.missing(code),
            locale=Observed.missing(code),
            defects=(
                Defect(severity=Severity.PAGE, code=code, detail=detail, field_name="csrf_token"),
            ),
        )

    defects: list[Defect] = []
    token: Secret | None = None
    value = parsed.get(_CSRF_KEY)
    if not isinstance(value, str) or not value.strip():
        # Значения токена в сообщении нет и быть не может: оно часть сессии
        # владельца. Названо только то, чего не хватило.
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="csrf_token_absent",
                detail=(
                    f"в объекте настроек нет пригодного ключа {_CSRF_KEY}. "
                    f"Прочитанные ключи: {sorted(parsed)}"
                ),
                field_name="csrf_token",
            )
        )
    else:
        token = Secret(value.strip(), label=_CSRF_KEY)

    return AppData(
        csrf_token=token,
        user_id=_json_field(parsed, "userId", "user_id"),
        locale=_json_field(parsed, "locale", "locale"),
        defects=tuple(defects),
    )
