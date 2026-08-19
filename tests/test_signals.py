"""Проверки сравнения изменяемых значений страницы.

Главное, что здесь проверяется, - что отчёт отвечает на вопрос «счётчик или
хеш», не вынося наружу ни одного значения. Если бы отчёт печатал значения, он
был бы непригоден ни для обсуждения, ни для приложения к задаче, а вопрос о
канале обновлений так и остался бы открытым.
"""

from __future__ import annotations

from pathlib import Path

from funora._signals import (
    ATTR_ALLOWLIST,
    ChangeKind,
    collect,
    compare,
    format_relations,
    format_report,
    relations,
)


def _page(node_msg: str, user_msg: str, chat_tag: str, dialog: str = "700000001") -> str:
    """Собирает страницу, похожую по составу на страницу чата.

    Args:
        node_msg (str): Значение data-node-msg.
        user_msg (str): Значение data-user-msg.
        chat_tag (str): Значение data-chat.
        dialog (str): Значение data-id, опознающее диалог.

    Returns:
        str: Разметка страницы.
    """
    return (
        "<html><body>"
        f'<div class="hidden" data-chat="{chat_tag}" data-orders="qq11ww22"></div>'
        f'<a class="contact-item" data-id="{dialog}" '
        f'data-node-msg="{node_msg}" data-user-msg="{user_msg}">'
        '<div class="contact-item-message">текст сообщения</div>'
        "</a>"
        "</body></html>"
    )


def test_collect_takes_only_allowlisted_attributes() -> None:
    """Проверяет, что собираются только атрибуты из белого списка."""
    found = collect(_page("1000000001", "1000000001", "aa11bb22"))
    names = {attr for _, attr in found}
    assert names <= ATTR_ALLOWLIST
    assert "data-node-msg" in names
    assert "class" not in names


def test_growth_is_detected_with_step() -> None:
    """Проверяет, что рост числового значения виден вместе с шагом.

    Это и есть ответ на вопрос о канале обновлений: у счётчика значение растёт
    на известную величину, у хеша - меняется без направления.
    """
    before = collect(_page("1000000001", "1000000001", "aa11bb22"))
    after = collect(_page("1000000004", "1000000001", "aa11bb22"))

    by_attr = {c.attr: c for c in compare(before, after)}
    assert by_attr["data-node-msg"].kind is ChangeKind.GREW
    assert by_attr["data-node-msg"].delta == 3
    assert by_attr["data-node-msg"].numeric
    assert by_attr["data-user-msg"].kind is ChangeKind.UNCHANGED


def test_non_numeric_change_has_no_direction() -> None:
    """Проверяет, что нечисловое значение меняется без направления и шага."""
    before = collect(_page("1000000001", "1000000001", "aa11bb22"))
    after = collect(_page("1000000001", "1000000001", "zz99xx88"))

    change = {c.attr: c for c in compare(before, after)}["data-chat"]
    assert change.kind is ChangeKind.CHANGED
    assert change.delta is None
    assert not change.numeric


def test_appeared_and_disappeared() -> None:
    """Проверяет распознавание нового и пропавшего элемента."""
    one = collect(_page("1000000001", "1000000001", "aa11bb22", dialog="700000001"))
    two = collect(_page("1000000009", "1000000009", "aa11bb22", dialog="700000002"))

    kinds = {c.kind for c in compare(one, two)}
    assert ChangeKind.APPEARED in kinds
    assert ChangeKind.DISAPPEARED in kinds


def test_element_survives_reordering() -> None:
    """Проверяет, что элемент опознаётся после переупорядочивания списка.

    Пришедшее сообщение поднимает диалог наверх. Сравнение по позиции в списке
    сопоставило бы разные диалоги и показало бы изменения там, где их нет.
    """
    first = (
        '<html><body><a class="contact-item" data-id="700000001" data-node-msg="1000000001">'
        '</a><a class="contact-item" data-id="700000002" data-node-msg="1000000002"></a>'
        "</body></html>"
    )
    swapped = (
        '<html><body><a class="contact-item" data-id="700000002" data-node-msg="1000000002">'
        '</a><a class="contact-item" data-id="700000001" data-node-msg="1000000001"></a>'
        "</body></html>"
    )
    changes = compare(collect(first), collect(swapped))
    assert all(c.kind is ChangeKind.UNCHANGED for c in changes), (
        "перестановка не является изменением значений"
    )


def test_report_contains_no_values() -> None:
    """Проверяет, что в отчёт не попадает ни одно наблюдаемое значение.

    Отчёт предназначен для показа и обсуждения, поэтому проверка обязательна:
    в нём настоящие данные страницы под авторизацией быть не должны.
    """
    before = collect(_page("1000000001", "1000000001", "aa11bb22", dialog="700000001"))
    after = collect(_page("1000000004", "1000000002", "zz99xx88", dialog="700000001"))
    report = format_report(compare(before, after), only_changed=False)

    for value in ("1000000001", "1000000004", "1000000002", "aa11bb22", "zz99xx88", "700000001"):
        assert value not in report, f"значение {value} попало в отчёт"


def test_report_mentions_step_for_numeric() -> None:
    """Проверяет, что шаг числового изменения попадает в отчёт."""
    before = collect(_page("1000000001", "1000000001", "aa11bb22"))
    after = collect(_page("1000000004", "1000000001", "aa11bb22"))
    report = format_report(compare(before, after))
    assert "выросло на 3" in report


def test_identical_reads_report_no_changes() -> None:
    """Проверяет, что два одинаковых чтения не дают ложных изменений."""
    page = _page("1000000001", "1000000001", "aa11bb22")
    report = format_report(compare(collect(page), collect(page)))
    assert "изменений нет" in report


def _fixture(name: str) -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Содержимое скелета.
    """
    path = Path(__file__).parent / "fixtures" / "pages" / f"{name}.skeleton.txt"
    return path.read_text(encoding="utf-8")


def test_relations_counts_all_contacts() -> None:
    """Проверяет подсчёт соотношений по списку диалогов."""
    rel = relations(_fixture("chat.logged.ru"))
    assert rel.contacts >= 2
    assert rel.equal + rel.differing + rel.incomplete == rel.contacts


def test_positions_are_equal_while_nothing_is_unread() -> None:
    """Проверяет наблюдение, отвечающее на смысл data-user-msg.

    У всех диалогов снимка позиции совпадают, а счётчик непрочитанного скрыт.
    Трактовка «последнее написанное этим аккаунтом» потребовала бы, чтобы
    последнее сообщение во всех сорока семи диалогах было написано владельцем
    аккаунта. Трактовка «последнее прочитанное» объясняет то же самое без
    натяжки.
    """
    rel = relations(_fixture("chat.logged.ru"))
    assert rel.differing == 0
    assert rel.equal == rel.contacts
    assert rel.unread_badge == "скрыт"


def test_relations_report_states_the_conclusion() -> None:
    """Проверяет, что отчёт печатает вывод, а не только числа.

    Без вывода отчёт остаётся набором цифр, а решение по нему всё равно
    придётся принимать - и принято оно будет по памяти.
    """
    text = format_relations(relations(_fixture("chat.logged.ru")))
    assert "последнее прочитанное" in text


def test_relations_leaks_no_values() -> None:
    """Проверяет, что в отчёт не попадают значения атрибутов."""
    html = (
        '<html><body><a class="contact-item" data-id="999" '
        'data-node-msg="1234567890" data-user-msg="1234567890"></a></body></html>'
    )
    text = format_relations(relations(html))
    for value in ("999", "1234567890"):
        assert value not in text


def test_relations_handles_page_without_contacts() -> None:
    """Проверяет поведение на странице без списка диалогов."""
    rel = relations("<html><body><div>пусто</div></body></html>")
    assert rel.contacts == 0
    assert "толковать нечего" in format_relations(rel)
