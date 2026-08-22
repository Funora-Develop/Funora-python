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

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from selectolax.parser import HTMLParser

from ._host import host_of, same_host
from .response_classes import STATUS_CLASS

__all__ = [
    "ResponseClass",
    "DEFAULT_IDENTITY_CSS",
    "DEFAULT_CONTENT_MARKERS",
    "Verdict",
    "Signature",
    "classify",
    "DEFAULT_SIGNATURES",
]


class ResponseClass(StrEnum):
    """Класс ответа площадки.

    Значения намеренно различают состояния доступа и поломку разметки: это разные
    проблемы с разным лечением, а внешне они выглядят одинаково.

    Превышение частоты отделено от блокировки по той же причине. Блокировка
    трактуется как отказ с закрытым замком, и попади код 429 в неё, первое же
    попадание в ограничение остановило бы опрос навсегда, а политика повторов
    для этого случая осталась бы недостижимым кодом. «Слишком быстро» и «вам
    сюда нельзя» - разные ответы.
    """

    OK = "ok"
    LOGIN_REQUIRED = "login_required"
    CHALLENGE = "challenge"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
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
_log = logging.getLogger("funora.classify")

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
#:
#: Таблица строится из порождённой, а не пишется здесь. Рукописная копия жила
#: рядом с объявленной в spec/extraction/session.yaml и ничем с ней не
#: сверялась: правка спецификации перестраивала бы порождённый файл, оставляя
#: поведение прежним. Расхождение обнаружилось бы в работе - и ровно на той
#: ошибке, от которой спецификация предостерегает отдельным разделом: код 429,
#: принятый за блокировку, навсегда останавливает опрос.
#:
#: Причина собирается из кода: она уходит в таблицу «вердикт - ошибка», где
#: ключи имеют вид http_401. Собирать её здесь дешевле, чем объявлять в
#: спецификации второе имя для того же числа.
_HARD_STATUS: Final[dict[int, tuple[ResponseClass, str]]] = {
    code: (ResponseClass(name), f"http_{code}") for code, name in STATUS_CLASS.items()
}

#: Максимум текста, по которому идёт поиск текстовых сигнатур. Ограничение
#: защищает от страницы, раздутой намеренно или по ошибке.
_TEXT_LIMIT: Final[int] = 200_000


#: Признаки того, что страница отдана приложением, а не перехватчиком.
#:
#: Перечень отвечает на вопрос «узнаём ли мы эту страницу», и текстовые сигнатуры
#: применяются только тогда, когда ответ отрицательный.
#:
#: Прежде здесь стоял обратный перечень: где на странице пишут люди. Форма была
#: неверна. Перечислять пользовательские участки значит перечислять их все, а
#: любой пропущенный открывает дыру заново - что и случилось: заголовок лота в
#: боковой панели переписки не попал в перечень, и одного слова в нём хватало,
#: чтобы весь конвейер признал страницу блокировкой и остановил бота.
#:
#: Перечень признаков площадки закрыт по построению: настоящая страница проверки
#: или блокировки заменяет содержимое целиком, и ни таблицы заказов, ни виджета
#: переписки на ней нет.
DEFAULT_CONTENT_MARKERS: Final[tuple[str, ...]] = (
    ".orders-table",
    ".chat-contacts",
    ".chat-message-list",
    ".contact-list",
    ".content-account",
)


def _page_text(html: str) -> str:
    """Извлекает видимый текст страницы в нижнем регистре.

    Args:
        html (str): Исходный HTML.

    Returns:
        str: Текст страницы, обрезанный до предела и приведённый к нижнему
        регистру.
    """
    try:
        tree = HTMLParser(html[:_TEXT_LIMIT])
    except Exception:
        return ""
    body = tree.body or tree.root
    if body is None:
        return ""
    return (body.text(separator=" ") or "").lower()


def _matches(tree: HTMLParser, selector: str, where: str) -> bool:
    """Применяет селектор, не давая непригодному уронить классификацию.

    Непригодный селектор пропускается, а не роняет разбор: классификация идёт по
    живой странице, и уронить её из-за одного выражения значило бы превратить
    опечатку в отказ читать площадку вовсе.

    Но и молча пропускать нельзя. Молча пропущенный селектор выключает свою
    подпись насовсем: страница входа перестаёт узнаваться, вердикт уходит в
    «разметка изменилась», и виноватой выглядит площадка. Отсюда строка в
    журнале - на каждое применение по разу, зато с именем виноватого.

    Args:
        tree (HTMLParser): Разобранный документ.
        selector (str): Выражение CSS.
        where (str): Откуда селектор взят - для строки журнала.

    Returns:
        bool: True, если селектор нашёл узел. False, если не нашёл либо
        оказался непригодным.
    """
    try:
        return tree.css_first(selector) is not None
    except Exception as exc:
        _log.warning(
            "селектор %r из %s не применился (%s); подпись работает без него",
            selector,
            where,
            type(exc).__name__,
        )
        return False


def _is_known_page(tree: HTMLParser | None, markers: tuple[str, ...]) -> bool:
    """Сообщает, узнаётся ли страница как отданная приложением.

    Args:
        tree (HTMLParser | None): Разобранный документ.
        markers (tuple[str, ...]): Признаки страниц приложения.

    Returns:
        bool: True, если найден хотя бы один признак.
    """
    if tree is None:
        return False
    return any(_matches(tree, selector, "признаков страниц приложения") for selector in markers)


def classify(
    *,
    status: int,
    final_url: str,
    html: str,
    expected_host: str,
    identity_css: str | None = DEFAULT_IDENTITY_CSS,
    signatures: tuple[Signature, ...] = DEFAULT_SIGNATURES,
    content_markers: tuple[str, ...] = DEFAULT_CONTENT_MARKERS,
    declared_length: int | None = None,
    received_length: int | None = None,
    content_encoding: str = "",
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
        declared_length (int | None): Длина тела, объявленная заголовком.
        received_length (int | None): Длина полученного тела.
        content_encoding (str): Кодирование тела. Непустое означает, что длины
            несравнимы: объявлена сжатая, получена распакованная.
        content_markers (tuple[str, ...]): Признаки страниц приложения. Если
            найден хотя бы один, текстовые сигнатуры не применяются вовсе:
            страница узнана, и догадываться по тексту не о чем. Иначе
            собеседник останавливает бота одним словом в переписке либо в
            названии своего лота.

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

    # Шаг 2. Целостность тела. ПОСЛЕ кода ответа и до всего остального.
    #
    # После кода намеренно: при 429 и 503 тело разбирать бессмысленно, и его
    # обрыв ничего не добавляет к диагнозу. Прежде порядок был обратным, и ответ
    # 429 с оборванным телом становился сетевым отказом - то есть повторялся
    # коротким отступлением, не урезая ёмкость ведра, ровно тогда, когда
    # площадка сказала «слишком быстро».
    #
    # До всего остального - тоже намеренно: страница, оборванная посреди
    # таблицы, проходит и классификацию как пригодную, и разбор как полный.
    # Вызывающий получает половину заказов и ноль повреждений.
    if declared_length is not None and received_length is not None:
        if content_encoding not in ("", "identity"):
            # Сравнивать нечего: объявленная длина относится к сжатому телу, а
            # полученная - к распакованному. Клиент просит не сжимать; если
            # сервер просьбу не выполнил, честнее сказать об этом, чем сравнить
            # несравнимое и объявить целостность подтверждённой.
            _log.warning(
                "тело пришло сжатым (%s) вопреки просьбе: целостность не "
                "проверена, обрыв на такой странице выглядел бы как изменение "
                "разметки",
                content_encoding,
            )
        elif received_length < declared_length:
            return Verdict(
                cls=ResponseClass.TRANSPORT_ERROR,
                reason="body_truncated",
                detail={"declared": str(declared_length), "received": str(received_length)},
            )

    # Шаг 3. Проверка личности. Идёт до детекторов: ответ с чужого хоста не
    # заслуживает того, чтобы его разбирали, каким бы он ни выглядел.
    # Правило берётся из _host.py, а не пишется здесь заново. Своя копия тут и
    # была - сравнение хоста с суффиксом, - и она принимала за наш хост адрес
    # вида https://evil.example\.funpay.com/: разборщик Python видит в нём один
    # хост, оканчивающийся на .funpay.com, а браузер идёт на evil.example. Это
    # тот же промах, из-за которого _host.py и появился, и это была четвёртая
    # его копия.
    host = host_of(final_url)
    if not host:
        # Хост нечитаем: это не расхождение, а невозможность судить. Ответу, о
        # происхождении которого сказать нечего, доверять нельзя тем более.
        # Причина названа отдельно, чтобы разбор видел разницу.
        return Verdict(
            cls=ResponseClass.WRONG_IDENTITY,
            reason="host_unreadable",
            detail={"expected": expected_host, "actual": final_url[:64]},
        )
    if not same_host(final_url, expected_host):
        return Verdict(
            cls=ResponseClass.WRONG_IDENTITY,
            reason="host_mismatch",
            detail={"expected": expected_host, "actual": host},
        )

    if not html.strip():
        return Verdict(cls=ResponseClass.UNKNOWN, reason="empty_body")

    # Шаг 4. Детекторы страниц-перехватчиков.
    tree: HTMLParser | None
    try:
        tree = HTMLParser(html)
    except Exception as exc:
        # Тело не разобралось вовсе. Прежде здесь ставилось None, и дальше всё
        # шло своим чередом: признаков страницы приложения не нашлось, текст
        # оказался пуст, сигнатуры не сработали, и вердикт выходил
        # identity_marker_absent - то есть «разметка изменилась». Диагноз не тот
        # и лечение не то: разметка могла не меняться вовсе, а ответ прийти не
        # тем, чем должен.
        _log.warning("тело ответа не разобралось (%s)", type(exc).__name__)
        return Verdict(
            cls=ResponseClass.UNKNOWN,
            reason="body_unparsable",
            detail={"error": type(exc).__name__},
        )

    # Текстовые сигнатуры применяются только к неузнанной странице. Узнанная -
    # это страница приложения, часть текста на которой пишут посторонние:
    # переписка, названия лотов, имена. Догадка по такому тексту даёт
    # контрагенту право остановить чужого бота одним словом.
    known = _is_known_page(tree, content_markers)

    text: str | None = None
    for sig in signatures:
        if tree is not None:
            for selector in sig.css:
                if _matches(tree, selector, f"подписи {sig.name}"):
                    return Verdict(
                        cls=sig.verdict,
                        reason=f"signature:{sig.name}",
                        matched=sig.name,
                        provisional=sig.provisional,
                        detail={"selector": selector},
                    )
        if sig.patterns and not known:
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

    # Шаг 5. Признак вошедшего пользователя проверяется после
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
