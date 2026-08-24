"""Приём наблюдений из браузера.

Инструмент поднимает одноразовый сервер на 127.0.0.1 и печатает строчку,
которую надо вставить в консоль браузера на уже открытой странице FunPay.
Дальше браузер сам отдаёт сюда наблюдения.

ЧЕМ ОН ОТЛИЧАЕТСЯ ОТ funora.observe. Тот ходит на площадку САМ, ключом сессии
из провайдера секретов, и умеет то, что делается одним чтением: скелет
страницы, сравнение двух чтений, соотношения по списку диалогов. Этот не ходит
никуда и ключа не просит вовсе - он смотрит из уже открытой вкладки. Отсюда
разделение: чтение по адресу берёт funora.observe, а всё, что требует действия
руками или наблюдения ЗАПРОСОВ, - этот.

ЗАЧЕМ ТАК, А НЕ ФАЙЛОМ. Сохранять HTML страницы под авторизацией нельзя: там
имена покупателей, тексты переписки, номера заказов. Через локальный сокет
сырая страница попадает прямо в память этого процесса и превращается в скелет
тем же кодом, что читает фикстуры; на диск ложится только скелет. Ни буфера
обмена, ни временного файла, ни истории оболочки.

ЧЕГО ИНСТРУМЕНТ НЕ ДЕЛАЕТ И НЕ БУДЕТ. Он не спрашивает логина и пароля, не
читает и не сохраняет куки, не входит на площадку и не отправляет ничего наружу.
Единственный слушатель - 127.0.0.1, единственный отправитель - вкладка браузера,
которую открыли вы.

Запуск::

    python tools/capture.py

Дальше инструмент печатает, что делать. Собранное ложится в observations/ -
каталог не отслеживается git и предназначен для просмотра ГЛАЗАМИ перед тем,
как что-то из него переносить в фикстуры.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from funora._skeleton import SKELETON_FORMAT, SkeletonError, skeletonize  # noqa: E402

#: Куда складывается собранное. Каталог не отслеживается git.
OUTPUT: Final[Path] = Path(__file__).resolve().parent.parent / "observations"

#: Где лежит браузерная часть.
SNIPPET: Final[Path] = Path(__file__).resolve().parent / "capture.js"

#: Порт по умолчанию. Выбран высоким и невзрачным, менять можно ключом.
DEFAULT_PORT: Final[int] = 8731

#: Порт, на котором сервер вправду поднялся. Нужен браузерной части: адрес
#: подставляется в неё при выдаче.
PORT: int = DEFAULT_PORT

#: Что и в каком виде принимается.
KINDS: Final[frozenset[str]] = frozenset({"page", "currency", "network"})

#: Имя снимка: только то, из чего складываются имена фикстур.
SAFE_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9._-]{1,80}$")


def _write_page(name: str, html: str, where: dict[str, Any] | None = None) -> str:
    """Превращает страницу в скелет и сохраняет только его.

    Кладётся ровно то же, что кладёт funora.observe: скелет в {имя}.skeleton.txt
    без всякой шапки и описание происхождения рядом. Иначе снимок пришлось бы
    руками переименовывать и переформатировать перед тем, как класть в фикстуры,
    а ручная правка снимка - именно то, чего формат и избегает.

    Args:
        name (str): Имя снимка.
        html (str): Сырой HTML страницы. Никуда, кроме памяти, не попадает.
        where (dict[str, Any] | None): Что браузер сообщил о странице: путь,
            локаль, заголовок ответа. Может отсутствовать.

    Returns:
        str: Что сказать в браузер.

    Raises:
        SkeletonError: Если скелет не прошёл самопроверку. Тогда не
            сохраняется ничего: снимок, о котором нельзя сказать, что в нём нет
            персональных данных, хуже отсутствия снимка.
    """
    skeleton = skeletonize(html)
    said = where or {}

    target = OUTPUT / f"{name}.skeleton.txt"
    already = OUTPUT / f"{name}.provenance.json"

    # Снимок под тем же именем, но с ДРУГОГО пути, - почти наверняка недосмотр.
    # Так уже потерялось восемь снимков подряд: все ушли под именем первого и
    # затёрли друг друга, а в консоли каждый раз печаталось «сохранён».
    if already.is_file():
        previous = json.loads(already.read_text(encoding="utf-8")).get("path", "")
        if previous and previous != said.get("path", ""):  # noqa: SIM102
            return (
                f"НЕ СОХРАНЕНО: под именем «{name}» уже лежит снимок пути "
                f"{previous}, а этот снят с {said.get('path', 'неизвестно откуда')}. "
                "Дайте новое имя - иначе прежний снимок пропадёт молча"
            )
    target.write_text(skeleton + "\n", encoding="utf-8", newline="\n")

    provenance = {
        # Момент ставит приёмник, а не браузер: у снимка должен быть один
        # источник времени, и часы вкладки к нему отношения не имеют.
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "captured_by": "tools/capture.py",
        "captured_format": SKELETON_FORMAT,
        "converted": False,
        "final_url": said.get("final_url", ""),
        "format": SKELETON_FORMAT,
        "http_status": said.get("http_status", 0),
        "locale": said.get("lang", ""),
        "redirects": said.get("redirects", 0),
        "note": (
            "Структурный скелет, снятый из открытой вкладки браузера. Текст "
            "заменён подписями, сегменты путей с идентификаторами обезличены. "
            "Сырой HTML не сохраняется и на диск не попадает."
        ),
        "path": said.get("path", ""),
        "title_signature": said.get("title", ""),
    }
    (OUTPUT / f"{name}.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    return (
        f"скелет сохранён: {target.name}, {len(skeleton.splitlines())} строк, "
        f"формат {SKELETON_FORMAT}"
    )


def _record_count(payload: Any) -> int:
    """Считает записи в наблюдении любого из двух видов.

    Сетевое наблюдение обзавелось шапкой: отпечаток сборки и время. Прежде оно
    было голым списком, и такие файлы в observations/ ещё лежат - считать надо
    оба вида.

    Args:
        payload (Any): Содержимое наблюдения.

    Returns:
        int: Число записей.
    """
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return len(payload["records"])
    return len(payload) if isinstance(payload, list) else 1


def _write_json(kind: str, name: str, payload: Any) -> str:
    """Сохраняет наблюдение, пришедшее уже обезличенным.

    Повторный сбор того же самого проходит молча - он ничего не теряет. А вот
    сбор ДРУГОГО содержимого под тем же именем отвергается: так уже пропали три
    сбора валюты подряд. Все три ушли под именем, выведенным из пути, а пути у
    них был один - менялось только положение переключателя, и каждый следующий
    стирал предыдущий, печатая «сохранено».

    Args:
        kind (str): Вид наблюдения.
        name (str): Имя наблюдения.
        payload (Any): Содержимое, собранное браузерной частью.

    Returns:
        str: Что сказать в браузер.
    """
    target = OUTPUT / f"{kind}.{name}.json"
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if target.is_file() and target.read_text(encoding="utf-8") != body:
        return (
            f"НЕ СОХРАНЕНО: под именем «{target.name}» уже лежит ДРУГОЕ "
            "наблюдение. Передайте своё имя - например "
            f'funora.{kind}("что-отличает"), - иначе прежнее пропадёт молча'
        )

    target.write_text(body, encoding="utf-8", newline="\n")
    return f"наблюдение сохранено: {target.name}, записей {_record_count(payload)}"


def _looks_like_a_secret(payload: Any) -> str | None:
    """Ищет в наблюдении то, чего там быть не должно.

    Обезличивает браузерная часть, но полагаться на одну сторону нельзя:
    инструмент пишет в репозиторий разработчика, и цена пропуска несимметрична.
    Проверка грубая нарочно - она отвергает подозрительное, а не доказывает
    безопасное.

    Args:
        payload (Any): Содержимое наблюдения.

    Returns:
        str | None: Что именно смутило, либо None.
    """
    text = json.dumps(payload, ensure_ascii=False)
    lowered = text.lower()
    for word in ("cookie", "authorization", "phpsessid", "golden_key", "password"):
        if word in lowered:
            return f"в наблюдении встретилось слово «{word}»"

    strange = _find_strange_key(payload, "")
    if strange is not None:
        where, shape = strange
        return (
            f"в наблюдении есть ключ, не похожий на имя поля: {where}, {shape}. "
            "Ключи записываются ДОСЛОВНО, и ключ, пришедший из данных, утечёт "
            "целиком - так уже утекли суммы операций 24.08.2026, когда ответ в "
            "виде разметки разобрался как форма"
        )

    found = _find_cyrillic(payload, "")
    if found is not None:
        where, shape = found
        return (
            f"в наблюдении есть кириллическое слово: подписи её не содержат. "
            f"Нашлось по пути {where}, {shape}. Само значение не печатается - "
            "для того проверка и стоит"
        )
    return None


#: Как выглядит имя поля - ключ, который можно записать дословно.
_FIELD_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\[\]-]{0,64}$")

#: Ключи, которые кладёт сам сборщик и которые именем поля не являются.
#:
#: Перечень закрытый: всё, чего в нём нет и что под образец имени не подходит,
#: отвергается. Проще расширить перечень, чем однажды не заметить утечку.
_OWN_KEYS: Final[frozenset[str]] = frozenset(
    {"...", "nested", "hint", "signature", "collector_build", "captured_at", "records"}
)


def _find_strange_key(value: Any, where: str) -> tuple[str, str] | None:
    """Ищет ключ, не похожий на имя поля.

    Ключи записываются ДОСЛОВНО - на том основании, что имя поля говорит о
    протоколе, а не о человеке. Основание верно ровно до тех пор, пока ключи
    вправду являются именами полей.

    24.08.2026 ответ на догрузку строк - разметка - разобрался как форма, ключами
    стали куски HTML, и вместе с ними записались настоящие суммы операций.
    Браузерная часть починена; эта проверка стоит независимо от неё, потому что
    полагаться на одну сторону нельзя.

    Args:
        value (Any): Значение любого вида.
        where (str): Путь до значения, накопленный обходом.

    Returns:
        tuple[str, str] | None: Путь и мерка ключа, либо None.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            text = str(key)
            known = text in _OWN_KEYS or text.startswith("...") or text.startswith("T")
            if not known and not _FIELD_NAME.match(text):
                return f"{where}.<ключ>" if where else "<ключ>", _shape_of(text)
            found = _find_strange_key(item, f"{where}.{text}" if where else text)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for at, item in enumerate(value):
            found = _find_strange_key(item, f"{where}[{at}]")
            if found is not None:
                return found
    return None


def _shape_of(value: str) -> str:
    """Описывает строку, не раскрывая её.

    Args:
        value (str): Строка.

    Returns:
        str: Длина и набор различных знаков пунктуации.
    """
    marks = "".join(sorted({one for one in value if not one.isalnum()}))
    return f"длина {len(value)}, пунктуация {marks!r}" if marks else f"длина {len(value)}"


def _find_cyrillic(value: Any, where: str) -> tuple[str, str] | None:
    """Ищет кириллическое слово и называет путь до него.

    Прежде проверка искала по всей записи разом и говорила «нашлось». Место
    оставалось неизвестным, и починить наблюдение было нельзя - только снять его
    заново наугад. Сегодня это стоило трёх отправок настоящих сообщений.

    Путь называть безопасно: он состоит из имён полей, а имена полей и так лежат
    в записи. Мерку называть безопасно: длина и знаки препинания не дают
    восстановить слово.

    Args:
        value (Any): Значение любого вида.
        where (str): Путь до значения, накопленный обходом.

    Returns:
        tuple[str, str] | None: Путь и мерка, либо None.
    """
    if isinstance(value, str):
        if re.search(r"[А-Яа-яЁё]{4,}", value):
            return where or "<корень>", _shape_of(value)
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            # Кириллица бывает и в ИМЕНИ поля, а не только в значении. Имя
            # структурно и записывается дословно по устройству, поэтому такой
            # случай надо называть отдельно: снимать его нечем.
            if isinstance(key, str) and re.search(r"[А-Яа-яЁё]{4,}", key):
                return f"{where}.<имя поля>" if where else "<имя поля>", _shape_of(key)
            found = _find_cyrillic(item, f"{where}.{key}" if where else str(key))
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for at, item in enumerate(value):
            found = _find_cyrillic(item, f"{where}[{at}]")
            if found is not None:
                return found
    return None


class Handler(BaseHTTPRequestHandler):
    """Принимает наблюдения от вкладки браузера."""

    #: Чтобы не засорять вывод строкой на каждый запрос.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102, N802
        return

    def _allow(self) -> None:
        """Разрешает обращение со страницы площадки.

        Returns:
            None
        """
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Отвечает на предварительный запрос браузера.

        Returns:
            None
        """
        self.send_response(204)
        self._allow()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        """Отдаёт браузерную часть сборщика.

        Returns:
            None
        """
        if self.path != "/snippet.js":
            self.send_response(404)
            self._allow()
            self.end_headers()
            return

        raw = SNIPPET.read_bytes()
        # Отпечаток редакции: вкладка держит ту сборку, которую загрузила, и по
        # виду это не отличить. Один сбор уже ушёл со старой редакцией, и понять
        # это удалось только по данным.
        build = sha256(raw).hexdigest()[:8]
        source = (
            raw.decode("utf-8")
            .replace("__FUNORA_ENDPOINT__", f"http://127.0.0.1:{PORT}/")
            .replace("__FUNORA_BUILD__", build)
        )
        body = source.encode("utf-8")
        self.send_response(200)
        self._allow()
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        """Принимает одно наблюдение.

        Returns:
            None
        """
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")

        try:
            envelope = json.loads(raw)
            kind = envelope["kind"]
            name = envelope["name"]
            payload = envelope["payload"]
        except (ValueError, KeyError) as error:
            self._answer(400, f"конверт не разбирается: {error}")
            return

        if kind not in KINDS:
            self._answer(400, f"вид «{kind}» неизвестен")
            return
        if not SAFE_NAME.match(str(name)):
            self._answer(400, f"имя «{name}» не годится: только буквы, цифры, точка, дефис")
            return

        try:
            if kind == "page":
                # Страница приходит парой: сама разметка и то, что браузер о ней
                # знает. Разметка в описание происхождения не попадает.
                if isinstance(payload, dict):
                    answer = _write_page(name, payload["html"], payload.get("where"))
                else:
                    answer = _write_page(name, payload)
            else:
                complaint = _looks_like_a_secret(payload)
                if complaint is not None:
                    self._answer(400, f"НЕ СОХРАНЕНО: {complaint}")
                    return
                answer = _write_json(kind, name, payload)
        except SkeletonError as error:
            self._answer(400, f"НЕ СОХРАНЕНО, скелет не прошёл самопроверку: {error}")
            return

        print(f"  + {answer}")
        self._answer(200, answer)

    def _answer(self, code: int, text: str) -> None:
        """Отвечает браузеру строкой.

        Args:
            code (int): Код ответа.
            text (str): Что показать в консоли браузера.

        Returns:
            None
        """
        body = text.encode("utf-8")
        self.send_response(code)
        self._allow()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    """Поднимает сервер и печатает, что делать.

    Returns:
        int: Код возврата.
    """
    global PORT  # noqa: PLW0603
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])
    port = PORT

    OUTPUT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    print()
    print("Сборщик наблюдений Funora")
    print(f"  слушает: http://127.0.0.1:{port}/  (только на этой машине)")
    print(f"  кладёт:  {OUTPUT}")
    print()
    print("1. Откройте FunPay в браузере и войдите как обычно. Пароль сюда не нужен")
    print("   и спрошен не будет.")
    print("2. Откройте консоль разработчика: F12, вкладка Console.")
    print("3. Вставьте одну строку:")
    print()
    print(f"   fetch('http://127.0.0.1:{port}/snippet.js').then(r=>r.text()).then(eval)")
    print()
    print("4. Дальше по надобности:")
    print('   funora.page("order.logged.ru")   - отдать структуру страницы')
    print('   funora.currency("метка")         - собрать символы валют')
    print('   funora.watch()  ... funora.stop("send-message")  - записать форму запросов')
    print()
    print("ВАЖНО: строку из пункта 3 надо вставлять ЗАНОВО на каждой странице и")
    print("после каждой правки сборщика. Вкладка держит ту редакцию, которую")
    print("загрузила, и по виду это не отличить - смотрите отпечаток сборки в")
    print("приветствии.")
    print()
    print("Останов: Ctrl+C. Всё собранное лежит в observations/ - посмотрите глазами,")
    print("прежде чем что-то оттуда переносить.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("остановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
