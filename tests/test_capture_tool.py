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
        "['chat_message','chat_node','orders_counters'].map((one) => constantOf('action', one))"
    )
    assert allowed == ["chat_message", "chat_node", "orders_counters"], allowed

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
    assert "<имя поля>" in said, said
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
