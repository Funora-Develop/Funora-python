"""Проверки отправки изображения в переписку.

ЧТО ЗДЕСЬ ЧЬЁ. Обе половины операции наблюдены НАМИ - и загрузка файла, и
отправка номера через канал. Чужого знания нет, и потому согласия эта операция,
в отличие от отметки прочтения и переключения видимости лота, не спрашивает.

Реестр три недели утверждал обратное: «адрес загрузки - /file/addImage, а сам
запрос загрузки не наблюдался ни разу». Неверно и то, и другое. Адрес -
/file/addChatImage; запрос наблюдён телом формы с единственным полем file и
ответом с ключом fileId.

Мешала не запись, а работа в реализации: транспорт не умел составного тела.

Отсюда главные проверки набора:

  без номера файла второй запрос НЕ уходит - выдуманный номер отправил бы
  покупателю ЧУЖОЙ файл;
  предел размера читается со страницы, а не выдумывается нами;
  содержимое сообщения при картинке пусто - наблюдено именно так.

Наблюдено 31.08.2026: network.send-image (загрузка и отправка), lot-edit
(объявленный предел размера в data-app-data).
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import RUNNER_PATH, UPLOAD_CHAT_PATH, Engine, Fetch, Submit, Upload
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability
from funora.errors import ProtocolChangedError, UsageError, ValidationError

NODE: Final[str] = "247450736"
PNG: Final[bytes] = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

_APP_DATA: Final[str] = json.dumps(
    {
        "csrf-token": "0123456789abcdef",
        "userId": 8524891,
        "uploadOptions": {"fileSizeMax": "5242880", "fileSizeMaxStr": "5 МБ"},
    },
    ensure_ascii=False,
)

THREAD_HTML: Final[str] = (
    f"<body data-app-data='{_APP_DATA}'>"
    '<div class="chat chat-float" data-id="247450736" data-tag="a1b2c3d4" '
    'data-name="users-8524891-9310582" data-node-msg="1749300" '
    'data-bookmarks-tag="e5f6a7b8" data-user-id="9310582"></div>'
    '<div class="hidden" data-orders="11223344" data-user="8524891"></div>'
    '<a class="contact-item active" data-id="247450736" data-node-msg="1749300" '
    'data-user-msg="1749299"><div class="media-user-name">покупатель</div></a>'
    '<button class="navbar-toggle-logged"></button>'
    '<a class="user-link-dropdown" href="/users/8524891/"></a>'
    '<a href="/users/8524891/" class="menu-item-1"></a>'
    "</body>"
)


def _observation(html: str, url: str) -> Observation:
    """Собирает наблюдение.

    Аргументы:
        html (str): Тело ответа.
        url (str): Конечный адрес.

    Возвращает:
        Observation: Наблюдение.
    """
    raw = html.encode("utf-8")
    return Observation(
        status=200,
        final_url=url,
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(raw),
        declared_length=len(raw),
    )


class _Scripted:
    """Отвечает страницей диалога, ответом загрузки и ответом канала."""

    def __init__(self, *, html: str = THREAD_HTML, upload_body: str | None = None) -> None:
        """Готовит сценарий.

        Аргументы:
            html (str): Разметка страницы диалога.
            upload_body (str | None): Тело ответа на загрузку.

        Возвращает:
            None
        """
        self.html = html
        self.upload_body = upload_body if upload_body is not None else '{"fileId": 918273}'
        self.uploads: list[Upload] = []
        self.submits: list[Submit] = []
        self.fetches: list[Fetch] = []

    def run(self, core: Any) -> Any:
        """Прокручивает ядро операции.

        Аргументы:
            core (Any): Сопрограмма.

        Возвращает:
            Any: Итог.
        """
        reply: Any = None
        while True:
            try:
                request = core.send(reply)
            except StopIteration as stop:
                return stop.value

            if isinstance(request, Upload):
                self.uploads.append(request)
                reply = _observation(self.upload_body, "https://funpay.com/file/addChatImage")
            elif isinstance(request, Submit):
                self.submits.append(request)
                body = json.dumps({"objects": [], "response": None}, ensure_ascii=False)
                reply = _observation(body, "https://funpay.com/runner/")
            elif isinstance(request, Fetch):
                self.fetches.append(request)
                reply = _observation(self.html, f"https://funpay.com{request.path}")
            else:
                reply = None


def _engine() -> Engine:
    """Собирает движок без сети.

    Возвращает:
        Engine: Движок.
    """
    return Engine(TransportSettings(), Budget())


def test_no_consent_is_asked_because_nothing_here_is_borrowed() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: согласия не спрашивают, и это не оплошность.

    Обе половины операции наблюдены нами. Спросить согласие там, где чужого
    знания нет, значило бы обесценить сам механизм: вызывающий, привыкший
    включать всё подряд, перестанет читать, ЧТО именно ему предлагают включить.

    Возвращает:
        None
    """
    from funora.operations import OPERATIONS

    contract = OPERATIONS["chats.send_image"]
    assert contract.request_provenance == "", (
        "у отправки картинки объявлено чужое происхождение, а обе её половины наблюдены нами"
    )

    script = _Scripted()
    # Согласия не давали вовсе - и операция всё равно доходит до сети.
    script.run(_engine().send_image(NODE, PNG, filename="снимок.png"))
    assert len(script.uploads) == 1
    assert len(script.submits) == 1


def test_the_file_goes_to_the_observed_address_in_the_observed_field() -> None:
    """Требует наблюдённого адреса и наблюдённого имени поля.

    Реестр три недели называл другой адрес - /file/addImage. Проверка стоит
    здесь затем, чтобы неверное имя нельзя было вернуть молча.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().send_image(NODE, PNG, filename="снимок.png"))

    sent = script.uploads[0]
    assert sent.path == UPLOAD_CHAT_PATH
    assert sent.path == "/file/addChatImage", "адрес разошёлся с наблюдённым"
    assert sent.field == "file", "имя поля разошлось с наблюдённым"
    assert sent.content == PNG
    assert sent.filename == "снимок.png"
    assert sent.headers.get("x-requested-with") == "XMLHttpRequest"


@pytest.mark.parametrize(
    "body",
    ['{"ok": true}', '{"fileId": "918273"}', '{"fileId": null}', '{"fileId": true}', "не json"],
)
def test_without_a_file_number_the_second_request_never_leaves(body: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: нет номера - нет и второго запроса.

    Выдуманный номер отправил бы покупателю ЧУЖОЙ файл - чей угодно, лежащий у
    площадки под этим номером. Это хуже неотправки на порядок.

    Строка вместо числа исключается отдельно, и логическое тоже: истина в Python
    - это единица, и fileId=true прочиталось бы как «файл номер один».

    Аргументы:
        body (str): Непригодное тело ответа на загрузку.

    Возвращает:
        None
    """
    script = _Scripted(upload_body=body)
    core = _engine().send_image(NODE, PNG, filename="снимок.png")

    with pytest.raises(ProtocolChangedError):
        script.run(core)

    assert script.uploads, "загрузка не ушла - проверка стала пустой"
    assert script.submits == [], "номера файла нет, а второй запрос всё равно ушёл"


def test_the_message_carries_the_number_and_no_text() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: при картинке содержимое пусто.

    Наблюдено именно так. Положить сюда имя файла значило бы отправить
    покупателю строку, которой он не ждёт, - и стереть её потом нельзя.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().send_image(NODE, PNG, filename="снимок.png"))

    sent = script.submits[0]
    assert sent.path == RUNNER_PATH
    action = json.loads(sent.fields["request"])
    assert action["action"] == "chat_message"
    assert action["data"]["image_id"] == 918273
    assert action["data"]["content"] == "", "при картинке ушёл текст"
    assert "снимок" not in json.dumps(action, ensure_ascii=False), "имя файла ушло сообщением"


def test_the_size_limit_is_read_from_the_page() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: предел размера читается, а не выдумывается.

    Свой предел отверг бы то, что площадка приняла бы, - и отверг бы молча, на
    нашей стороне, где вызывающему нечего возразить.

    Возвращает:
        None
    """
    script = _Scripted()
    core = _engine().send_image(NODE, b"x" * 5_242_881, filename="снимок.png")

    with pytest.raises(UsageError) as raised:
        script.run(core)

    assert script.uploads == [], "файл больше предела, а загрузка всё равно ушла"
    assert "5242880" in str(raised.value), "отказ не называет прочитанного предела"


def test_a_page_without_a_declared_limit_does_not_invent_one() -> None:
    """Требует пропускать файл, когда предел на странице не объявлен.

    Отсутствие объявления - не повод завести свой: это была бы догадка, и
    отвергала бы она то, что площадка приняла бы.

    Возвращает:
        None
    """
    # Разметка собирается заново, а не правится строкой: порядок ключей в
    # объекте настроек - не наше дело, и правка подстрокой сломалась бы от него
    # молча, оставив проверку зелёной и пустой.
    bare = json.dumps({"csrf-token": "0123456789abcdef", "userId": 8524891}, ensure_ascii=False)
    without = THREAD_HTML.replace(_APP_DATA, bare)
    assert "uploadOptions" not in without, "предел из разметки убрать не удалось"

    script = _Scripted(html=without)
    script.run(_engine().send_image(NODE, b"x" * 9_000_000, filename="снимок.png"))

    assert len(script.uploads) == 1, "предела не объявлено, а файл всё равно отвергнут"


@pytest.mark.parametrize(
    ("node", "name", "content"),
    [
        ("", "a.png", PNG),
        ("24/../", "a.png", PNG),
        (NODE, "", PNG),
        (NODE, "  ", PNG),
        (NODE, "../../etc/passwd", PNG),
        (NODE, "папка\\файл.png", PNG),
        (NODE, "a.png", b""),
    ],
)
def test_bad_input_is_refused_before_the_network(node: str, name: str, content: bytes) -> None:
    """Требует отказа ДО сети на непригодном вводе.

    Имя с разделителем пути отвергается не ради нашей безопасности - файл уходит
    в ЧУЖУЮ файловую систему. Что площадка сделает с таким именем, никто не
    наблюдал, а узнавать это на живом аккаунте незачем.

    Аргументы:
        node (str): Идентификатор диалога.
        name (str): Имя файла.
        content (bytes): Содержимое.

    Возвращает:
        None
    """
    script = _Scripted()
    core = _engine().send_image(node, content, filename=name)

    with pytest.raises(ValidationError):
        script.run(core)

    assert script.fetches == [], "непригодный ввод, а страница всё равно прочитана"
    assert script.uploads == []


def test_the_subscription_makes_the_answer_confirmable() -> None:
    """Требует подписки на диалог во втором запросе.

    Канал подтверждает только подписанное. Без подписки исход отправки картинки
    не устанавливается ничем.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().send_image(NODE, PNG, filename="снимок.png"))

    objects = json.loads(script.submits[0].fields["objects"])
    assert any(one["type"] == "chat_node" for one in objects)
    assert objects[0]["data"]["image_id"] == 918273


def test_an_unusable_page_stops_before_the_upload() -> None:
    """Требует не загружать файл на непригодной странице.

    Загруженный впустую файл остаётся у площадки навсегда: удалять его нам
    нечем.

    Возвращает:
        None
    """
    logged_in_but_useless = (
        '<body data-app-data=\'{"csrf-token": "0123456789abcdef"}\'>'
        '<button class="navbar-toggle-logged"></button>'
        '<a class="user-link-dropdown" href="/users/8524891/"></a>'
        "<div>ни виджета переписки, ни списка диалогов</div>"
        "</body>"
    )
    script = _Scripted(html=logged_in_but_useless)
    core = _engine().send_image(NODE, PNG, filename="снимок.png")

    with pytest.raises(ProtocolChangedError) as raised:
        script.run(core)

    assert "не годится для отправки" in str(raised.value)
    assert script.uploads == [], "страница непригодна, а файл всё равно загружен"


def test_the_capability_is_named_in_the_implemented_set() -> None:
    """Требует, чтобы возможность числилась выполняемой.

    Возвращает:
        None
    """
    from funora._engine import IMPLEMENTED

    assert Capability.CHATS_SEND_IMAGE in IMPLEMENTED


@pytest.mark.parametrize(
    ("declared", "reason"),
    [
        (True, "логическое"),
        (False, "логическое"),
        (0, "ноль"),
        (-1, "отрицательное"),
        ("0", "ноль строкой"),
        ("много", "не число"),
        ("", "пусто"),
        (5.5, "дробное"),
    ],
)
def test_an_unusable_declared_limit_is_not_taken_as_observed(declared: Any, reason: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: непригодный предел не выдаётся за наблюдённый.

    Логическое исключается отдельно: истина в Python - это единица, и предел
    True прочитался бы как «один байт», отвергнув любой файл. Ноль и
    отрицательное - тоже: предел, запрещающий вообще всё, скорее означает, что
    мы прочитали не то поле, чем что площадка запретила выгрузку.

    Прочитанный не тот предел хуже непрочитанного: непрочитанный честно молчит,
    а неверный отвергает молча и на нашей стороне.

    Аргументы:
        declared (Any): Непригодное объявление предела.
        reason (str): Чем оно непригодно.

    Возвращает:
        None
    """
    from funora._whoami import parse_app_data

    body = json.dumps(
        {
            "csrf-token": "0123456789abcdef",
            "userId": 8524891,
            "uploadOptions": {"fileSizeMax": declared},
        },
        ensure_ascii=False,
    )
    settings = parse_app_data(f"<body data-app-data='{body}'></body>")

    assert settings.upload_size_max.or_none() is None, (
        f"{reason}: предел {declared!r} прочитан как наблюдённый"
    )


def test_a_declared_limit_is_read_as_a_number() -> None:
    """Обратная половина: пригодный предел вправду читается.

    Без неё предыдущая проверка проходила бы и на разборе, который не читает
    ничего никогда.

    Возвращает:
        None
    """
    from funora._whoami import parse_app_data

    for declared, expected in (("5242880", 5_242_880), (1048576, 1_048_576)):
        body = json.dumps(
            {"csrf-token": "0123456789abcdef", "uploadOptions": {"fileSizeMax": declared}},
            ensure_ascii=False,
        )
        settings = parse_app_data(f"<body data-app-data='{body}'></body>")
        assert settings.upload_size_max.or_none() == expected, f"предел {declared!r} не прочитан"
