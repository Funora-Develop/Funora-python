"""Регрессия классификатора на настоящих страницах площадки.

Фикстуры в tests/fixtures/pages - структурные скелеты реальных ответов, снятые
инструментом funora-observe. Сырого HTML в них нет по построению формата, но
разметка сохранена целиком, поэтому селекторы проверяются ровно те же, что
работают на живой странице.

Смысл этих тестов в том, что признаки в DEFAULT_SIGNATURES выведены из этих
самых снимков. Без регрессии ничто не мешает позже поправить признак так, что он
перестанет узнавать страницу, ради которой был написан, и отказ будет тихим.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from funora._classify import ResponseClass, classify
from funora._skeleton import (
    NUMBERED_SKELETON_FORMATS,
    SUPPORTED_SKELETON_FORMATS,
    SkeletonError,
    _self_check,
    skeletonize,
)

#: Каталог с фикстурами страниц.
PAGES = Path(__file__).parent / "fixtures" / "pages"

#: Рабочая копия спецификации. Без неё сверка со снимком пропускается, и
#: пропуск ловится отдельным шагом сборки: молчаливый здесь недопустим.
_SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Ожидаемый вердикт для каждой фикстуры.
EXPECTED = {
    "order.logged.ru": ResponseClass.OK,
    "orders-trade.logged.ru": ResponseClass.OK,
    "chat.logged.ru": ResponseClass.OK,
    "chat-thread.logged.ru": ResponseClass.OK,
    "orders-trade.guest.ru": ResponseClass.LOGIN_REQUIRED,
    # Список продаж без единой продажи. Снят 24.08.2026 фильтром по
    # состоянию, в котором заказов нет: у аккаунта продажи были.
    "orders-trade.empty.ru": ResponseClass.OK,
    "user.logged.ru": ResponseClass.OK,
    "account-balance.logged.ru": ResponseClass.OK,
    "root.logged.ru": ResponseClass.OK,
    # Свои лоты в категории. Снят 28.08.2026 форматом v8 - первым, сохраняющим
    # ключи объекта настроек, и потому первым, на котором проверяется разбор
    # защитного токена.
    #
    # Сборщик записал этому снимку вердикт challenge. Ложный: слово captcha
    # стоит в описании лота владельца, торгующего защитой игровых серверов от
    # ботов, а текстовая подпись искала его по всему тексту страницы. Исправлено
    # в контракте 0.16.0, прежний вердикт сохранён в файле происхождения.
    "lots-trade.logged.ru": ResponseClass.OK,
    # Второй снимок страницы диалога, РЯДОМ с chat-thread.logged.ru, а не
    # вместо него. Тот снят форматом v5 и служит свидетельством числу сообщений
    # - одиннадцать строк, пять ссылок автора; замена сломала бы дюжину
    # счётных утверждений контракта.
    #
    # Этот снят 29.08.2026 форматом v8 и служит другому: в нём есть ключи
    # объекта настроек, то есть защитный токен, и на нём впервые проверяется
    # ПОЛОЖИТЕЛЬНЫЙ случай признака пригодности страницы для отправки. Прежде
    # он не бывал истинным ни на одной фикстуре.
    "chat-thread.v8.logged.ru": ResponseClass.OK,
    # ЕДИНСТВЕННАЯ ОБРЕЗАННАЯ фикстура каталога, и это записано у неё в
    # происхождении полем derived. Снимок публичного списка предложений раздела
    # - семь с половиной мегабайт и три тысячи строк; в репозиторий положены
    # двадцать пять, по нескольку каждой различной формы строки.
    #
    # Обрезка воспроизводима: tools/trim_skeleton.py по тому же наблюдению даёт
    # тот же файл посимвольно, и это отдельно проверяется в test_market.py.
    #
    # Числа площадки за ней НЕ ЧИСЛЯТСЯ: утверждения о трёх тысячах строк
    # проверяются по самому наблюдению и пропускаются без него. Здесь она стоит
    # ради того же, ради чего и соседние, - вердикта классификатора и проверки
    # на утечку текста.
    "market-offers.trimmed.logged.ru": ResponseClass.OK,
    # ТРИ СНИМКА ФОРМАТА v9, снятые 31.08.2026 ради одного - ИМЁН ПОЛЕЙ ФОРМЫ.
    #
    # На снимках прежнего формата у этих страниц видны АДРЕСА точек записи и не
    # видны их поля: /orders/refund, /withdraw/withdraw, /users/reviews,
    # /users/transactions. По адресу без полей запроса не собрать, и три
    # области площадки оставались недоступными не потому, что их не наблюдали,
    # а потому, что наше собственное правило маскировало имена.
    #
    # Соседние снимки прежнего формата оставлены: каждый снят в свою минуту и
    # вторым таким же не будет.
    "account-balance.v9.logged.ru": ResponseClass.OK,
    "order.v9.logged.ru": ResponseClass.OK,
    "user.v9.logged.ru": ResponseClass.OK,
    # ТОТ ЖЕ СПИСОК, СНЯТЫЙ ЗАНОВО 31.08.2026 форматом v9 и БЕЗ СЕССИИ.
    #
    # Два отличия от соседа, и оба нужны.
    #
    # Формат: v9 сохраняет ИМЕНА параметров строки запроса, и только на нём
    # виден идентификатор предложения - он лежит в ней и больше нигде. На
    # снимке v8 его не восстановить: формат необратим по построению.
    #
    # Сессия: страница публичная, и снята она гостем. Это утверждение о
    # площадке, которое стоило проверить: список предложений виден без входа
    # целиком, со всеми ценами и продавцами.
    #
    # ВЕРДИКТ ЗДЕСЬ - login_required, И ЭТО НАХОДКА, А НЕ ОПЕЧАТКА.
    #
    # Классификатор прав про сессию: мы не вошли, и признак гостя на странице
    # есть. Но про ОТВЕТ он неправ: страница отдана целиком и годна к разбору
    # полностью.
    #
    # Значит объявленная дорожка public_read сегодня непригодна вдвойне: она не
    # заведена (это записано в реестре неисполненного) и, будь заведена,
    # получала бы отказ на каждом чтении. Операция рынка обязана либо не звать
    # классификатор личности вовсе, либо звать его с другим ожиданием.
    #
    # Записано вердиктом, а не исправлено на ходу: снимок обязан говорить, что
    # площадка отвечает на самом деле.
    "market-offers.trimmed.guest.ru": ResponseClass.LOGIN_REQUIRED,
    # ВТОРОЙ РЫНОК ПЛОЩАДКИ, снят 31.08.2026 гостем. Вердикт тот же и по той же
    # причине: страница публичная, а классификатор честно говорит про сессию.
    "chips.trimmed.guest.ru": ResponseClass.LOGIN_REQUIRED,
    # РАЗДЕЛ СО СХЕМОЙ ПОЛЕЙ, снят 31.08.2026. Схема числилась ненаблюдённой и
    # лежала в форме фильтра: .lot-fields, у каждого поля свой data-id, имя вида
    # f-{id} и вид управления - выбор либо диапазон.
    "catalog-fields.logged.ru": ResponseClass.OK,
    # СВОИ ЛОТЫ, ОДИН ВЫКЛЮЧЕН. Снят 31.08.2026 ради одного класса: warning на
    # выключенной строке. Прежний снимок той же страницы сделан при всех
    # включённых, и потому признак на нём не наблюдался - отсюда и пошло
    # утверждение, что его нет вовсе.
    "lots-trade.off.logged.ru": ResponseClass.OK,
    # ЧУЖОЙ профиль продавца, снят 30.08.2026. Первый в наборе: разбор отзывов
    # до сих пор проверялся только на СВОЁМ, а у чужого нет целых полей -
    # имени автора отзыва, ссылки на заказ, ссылки на фото автора. Проверка на
    # своём объявляла их наблюдёнными.
    #
    # На нём же впервые наблюдена ПОКАЗАННАЯ кнопка догрузки отзывов: прежде
    # она встречалась только скрытой, и положительная ветка неполноты стояла
    # непроверенной настоящей страницей.
    "user-foreign.logged.ru": ResponseClass.OK,
    # Форма правки предложения, снята 30.08.2026 ЧТЕНИЕМ. До неё три операции
    # записи над лотами не имели ни адреса, ни имён полей, и добыть их
    # предлагалось настоящим сохранением чужого лота.
    #
    # Открылась она не походом на площадку, а починкой двух правил скелета:
    # имя метода в адресе маскировалось (в браузерном сборщике исключение было,
    # в питоне нет), а имена полей формы маскировались как всякий атрибут.
    "lot-edit.logged.ru": ResponseClass.OK,
}


def _read(name: str) -> str:
    """Читает скелет фикстуры.

    Args:
        name (str): Имя фикстуры без расширения.

    Returns:
        str: Содержимое скелета.
    """
    return (PAGES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_parses(name: str) -> None:
    """Проверяет, что фикстура разбирается и содержит узлы.

    Args:
        name (str): Имя фикстуры.
    """
    tree = HTMLParser(_read(name))
    assert tree.body is not None, "тело документа не разобралось"
    assert len(tree.css("*")) > 100, "дерево подозрительно мелкое"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_classification(name: str) -> None:
    """Проверяет вердикт классификатора на снимке настоящей страницы.

    Args:
        name (str): Имя фикстуры.
    """
    verdict = classify(
        status=200,
        final_url="https://funpay.com/orders/trade",
        html=_read(name),
        expected_host="funpay.com",
    )
    assert verdict.cls is EXPECTED[name]
    assert not verdict.provisional, (
        "вердикт на настоящей странице не должен опираться на непроверенный признак"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_contains_no_text(name: str) -> None:
    """Проверяет, что фикстура удовлетворяет формату скелета.

    Фикстуры лежат в открытом репозитории, поэтому проверка повторяется здесь, а
    не считается выполненной один раз при захвате.

    Args:
        name (str): Имя фикстуры.
    """
    _self_check(_read(name))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_has_provenance(name: str) -> None:
    """Проверяет наличие и состав описания происхождения.

    Args:
        name (str): Имя фикстуры.
    """
    data = json.loads((PAGES / f"{name}.provenance.json").read_text(encoding="utf-8"))
    for key in ("path", "captured_at", "http_status", "locale", "format"):
        assert key in data, f"в описании нет поля {key}"
    # Версия сверяется с кодом, а не записана числом: иначе она расходится
    # с форматом молча, и снимок начинает читаться правилами, по которым
    # его не снимали.
    assert data["format"] in SUPPORTED_SKELETON_FORMATS


def test_logged_and_guest_markers_do_not_overlap() -> None:
    """Проверяет, что признаки вошедшего и гостя взаимно исключают друг друга.

    Это условие важнее самих селекторов: если признак встречается на обеих
    страницах, он не различает состояния, и классификатор построен на песке.
    """
    logged = HTMLParser(_read("orders-trade.logged.ru"))
    guest = HTMLParser(_read("orders-trade.guest.ru"))

    only_logged = (".navbar-toggle-logged",)
    only_guest = (
        ".navbar-toggle-guest",
        ".menu-item-login",
        ".menu-item-register",
        ".content-account-login",
    )
    for sel in only_logged:
        assert logged.css_first(sel) is not None, f"{sel} пропал у вошедшего"
        assert guest.css_first(sel) is None, f"{sel} нашёлся у гостя"
    for sel in only_guest:
        assert guest.css_first(sel) is not None, f"{sel} пропал у гостя"
        assert logged.css_first(sel) is None, f"{sel} нашёлся у вошедшего"


def test_system_message_is_recognized_structurally() -> None:
    """Проверяет, что системное сообщение отличается от пользовательского разметкой.

    Это самая дорогая проверка во всём наборе. Если системное сообщение об оплате
    отличается от обычного только текстом, покупатель отправляет сообщение с
    таким же текстом, и бот, доверяющий тексту, выдаёт товар. Признак обязан быть
    структурным, и подделать его отправитель не должен уметь.

    Признаков три, и на снимке они согласованы полностью: обёртка alert в теле,
    отсутствие ссылки на автора и ярлык label-primary.
    """
    tree = HTMLParser(_read("chat-thread.logged.ru"))
    messages = tree.css(".chat-msg-item")

    system, human = [], []
    for node in messages:
        (system if node.css_first("a.chat-msg-author-link") is None else human).append(node)

    # Числами здесь не проверяется ничего: точный состав снимка меняется при
    # каждой пересъёмке и сам по себе ни о чём не говорит. Проверяется другое -
    # что оба вида сообщений в снимке есть, иначе согласованность признаков
    # ниже подтвердилась бы на пустом множестве.
    assert system, "в снимке нет системных сообщений, проверять признак не на чем"
    assert human, "в снимке нет сообщений пользователя, сравнивать не с чем"

    for node in system:
        assert node.css_first(".chat-msg-body .alert") is not None, (
            "у системного сообщения обязана быть обёртка alert"
        )
        label = node.css_first(".chat-msg-author-label")
        assert label is not None
        assert "label-primary" in (label.attributes.get("class") or "")

    for node in human:
        assert node.css_first(".chat-msg-body .alert") is None, (
            "у сообщения пользователя обёртки alert быть не должно"
        )


def test_author_link_and_alert_never_coincide() -> None:
    """Проверяет, что два признака системного сообщения не пересекаются.

    Правило в спецификации фиксирует закрытый отказ: системным сообщение
    считается, только если обёртка есть и ссылки на автора нет. Проверка нужна
    затем, что при пересечении признаков такое правило не даст ни одного
    срабатывания, и отказ будет тихим.
    """
    for node in HTMLParser(_read("chat-thread.logged.ru")).css(".chat-msg-item"):
        has_alert = node.css_first(".chat-msg-body .alert") is not None
        has_author = node.css_first("a.chat-msg-author-link") is not None
        assert has_alert != has_author, "признаки обязаны быть взаимно исключающими"


def test_message_carries_own_dom_id() -> None:
    """Проверяет, что у каждого сообщения есть собственный идентификатор в разметке.

    Без него позиция переигрывания не адресует ничего конкретного: знать, что
    после нашей отметки что-то появилось, и уметь показать, что именно, - разные
    возможности.
    """
    for node in HTMLParser(_read("chat-thread.logged.ru")).css(".chat-msg-item"):
        assert node.attributes.get("id"), "у сообщения нет идентификатора"


def test_message_text_may_contain_foreign_links() -> None:
    """Проверяет наличие внешних ссылок в тексте сообщения.

    Проверка закрепляет факт, а не желаемое: текст сообщения содержит ссылки,
    которые ввёл собеседник. Ни один SDK не имеет права по ним ходить, и правило
    в спецификации опирается на это наблюдение.
    """
    links = HTMLParser(_read("chat-thread.logged.ru")).css(".chat-msg-text a[href]")
    external = [
        a for a in links if not (a.attributes.get("href") or "").startswith("https://funpay.com")
    ]
    assert external, "в снимке есть сообщение со ссылкой на сторонний сайт"


def test_every_snapshot_is_registered() -> None:
    """Проверяет, что ни один снимок не лежит в каталоге без проверок.

    Перечень EXPECTED заполняется руками, и это его слабое место: снимок,
    положенный рядом и забытый, не проверяется ни на утечку текста, ни на
    вердикт классификатора, ни на формат. Он при этом лежит в открытом
    репозитории и выглядит проверенным - как и все соседние файлы.

    Проверка идёт по каталогу, а не по перечню: только так она замечает то,
    чего в перечне нет.

    Returns:
        None
    """
    on_disk = {p.name.removesuffix(".skeleton.txt") for p in PAGES.glob("*.skeleton.txt")}
    assert on_disk == set(EXPECTED), (
        f"снимки без проверок: {sorted(on_disk - set(EXPECTED))}; "
        f"проверки без снимков: {sorted(set(EXPECTED) - on_disk)}"
    )


def test_every_snapshot_has_provenance() -> None:
    """Проверяет, что у каждого снимка есть описание захвата.

    Без описания снимок нельзя ни повторить, ни датировать, ни объяснить: он
    превращается в разметку неизвестного происхождения, которой почему-то верят.

    Returns:
        None
    """
    for snapshot in sorted(PAGES.glob("*.skeleton.txt")):
        beside = snapshot.with_name(snapshot.name.replace(".skeleton.txt", ".provenance.json"))
        assert beside.exists(), f"{snapshot.name}: рядом нет provenance"


def test_provenance_says_whether_the_file_was_converted() -> None:
    """Проверяет, что описание захвата различает снимок и его преобразование.

    Поле format говорит, каков файл сейчас. Само по себе оно вводит в
    заблуждение: четыре снимка помечены текущим форматом, а сняты были прежними
    и преобразованы повторной маскировкой. Расхождение между таким файлом и
    снятым нативно объясняется сменой формата с тем же успехом, что и сменой
    разметки, - и решить, что важнее, можно только зная происхождение.

    Returns:
        None
    """
    for snapshot in sorted(PAGES.glob("*.provenance.json")):
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        assert "captured_format" in data, f"{snapshot.name}: не сказано, в чём снят"
        assert "converted" in data, f"{snapshot.name}: не сказано, преобразован ли"
        assert data["converted"] is (data["captured_format"] != data["format"]), (
            f"{snapshot.name}: пометка преобразования расходится с форматами"
        )


def test_rows_are_distinguishable_in_current_format() -> None:
    """Проверяет, что строки снимка отличимы друг от друга.

    Ради этого формат и поднимался дважды. Пока идентификаторы схлопывались в
    одну подпись, всякая проверка курсора, гашения и порождения событий
    проходила впустую - и выглядела при этом пройденной. Дважды на это
    наступали.

    Проверка идёт по снимкам НУМЕРУЮЩИХ версий, а не одной текущей. Снятые до
    нумерации различимость восстановить не могут, и требовать её от них
    нечестно; снятые после - обязаны, сколько бы версий с тех пор ни прошло.

    Прежде здесь стояло равенство текущему формату, и первый же подъём версии
    по другой причине оставил проверку без единого снимка. Она честно упала на
    «проверять нечего» - но это и была вся её защита: сравнение с одной версией
    отменяет проверку при каждом подъёме.

    Returns:
        None
    """
    checked = 0
    for name in sorted(EXPECTED):
        data = json.loads((PAGES / f"{name}.provenance.json").read_text(encoding="utf-8"))
        if data["format"] not in NUMBERED_SKELETON_FORMATS:
            continue

        tree = HTMLParser(_read(name))
        for selector, attribute in (("a.tc-item", "href"), (".contact-item", "href")):
            rows = tree.css(selector)
            if len(rows) < 2:
                continue
            values = [(r.attributes.get(attribute) or "") for r in rows]
            assert len(set(values)) == len(values), (
                f"{name}: {len(rows)} строк {selector}, "
                f"а различимых значений {attribute} только {len(set(values))}"
            )
            checked += 1

    assert checked, "ни одного снимка нумерующей версии со строками - проверять нечего"


#: Пары «что подсунули - что должно случиться».
#:
#: Кириллица выбрана не для красоты: аудитория площадки русскоязычная, и утечка
#: текста выглядела бы именно так. Латиница здесь ничего не доказала бы - её
#: полно в именах классов и в путях, которые формат сохраняет намеренно.
LEAKS: dict[str, str] = {
    "кириллица в имени класса": '<div class="кнопка">x</div>',
    "кириллица в имени атрибута": '<div class="a" данные="1">x</div>',
}


@pytest.mark.parametrize(("label", "html"), sorted(LEAKS.items()))
def test_cyrillic_in_a_structural_line_is_refused(label: str, html: str) -> None:
    """Проверяет, что кириллица в структурной строке отвергается.

    Формат обещает: текстов в скелете нет. Обещание держится не тем, что при
    захвате всё вычистили, а тем, что скелет проверяет себя сам - разбор
    приходит от стороннего парсера, и смена его поведения не должна тихо
    превратить безопасный формат в небезопасный.

    Ветка про кириллицу существовала и не проверялась ничем: снять её можно
    было незаметно, и весь набор оставался зелёным. Между тем это ровно то
    место, где утечка и произошла бы: класс и имя атрибута сохраняются
    дословно, потому что без них не написать селектора.

    Args:
        label (str): Что подсунули.
        html (str): Разметка.

    Returns:
        None
    """
    with pytest.raises(SkeletonError, match="кириллиц"):
        skeletonize(html)


def test_cyrillic_text_does_not_reach_the_skeleton() -> None:
    """Проверяет обратную половину: текст на кириллице маскируется, а не падает.

    Отвергать страницу из-за русского текста было бы негодно - он там всегда.
    Текст обязан превратиться в подпись, и в скелете кириллицы не остаться.

    Returns:
        None
    """
    skeleton = skeletonize('<div class="a">Алёша купил ключ</div>')

    leaked = sorted({ch for ch in skeleton if "Ѐ" <= ch <= "ӿ"})
    assert not leaked, f"кириллица дошла до скелета: {''.join(leaked)}"
    assert "Алёша" not in skeleton
    assert "T16:cs" in skeleton or "T" in skeleton, "текст не превратился в подпись"


@pytest.mark.skipif(
    not _SPEC_DIR or not (Path(_SPEC_DIR) / "spec" / "extraction" / "chats.yaml").is_file(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_the_declared_count_of_system_messages_matches_the_fixture() -> None:
    """Сверяет объявленное число системных сообщений со снимком.

    Число подано как наблюдение и читается как подтверждение того, что два
    признака системного сообщения согласованы. На нём стоит правило, отличающее
    уведомление площадки от сообщения покупателя.

    Условия берутся из markers[].condition, а не из литералов: поле condition
    объявлено машиночитаемым, и до сих пор его не читал никто. Прозаические
    условия пропускаются вслух - обрабатываются только absent и present.

    Проверка нужна потому, что объявление уже было ложным: рядом стояло
    total_messages: 10 при одиннадцати узлах и human: 4 при пяти.

    Returns:
        None
    """
    import yaml

    doc = yaml.safe_load(
        (Path(_SPEC_DIR or ".") / "spec" / "extraction" / "chats.yaml").read_text(encoding="utf-8")
    )
    block = doc["system_message"]
    declared = block["observed_distribution"]["system"]
    snapshot = block["observed_distribution"]["evidence"]

    # Решающие признаки - те, чья сила объявлена главной либо подтверждающей.
    # Слабый признак объявлен вспомогательным вслух, смысл его неизвестен, и
    # условие у него прозаическое законно.
    #
    # Требование машиночитаемого условия именно у решающих - не придирка.
    # Отбирай проверка признаки по одному лишь виду условия, и перевод любого
    # решающего в прозу молча сократил бы её до оставшихся: счёт сошёлся бы, а
    # проверяла бы она половину правила. Это показала мутация.
    decisive = [one for one in block["markers"] if one.get("strength") in ("primary", "confirming")]
    assert decisive, "ни один признак системного сообщения не объявлен решающим"

    for one in decisive:
        assert one.get("condition") in ("absent", "present"), (
            f"решающий признак «{one['name']}» объявил условие прозой "
            f"({one.get('condition')!r}). Правило, отличающее уведомление "
            "площадки от сообщения покупателя, перестаёт быть проверяемым, а "
            "счёт сходится и без него - проверка тихо станет проверять меньше"
        )

    machine = {one["name"]: one for one in decisive}

    tree = HTMLParser((PAGES / f"{snapshot}.skeleton.txt").read_text(encoding="utf-8"))
    items = tree.css(doc["message"]["item"]["selector"])
    assert items, f"в снимке {snapshot} нет ни одного сообщения"

    counted = 0
    for node in items:
        agrees = True
        for one in machine.values():
            found = node.css_first(one["selector"]) is not None
            if found != (one["condition"] == "present"):
                agrees = False
                break
        counted += 1 if agrees else 0

    assert counted == declared, (
        f"спецификация объявляет {declared} системных сообщений на снимке "
        f"{snapshot}, а по объявленным признакам их {counted}. На этом числе "
        "стоит правило, отличающее уведомление площадки от сообщения "
        "покупателя: разойдясь со снимком, оно опирается на счёт, которого "
        "никто не делал"
    )
