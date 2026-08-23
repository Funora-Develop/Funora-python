"""Проверяет, что селекторы разбора живут в одном месте, а не в двух.

Прежде каждый селектор существовал дважды: объявлением в spec/extraction и
литералом в коде. Площадка меняет разметку - правят один файл из двух, и
расхождение молчит. Проверки этого не ловили и поймать не могли: они гоняют
разбор по снимкам, а текст спецификации с кодом не сверял никто.

Отсюда две проверки. Первая запрещает литерал: селектор берётся из порождённой
таблицы. Вторая запрещает объявление, которое никто не читает, - это та же
болезнь наоборот.
"""

from __future__ import annotations

import re
from pathlib import Path

from funora.extraction import SELECTOR_GROUPS, SELECTORS

#: Каталог исходников пакета.
SRC = Path(__file__).resolve().parent.parent / "src" / "funora"

#: Модуль, в котором селекторам жить положено.
GENERATED = "extraction.py"

#: Часть селектора: тег, классы, идентификаторы, атрибуты.
_PART = re.compile(r"^[a-z]*(?:[.#][A-Za-z][\w-]*|\[[A-Za-z][^\]]*\])+$")

#: Класс либо идентификатор с дефисом внутри имени.
_HYPHENATED = re.compile(r"[.#][A-Za-z][\w]*-")

#: Селектор по атрибуту: имя атрибута начинается с буквы.
_ATTRIBUTE = re.compile(r"\[[A-Za-z][^\]]*\]")


def _looks_like_selector(literal: str) -> bool:
    """Сообщает, похожа ли строка на селектор CSS.

    Правило нарочно узкое, и первая редакция была широкой: она ловила любой
    точечный идентификатор - имя журнала «funora.client», идентификатор
    возможности «orders.list», ключ самой таблицы «chats.widget» - и выдавала
    триста нарушений там, где их не было. Проверка, кричащая на всё, не
    отличается от молчащей.

    Признаком служит то, чего у точечного идентификатора не бывает: дефис
    внутри имени класса либо запрос по атрибуту. Имя атрибута обязано
    начинаться с буквы - иначе правило считало бы селектором индекс «[0]»
    внутри ключа таблицы.

    Args:
        literal (str): Строковый литерал из кода.

    Returns:
        bool: True, если строка похожа на селектор CSS.
    """
    parts = [part for part in re.split(r"[\s>+~]+", literal) if part]
    if not parts or not all(_PART.match(part) for part in parts):
        return False
    return any(_HYPHENATED.search(part) or _ATTRIBUTE.search(part) for part in parts)


#: Строки, похожие на селектор и селекторами не являющиеся.
#:
#: Перечисление, а не признак: признак пришлось бы ставить в коде, а он там
#: выглядел бы разрешением, которого никто не проверяет.
ALLOWED: frozenset[str] = frozenset(
    {
        # Разбор адреса ссылки в теле сообщения: проверка, что ссылка ведёт на
        # свой хост. Не запрос к документу, а имя атрибута.
        "a[href]",
        # Расширение временного файла состояния.
        ".tmp",
        # Классы знаков в подписи скелета страницы.
        "[0-9]",
        "[A-Za-z]",
    }
)


def _executable(text: str) -> str:
    """Отбрасывает строки документации и комментарии.

    Селектор, упомянутый в docstring как пример, литералом не является:
    он ничего не выбирает. Считать его нарушением значило бы запретить
    объяснять, что делает функция.

    Args:
        text (str): Исходный текст модуля.

    Returns:
        str: Текст без строк документации и комментариев.
    """
    without_docs = re.sub(r'"""(?:.|\n)*?"""', "", text)
    return re.sub(r"#.*", "", without_docs)


def test_no_selector_literals_outside_the_generated_table() -> None:
    """Проверяет, что в коде не осталось литералов селекторов.

    Литерал - это вторая копия величины, объявленной спецификацией. Две копии
    расходятся молча, и заметит это не проверка, а пользователь, у которого
    перестало приходить событие.

    Returns:
        None
    """
    offenders: list[str] = []

    for path in sorted(SRC.glob("*.py")):
        if path.name == GENERATED:
            continue
        text = _executable(path.read_text(encoding="utf-8"))
        for quoted in re.findall(r"""(?:"([^"\n]+)"|'([^'\n]+)')""", text):
            literal = quoted[0] or quoted[1]
            if literal in ALLOWED or not _looks_like_selector(literal):
                continue
            offenders.append(f"{path.name}: {literal!r}")

    assert not offenders, (
        f"селекторы записаны литералом мимо порождённой таблицы: {offenders}. "
        "Объявите селектор в spec/extraction и берите его из SELECTORS - "
        "иначе спецификация и код разойдутся молча"
    )


def test_every_generated_selector_is_read() -> None:
    """Проверяет, что объявленный селектор кем-нибудь читается.

    Та же болезнь наоборот. Селектор, объявленный спецификацией и не читаемый
    ничем, обещает автору второго SDK, что разбор на него опирается, - а он не
    опирается.

    Перечни проверяются целиком: они и берутся целиком, потому что порядок в
    них значим и делить их по одному незачем.

    Returns:
        None
    """
    blob = "\n".join(
        _executable(path.read_text(encoding="utf-8"))
        for path in SRC.glob("*.py")
        if path.name != GENERATED
    )

    # Пробелы выброшены: форматтер вправе перенести длинное обращение на
    # несколько строк, и поиск по подстроке перестал бы его находить.
    tight = re.sub(r"\s+", "", blob)

    silent = [key for key in sorted(SELECTORS) if f'SELECTORS["{key}"]' not in tight]
    silent += [key for key in sorted(SELECTOR_GROUPS) if f'SELECTOR_GROUPS["{key}"]' not in tight]

    # Перечень нечитаемых объявлен рядом с проверкой, а не выведен по факту:
    # выведенный по факту всегда сходится сам с собой.
    known_silent = {
        # Отправка сообщения: операции нет, записано в not-implemented.yaml.
        "chats.sending.form",
        # Второе имя того же селектора: разбор берёт ссылку автора один раз, и
        # имя автора вытаскивает из неё же.
        "chats.message.fields.author_name",
        # Сумма продавца: поле не читается, операции получения заказа нет.
        "orders.fields.seller_sum_text",
        # Признаки вошедшего: разбор довольствуется первым, остальные
        # вспомогательные и объявлены как подтверждающие.
        "session.markers.logged_in",
        # Запись наблюдения, а не правило для кода: виджет проверки стоит на
        # ОБЫЧНОЙ странице входа, поэтому подписью проверки быть не может.
        # Поведение закреплено в tests/test_classify.py - гостевая страница
        # обязана давать login_required, а не challenge.
        "session.markers.challenge_widget_on_login",
        # Канал обновлений: реализации нет, записано в not-implemented.yaml как
        # updates_channel. Селекторы объявлены и сверяются со снимками, читать
        # их пока некому.
        "updates.positions",
        "updates.tags.locations",
    }

    unexpected = [key for key in silent if key not in known_silent]
    assert not unexpected, (
        f"объявлены и не читаются: {unexpected}. Либо примените селектор, либо "
        "внесите его в перечень известных с обоснованием"
    )

    declared = set(SELECTORS) | set(SELECTOR_GROUPS)
    gone = [key for key in sorted(known_silent) if key not in declared]
    assert not gone, (
        f"перечень известных нечитаемых называет то, чего в таблице нет: {gone}. "
        "Перечень протух и пропускает настоящие нарушения"
    )
