"""Проверки сверки после неоднозначного исхода отправки.

Сверка отвечает на вопрос «что вышло из отправки» и НИЧЕГО НЕ ПРЕДПРИНИМАЕТ.
Решение о повторной отправке принимает вызывающий: у отправленного сообщения нет
отмены, и автоматика, решающая за человека, однажды напишет покупателю дважды.

Опознание проверяется НА СНИМКЕ, и это возможно потому, что адрес собственного
профиля и адреса авторов лежат в одном документе: нумерация значений скелета
внутридокументная, и равенство видно прямо.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from funora._runner import Anchor, reconcile, take_anchor
from funora._thread import parse_thread
from funora.reconciliation import RECONCILE_DELAYS_MS, RECONCILE_READS, ReconcileVerdict

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Снимок переписки: одиннадцать сообщений, из них пять своих.
THREAD: Final[str] = "chat-thread.logged.ru"


def _page() -> str:
    """Читает снимок переписки.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{THREAD}.skeleton.txt").read_text(encoding="utf-8")


def _thread(html: str) -> object:
    """Разбирает историю переписки.

    Аргументы:
        html (str): разметка.

    Возвращает:
        object: разобранная история.
    """
    return parse_thread(html, observed_at=datetime(2026, 8, 29, tzinfo=UTC))


def test_the_own_message_marker_shows_itself_on_the_snapshot() -> None:
    """Требует, чтобы признак своего сообщения работал на снимке.

    Адрес собственного профиля равен адресу автора ровно пяти сообщений из
    одиннадцати; у остальных шести адреса автора нет вовсе - это системные.

    Возвращает:
        None
    """
    html = _page()
    anchor = take_anchor(html)

    assert anchor.own_href, "адрес собственного профиля не прочитан"
    assert anchor.messages_seen == 11, anchor.messages_seen
    assert len(anchor.known_ids) == 11, len(anchor.known_ids)

    outcome = reconcile(_thread(html), anchor)
    assert outcome.own_messages_seen == 5, (
        f"своих сообщений опознано {outcome.own_messages_seen}, а на снимке их пять"
    )


def test_nothing_new_gives_absence_not_delivery() -> None:
    """Требует вердикта отсутствия, когда нового сообщения нет.

    Опора снята с того же чтения - значит нового не появилось.

    Возвращает:
        None
    """
    html = _page()
    outcome = reconcile(_thread(html), take_anchor(html))

    assert outcome.verdict is ReconcileVerdict.ABSENT_FROM_HISTORY
    assert outcome.reason == "not_in_history"
    assert not outcome.found_id.is_observed


def test_a_new_own_message_is_found() -> None:
    """Требует находить своё НОВОЕ сообщение.

    Опора берётся так, будто последнего своего сообщения на ней ещё не было.

    Возвращает:
        None
    """
    html = _page()
    anchor = take_anchor(html)
    thread = _thread(html)

    mine = [
        one
        for one in thread.messages()  # type: ignore[attr-defined]
        if one.author_href.is_observed and one.message_id.is_observed
    ]
    assert mine, "своих сообщений на снимке нет - проверять нечего"
    newest = mine[-1].message_id.value

    without = Anchor(
        own_href=anchor.own_href,
        known_ids=frozenset(anchor.known_ids - {newest}),
        messages_seen=anchor.messages_seen,
    )
    outcome = reconcile(thread, without)

    assert outcome.verdict is ReconcileVerdict.DELIVERED
    assert outcome.reason == "found_in_history"
    assert outcome.found_id.value == newest


def test_someone_elses_message_with_the_same_text_is_not_mine() -> None:
    """Требует не опознавать чужое сообщение по совпавшему тексту.

    Текст - чужой ввод, и совпадение подделывает собеседник: достаточно прислать
    в ответ то же самое. Реализация, опознающая по тексту, обязана здесь упасть.

    Проверка подменяет адрес автора у ОДНОГО сообщения, оставляя всё прочее на
    месте: текст, время, порядок. Опознание обязано его потерять.

    Возвращает:
        None
    """
    html = _page()
    anchor = take_anchor(html)
    before = reconcile(_thread(html), anchor).own_messages_seen

    # Один автор становится чужим. Ссылка чужая, но того же вида.
    spoiled = html.replace(
        'href="https://funpay.com/users/{n5}/"', 'href="https://funpay.com/users/{n9}/"', 2
    )
    assert spoiled != html, "подмена автора не сработала"

    after = reconcile(_thread(spoiled), take_anchor(spoiled)).own_messages_seen
    assert after < before, (
        f"после подмены автора своих осталось столько же ({after}): опознание "
        "не смотрит на адрес автора"
    )


def test_a_broken_own_link_gives_undetermined_not_absence() -> None:
    """Требует НЕ объявлять отсутствие, когда признак себя не показал.

    Без этого условия отсутствие искомого означало бы что угодно: и что его нет,
    и что опознание не работает на этой странице.

    Возвращает:
        None
    """
    html = _page()
    # Заменяются ВСЕ вхождения: узлов с этим классом на странице два, и
    # подмена одного оставила бы признак работающим.
    spoiled = html.replace("user-link-dropdown", "user-link-was-dropdown")
    assert "user-link-dropdown" not in spoiled, "ссылка на свой профиль не снялась"

    outcome = reconcile(_thread(spoiled), take_anchor(spoiled))

    assert outcome.verdict is ReconcileVerdict.UNDETERMINED, (
        f"без признака объявлен вердикт {outcome.verdict}: неудачный поиск выдан "
        "за наблюдение отсутствия"
    )
    assert outcome.reason == "self_marker_not_demonstrated"
    assert outcome.own_messages_seen == 0


def test_an_incomplete_read_never_gives_absence() -> None:
    """Требует не объявлять отсутствие по неполному чтению.

    Половина истории, принятая за целую, объявила бы неотправленным то, что
    отправлено, и вызывающий написал бы второй раз.

    Возвращает:
        None
    """
    html = _page()
    anchor = take_anchor(html)

    # Обрезаем разметку так, чтобы разбор объявил чтение неполным.
    at = html.index("chat-msg-item")
    truncated = html[: html.rindex("<", 0, at)] + "</body></html>"

    outcome = reconcile(_thread(truncated), anchor)
    assert outcome.verdict is ReconcileVerdict.UNDETERMINED
    assert outcome.reason == "incomplete_read"


def test_an_unread_anchor_gives_undetermined_not_delivery() -> None:
    """Требует отличать пустой диалог от непрочитанной опоры.

    Пустое опорное множество при НЕПУСТОЙ странице означает, что идентификаторы
    не прочитались. Тогда своё СТАРОЕ сообщение выглядит новым, и вызывающий
    узнаёт «доставлено» о том, что не уходило.

    Возвращает:
        None
    """
    html = _page()
    anchor = take_anchor(html)

    broken = Anchor(own_href=anchor.own_href, known_ids=frozenset(), messages_seen=11)
    outcome = reconcile(_thread(html), broken)
    assert outcome.verdict is ReconcileVerdict.UNDETERMINED
    assert outcome.reason == "anchor_not_read"

    # А пустой диалог - другое дело: там всякое своё сообщение вправду новое.
    empty = Anchor(own_href=anchor.own_href, known_ids=frozenset(), messages_seen=0)
    assert reconcile(_thread(html), empty).verdict is ReconcileVerdict.DELIVERED


def test_the_profile_link_is_compared_normalized() -> None:
    """Требует сравнивать адрес профиля нормализованным.

    Площадка отдаёт его и с завершающей косой чертой, и без неё. Сравнение как
    есть объявило бы разными один и тот же профиль.

    Возвращает:
        None
    """
    html = _page()
    anchor = take_anchor(html)

    assert not anchor.own_href.endswith("/"), anchor.own_href

    honest = reconcile(_thread(html), anchor).own_messages_seen
    assert honest == 5, honest

    # Адрес в разметке БЕЗ завершающей черты - опознание обязано не заметить.
    slashless = html.replace(
        'href="https://funpay.com/users/{n5}/"', 'href="https://funpay.com/users/{n5}"'
    )
    assert slashless != html, "черта не снялась"

    after = reconcile(_thread(slashless), take_anchor(slashless)).own_messages_seen
    assert after == honest, (
        f"без завершающей черты опознано {after} своих вместо {honest}: "
        "адреса сравниваются как есть, а площадка отдаёт их обоими видами"
    )


def test_there_is_no_negative_verdict() -> None:
    """Требует, чтобы вердикта отрицания не было среди объявленных.

    Отсутствие сообщения в истории - свидетельство отрицательное, и объявлять по
    нему «не отправлено» значило бы подтолкнуть вызывающего написать второй раз.

    Возвращает:
        None
    """
    names = {str(one) for one in ReconcileVerdict}
    assert names & {"not_delivered", "failed", "not_sent"} == set(), names
    assert "absent_from_history" in names


def test_the_schedule_is_declared_and_grows() -> None:
    """Требует, чтобы расписание чтений было объявлено и возрастало.

    Растущая пауза ловит и быстрый случай, и медленный; постоянная - только один
    из двух.

    Возвращает:
        None
    """
    assert len(RECONCILE_DELAYS_MS) == RECONCILE_READS, (
        f"чтений {RECONCILE_READS}, а пауз {len(RECONCILE_DELAYS_MS)}: лишнюю никто "
        "не выждет, недостающую никто не заметит"
    )
    assert list(RECONCILE_DELAYS_MS) == sorted(RECONCILE_DELAYS_MS)
    assert all(one > 0 for one in RECONCILE_DELAYS_MS)


def test_a_system_message_linking_to_my_profile_never_yields_delivery() -> None:
    """Требует, чтобы системное сообщение со ссылкой на свой профиль не давало доставки.

    Случай не выдуман: оповещение площадки о возврате денег содержит ссылки и на
    продавца, и на покупателя. Сообщение при этом системное, и написал его не
    продавец.

    На снимке такого нет - у системных сообщений ссылки автора нет вовсе, - и
    потому проверка его СТРОИТ: берёт системное сообщение и вкладывает в него
    ссылку на собственный профиль.

    ЗАМКОВ ЗДЕСЬ ДВА, и проверка требует результата, а не конкретного из них.
    Первый - сам разбор: признаки сообщения расходятся, происхождение выходит
    неизвестным, и чтение объявляется неполным. Второй - опознание, берущее
    только человеческие сообщения. Хватает и одного; стоят оба, потому что
    правило разбора о полноте волен изменить кто угодно, а цена ошибки здесь -
    второе сообщение покупателю.

    Возвращает:
        None
    """
    from funora.extraction import SELECTOR_GROUPS

    html = _page()

    # Признак системного сообщения объявлен вложенным селектором; для поиска в
    # разметке берётся его последний класс.
    marker = SELECTOR_GROUPS["chats.system_message.markers"][1].split()[-1].lstrip(".")
    at = html.index(f'class="{marker} ')
    opened = html.index(">", at) + 1
    injected = (
        html[:opened]
        + '<a class="chat-msg-author-link" href="https://funpay.com/users/{n5}/">свои</a>'
        + html[opened:]
    )
    assert injected != html, "ссылка в системное сообщение не вложилась"

    # Опора без единого известного идентификатора того же диалога: если бы
    # вложенное сообщение сочли своим и новым, вердикт вышел бы delivered.
    anchor = take_anchor(html)
    outcome = reconcile(
        _thread(injected),
        Anchor(
            own_href=anchor.own_href,
            known_ids=anchor.known_ids,
            messages_seen=anchor.messages_seen,
        ),
    )

    assert outcome.verdict is not ReconcileVerdict.DELIVERED, (
        "системное сообщение со ссылкой на свой профиль дало вердикт доставки"
    )
    assert outcome.reason == "incomplete_read", (
        f"сработал не тот замок: {outcome.reason}. Ожидался первый - разбор, "
        "объявляющий чтение неполным при расходящихся признаках"
    )


def test_two_profile_links_that_disagree_give_no_address() -> None:
    """Требует сверять два носителя адреса профиля.

    Узлов два - настольное меню и мобильное, - и значение в них одно. Взять
    первый попавшийся значило бы повторить ошибку, на которой уже спотыкались
    разбор списка продаж, разбор отзывов и чтение своего имени.

    Здесь строже, чем у имени: по адресу опознаётся СВОЁ сообщение, и ошибка
    оканчивается вторым сообщением покупателю.

    Возвращает:
        None
    """
    html = _page()
    assert take_anchor(html).own_href, "адрес не прочитан на исправном снимке"

    # Один из двух носителей уводится на чужой профиль.
    at = html.index("user-link-dropdown")
    end = html.index(">", at)
    spoiled = html[:at] + html[at:end].replace("{n5}", "{n9}", 1) + html[end:]
    assert spoiled != html, "подмена одного носителя не сработала"

    anchor = take_anchor(spoiled)
    assert anchor.own_href == "", (
        f"носители разошлись, а адрес всё же прочитан: {anchor.own_href!r}. Взят первый попавшийся"
    )

    # А сверка на таком снимке ответа не даёт - и это верно.
    outcome = reconcile(_thread(spoiled), anchor)
    assert outcome.verdict is ReconcileVerdict.UNDETERMINED
    assert outcome.reason == "self_marker_not_demonstrated"
