"""Проверяет сборщик наблюдений.

Инструмент принимает страницу под авторизацией и обязан не дать ей попасть на
диск. Цена ошибки несимметрична: лишняя строгость стоит одного пересобранного
снимка, недостающая - имени покупателя в открытом репозитории, откуда его потом
не вычистить ни из форков, ни из кэшей.

Поэтому проверки тут не про удобство, а про то, чего инструмент НЕ делает.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

#: Корень репозитория.
ROOT = Path(__file__).resolve().parent.parent

#: Браузерная часть сборщика.
SNIPPET = ROOT / "tools" / "capture.js"

#: Страница с тем, чему в снимке быть нельзя.
DANGEROUS = """<html lang="ru"><head><title>Заказы</title>
<script>var golden_key="СЕКРЕТНОЕЗНАЧЕНИЕ";</script></head>
<body><div class="tc-item" data-id="ORDER-8471223">
<a href="/orders/8471223/" class="tc-order">#8471223</a>
<div class="media-user-name">ИванПетров</div>
<div class="tc-price">1500 &#8381;</div>
<a href="https://t.me/ivanpetrov">telegram</a>
</div></body></html>"""

#: Что из этой страницы обязано исчезнуть без следа.
MUST_VANISH = (
    "СЕКРЕТНОЕЗНАЧЕНИЕ",
    "golden_key",
    "ИванПетров",
    "ivanpetrov",
    "8471223",
    "ORDER",
)


def _tool() -> Any:
    """Загружает инструмент как модуль.

    Returns:
        Any: Модуль tools/capture.py.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import capture

        return capture
    finally:
        sys.path.pop(0)


def _in_node(call: str) -> Any:
    """Выполняет кусок браузерной части под node и возвращает результат.

    Берутся функции от charClass до headerNames - те, что строят ЗАПИСЬ. Всё
    остальное завязано на браузер и здесь не нужно.

    Args:
        call (str): Выражение на JavaScript, результат которого вернуть.

    Returns:
        Any: Разобранный из JSON результат.
    """
    lines = [
        "const source = require('fs').readFileSync(process.argv[1], 'utf8');",
        # Адреса разбираются относительно location.href, а в node его нет.
        # Без заглушки maskUrl падает в свой же catch и возвращает подпись -
        # то есть проверка мерила бы отказ, а не разбор.
        "globalThis.location = globalThis.location || {href: 'https://funpay.com/'};",
        "const start = source.indexOf('function charClass');",
        "const end = source.indexOf('const recorded');",
        "eval(source.slice(start, end));",
        f"console.log(JSON.stringify({call}));",
    ]
    run = subprocess.run(  # noqa: S603
        ["node", "-e", "\n".join(lines), str(SNIPPET)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert run.returncode == 0, f"браузерная часть не выполнилась: {run.stderr}"
    return json.loads(run.stdout)


def test_the_page_never_reaches_the_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что из страницы на диск попадает только скелет.

    Args:
        tmp_path (Path): Временный каталог вместо observations.
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    capture = _tool()
    monkeypatch.setattr(capture, "OUTPUT", tmp_path)

    capture._write_page("проба", DANGEROUS)

    written = sorted(one.name for one in tmp_path.iterdir())
    assert written == ["проба.provenance.json", "проба.skeleton.txt"], written

    body = "".join(one.read_text(encoding="utf-8") for one in tmp_path.iterdir())
    for secret in MUST_VANISH:
        assert secret not in body, f"«{secret}» уцелел в снимке"


def test_a_skeleton_that_fails_self_check_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверяет, что при отказе самопроверки не сохраняется ничего.

    Снимок, о котором нельзя сказать, что в нём нет персональных данных, хуже
    отсутствия снимка: он выглядит проверенным.

    Args:
        tmp_path (Path): Временный каталог вместо observations.
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    capture = _tool()
    monkeypatch.setattr(capture, "OUTPUT", tmp_path)

    def broken(html: str) -> str:
        """Изображает скелет, не прошедший самопроверку.

        Args:
            html (str): Исходная страница.

        Returns:
            str: Ничего не возвращает.

        Raises:
            SkeletonError: Всегда.
        """
        raise capture.SkeletonError("подделка ради проверки")

    monkeypatch.setattr(capture, "skeletonize", broken)

    with pytest.raises(capture.SkeletonError):
        capture._write_page("проба", DANGEROUS)

    assert list(tmp_path.iterdir()) == [], "при отказе самопроверки что-то всё же записано"


@pytest.mark.parametrize(
    "payload",
    [
        [{"request_headers": ["cookie"]}],
        [{"note": "Authorization: Bearer"}],
        {"golden_key": "T10:a"},
        [{"field": "PHPSESSID"}],
        [{"value": "Приветствую"}],
    ],
)
def test_a_suspicious_observation_is_refused(payload: Any) -> None:
    """Проверяет, что подозрительное наблюдение не сохраняется.

    Обезличивает браузерная часть, но полагаться на одну сторону нельзя.
    Проверка грубая нарочно: она отвергает подозрительное, а не доказывает
    безопасное.

    Args:
        payload (Any): Наблюдение, которое обязано быть отвергнуто.

    Returns:
        None
    """
    capture = _tool()
    assert capture._looks_like_a_secret(payload) is not None


def test_an_honest_observation_passes() -> None:
    """Проверяет, что честное наблюдение проходит.

    Без этой проверки предыдущая ничего не значила бы: сторож, отвергающий всё
    подряд, тоже отвергает подозрительное.

    Returns:
        None
    """
    capture = _tool()
    honest = [
        {
            "method": "POST",
            "path": "/runner/",
            "request": {"kind": "form", "fields": {"objects": "T120:adp"}},
            "status": 200,
            "response": {"kind": "json", "fields": {"objects": ["int", "...of 3"]}},
        }
    ]
    assert capture._looks_like_a_secret(honest) is None


def test_the_record_never_carries_cyrillic() -> None:
    """Запрещает кириллицу в том, что уезжает записью.

    Принимающая сторона отвергает наблюдение с кириллическим словом: подписи её
    не содержат, значит появление означает просочившийся настоящий текст.
    Собственный словарь браузерной части не должен ронять эту проверку - а он
    ронял, пока в описании массива стояло русское слово.

    Проверка поведенческая, а не текстовая: словарь прогоняется через те же
    тела, что придут с площадки. Комментарии и сообщения пользователю остаются
    на русском и сюда не попадают.

    Returns:
        None
    """
    bodies = [
        "''",
        "'привет мир'",
        "'a=1&b=Иван'",
        '\'{"nick":"Иван","count":3,"ratio":1.5}\'',
        '\'{"deep":{"a":{"b":{"c":{"d":{"e":{"f":{"g":1}}}}}}}}\'',
        '\'{"list":["Иван","Пётр","Сидор"]}\'',
        '\'{"empty":[],"nothing":null,"flag":true}\'',
        "new URLSearchParams({node:'Иван', text:'привет'})",
        "42",
        "null",
    ]
    shapes = _in_node("[" + ",".join(f"shapeOf({one})" for one in bodies) + "]")
    text = json.dumps(shapes, ensure_ascii=False)

    offenders = sorted({ch for ch in text if "А" <= ch <= "я" or ch in "Ёё"})
    assert not offenders, (
        f"в записи оказалась кириллица {offenders}: принимающая сторона отвергнет "
        f"честное наблюдение. Запись: {text}"
    )


def test_the_record_never_carries_a_value() -> None:
    """Требует, чтобы значения не доезжали до записи ни в каком виде.

    Returns:
        None
    """
    body = '\'{"nick":"ИванПетров","order":"8471223","token":"abc123secret"}\''
    text = json.dumps(_in_node(f"shapeOf({body})"), ensure_ascii=False)

    for secret in ("ИванПетров", "8471223", "abc123secret"):
        assert secret not in text, f"«{secret}» уцелел в записи: {text}"
    assert "nick" in text and "order" in text and "token" in text, (
        f"имена полей потеряны вместе со значениями, запись бесполезна: {text}"
    )


def test_a_json_string_inside_a_field_is_opened() -> None:
    """Требует разбирать вглубь поле, внутри которого лежит JSON.

    Без этого главное осталось бы неизвестным. Первое настоящее наблюдение
    показало, что отправка сообщения идёт полем формы, внутри которого JSON:
    снаружи видно только подпись, и что там за поля - неизвестно. Именно эти
    имена и нужны, чтобы завести операцию отправки.

    Значения при этом не сохраняются: вложенное проходит те же правила.

    Returns:
        None
    """
    body = (
        "new URLSearchParams({"
        "objects: JSON.stringify([{type:'chat_node', id:12345, tag:'a1b2c3d4'}]),"
        "request: JSON.stringify({action:'chat_message', "
        "data:{node:'users-1-2', content:'Привет, Иван'}})"
        "})"
    )
    shape = _in_node(f"shapeOf({body})")
    text = json.dumps(shape, ensure_ascii=False)

    assert "action" in text and "content" in text, (
        f"вложенный JSON не разобран, имена полей потеряны: {text}"
    )
    # Имя действия - протокольная константа и записывается дословно нарочно:
    # без него операцию не завести. Всё прочее по-прежнему только подписью.
    assert "chat_message" in text, f"имя действия потеряно вместе со значениями: {text}"
    for secret in ("Привет", "Иван", "a1b2c3d4", "users-1-2", "12345"):
        assert secret not in text, f"«{secret}» уцелел в записи: {text}"


def test_only_a_protocol_constant_is_written_down_verbatim() -> None:
    """Проверяет узкое исключение из правила «значения не записываются».

    Имя действия и вид объекта - протокольные константы, и без них операцию не
    завести: подпись говорит, что там двенадцать знаков латиницы с пунктуацией,
    а какое это действие - нет.

    Исключение держится на ДВУХ условиях сразу: имя поля из закрытого списка и
    форма значения - строчный идентификатор без цифр и дефисов. Идентификатор
    диалога, имя пользователя и всякий токен такой формы не имеют.

    Returns:
        None
    """
    allowed = _in_node(
        "['chat_message','chat_node','orders_counters','chat-node'].map("
        "(one) => constantOf('action', one))"
    )
    assert allowed == ["chat_message", "chat_node", "orders_counters", "chat-node"], allowed

    refused = _in_node(
        "['users-1-2','a1b2c3d4','ABC','иван','x','user@mail'].map("
        "(one) => constantOf('type', one))"
    )
    assert refused == [None] * 6, refused

    # Поле не из списка не открывается, какой бы формы значение ни было.
    assert _in_node("constantOf('node', 'chat_message')") is None
    assert _in_node("constantOf('content', 'privet')") is None


def test_the_dangerous_headers_are_dropped_by_name() -> None:
    """Проверяет, что куки и авторизация не попадают даже именем.

    Их наличие очевидно, а упоминание соблазняет однажды записать и значение.

    Returns:
        None
    """
    names = _in_node(
        "headerNames({Cookie:'x', AUTHORIZATION:'y', 'Content-Type':'z', 'X-Requested-With':'w'})"
    )
    assert names == ["content-type", "x-requested-with"], names


def test_the_route_name_rule_matches_between_the_two_languages() -> None:
    """ЗАКРЫВАЕТ РАСХОЖДЕНИЕ, стоившее скрытого адреса сохранения лота.

    Правило одно: имя метода в адресе сохраняется, идентификатор маскируется.
    Описаний у него было два - в браузерном сборщике и в питоне, - и они
    разошлись. Браузер получил исключение 24.08.2026, питон не получил.

    Цена: снимок формы правки лота отдал адрес сохранения как /lots/{n12}. Имя
    метода, единственное, ради чего форму и снимали, было скрыто, и добыть его
    предлагалось настоящим сохранением лота - то есть записью на площадке.

    Returns:
        None
    """
    from funora._skeleton import DEFAULT_OWN_HOST, mask_path

    cases = [
        "offerSave",
        "offerEdit",
        "trade",
        "addImage",
        "75289502",
        "ABCD1234",
        "1908",
        "v2Api",
    ]
    theirs = _in_node(json.dumps(cases) + ".map(isRouteName)")
    ours = [
        mask_path(f"https://funpay.com/x/{one}", DEFAULT_OWN_HOST, {}).endswith(one)
        for one in cases
    ]

    for case, mine, other in zip(cases, ours, theirs, strict=True):
        assert mine == other, (
            f"о сегменте {case!r} питон говорит {mine}, браузер {other}. Два "
            "описания одного правила разошлись - снимок страницы и запись "
            "запроса скажут о площадке разное"
        )

    # И сама суть: имя метода видно, идентификатор нет.
    assert ours[cases.index("offerSave")] is True
    assert ours[cases.index("75289502")] is False
    assert ours[cases.index("ABCD1234")] is False


def test_a_form_field_name_survives_but_its_value_never_does() -> None:
    """Требует раскрывать ИМЯ поля формы и никогда - его значение.

    Имя выбирает площадка: csrf_token, price, fields[summary][ru]. По нему
    собирается запрос, и без него снятая форма нечитаема как договор.

    Значение выбирает человек: цена, описание лота, сообщение покупателю. Его
    раскрывать нельзя ни при каких условиях.

    Returns:
        None
    """
    from funora._skeleton import skeletonize

    out = skeletonize(
        "<form action='/lots/offerSave'>"
        "<input name='csrf_token' value='СЕКРЕТНЫЙТОКЕН'>"
        "<input name='fields[summary][ru]' value='Мой лот про CS2'>"
        "<input name='price' value='299.00'>"
        "<input name='имя по-русски' value='x'>"
        "<input name='имя с пробелом' value='y'>"
        "<textarea name='fields[desc][ru]'>Длинное описание лота</textarea>"
        "</form>"
    )

    for name in ("csrf_token", "fields[summary][ru]", "price", "fields[desc][ru]"):
        assert name in out, f"имя поля {name} потеряно - форму не собрать"

    for value in ("СЕКРЕТНЫЙТОКЕН", "Мой лот про CS2", "299.00", "Длинное описание лота"):
        assert value not in out, f"значение {value!r} уехало в скелет"

    for odd in ("имя по-русски", "имя с пробелом"):
        assert odd not in out, (
            f"имя {odd!r} сохранено дословно: правило обязано пропускать только "
            "то, что похоже на имя поля, иначе через него утечёт что угодно"
        )


def test_the_signature_matches_between_the_two_languages() -> None:
    """Сверяет подпись значения в питоне и в браузерной части.

    Подпись - единственное, что уносит с собой сведения о длине и составе, и
    считают её обе стороны: страницу подписывает питон, форму запроса - браузер.
    Разошедшиеся подписи означали бы, что два наблюдения одного и того же
    выглядят разными.

    Returns:
        None
    """
    from funora._skeleton import text_signature

    cases = [
        "1500",
        "Иван Петров",
        "#8471223",
        "ORDER-8471223",
        "  пробелы по краям  ",
        "mixed Кир и latin 42",
        "",
        "   ",
        "é",
        "é",
        "a-b_c.d",
        "€",
    ]
    theirs = _in_node(json.dumps(cases, ensure_ascii=False) + ".map(signature)")
    ours = [text_signature(one) for one in cases]

    for case, mine, other in zip(cases, ours, theirs, strict=True):
        assert mine == other, f"на {case!r} питон даёт {mine!r}, браузер {other!r}"


def test_a_refused_constant_says_why_without_saying_what() -> None:
    """Проверяет подсказку о значении, не прошедшем образец протокольного знака.

    Три значения, без которых не собрать запрос отправки сообщения, остались
    подписями: подпись говорит «латиница и пунктуация», а какая пунктуация - не
    говорит. Расширять образец по такой подписи пришлось бы наугад, и наугад
    расширенный образец однажды пропустил бы токен - первая попытка это и
    сделала, а проверка выше её поймала.

    Подсказка даёт мерку: длину, набор различных знаков пунктуации и наличие
    заглавных с цифрами. Восстановить по ней значение нельзя.

    Returns:
        None
    """
    hint = _in_node("constantHint('action', 'chat.message')")
    assert hint["length"] == 12
    assert hint["punctuation"] == "."
    assert hint["has_upper"] is False
    assert hint["has_digit"] is False

    # Буквы не сохраняются даже набором: подсказка о пунктуации, а не о слове.
    text = json.dumps(hint, ensure_ascii=False)
    for letter in ("chat", "message", "c", "m"):
        assert letter not in text.replace("punctuation", "").replace("has_", ""), (
            f"«{letter}» уцелел в подсказке: {text}"
        )

    # Прошедшее образец подсказки не получает: значение и так записано дословно.
    assert _in_node("constantHint('action', 'chat_message')") is None
    # Поле не из закрытого списка не описывается вовсе, какой бы формы ни было.
    assert _in_node("constantHint('content', 'privet-vsem')") is None
    assert _in_node("constantHint('node', 'users-1-2')") is None


def test_the_hint_never_carries_a_human_string() -> None:
    """Требует, чтобы подсказка не выдавала человеческий текст.

    Поля action и type несут имя действия протокола. Окажись в них однажды то,
    что написал человек, - подсказка обязана сказать о нём длину и знаки
    препинания, и ни слова больше.

    Returns:
        None
    """
    hint = _in_node("constantHint('type', 'Привет, Иван! Купил ключ 12345.')")
    assert hint is not None, "подсказки нет вовсе: измерить нечем"

    text = json.dumps(hint, ensure_ascii=False)
    for secret in ("Привет", "Иван", "Купил", "ключ", "12345"):
        assert secret not in text, f"«{secret}» уцелел в подсказке: {text}"

    assert hint["has_digit"] is True
    assert set(hint["punctuation"]) <= set(" ,!.")


def test_a_route_name_survives_but_an_identifier_does_not() -> None:
    """Проверяет, чем имя метода в адресе отличается от идентификатора.

    Загрузка изображения идёт на POST /file/<имя метода>, и имя писано горбатым
    письмом. Прежнее правило маскировало всякий сегмент с заглавной буквой, и
    операцию по такой записи собрать было нельзя: в записи стоял /file/{n}.

    Расширение узкое по построению. Сегмент из одних строчных букв правило
    пропускало и раньше - /orders/trade писался дословно. Меняется ровно одно:
    заглавная ВНУТРИ сегмента, который начинается со строчной и не имеет цифр.

    Returns:
        None
    """
    kept = _in_node("maskUrl('https://funpay.com/file/addChatImage')")
    assert kept["path"] == "/file/addChatImage", kept
    assert "masked_segments" not in kept, "имя метода не маскируется, мерке взяться неоткуда"

    # Восемь цифр - человек. Восемь ЗАГЛАВНЫХ - номер заказа. Оба под правило
    # имени метода не подходят и остаются замаскированными.
    for address, length, upper, digit in (
        ("https://funpay.com/users/12345678/", 8, False, True),
        ("https://funpay.com/orders/ZVVABCDE/", 8, True, False),
    ):
        masked = _in_node(f"maskUrl('{address}')")
        assert "{n}" in masked["path"], f"{address}: идентификатор уцелел в {masked['path']}"
        hint = masked["masked_segments"][0]
        assert (hint["length"], hint["has_upper"], hint["has_digit"]) == (length, upper, digit), (
            hint
        )


def test_the_masked_segment_hint_never_carries_the_segment() -> None:
    """Требует, чтобы мерка замаскированного сегмента не выдавала его самого.

    Мерка заведена затем, что отказ записать значение молчал о причине, и
    следующее решение принималось наугад. Молчание она снимает, значения не
    выдаёт.

    Returns:
        None
    """
    masked = _in_node("maskUrl('https://funpay.com/users/ZVV12345/')")
    text = json.dumps(masked, ensure_ascii=False)
    for piece in ("ZVV", "12345", "ZVV12345"):
        assert piece not in text, f"«{piece}» уцелел в записи: {text}"

    hint = masked["masked_segments"][0]
    assert hint["has_upper"] is True
    assert hint["has_digit"] is True
    assert hint["length"] == 8


def test_an_array_gives_every_distinct_shape_not_only_the_first() -> None:
    """Проверяет, что из массива записывается каждая РАЗЛИЧНАЯ форма.

    Прежде записывалась одна - форма первого элемента, - и канал обновлений
    остался наполовину неизвестным: в подписке четыре объекта разных видов, а
    знали мы про один.

    Returns:
        None
    """
    shapes = _in_node("shapeOfValue([{a:1},{a:2},{b:'x'},{c:true}],0)")
    assert shapes[:-1] == [{"a": "int"}, {"b": "T1:a"}, {"c": "boolean"}], shapes
    assert shapes[-1] == "...of 4, distinct 3", shapes

    # Одинаковые формы схлопываются: перечень описывает виды, а не длину.
    same = _in_node("shapeOfValue([{a:1},{a:2},{a:3}],0)")
    assert same == [{"a": "int"}, "...of 3, distinct 1"], same

    # Восемь - предел. Длинный список не выгружается в запись целиком.
    many = _in_node("shapeOfValue(Array.from({length: 30}, (_, i) => ({['k'+i]: 1})),0)")
    assert len(many) == 9, f"форм записано {len(many) - 1}, предел восемь"


def test_the_array_marker_stays_free_of_cyrillic() -> None:
    """Требует, чтобы служебная метка массива осталась на латинице.

    Кириллица в записи означает утёкший русский текст, и принимающая сторона
    отвергает такую запись целиком. Метка «различных форм 2» уронила бы честное
    наблюдение - проверка это и поймала.

    Returns:
        None
    """
    text = json.dumps(_in_node("shapeOfValue([{a:1},{b:2}],0)"), ensure_ascii=False)
    offenders = sorted({ch for ch in text if "А" <= ch <= "я" or ch in "Ёё"})
    assert not offenders, f"в метке кириллица {offenders}: {text}"


def _run_collector(script: str) -> Any:
    """Выполняет браузерную часть ЦЕЛИКОМ под node с заглушками вместо браузера.

    Проверка выше берёт из сборщика отдельные функции - от charClass до
    headerNames. Этого хватает для мерок и подписей и не хватает для того, что
    сборщик ОТПРАВЛЯЕТ: перехват запросов и window.funora лежат за пределами
    среза.

    Заглушкой служит сам fetch: сборщик запоминает исходный при загрузке и
    ходит через него. Подменённый до выполнения, он ловит и перехваченные
    обращения, и собственную отправку наблюдения - никуда наружу при этом не
    уходит ничего.

    Args:
        script (str): Выражение на JavaScript, выполняемое после загрузки
            сборщика. Результат печатается вызывающим через console.log.

    Returns:
        Any: Разобранный из JSON результат.
    """
    prelude = [
        "const sent = [];",
        "globalThis.window = globalThis;",
        # origin обязателен: запись отбрасывает чужие источники, и заглушка
        # без него молча не записала бы ничего.
        "globalThis.location = {href: 'https://funpay.com/chat/', origin: 'https://funpay.com'};",
        "globalThis.document = {documentElement: {outerHTML: '', lang: 'ru'}, title: ''};",
        "globalThis.performance = {getEntriesByType: () => []};",
        "globalThis.XMLHttpRequest = function () {};",
        "globalThis.XMLHttpRequest.prototype = {open(){}, send(){}, "
        "setRequestHeader(){}, addEventListener(){}};",
        "globalThis.fetch = async (url, init) => {",
        "  sent.push({url, init});",
        "  return {ok: true, status: 200, text: async () => 'принято', "
        "json: async () => ({}), clone(){ return this }, headers: {forEach(){}}};",
        "};",
        "const source = require('fs').readFileSync(process.argv[1], 'utf8')",
        "  .replace('__FUNORA_ENDPOINT__', 'http://127.0.0.1:8731/')",
        "  .replace('__FUNORA_BUILD__', 'sborka99');",
        "eval(source);",
        # Сборщик и сам печатает - приветствие и ответ приёмника. Свой ответ
        # помечается, иначе он тонет в чужом выводе.
        f"(async () => {{ console.log('@@' + JSON.stringify(await ({script}))); }})();",
    ]
    run = subprocess.run(  # noqa: S603
        ["node", "-e", "\n".join(prelude), str(SNIPPET)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert run.returncode == 0, f"сборщик не выполнился: {run.stderr}"
    marked = [one for one in (run.stdout or "").splitlines() if one.startswith("@@")]
    assert marked, f"сборщик ничего не вернул. Вывод: {run.stdout!r} {run.stderr!r}"
    return json.loads(marked[-1][2:])


def test_the_tag_probe_summary_never_carries_a_single_word_of_the_platform() -> None:
    """Требует, чтобы сводка опроса канала не выносила чужого текста.

    ЭТО НЕ ПРИДИРКА. Ответ канала несёт разметку списка диалогов, то есть имена
    собеседников и куски их сообщений. Сводка печатается в консоль браузера, а
    оттуда её копируют в переписку, в issue, в чат с помощником.

    Правило потому и такое: наружу идут ИМЕНА полей и ЧИСЛА, а значения - нет.
    Имя поля говорит о площадке, значение - о человеке.

    Returns:
        None
    """
    # Ответ канала подставляется заведомо «грязным»: имя собеседника, текст
    # сообщения, разметка. Ни одно из этого не имеет права уехать в сводку.
    dirty = {
        "objects": [
            {
                "type": "chat_bookmarks",
                "id": "77",
                "tag": "новая-метка-1",
                "data": {"html": "<div>Иван Петров</div>", "counter": 3, "order": [1, 2]},
            },
            {
                "type": "orders_counters",
                "id": "77",
                "tag": "новая-метка-2",
                "data": {"buyer": 0, "seller": 4},
            },
        ],
        "response": False,
    }

    summary = _run_collector(
        "(async () => {"
        "  globalThis.document.querySelector = (one) => {"
        "    if (one === '[data-user]') return {getAttribute: () => '77'};"
        "    if (one === 'body[data-app-data]') return "
        "      {getAttribute: () => JSON.stringify({'csrf-token': 'tok'})};"
        "    return null;"
        "  };"
        "  globalThis.document.querySelectorAll = () => [];"
        f"  const answer = {json.dumps(dirty, ensure_ascii=False)};"
        "  globalThis.fetch = async () => ({ok: true, status: 200,"
        "    text: async () => JSON.stringify(answer), json: async () => answer,"
        "    clone(){ return this }, headers: {forEach(){}}});"
        "  return await funora.probeTag();"
        "})()"
    )

    printed = json.dumps(summary, ensure_ascii=False)

    for leak in ("Иван", "Петров", "<div>", "новая-метка"):
        assert leak not in printed, (
            f"в сводку уехало {leak!r}: {printed}. Ответ канала несёт имена "
            "собеседников, и печатать его в консоль нельзя"
        )

    # А то, ради чего сводка и нужна, - в ней есть.
    first = summary["выдуманная_метка"]
    assert first["объектов"] == 2
    assert first["виды"] == ["chat_bookmarks", "orders_counters"]
    assert sorted(first["поля"][1]) == ["buyer", "seller"], (
        "имена полей не дошли: по ним и решают, годится ли канал"
    )
    assert first["метки_сменились"] is True


def test_the_tag_probe_asks_three_times_in_one_go() -> None:
    """Требует, чтобы опрос сам слал второй запрос вернувшимися метками.

    Переносить метки руками НЕЛЬЗЯ: ответ в консоль не показывается, а
    показать его значит показать имена собеседников. Раньше здесь был один
    запрос и указание «повторите с метками из ответа» - указание неисполнимое.

    Метки к тому же сменяются от ответа к ответу: сделать второй запрос позже
    уже не выйдет.

    Returns:
        None
    """
    answer = {
        "objects": [
            {"type": "chat_bookmarks", "id": "77", "tag": "вторая", "data": {}},
            {"type": "orders_counters", "id": "77", "tag": "третья", "data": {}},
        ]
    }

    bodies = _run_collector(
        "(async () => {"
        "  const asked = [];"
        "  globalThis.document.querySelector = (one) => {"
        "    if (one === '[data-user]') return {getAttribute: () => '77'};"
        "    if (one === 'body[data-app-data]') return "
        "      {getAttribute: () => JSON.stringify({'csrf-token': 'tok'})};"
        "    return null;"
        "  };"
        "  globalThis.document.querySelectorAll = () => [];"
        f"  const answer = {json.dumps(answer, ensure_ascii=False)};"
        "  globalThis.fetch = async (url, init) => {"
        "    asked.push(String(init.body));"
        "    return {ok: true, status: 200, text: async () => JSON.stringify(answer),"
        "      json: async () => answer, clone(){ return this }, headers: {forEach(){}}};"
        "  };"
        "  await funora.probeTag();"
        "  return asked;"
        "})()"
    )

    assert len(bodies) == 2, (
        f"опросов было {len(bodies)}, а нужно два: с выдуманной меткой и с "
        "вернувшимися. Третий пропускается, если на странице мало строк"
    )
    assert "0000000000" in bodies[0], "первый опрос ушёл не с выдуманной меткой"
    assert "0000000000" not in bodies[1], (
        "второй опрос ушёл с той же выдуманной меткой: вернувшиеся не подставились"
    )
    assert "%D0%B2%D1%82%D0%BE%D1%80%D0%B0%D1%8F" in bodies[1] or "вторая" in bodies[1], (
        f"во втором опросе нет метки из первого ответа: {bodies[1]}"
    )


def test_a_form_that_navigates_is_recorded_at_all() -> None:
    """ЗАКРЫВАЕТ ПРОБЕЛ, стоивший потерянного наблюдения.

    Сборщик перехватывает fetch и XMLHttpRequest. Форма с обычной отправкой не
    пользуется ни тем, ни другим: браузер уходит на новый адрес сам, и для
    JavaScript этого запроса не существует вовсе.

    Так потерялось сохранение лота: запись велась, кнопка нажата, страница ушла
    - а в записи оказался один фоновый опрос канала.

    Returns:
        None
    """
    payload = _run_collector(
        "(async () => {"
        "  const form = {"
        "    action: 'https://funpay.com/lots/offerSave',"
        "    method: 'post',"
        "  };"
        "  globalThis.HTMLFormElement = function () {};"
        "  Object.setPrototypeOf(form, globalThis.HTMLFormElement.prototype);"
        "  globalThis.FormData = function () {"
        "    this.entries = () => [['csrf_token', 'abcdef0123456789'],"
        "      ['price', '1.015'], ['active', 'on'],"
        "      ['fields[desc][ru]', 'Длинное описание лота']][Symbol.iterator]();"
        "  };"
        "  funora.watch();"
        "  submitted(form);"
        "  await funora.stop('проба');"
        "  const last = sent[sent.length - 1];"
        "  return JSON.parse(last.init.body).payload.records;"
        "})()"
    )

    assert len(payload) == 1, f"отправка формы не записана: {payload}"
    one = payload[0]
    assert one["navigation"] is True
    assert one["path"] == "/lots/offerSave", one["path"]
    assert one["method"] == "POST"
    assert sorted(one["request"]["fields"]) == [
        "active",
        "csrf_token",
        "fields[desc][ru]",
        "price",
    ]

    printed = json.dumps(payload, ensure_ascii=False)
    assert "Длинное описание лота" not in printed, f"значение поля уехало: {printed}"
    assert "abcdef0123456789" not in printed, "токен уехал в запись"
    assert "1.015" not in printed, "цена уехала в запись"


def test_a_form_pointing_at_another_host_is_not_recorded() -> None:
    """Требует не записывать форму, уходящую на ЧУЖОЙ хост.

    Правило то же, что у перехвата обращений: наблюдение ведётся о площадке, и
    чужой адресат в записи означал бы, что мы записали чей-то посторонний
    запрос - например, платёжной формы, встроенной в страницу.

    Returns:
        None
    """
    payload = _run_collector(
        "(async () => {"
        "  const form = { action: 'https://example.com/pay', method: 'post' };"
        "  globalThis.HTMLFormElement = function () {};"
        "  Object.setPrototypeOf(form, globalThis.HTMLFormElement.prototype);"
        "  globalThis.FormData = function () {"
        "    this.entries = () => [['card', '4111111111111111']][Symbol.iterator]();"
        "  };"
        "  funora.watch();"
        "  submitted(form);"
        "  return funora.status().recorded;"
        "})()"
    )

    assert payload == 0, (
        f"форма на чужой хост записана ({payload} записей). Наблюдение ведётся "
        "о площадке, а чужой адресат означал бы, что мы записали посторонний "
        "запрос - например, встроенной платёжной формы"
    )


def test_a_changed_token_is_noticed_without_being_shown() -> None:
    """Требует замечать смену защитного токена, не раскрывая его.

    ЗАЧЕМ ЭТО ВООБЩЕ НУЖНО. Подпись значения говорит «шестнадцать знаков
    латиницы с цифрами», и две РАЗНЫЕ шестнадцатизначные строки по ней
    неотличимы. По записи нельзя было понять, тот же токен ушёл во втором
    запросе или другой.

    А от этого зависит главное решение о скорости: можно ли взять токен одним
    стартовым чтением страницы и держать всю сессию, или каждый опрос обязан
    перечитывать страницу.

    Returns:
        None
    """
    records = _run_collector(
        "(async () => {"
        "  funora.watch();"
        "  const go = (t) => fetch('https://funpay.com/runner/', {method: 'POST',"
        "    body: 'objects=%5B%5D&request=false&csrf_token=' + t});"
        "  await go('ПЕРВЫЙТОКЕН00001');"
        "  await go('ПЕРВЫЙТОКЕН00001');"
        "  await go('ВТОРОЙТОКЕН00002');"
        "  await funora.stop('проба');"
        "  const last = sent[sent.length - 1];"
        "  return JSON.parse(last.init.body).payload.records"
        "    .map((one) => one.request.fields.csrf_token);"
        "})()"
    )

    printed = json.dumps(records, ensure_ascii=False)
    for leak in ("ПЕРВЫЙТОКЕН", "ВТОРОЙТОКЕН"):
        assert leak not in printed, f"значение токена уехало в запись: {printed}"

    assert len(records) == 3, records

    # У первого сравнивать не с чем: подпись без признака, то есть строка.
    assert isinstance(records[0], str), f"у первого запроса взялся признак: {records[0]}"
    assert records[1]["since_previous"] == "same", records[1]
    assert records[2]["since_previous"] == "changed", records[2]


def test_the_held_token_is_never_shown_to_the_human() -> None:
    """Требует, чтобы запомненный токен не выходил наружу ни разу.

    ЭТО ПРАВИЛО НАПИСАНО ПО ПРОИСШЕСТВИЮ. Прежде наблюдение канала при истёкшей
    сессии требовало от человека скопировать защитный токен со страницы. Человек,
    отправленный искать токен, пошёл в инструменты разработчика - а там рядом
    лежат ключ сессии и прочие куки, и один снимок экрана отдал аккаунт целиком.

    Отсюда правило: человек не переносит руками ничего, что похоже на секрет. Не
    потому, что не справится, а потому, что путь к значению ведёт мимо настоящих
    секретов.

    Returns:
        None
    """
    said = _run_collector(
        "(async () => {"
        "  const store = {};"
        "  globalThis.localStorage = {"
        "    getItem: (k) => (k in store ? store[k] : null),"
        "    setItem: (k, v) => { store[k] = String(v) },"
        "    removeItem: (k) => { delete store[k] },"
        "  };"
        # Страница ВОШЕДШЕГО: меню с ссылкой на свой профиль на месте. Без
        # него сборщик откажет - и правильно откажет, см. соседнюю проверку.
        "  globalThis.document.querySelector = (one) => {"
        "    if (one === 'body[data-app-data]') return "
        "      {getAttribute: () => JSON.stringify({'csrf-token': 'СЕКРЕТНЫЙТОКЕН'})};"
        "    if (one === 'a.user-link-dropdown') return {};"
        "    return null;"
        "  };"
        "  return {ответ: funora.holdToken(), в_хранилище: store['funora.held-token']};"
        "})()"
    )

    assert "СЕКРЕТНЫЙТОКЕН" not in said["ответ"], f"токен показан человеку: {said['ответ']!r}"
    assert said["в_хранилище"] == "СЕКРЕТНЫЙТОКЕН", "токен не запомнен - опыт не выйдет"


def test_the_token_is_refused_on_a_guest_page() -> None:
    """Требует отказать, когда токен просят запомнить ПОСЛЕ выхода.

    Порядок здесь перепутать легко, и перепутанный он не виден никак: токен
    есть и у гостевой страницы, и выглядит он точно так же.

    Запомненный после выхода, он отвечает на другой вопрос - «что канал скажет
    гостю» вместо «что он скажет тому, у кого сессия истекла». Отличить это
    потом по записи наблюдения НЕЛЬЗЯ: в ней подписи, а не значения. Значит
    проверять надо в момент, когда ещё можно.

    Returns:
        None
    """
    said = _run_collector(
        "(async () => {"
        "  const store = {};"
        "  globalThis.localStorage = {"
        "    getItem: () => null, setItem: (k, v) => { store[k] = v }, removeItem: () => {},"
        "  };"
        # Носитель настроек есть - как на настоящей гостевой странице, - а
        # меню вошедшего нет.
        "  globalThis.document.querySelector = (one) => (one === 'body[data-app-data]'"
        "    ? {getAttribute: () => JSON.stringify({'csrf-token': 'ГОСТЕВОЙ'})}"
        "    : null);"
        "  return {ответ: funora.holdToken(), запомнено: store['funora.held-token'] || null};"
        "})()"
    )

    assert said["запомнено"] is None, "гостевой токен запомнен: опыт выйдет не тот"
    assert "ОТКАЗ" in said["ответ"], said["ответ"]
    assert "войдите" in said["ответ"].lower(), f"отказ не сказал, что делать: {said['ответ']!r}"


def test_the_guest_page_really_carries_a_token_but_no_logged_in_menu() -> None:
    """Показывает, ПОЧЕМУ защита порядка вообще нужна, и на снимках.

    Проверка не о сборщике, а о площадке, и без неё защита держится на моём
    слове. Обе половины важны:

    носитель настроек body[data-app-data] есть и у ГОСТЯ - значит токен там
    лежит, и «нет токена» гостевую страницу не отсеет;

    ссылки на собственный профиль у гостя нет ни одной, а у вошедшего их две -
    значит отсеять можно по ней.

    Returns:
        None
    """
    from selectolax.parser import HTMLParser

    pages = ROOT / "tests" / "fixtures" / "pages"

    guest = HTMLParser((pages / "orders-trade.guest.ru.skeleton.txt").read_text(encoding="utf-8"))
    assert len(guest.css("body[data-app-data]")) == 1, (
        "у гостевой страницы нет носителя настроек: тогда защита порядка не "
        "нужна вовсе, и проверять надо было бы другое"
    )
    assert len(guest.css("a.user-link-dropdown")) == 0, (
        "у гостя нашлась ссылка на собственный профиль: признак вошедшего "
        "выбран неверно, и захват токена после выхода пройдёт молча"
    )

    for name in ("orders-trade.logged.ru", "chat.logged.ru", "root.logged.ru"):
        page = HTMLParser((pages / f"{name}.skeleton.txt").read_text(encoding="utf-8"))
        assert page.css("a.user-link-dropdown"), (
            f"на снимке вошедшего {name} нет признака вошедшего: захват токена "
            "будет отказывать там, где отказывать не должен"
        )


def test_the_dead_session_probe_forgets_the_token_right_after() -> None:
    """Требует стирать запомненный токен сразу, пригодился он или нет.

    Лежать ему во вкладке незачем: опыт делается один раз.

    Returns:
        None
    """
    said = _run_collector(
        "(async () => {"
        "  const store = {'funora.held-token': 'СЕКРЕТНЫЙТОКЕН'};"
        "  globalThis.localStorage = {"
        "    getItem: (k) => (k in store ? store[k] : null),"
        "    setItem: (k, v) => { store[k] = String(v) },"
        "    removeItem: (k) => { delete store[k] },"
        "  };"
        "  globalThis.fetch = async () => ({ok: false, status: 403, redirected: true,"
        "    text: async () => 'нужен вход', clone(){ return this }, headers: {forEach(){}}});"
        "  const answer = await funora.probeDead();"
        "  return {ответ: answer, осталось: store['funora.held-token'] || null};"
        "})()"
    )

    assert said["осталось"] is None, "токен остался лежать во вкладке после опыта"

    printed = json.dumps(said["ответ"], ensure_ascii=False)
    assert "СЕКРЕТНЫЙТОКЕН" not in printed, f"токен уехал в сводку: {printed}"
    assert "нужен вход" not in printed, (
        f"тело ответа уехало в сводку: {printed}. Наружу идут код, признаки и "
        "имена полей - но не содержимое"
    )
    assert said["ответ"]["код"] == 403
    assert said["ответ"]["перенаправлен"] is True
    assert said["ответ"]["разобрано_как_json"] is False


def test_the_dead_session_probe_refuses_without_a_held_token() -> None:
    """Требует внятного отказа, когда токен не запомнили заранее.

    Порядок здесь легко перепутать: holdToken зовётся ДО выхода, probeDead -
    после. Молчаливый отказ отправил бы человека выходить и входить второй раз.

    Returns:
        None
    """
    said = _run_collector(
        "(async () => {"
        "  const store = {};"
        "  globalThis.localStorage = {"
        "    getItem: () => null, setItem: () => {}, removeItem: () => {},"
        "  };"
        "  try { await funora.probeDead(); return 'отказа не было' }"
        "  catch (e) { return String(e.message) }"
        "})()"
    )

    assert "holdToken" in said, f"отказ не назвал, что делать: {said!r}"
    assert "ДО выхода" in said


def test_the_collector_stamps_its_build_into_what_it_sends() -> None:
    """Требует, чтобы браузерная часть ВПРАВДУ клала отпечаток сборки.

    Вкладка держит ту редакцию сборщика, которую загрузила, и по виду записи это
    не отличить. Три наблюдения отправки сообщения были сделаны старой
    редакцией, и я объяснил их подписи слишком узким образцом - настоящей
    причиной была старая вкладка.

    Прежняя проверка мерила приёмник на нагрузке, которую сама же и сложила.
    Мутация «отпечаток не кладётся» её пережила.

    Returns:
        None
    """
    payload = _run_collector(
        "(async () => {"
        "  funora.watch();"
        "  await fetch('https://funpay.com/runner/', "
        "    {method: 'POST', body: 'csrf_token=x&request=false'});"
        "  await funora.stop('proba');"
        "  const last = sent[sent.length - 1];"
        "  return JSON.parse(last.init.body);"
        "})()"
    )

    assert payload["kind"] == "network", payload
    body = payload["payload"]
    assert body["collector_build"] == "sborka99", (
        f"отпечаток сборки не дошёл до наблюдения: {body}. Без него нельзя "
        "отличить запись старой вкладки от свежей"
    )
    assert body["captured_at"].startswith("20"), body["captured_at"]
    assert len(body["records"]) == 1, body["records"]
    assert body["records"][0]["path"] == "/runner/", body["records"][0]


def test_a_network_observation_carries_its_collector_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Требует, чтобы сетевое наблюдение называло сборку, которая его сделала.

    Вкладка держит ту редакцию сборщика, которую загрузила, и по виду записи это
    не отличить. Три наблюдения отправки сообщения были сделаны старой
    редакцией, без механизма протокольных констант, и я объяснил их подписи
    слишком узким образцом. Настоящей причиной была старая вкладка, и узнать это
    было неоткуда.

    Args:
        tmp_path (Path): Временный каталог вместо observations.
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    capture = _tool()
    monkeypatch.setattr(capture, "OUTPUT", tmp_path)

    payload = {
        "collector_build": "66d0c1ef",
        "captured_at": "2026-08-24T04:09:00.000Z",
        "records": [{"method": "POST", "path": "/runner/"}, {"method": "POST", "path": "/runner/"}],
    }
    said = capture._write_json("network", "проба", payload)
    assert "записей 2" in said, said

    written = json.loads((tmp_path / "network.проба.json").read_text(encoding="utf-8"))
    assert written["collector_build"] == "66d0c1ef"

    # Голый список - прежний вид записи, и такие файлы ещё лежат. Считаться он
    # обязан по-прежнему: иначе счётчик молча съедет на единицу.
    assert capture._record_count([1, 2, 3]) == 3
    assert capture._record_count(payload) == 2


def test_a_refusal_names_the_place_and_the_shape_but_not_the_value() -> None:
    """Требует, чтобы отказ приёмника называл место находки.

    Прежде проверка искала кириллицу по всей записи разом и говорила
    «нашлось». Место оставалось неизвестным, и починить наблюдение было нельзя -
    только снять заново наугад. Это стоило трёх отправок настоящих сообщений в
    настоящую переписку, и все три пропали впустую.

    Путь называть безопасно: он состоит из имён полей, а имена полей и так лежат
    в записи. Мерку называть безопасно: длина и знаки препинания не дают
    восстановить слово. Само слово - нельзя, для того проверка и стоит.

    Returns:
        None
    """
    capture = _tool()

    said = capture._looks_like_a_secret(
        {"records": [{"response": {"objects": [{"data": {"html": "Привет всем"}}]}}]}
    )
    assert said is not None, "кириллица прошла молча"
    assert "records[0].response.objects[0].data.html" in said, said
    assert "длина 11" in said, said

    # Самого слова в отказе нет ни в каком виде.
    for secret in ("Привет", "всем", "Привет всем"):
        assert secret not in said, f"«{secret}» уцелел в отказе: {said}"


def test_a_refusal_tells_apart_a_field_name_from_a_field_value() -> None:
    """Требует различать кириллицу в ИМЕНИ поля и в его значении.

    Имя поля структурно и записывается дословно по устройству: маскировать его
    нельзя, не потеряв разбор. Значение маскируется всегда. Лечение у этих двух
    случаев разное, и отказ обязан сказать, какой из них перед ним.

    Returns:
        None
    """
    capture = _tool()

    said = capture._looks_like_a_secret({"records": [{"data": {"название": "x"}}]})
    assert said is not None
    # Ключ отвергается ДВАЖДЫ по разным основаниям, и первым срабатывает
    # структурное: кириллическое слово - частный случай ключа, не похожего на
    # имя поля. Годится любое из двух, лишь бы речь шла о ключе и лишь бы самого
    # ключа в отказе не было.
    assert "<ключ>" in said or "<имя поля>" in said, said
    assert "название" not in said, said

    # Чистая запись проходит молча.
    assert (
        capture._looks_like_a_secret(
            {"records": [{"path": "/runner/", "request": {"fields": {"csrf_token": "T16:ad"}}}]}
        )
        is None
    )


def test_the_cyrillic_check_still_refuses_what_it_refused_before() -> None:
    """Требует, чтобы уточнение отказа не ослабило самого правила.

    Проверка стала подробнее. Подробнее - не значит мягче: всё, что отвергалось
    прежде, обязано отвергаться и теперь.

    Returns:
        None
    """
    capture = _tool()

    for payload in (
        {"headers": ["cookie"]},
        {"a": {"b": "Authorization"}},
        ["golden_key"],
        {"x": ["y", {"z": "пароль от аккаунта"}]},
    ):
        assert capture._looks_like_a_secret(payload) is not None, payload

    # Три кириллические буквы подряд - не слово. Порог тот же, что был.
    assert capture._looks_like_a_secret({"a": "или"}) is None


def test_nothing_the_collector_writes_is_ever_in_cyrillic() -> None:
    """Требует, чтобы в записи не было кириллицы, что бы ни попало на вход.

    Принимающая сторона отвергает запись с кириллицей ЦЕЛИКОМ: кириллица в
    записи означает утёкший русский текст. Проверка на это уже была, но
    проверяла она только подписи значений - и мимо неё прошла поясняющая строка,
    которую сборщик клал в подсказку сам, по-русски.

    Механизм, объясняющий отказ записать значение, сделал наблюдение
    несохраняемым. Три попытки снять его пропали впустую.

    Здесь сборщик выполняется целиком и через него прогоняются разом все
    механизмы, которые вправе что-то дописать в запись: подпись значения, мерка
    непрошедшего протокольного знака, мерка замаскированного сегмента адреса,
    служебная метка массива.

    Returns:
        None
    """
    payload = _run_collector(
        "(async () => {"
        "  funora.watch();"
        # Адрес с идентификатором - сработает мерка замаскированного сегмента.
        # Поле type со значением не под образец - сработает мерка знака.
        # Массив разных форм - сработает служебная метка.
        "  await fetch('https://funpay.com/users/ZVV12345/', {method: 'POST', "
        "    body: 'csrf_token=abc&objects=' + encodeURIComponent(JSON.stringify("
        "      [{type: 'chat-node-2'}, {type: 'orders_counters'}, {other: 1}]))"
        "  });"
        "  await funora.stop('proba');"
        "  return JSON.parse(sent[sent.length - 1].init.body);"
        "})()"
    )

    text = json.dumps(payload, ensure_ascii=False)
    offenders = sorted({ch for ch in text if "А" <= ch <= "я" or ch in "Ёё"})
    assert not offenders, (
        f"в записи оказалась кириллица {offenders}: принимающая сторона отвергнет "
        f"честное наблюдение целиком. Запись: {text}"
    )

    # Проверка что-то проверяет только если механизмы вправду сработали.
    assert "masked_segments" in text, f"мерка сегмента не сработала: {text}"
    assert "hint" in text, f"мерка протокольного знака не сработала: {text}"
    assert "distinct" in text, f"служебная метка массива не сработала: {text}"

    # И то же самое глазами принимающей стороны.
    assert _tool()._looks_like_a_secret(payload["payload"]) is None, _tool()._looks_like_a_secret(
        payload["payload"]
    )


def test_the_hyphen_was_admitted_by_measurement_and_admits_nothing_else() -> None:
    """Проверяет, что расширение образца не ослабило ни одного отказа.

    Дефис добавлен 24.08.2026 по измерению: четвёртый вид объекта подписки
    канала под прежний образец не подошёл, и мерка сказала чем - длина пять,
    строчные, пунктуация «-», цифр и заглавных нет.

    Первая попытка расширить образец делалась НАУГАД и пропустила бы токен вида
    a1b2c3d4. Разница между тем разом и этим - в том, что теперь известно, какой
    именно знак мешает, и добавлен ровно он.

    Возвращает:
        None
    """
    admitted = _in_node(
        "['chat-node','a-b-c','chat_message','ab-cd'].map((one) => constantOf('type', one))"
    )
    assert admitted == ["chat-node", "a-b-c", "chat_message", "ab-cd"], admitted

    # Всё, что отвергалось до расширения, отвергается и после. Перечень тот же,
    # что у проверки выше, и повторён нарочно: ослабление образца обязано
    # ронять проверку, а не проходить незамеченным.
    refused = _in_node(
        "['users-1-2','a1b2c3d4','ABC','иван','x','user@mail','chat node','ZVVABCDE']"
        ".map((one) => constantOf('type', one))"
    )
    assert refused == [None] * 8, refused


def test_markup_is_never_parsed_as_a_form() -> None:
    """Требует не принимать разметку за строку запроса.

    24.08.2026 в наблюдение утекли настоящие суммы операций. Ответ на догрузку
    строк - это HTML, а сборщик разобрал его КАК ФОРМУ: знак равенства есть в
    каждом атрибуте разметки, разбор строки запроса сделал ключами куски HTML,
    и ключи записались дословно.

    Допущение было такое: значения маскируются, а ключи структурны, потому что
    имя поля говорит о протоколе, а не о человеке. Верно оно ровно до тех пор,
    пока ключи вправду являются именами полей.

    Returns:
        None
    """
    markup = '<div class="tc-price">1031.40 <span class="unit">x</span></div>'
    shape = _in_node(f"shapeOf({json.dumps(markup)})")

    assert shape["kind"] == "string", f"разметка разобрана как {shape['kind']}: {shape}"
    text = json.dumps(shape, ensure_ascii=False)
    for secret in ("1031.40", "tc-price", "unit"):
        assert secret not in text, f"«{secret}» уцелел в записи: {text}"

    # Настоящая строка запроса при этом разбирается по-прежнему.
    real = _in_node("shapeOf('user_id=12345678&continue=987654321&filter=')")
    assert real["kind"] == "form", real
    assert sorted(real["fields"]) == ["continue", "filter", "user_id"], real


def test_a_key_that_is_not_a_field_name_is_masked_too() -> None:
    """Требует маскировать ключ, пришедший из данных.

    Ключом бывает и то, что написал человек, - в словаре, собранном из данных.
    Проверка стоит с той же стороны, что и маскирование значений.

    Returns:
        None
    """
    shape = _in_node("shapeOfValue({'ok_name': 1, 'Иван Петров': 2, '<div class': 3}, 0)")
    text = json.dumps(shape, ensure_ascii=False)

    assert "ok_name" in shape, f"имя поля замаскировано зря: {shape}"
    for secret in ("Иван", "Петров", "<div"):
        assert secret not in text, f"«{secret}» уцелел в записи: {text}"


def test_the_receiver_refuses_a_strange_key_on_its_own() -> None:
    """Требует, чтобы приёмник ловил странный ключ независимо от сборщика.

    Полагаться на одну сторону нельзя: инструмент пишет в каталог разработчика,
    и цена пропуска несимметрична. Браузерная часть чинена - эта проверка стоит
    на случай, если во вкладке окажется старая её редакция.

    Returns:
        None
    """
    capture = _tool()

    leaked = {
        "records": [
            {
                "response": {
                    "kind": "form",
                    "fields": {"minus; 1031.40 <span class": "T1706:acdops"},
                }
            }
        ]
    }
    said = capture._looks_like_a_secret(leaked)
    assert said is not None, "утечка прошла молча"
    assert "<ключ>" in said, said
    for secret in ("1031.40", "minus", "span"):
        assert secret not in said, f"«{secret}» уцелел в отказе: {said}"

    # Честная запись проходит.
    assert (
        capture._looks_like_a_secret(
            {
                "collector_build": "d03c765c",
                "captured_at": "2026-08-24T05:00:00.000Z",
                "records": [
                    {
                        "path": "/users/transactions",
                        "request": {
                            "kind": "form",
                            "fields": {"user_id": "T8:d", "continue": "T9:d", "filter": ""},
                        },
                    }
                ],
            }
        )
        is None
    )


def test_markup_whose_first_key_looks_like_a_field_name_is_still_not_a_form() -> None:
    """Требует не признавать формой разметку, начинающуюся именем поля.

    Проверка отделяет ОДИН заслон от соседнего. Ключ, не похожий на имя поля,
    ловится другой проверкой; здесь тело таково, что первый ключ имя поля
    напоминает - и без заслона по угловым скобкам запись объявила бы разметку
    формой.

    Утечки от этого не будет: значения маскируются. Соврала бы сама запись -
    она сказала бы «форма» там, где пришла разметка, и следующий читатель стал
    бы искать поля запроса в ответе страницы.

    Returns:
        None
    """
    markup = 'data=1<div class="tc-price">1031.40</div>'
    shape = _in_node(f"shapeOf({json.dumps(markup)})")

    assert shape["kind"] == "string", (
        f"разметка объявлена формой: {shape}. Ключ data имя поля напоминает, и "
        "без заслона по угловым скобкам запись соврала бы о виде тела"
    )
    assert "1031.40" not in json.dumps(shape, ensure_ascii=False)

    # Пробел внутри - второй признак того же: строка запроса пробелов не несёт,
    # они в ней закодированы.
    spaced = _in_node(f"shapeOf({json.dumps('name=Иван Петров')})")
    assert spaced["kind"] == "string", spaced
    assert "Иван" not in json.dumps(spaced, ensure_ascii=False)


#: Разметка страницы с метками разделов - той формы, в какой их отдаёт площадка.
WITH_TAGS = (
    '<html><body data-app-data=\'{"csrf-token": "abcdefgh12345678"}\'>'
    '<div class="hidden" id="a" data-orders="7f3a9b21"></div>'
    '<div class="hidden" id="b" data-message="41"></div>'
    "</body></html>"
)

#: Подставной браузер: документ, сеть и способ достать записанное.
BROWSER = r"""
const source = require('fs').readFileSync(process.argv[1], 'utf8')
const html = process.argv[2]

// Подставной документ. Разбирать разметку целиком незачем: сборщику нужен один
// вызов, но их стало больше: команда отправки читает и виджет, и строку списка.
// Поэтому разбираются НАСТОЯЩИЕ элементы - тег, классы, атрибуты.
const elements = []
for (const match of html.matchAll(/<([a-z]+)\s+([^>]*?)\/?>/g)) {
  const attrs = {}
  for (const pair of match[2].matchAll(/([a-z-]+)=("([^"]*)"|'([^']*)')/g)) {
    attrs[pair[1]] = pair[3] !== undefined ? pair[3] : pair[4]
  }
  elements.push({
    tag: match[1],
    attrs,
    classes: (attrs.class || '').split(/\s+/).filter(Boolean),
  })
}

/**
 * Строит объект элемента, каким его ждёт сборщик.
 *
 * @param {object} one Разобранный элемент.
 * @returns {object} Элемент с getAttribute и classList.
 */
function asNode(one) {
  return {
    getAttribute: (name) => (name in one.attrs ? one.attrs[name] : null),
    classList: { contains: (name) => one.classes.includes(name) },
  }
}

/**
 * Проверяет простой селектор: тег, классы через точку, атрибут в скобках.
 *
 * @param {object} one Разобранный элемент.
 * @param {string} selector Селектор.
 * @returns {boolean} Подходит ли элемент.
 */
function matches(one, selector) {
  const attr = selector.match(/\[([a-z-]+)\]$/)
  const head = attr ? selector.slice(0, attr.index) : selector
  if (attr && !(attr[1] in one.attrs)) return false

  const parts = head.split('.')
  const tag = parts.shift()
  if (tag && one.tag !== tag) return false
  return parts.every((name) => one.classes.includes(name))
}

globalThis.moveTheTag = function () {
  for (const one of elements) {
    for (const name of Object.keys(one.attrs)) {
      if (name.startsWith('data-') && name !== 'data-app-data') one.attrs[name] = 'ИНОЕ'
    }
  }
}
globalThis.document = {
  querySelectorAll(selector) {
    return elements.filter((one) => matches(one, selector)).map(asNode)
  },
  querySelector(selector) {
    const found = elements.find((one) => matches(one, selector))
    return found ? asNode(found) : null
  },
  documentElement: { outerHTML: html, lang: 'ru' },
  title: '',
}
globalThis.location = { href: 'https://funpay.com/chat/', origin: 'https://funpay.com' }
globalThis.performance = { getEntriesByType: () => [] }
globalThis.window = globalThis
globalThis.XMLHttpRequest = function () {}
globalThis.XMLHttpRequest.prototype = { open() {}, send() {}, setRequestHeader() {} }

// Сеть подставная: она отвечает и НЕ ходит никуда.
globalThis.sent = []
// Что площадка делает, пока запрос в пути. Ставится проверкой на порядок:
// приложение обновляет метки по ответу канала, и подмена обязана происходить
// ПОСЛЕ снимка, но ДО записи - иначе проверка проверяет не то.
globalThis.inFlight = null
globalThis.window.fetch = async function (url, init) {
  sent.push({ url, init: init || {} })
  if (inFlight) inFlight()
  const body = JSON.stringify({ objects: [], response: false })
  return {
    status: 200,
    text: async () => 'ok',
    clone: () => ({ text: async () => body }),
    headers: new Map(),
  }
}

// Записи достаются так же, как в жизни: stop отправляет их приёмнику, а
// подставная сеть их запоминает. Внутрь сборщика проверка не лезет - она
// смотрит на то, что он ОТДАЁТ.
globalThis.takeRecords = async function () {
  await funora.stop('proba')
  const to = sent.filter((one) => String(one.url).indexOf('FUNORA_ENDPOINT') >= 0)
  if (to.length === 0) throw new Error('сборщик ничего не отправил приёмнику')
  return JSON.parse(to[to.length - 1].init.body).payload.records
}

// Сборщик печатает в консоль сам - и при загрузке, и после каждой отправки.
// Поэтому консоль остаётся немой НАВСЕГДА, а проверка кладёт свой ответ прямо в
// поток вывода: смешайся они, разбор ответа ловил бы приветствие сборщика.
globalThis.console = { log() {}, error() {}, warn() {} }
eval(source)
globalThis.answer = function (value) {
  process.stdout.write(JSON.stringify(value))
}
"""


def _in_browser(script: str, *, html: str = WITH_TAGS) -> Any:
    """Выполняет сборщик ЦЕЛИКОМ под node, подставив браузер.

    Отличается от _in_node тем, что берёт не кусок исходника, а весь файл: то,
    ради чего проверка и пишется, живёт в перехвате запроса, а он в вырезанный
    кусок не попадает.

    Args:
        script (str): Тело асинхронной функции на JavaScript. Печатает результат.
        html (str): Разметка страницы для подставного document.

    Returns:
        Any: Разобранный из JSON результат.
    """
    wrapped = (
        BROWSER
        + "\n;(async () => {\n"
        + script
        + "\n})().catch((e) => { process.stderr.write(String((e && e.stack) || e)); "
        + "process.exit(1) })\n"
    )
    run = subprocess.run(  # noqa: S603
        ["node", "-e", wrapped, str(SNIPPET), html],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert run.returncode == 0, f"сборщик не выполнился: {run.stderr}"
    return json.loads(run.stdout)


def test_the_tag_is_matched_against_the_page_and_only_its_name_is_written() -> None:
    """Требует записывать ИМЯ совпавшего атрибута и никогда - значение.

    Метка подписки - то единственное, обо что упирается сборка запроса записи.
    Откуда она берётся, по двум файлам не узнать: скелет и сетевая запись
    маскируют значения независимо, и две подписи «восемь знаков латиницы с
    цифрами» совпадают у любых двух таких строк.

    Сравнить может только сборщик - в живой вкладке, где страница и тело запроса
    видны разом. Наружу при этом обязано уходить имя атрибута, и ничего больше.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        await window.fetch('/runner/', {
          method: 'POST',
          body: 'objects=' + encodeURIComponent(JSON.stringify([
            {type: 'orders_counters', id: '12345678', tag: '7f3a9b21', data: true},
            {type: 'c-p-u', id: '12345678', tag: 'nesovpalo', data: true}
          ]))
        })
        answer(await takeRecords())
        """
    )

    # Поле формы несёт строкой JSON, и сборщик разбирает его вглубь: отсюда
    # обёртка nested.
    subscription = out[0]["request"]["fields"]["objects"]["nested"]
    first, second = subscription[0], subscription[1]

    assert first["tag"]["from_attribute"] == "data-orders", first["tag"]
    assert second["tag"]["from_attribute"] is False, second["tag"]

    # Ни одного значения - ни совпавшего, ни несовпавшего.
    written = json.dumps(out, ensure_ascii=False)
    assert "7f3a9b21" not in written, "значение совпавшей метки ушло в запись"
    assert "nesovpalo" not in written, "значение несовпавшей метки ушло в запись"


def test_the_tags_are_read_before_the_request_leaves() -> None:
    """Требует снимать метки ДО ухода запроса, а не после ответа.

    Запись делается после ответа, а метки к тому времени приложение вправе уже
    обновить - в этом их назначение. Сверка с обновлённой меткой дала бы «не
    совпало» там, где совпадало, и вывод вышел бы обратным наблюдению.

    Проверка подменяет значение атрибута МЕЖДУ вызовом и ответом.

    Returns:
        None
    """
    out = _in_browser(
        """
        // Страница правится, пока запрос в пути: ровно так ведёт себя
        // приложение площадки, обновляя метку по ответу канала. Подмена стоит
        // ВНУТРИ сети, а не поверх сборщика, - иначе она случилась бы до
        // снимка, и проверка проверяла бы не порядок, а саму сверку.
        inFlight = moveTheTag
        funora.watch()
        await window.fetch('/runner/', {
          method: 'POST',
          body: 'objects=' + encodeURIComponent(JSON.stringify([
            {type: 'orders_counters', id: '12345678', tag: '7f3a9b21', data: true}
          ]))
        })
        inFlight = null
        answer(await takeRecords())
        """
    )

    tag = out[0]["request"]["fields"]["objects"]["nested"][0]["tag"]
    assert tag["from_attribute"] == "data-orders", (
        f"метка сверялась с уже обновлённой страницей: {tag}. "
        "Снимок атрибутов обязан браться до ухода запроса"
    )


def test_the_probe_sends_one_poll_with_no_action() -> None:
    """Требует, чтобы опрос с пустой подпиской не нёс действия.

    Команда существует ради одного вопроса: принимает ли канал пустое поле
    objects. Отвечать на него догадкой нельзя - отправка сообщения упирается
    ровно сюда, а отменить отправленное покупателю сообщение невозможно.

    Опрос обязан быть именно опросом: поле request несёт false, то есть на
    площадке не меняется ничего. Уйди туда действие - команда стала бы
    отправкой, а называлась бы пробой.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        await funora.probe()
        const mine = sent.filter((one) => String(one.url) === '/runner/')
        answer(mine.map((one) => ({
          method: one.init.method,
          body: one.init.body,
          headers: Object.keys(one.init.headers).sort(),
        })))
        """
    )

    assert len(out) == 1, f"ушёл не один запрос к каналу, а {len(out)}"
    one = out[0]
    assert one["method"] == "POST"

    fields = dict(pair.split("=", 1) for pair in one["body"].split("&"))
    assert fields["objects"] == "%5B%5D", f"подписка не пуста: {fields['objects']}"
    assert fields["request"] == "false", (
        f"в поле request оказалось {fields['request']!r}. Проба обязана быть опросом: "
        "действие сделало бы её отправкой"
    )
    assert fields["csrf_token"], "защитный токен не подставлен"
    assert "X-Requested-With" in one["headers"], "заголовок канала не выставлен"


def test_the_probe_refuses_when_nothing_is_recording() -> None:
    """Требует отказаться от опроса, пока запись не идёт.

    Иначе запрос уйдёт, а наблюдения не будет: единственное, ради чего проба
    делается, - её запись.

    Returns:
        None
    """
    out = _in_browser(
        """
        let refused = ''
        try { await funora.probe() } catch (e) { refused = String(e.message) }
        answer({
          refused,
          sent: sent.filter((one) => String(one.url) === '/runner/').length,
        })
        """
    )

    assert out["sent"] == 0, "запрос ушёл впустую"
    assert "watch" in out["refused"], out["refused"]


def test_the_page_attributes_are_read_from_a_closed_list() -> None:
    """Требует брать имена атрибутов из ИСХОДНИКА, а не со страницы.

    Перечень, собранный со страницы, принёс бы в наблюдение имена атрибутов,
    которых никто не читал. Имя атрибута на странице продавца бывает и
    говорящим.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        await window.fetch('/runner/', {
          method: 'POST',
          body: 'objects=' + encodeURIComponent(JSON.stringify([
            {type: 'x', id: '1', tag: 'znachenie', data: true}
          ]))
        })
        answer(await takeRecords())
        """,
        html=(
            '<html><body data-app-data=\'{"csrf-token": "abcdefgh12345678"}\'>'
            '<div data-seller-note="znachenie"></div>'
            "</body></html>"
        ),
    )

    tag = out[0]["request"]["fields"]["objects"]["nested"][0]["tag"]
    origin = tag.get("from_attribute") if isinstance(tag, dict) else None
    assert origin is not True and origin != "data-seller-note", (
        f"метка сверилась с атрибутом вне закрытого перечня: {tag}"
    )
    assert "seller-note" not in json.dumps(out), "имя чужого атрибута попало в запись"


def test_a_broken_page_never_breaks_the_page_own_requests() -> None:
    """Требует, чтобы сборщик не ронял запросы САМОЙ страницы.

    Снимок меток берётся в подменённом window.fetch, ДО ухода запроса - то есть
    на пути чужого запроса, который делает площадка, а не мы. Урони сборщик
    исключение там, и он сломал бы работу площадки в браузере наблюдателя:
    переписка перестала бы обновляться, отправка - уходить.

    Наблюдатель обязан быть незаметен для наблюдаемого. Проверка подставляет
    документ, у которого querySelectorAll бросает, и требует, чтобы запрос
    прошёл, а запись состоялась - просто без сверки.

    Returns:
        None
    """
    out = _in_browser(
        """
        document.querySelectorAll = function () { throw new Error('DOM недоступен') }
        funora.watch()
        const response = await window.fetch('/runner/', {
          method: 'POST',
          body: 'objects=' + encodeURIComponent(JSON.stringify([
            {type: 'orders_counters', id: '12345678', tag: '7f3a9b21', data: true}
          ]))
        })
        answer({status: response.status, records: await takeRecords()})
        """
    )

    assert out["status"] == 200, "запрос страницы не прошёл из-за сборщика"
    assert len(out["records"]) == 1, "запись не состоялась"

    # Сверки нет - и это правильно: сверять было нечем. Но подпись на месте.
    tag = out["records"][0]["request"]["fields"]["objects"]["nested"][0]["tag"]
    assert tag == "T8:ad", f"без сверки метка обязана остаться обычной подписью, а стоит {tag}"


def test_the_action_fields_are_matched_against_the_page_too() -> None:
    """Требует сверять со страницей не только метку, но и поля действия.

    Метка была первой, и на ней приём себя оправдал: выяснилось, что метки
    разных видов подписки - разные значения. Тем же приёмом отвечается и
    следующий вопрос - откуда берётся то, что площадка кладёт в поля действия
    отправки. Иначе на него не ответить по той же причине: маскирование у снимка
    страницы и у сетевой записи независимое.

    ЧИСЛО СВЕРЯЕТСЯ ПО СВОЕЙ ЗАПИСИ. Поле last_message приходит числом, а в
    атрибуте страницы лежит строка тех же цифр. Правило «сверяем только строки»
    отвечало бы «сверять нечем» там, где сверить можно.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        await window.fetch('/runner/', {
          method: 'POST',
          body: 'request=' + encodeURIComponent(JSON.stringify({
            action: 'chat_message',
            data: {node: 'users-12345678-87654321', last_message: 2010613313, content: 'privet'}
          }))
        })
        answer(await takeRecords())
        """,
        html=(
            '<html><body data-app-data=\'{"csrf-token": "abcdefgh12345678"}\'>'
            '<div class="chat chat-float" data-name="users-12345678-87654321"></div>'
            '<div class="contact-item" data-node-msg="2010613313"></div>'
            "</body></html>"
        ),
    )

    data = out[0]["request"]["fields"]["request"]["nested"]["data"]

    assert data["node"]["from_attribute"] == "data-name", data["node"]
    assert data["last_message"]["from_attribute"] == "data-node-msg", (
        f"число не сверилось со строкой атрибута: {data['last_message']}"
    )

    # Содержимое сообщения не сверяется вовсе: это текст человека.
    assert data["content"] == "T6:a", f"содержимое попало под сверку: {data['content']}"

    written = json.dumps(out, ensure_ascii=False)
    assert "users-12345678-87654321" not in written, "значение узла ушло в запись"
    assert "2010613313" not in written, "значение позиции ушло в запись"


#: Страница ОТКРЫТОГО диалога: только с неё можно отправить.
WITH_DIALOGUE = (
    '<html><body data-app-data=\'{"csrf-token": "abcdefgh12345678"}\'>'
    '<div class="chat chat-float" data-name="users-12345678-87654321" '
    'data-id="283028758" data-tag="7f3a9b21" data-user="12345678"></div>'
    '<a class="contact-item" data-id="111111111" data-node-msg="1000000001"></a>'
    '<a class="contact-item active" data-id="283028758" data-node-msg="2010613313"></a>'
    "</body></html>"
)

#: Список диалогов без открытого собеседника.
WITHOUT_DIALOGUE = (
    '<html><body data-app-data=\'{"csrf-token": "abcdefgh12345678"}\'>'
    '<div class="chat chat-float chat-not-selected" data-user="12345678"></div>'
    "</body></html>"
)


def test_the_sending_probe_refuses_without_a_text() -> None:
    """Требует отказать от отправки, пока текст не назван явно.

    Команда единственная во всём сборщике, которая что-то МЕНЯЕТ на площадке.
    Значения текста по умолчанию нет нарочно: отправка не должна случаться от
    вызова без доводов - ни по опечатке, ни по автодополнению консоли.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        const refused = []
        for (const bad of [undefined, '', '   ', 42, null]) {
          try { await funora.probeSend(bad) } catch (e) { refused.push(String(e.message)) }
        }
        const mine = sent.filter((one) => String(one.url) === '/runner/')
        answer({refused: refused.length, sent: mine.length})
        """,
        html=WITH_DIALOGUE,
    )

    assert out["sent"] == 0, f"ушло {out['sent']} запросов при пустом тексте"
    assert out["refused"] == 5, out["refused"]


def test_the_sending_probe_refuses_when_nothing_is_recording() -> None:
    """Требует отказать, пока запись не идёт.

    Иначе сообщение уйдёт, а наблюдения не будет - то есть неотменяемое действие
    случится впустую.

    Returns:
        None
    """
    out = _in_browser(
        """
        let refused = ''
        try { await funora.probeSend('проба') } catch (e) { refused = String(e.message) }
        answer({refused, sent: sent.filter((one) => String(one.url) === '/runner/').length})
        """,
        html=WITH_DIALOGUE,
    )

    assert out["sent"] == 0, "сообщение ушло без записи"
    assert "watch" in out["refused"], out["refused"]


def test_the_sending_probe_refuses_on_a_page_without_an_open_dialogue() -> None:
    """Требует отказать на списке диалогов без открытого собеседника.

    Там у виджета нет ни имени диалога, ни его метки. Отправить не в диалог
    нельзя, и отказ обязан сказать, что делать.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        let refused = ''
        try { await funora.probeSend('проба') } catch (e) { refused = String(e.message) }
        answer({refused, sent: sent.filter((one) => String(one.url) === '/runner/').length})
        """,
        html=WITHOUT_DIALOGUE,
    )

    assert out["sent"] == 0
    assert "data-name" in out["refused"], out["refused"]
    assert "откройте диалог" in out["refused"], out["refused"]


def test_the_sending_probe_sends_one_message_with_an_empty_subscription() -> None:
    """Требует отправить РОВНО ОДНО сообщение и с пустой подпиской.

    Пустая подписка - ровно то, ради чего команда и заведена: наблюдено, что
    канал принимает её при опросе, а выполняет ли он при этом действие - другое
    утверждение, и оно не наблюдалось.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        await funora.probeSend('проба')
        const mine = sent.filter((one) => String(one.url) === '/runner/')
        answer(mine.map((one) => ({method: one.init.method, body: one.init.body})))
        """,
        html=WITH_DIALOGUE,
    )

    assert len(out) == 1, f"ушло не одно обращение, а {len(out)}"
    fields = dict(pair.split("=", 1) for pair in out[0]["body"].split("&"))

    assert fields["objects"] == "%5B%5D", f"подписка не пуста: {fields['objects']}"
    assert fields["csrf_token"] == "abcdefgh12345678"

    from urllib.parse import unquote_plus

    action = json.loads(unquote_plus(fields["request"]))
    assert action["action"] == "chat_message"
    assert action["data"]["node"] == "users-12345678-87654321", action["data"]["node"]
    assert action["data"]["content"] == "проба"

    # Позиция берётся у ОТКРЫТОЙ строки, а не у первой попавшейся.
    assert action["data"]["last_message"] == 2010613313, (
        f"позиция {action['data']['last_message']} взята не у открытого диалога"
    )


def test_the_open_row_is_found_by_identity_not_by_styling() -> None:
    """Требует искать открытую строку по идентификатору, а не по подсветке.

    Класс - чужое решение об оформлении. Проверка снимает подсветку и ставит её
    ЧУЖОЙ строке: позиция обязана остаться от нужного диалога.

    Returns:
        None
    """
    misleading = WITH_DIALOGUE.replace(
        '<a class="contact-item" data-id="111111111"',
        '<a class="contact-item active" data-id="111111111"',
    ).replace(
        '<a class="contact-item active" data-id="283028758"',
        '<a class="contact-item" data-id="283028758"',
    )
    assert misleading != WITH_DIALOGUE, "подсветка не переставилась"

    out = _in_browser(
        """
        funora.watch()
        await funora.probeSend('проба')
        const mine = sent.filter((one) => String(one.url) === '/runner/')
        answer(mine.map((one) => one.init.body))
        """,
        html=misleading,
    )

    from urllib.parse import unquote_plus

    fields = dict(pair.split("=", 1) for pair in out[0].split("&"))
    action = json.loads(unquote_plus(fields["request"]))
    assert action["data"]["last_message"] == 2010613313, (
        f"позиция {action['data']['last_message']} взята у подсвеченной чужой строки"
    )


def test_the_sending_probe_can_subscribe_to_exactly_one_node() -> None:
    """Требует собирать подписку ровно из одного узла - и только из наблюдённого.

    Наблюдено 30.08.2026 контрольной парой: ответ канала несёт изменения ТОЛЬКО
    подписанных объектов. При пустой подписке отправка проходит, а ответ
    приходит пустым - подтверждать нечем.

    Подписка из одного узла собирается со страницы целиком: идентификатор из
    data-name, метка из data-tag, данные те же, что у действия. Второго вида
    объектов здесь нет нарочно - метка закладок не наблюдалась, и собрать её
    было бы догадкой.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        await funora.probeSend('проба', {subscribe: true})
        const mine = sent.filter((one) => String(one.url) === '/runner/')
        answer(mine.map((one) => one.init.body))
        """,
        html=WITH_DIALOGUE,
    )

    from urllib.parse import unquote_plus

    fields = dict(pair.split("=", 1) for pair in out[0].split("&"))
    objects = json.loads(unquote_plus(fields["objects"]))

    assert len(objects) == 1, f"подписка не из одного объекта: {objects}"
    one = objects[0]
    assert one["type"] == "chat_node"
    assert one["id"] == "users-12345678-87654321"
    assert one["tag"] == "7f3a9b21", one["tag"]
    assert one["data"]["node"] == one["id"]
    assert one["data"]["content"] == "проба"

    # Действие при этом то же самое, что и без довеска.
    action = json.loads(unquote_plus(fields["request"]))
    assert action["action"] == "chat_message"
    assert action["data"] == one["data"]


def test_without_the_option_the_subscription_stays_empty() -> None:
    """Требует, чтобы без довеска подписка оставалась пустой.

    Довесок обязан быть ЯВНЫМ: команда с подпиской и команда без неё отвечают на
    разные вопросы, и перепутать их значит потерять наблюдение.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        await funora.probeSend('проба')
        await funora.probeSend('проба', {})
        await funora.probeSend('проба', {subscribe: false})
        const mine = sent.filter((one) => String(one.url) === '/runner/')
        answer(mine.map((one) => one.init.body))
        """,
        html=WITH_DIALOGUE,
    )

    assert len(out) == 3
    for body in out:
        fields = dict(pair.split("=", 1) for pair in body.split("&"))
        assert fields["objects"] == "%5B%5D", f"подписка не пуста: {fields['objects']}"


def test_the_subscription_refuses_without_the_dialogue_tag() -> None:
    """Требует отказать, если метки диалога на странице нет.

    Собрать подписку без метки нельзя, а подставить её нечем: метка наблюдается,
    а не выводится.

    Returns:
        None
    """
    without_tag = WITH_DIALOGUE.replace(' data-tag="7f3a9b21"', "")
    assert without_tag != WITH_DIALOGUE, "метка не снялась"

    out = _in_browser(
        """
        funora.watch()
        let refused = ''
        try {
          await funora.probeSend('проба', {subscribe: true})
        } catch (e) { refused = String(e.message) }
        const mine = sent.filter((one) => String(one.url) === '/runner/')
        answer({refused, sent: mine.length})
        """,
        html=without_tag,
    )

    assert out["sent"] == 0, "сообщение ушло без метки подписки"
    assert "data-tag" in out["refused"], out["refused"]


def test_the_sending_probe_reports_which_subscription_it_used() -> None:
    """Требует, чтобы ответ команды называл вид подписки.

    Первая редакция говорила «отправка с пустой подпиской сделана» ВСЕГДА, даже
    с довеском. Наблюдатель, читающий консоль, записал бы наблюдение не тем,
    чем оно есть, - а различаются эти два наблюдения ровно подпиской.

    Returns:
        None
    """
    out = _in_browser(
        """
        funora.watch()
        const empty = await funora.probeSend('раз')
        const one = await funora.probeSend('два', {subscribe: true})
        answer({empty, one})
        """,
        html=WITH_DIALOGUE,
    )

    assert "пуст" in out["empty"], out["empty"]
    assert "пуст" not in out["one"], out["one"]
    assert "узел" in out["one"], out["one"]


def test_the_query_name_rule_matches_between_the_two_languages() -> None:
    """ЗАКРЫВАЕТ ЧЕТВЁРТОЕ расхождение двух описаний одного правила.

    Браузерный сборщик записывал имена параметров с самого своего появления -
    searchParams.keys(), - а снимок страницы их маскировал. То есть правило
    было одно, описаний два, и они молча разошлись: записи запросов имена
    несли, снимки нет.

    Цена: идентификатор чужого предложения, лежащий ТОЛЬКО в строке запроса,
    было не наблюдать со страницы вовсе.

    Returns:
        None
    """
    from funora._skeleton import DEFAULT_OWN_HOST, mask_path

    theirs = _in_node("maskUrl('https://funpay.com/lots/offer?id=75289502&sort=price').query")
    ours = mask_path("https://funpay.com/lots/offer?id=75289502&sort=price", DEFAULT_OWN_HOST, {})

    for name in theirs:
        assert f"{name}=" in ours, (
            f"браузер записывает имя параметра {name!r}, а снимок его прячет. "
            "Два описания одного правила разошлись - снимок страницы и запись "
            "запроса скажут о площадке разное"
        )
    assert "75289502" not in ours, "значение параметра попало в снимок"
