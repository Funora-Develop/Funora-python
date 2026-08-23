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
        "'{\"nick\":\"Иван\",\"count\":3,\"ratio\":1.5}'",
        "'{\"deep\":{\"a\":{\"b\":{\"c\":{\"d\":{\"e\":{\"f\":{\"g\":1}}}}}}}}'",
        "'{\"list\":[\"Иван\",\"Пётр\",\"Сидор\"]}'",
        "'{\"empty\":[],\"nothing\":null,\"flag\":true}'",
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
    body = "'{\"nick\":\"ИванПетров\",\"order\":\"8471223\",\"token\":\"abc123secret\"}'"
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
