"""Классификация ответа до разбора страницы.

Порядок шагов здесь важнее их содержания. Если поставить проверку отпечатка
раньше детекторов, страница логина, отданная с кодом 200, уйдёт в парсер как
валидная и вернёт пустой список - а пустой список неотличим от «данных нет».
Страница проверки при этом станет неотличима от поломки разметки, и клиент
продолжит стучаться, подтверждая подозрение.

Конвейер:

  1. Транспорт и код ответа.
  2. Проверка личности: конечный URL после переходов принадлежит ожидаемому
     хосту, а в теле есть признак того, что мы вошли под ожидаемым аккаунтом.
     Тот же шаг ловит перепутанные cookie при работе с несколькими аккаунтами.
  3. Детекторы страниц-перехватчиков: логин, проверка, блокировка, обслуживание.
  4. Отпечаток страницы.
  5. Разбор.

Шаги 4 и 5 живут в адаптере и в этом модуле не реализованы.

Признаки выхода из сессии подтверждены наблюдением 18.08.2026 и помечены
provisional=False. Признаки проверки, блокировки и технических работ остаются
умозрительными: таких страниц мы ещё не видели, и придумывать их точный вид
хуже, чем честно вернуть unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

__all__ = [
    "ResponseClass",
    "DEFAULT_IDENTITY_CSS",
    "Verdict",
    "Signature",
    "classify",
    "DEFAULT_SIGNATURES",
]


class ResponseClass(StrEnum):
    """Класс ответа площадки.

    Значения намеренно различают состояния доступа и поломку разметки: это разные
    проблемы с разным лечением, а внешне они выглядят одинаково.
    """

    OK = "ok"
    LOGIN_REQUIRED = "login_required"
    CHALLENGE = "challenge"
    BLOCKED = "blocked"
    MAINTENANCE = "maintenance"
    WRONG_IDENTITY = "wrong_identity"
    TRANSPORT_ERROR = "transport_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Signature:
    """Признак, по которому распознаётся страница-перехватчик.

    Args:
        name (str): Имя сигнатуры для диагностики.
        verdict (ResponseClass): Класс, который присваивается при срабатывании.
        css (tuple[str, ...]): Селекторы, наличие любого из которых считается
            срабатыванием.
        patterns (tuple[str, ...]): Регулярные выражения по тексту страницы.
            Проверяются только если селекторы не заданы или не сработали.
        provisional (bool): Признак того, что сигнатура составлена умозрительно
            и не подтверждена наблюдением настоящей страницы.
    """

    name: str
    verdict: ResponseClass
    css: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    provisional: bool = True


@dataclass(frozen=True, slots=True)
class Verdict:
    """Результат классификации.

    Args:
        cls (ResponseClass): Класс ответа.
        reason (str): Машиночитаемая причина решения.
        matched (str | None): Имя сработавшей сигнатуры, если решение принято ею.
        provisional (bool): True, если решение принято непроверенной сигнатурой
            и потому требует ручного подтверждения.
        detail (dict[str, str]): Дополнительные сведения для диагностики.
            Содержимого страницы и персональных данных здесь нет.
    """

    cls: ResponseClass
    reason: str
    matched: str | None = None
    provisional: bool = False
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        """Сообщает, можно ли передавать ответ дальше по конвейеру.

        Returns:
            bool: True, если ответ пригоден для проверки отпечатка и разбора.
        """
        return self.cls is ResponseClass.OK


#: Селектор, подтверждающий, что страница отдана вошедшему пользователю.
#:
#: Наблюдение 18.08.2026 показало пару признаков, различающих состояния: у
#: вошедшего в шапке стоит navbar-toggle-logged, у гостя - navbar-toggle-guest.
#: Признак структурный и не зависит от языка интерфейса, что здесь принципиально:
#: локаль привязана к аккаунту, а не к адресу, и через URL её не переключить.
DEFAULT_IDENTITY_CSS: Final[str] = ".navbar-toggle-logged"

#: Сигнатуры по умолчанию.
#:
#: Признаки выхода из сессии подтверждены наблюдением и помечены provisional=False.
#: Признаки проверки, блокировки и технических работ остаются умозрительными:
#: таких страниц мы ещё не видели, и придумывать их точный вид хуже, чем честно
#: вернуть unknown.
DEFAULT_SIGNATURES: Final[tuple[Signature, ...]] = (
    Signature(
        name="guest_navbar",
        verdict=ResponseClass.LOGIN_REQUIRED,
        css=(".navbar-toggle-guest", ".menu-item-login", ".menu-item-register"),
        provisional=False,
    ),
    Signature(
        name="login_page",
        verdict=ResponseClass.LOGIN_REQUIRED,
        css=(".content-account-login", ".modal-auth"),
        provisional=False,
    ),
    Signature(
        name="login_form",
        verdict=ResponseClass.LOGIN_REQUIRED,
        css=('input[type="password"]', 'form[action*="login"]', 'form[action*="auth"]'),
    ),
    Signature(
        name="challenge_widget",
        verdict=ResponseClass.CHALLENGE,
        css=(
            "#challenge-form",
            ".g-recaptcha",
            ".h-captcha",
            "[data-sitekey]",
            'script[src*="captcha"]',
        ),
    ),
    Signature(
        name="challenge_text",
        verdict=ResponseClass.CHALLENGE,
        patterns=(r"\bcaptcha\b", r"подозрительн", r"подтвердите,?\s+что\s+вы"),
    ),
    Signature(
        name="blocked_text",
        verdict=ResponseClass.BLOCKED,
        patterns=(r"\bбан\b", r"заблокирован", r"доступ\s+запрещ", r"access\s+denied"),
    ),
    Signature(
        name="maintenance_text",
        verdict=ResponseClass.MAINTENANCE,
        patterns=(r"технически[ей]\s+работ", r"maintenance", r"временно\s+недоступ"),
    ),
)

#: Коды, при которых тело разбирать бессмысленно.
_HARD_STATUS: Final[dict[int, tuple[ResponseClass, str]]] = {
    401: (ResponseClass.LOGIN_REQUIRED, "http_401"),
    403: (ResponseClass.BLOCKED, "http_403"),
    429: (ResponseClass.BLOCKED, "http_429"),
    503: (ResponseClass.MAINTENANCE, "http_503"),
}

#: Максимум текста, по которому идёт поиск текстовых сигнатур. Ограничение
#: защищает от страницы, раздутой намеренно или по ошибке.
_TEXT_LIMIT: Final[int] = 200_000


def _page_text(html: str) -> str:
    """Извлекает видимый текст страницы в нижнем регистре.

    Args:
        html (str): Исходный HTML.

    Returns:
        str: Текст страницы, обрезанный до предела и приведённый к нижнему регистру.
    """
    try:
        tree = HTMLParser(html[:_TEXT_LIMIT])
    except Exception:
        return ""
    body = tree.body or tree.root
    if body is None:
        return ""
    return (body.text(separator=" ") or "").lower()


def classify(
    *,
    status: int,
    final_url: str,
    html: str,
    expected_host: str,
    identity_css: str | None = DEFAULT_IDENTITY_CSS,
    signatures: tuple[Signature, ...] = DEFAULT_SIGNATURES,
) -> Verdict:
    """Классифицирует ответ площадки.

    Args:
        status (int): Код состояния HTTP.
        final_url (str): URL после всех переходов. Важен именно конечный: переход
            на страницу входа - самый частый способ получить код 200 с чужим
            содержимым.
        html (str): Тело ответа.
        expected_host (str): Хост, которому должен принадлежать конечный URL.
        identity_css (str | None): Селектор элемента, который присутствует только
            на страницах, отданных вошедшему пользователю. По умолчанию берётся
            подтверждённый наблюдением признак. Передайте None, чтобы пропустить
            шаг проверки личности; это отразится в причине.
        signatures (tuple[Signature, ...]): Реестр сигнатур детекторов.

    Returns:
        Verdict: Класс ответа с причиной и признаком того, было ли решение принято
        непроверенной сигнатурой.
    """
    # Шаг 1. Транспорт и код ответа.
    if status in _HARD_STATUS:
        cls, reason = _HARD_STATUS[status]
        return Verdict(cls=cls, reason=reason, detail={"status": str(status)})
    if status >= 500:
        return Verdict(
            cls=ResponseClass.TRANSPORT_ERROR, reason="http_5xx", detail={"status": str(status)}
        )
    if status >= 400:
        return Verdict(
            cls=ResponseClass.TRANSPORT_ERROR, reason="http_4xx", detail={"status": str(status)}
        )

    # Шаг 2. Проверка личности. Идёт до детекторов: ответ с чужого хоста не
    # заслуживает того, чтобы его разбирали, каким бы он ни выглядел.
    host = urlparse(final_url).hostname or ""
    if host and host != expected_host and not host.endswith("." + expected_host):
        return Verdict(
            cls=ResponseClass.WRONG_IDENTITY,
            reason="host_mismatch",
            detail={"expected": expected_host, "actual": host},
        )

    if not html.strip():
        return Verdict(cls=ResponseClass.UNKNOWN, reason="empty_body")

    # Шаг 3. Детекторы страниц-перехватчиков.
    tree: HTMLParser | None
    try:
        tree = HTMLParser(html)
    except Exception:
        tree = None

    text: str | None = None
    for sig in signatures:
        if tree is not None:
            for selector in sig.css:
                try:
                    if tree.css_first(selector) is not None:
                        return Verdict(
                            cls=sig.verdict,
                            reason=f"signature:{sig.name}",
                            matched=sig.name,
                            provisional=sig.provisional,
                            detail={"selector": selector},
                        )
                except Exception:
                    continue
        if sig.patterns:
            if text is None:
                text = _page_text(html)
            for pattern in sig.patterns:
                if re.search(pattern, text):
                    return Verdict(
                        cls=sig.verdict,
                        reason=f"signature:{sig.name}",
                        matched=sig.name,
                        provisional=sig.provisional,
                        detail={"pattern": pattern},
                    )

    # Шаг 2 продолжение: признак вошедшего пользователя проверяется после
    # детекторов. Его отсутствие само по себе не означает, что мы не вошли -
    # оно может означать, что изменилась разметка, и это разные диагнозы.
    if identity_css:
        if tree is None or tree.css_first(identity_css) is None:
            return Verdict(
                cls=ResponseClass.UNKNOWN,
                reason="identity_marker_absent",
                detail={"selector": identity_css},
            )
        return Verdict(cls=ResponseClass.OK, reason="identity_confirmed")

    return Verdict(cls=ResponseClass.OK, reason="no_signature_matched_identity_unchecked")
