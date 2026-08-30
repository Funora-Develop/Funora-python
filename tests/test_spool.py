"""Проверки очереди исходящих между процессами.

ГЛАВНОЕ ЗДЕСЬ НЕ ОЧЕРЕДЬ, А ЗАСТРЯВШЕЕ. Задание, взятое умершим процессом, -
это сообщение с неизвестной судьбой: могло уйти, могло не уйти. Отправить его
снова значит послать покупателю второе сообщение; выбросить молча - потерять
первое. Проверки требуют, чтобы не делалось ни того, ни другого.

Процесс здесь настоящий, а не изображённый: второй интерпретатор кладёт задание
в тот же каталог, и первый его забирает. Изобразить это одним процессом можно,
но тогда проверка не заметила бы ровно ту беду, ради которой очередь заведена.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from funora.bot import SendCommand, Spool
from funora.errors import UsageError, ValidationError


def _command(key: str = "k1", *, chat_id: str = "1234", text: str = "здравствуйте") -> SendCommand:
    """Собирает задание.

    Аргументы:
        key (str): ключ идемпотентности.
        chat_id (str): диалог.
        text (str): текст.

    Возвращает:
        SendCommand: задание.
    """
    return SendCommand(chat_id=chat_id, text=text, idempotency_key=key)


def test_a_command_survives_a_second_process(tmp_path: Path) -> None:
    """Требует, чтобы задание, положенное ДРУГИМ процессом, дошло до первого.

    Ради этого очередь и заведена: телеграм-бот поднимается отдельной командой,
    и до очереди в памяти наблюдения он не дотягивается ничем.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    Spool(root)

    code = (
        "from funora.bot import Spool, SendCommand;"
        f"s = Spool({str(root)!r});"
        "print(s.submit(SendCommand("
        "chat_id='777', text='из чужого процесса', idempotency_key='cross')))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "True", done.stdout

    taken = Spool(root).take(5)
    assert [one.command.chat_id for one in taken] == ["777"]
    assert taken[0].command.text == "из чужого процесса"


def test_the_outcome_is_readable_from_the_other_process(tmp_path: Path) -> None:
    """Требует, чтобы положивший узнал исход, не имея доступа к клиенту.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    spool = Spool(root)
    spool.submit(_command("k-out"))
    entry = spool.take(1)[0]
    spool.settle(entry, state="sent", detail="confirmed")

    code = (
        "from funora.bot import Spool;"
        f"s = Spool({str(root)!r});"
        "o = s.outcome('k-out');"
        "print(o.state, o.detail)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "sent confirmed"


def test_order_is_kept_past_ten(tmp_path: Path) -> None:
    """Требует порядка отправки, а не порядка имён.

    Имена сортируются как строки, и без выравнивания нулями десятое задание
    встало бы раньше второго. Порядок здесь - это порядок сообщений покупателям.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")
    for step in range(1, 13):
        assert spool.submit(_command(f"k{step}", text=str(step)))

    taken = spool.take(12)
    assert [one.command.text for one in taken] == [str(one) for one in range(1, 13)]


def test_a_repeated_key_is_not_queued_twice(tmp_path: Path) -> None:
    """Требует, чтобы повтор ключа не превращался во второе сообщение.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")
    assert spool.submit(_command("same"))
    assert not spool.submit(_command("same", text="другой текст"))
    assert spool.pending == 1


def test_a_settled_key_is_refused_afterwards(tmp_path: Path) -> None:
    """Требует, чтобы отработанный ключ не принимался снова.

    Иначе перезапуск телеграм-бота, повторяющего свои задания, шлёт всё второй
    раз.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")
    spool.submit(_command("once"))
    entry = spool.take(1)[0]
    spool.settle(entry, state="sent", detail="confirmed")

    assert not spool.submit(_command("once"))
    assert spool.pending == 0


def test_a_taken_command_is_not_taken_twice(tmp_path: Path) -> None:
    """Требует, чтобы взятое не досталось второму разбирающему.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    Spool(root).submit(_command("solo"))

    first = Spool(root).take(5)
    second = Spool(root).take(5)
    assert len(first) == 1
    assert second == []


def test_a_stranded_command_is_never_resent(tmp_path: Path) -> None:
    """ГЛАВНАЯ ПРОВЕРКА: взятое умершим процессом не уходит повторно.

    Файл во взятых означает «отправка могла состояться». Вернуть его в очередь
    значит послать покупателю второе сообщение, а второго не отменить.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    spool = Spool(root)
    spool.submit(_command("lost"))
    spool.take(1)  # взяли и «умерли»: исход не записан

    revived = Spool(root)
    stranded = revived.recover()

    assert stranded == ("lost",)
    assert revived.take(5) == [], "задание с неизвестной судьбой ушло бы вторым сообщением"
    assert revived.stuck == ("lost",)


def test_a_stranded_command_is_not_thrown_away_silently(tmp_path: Path) -> None:
    """Требует, чтобы о застрявшем можно было узнать.

    Выбросить молча нельзя: первое сообщение могло не уйти вовсе.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    spool = Spool(root)
    spool.submit(_command("lost"))
    spool.take(1)

    Spool(root).recover()
    outcome = Spool(root).outcome("lost")
    assert outcome is not None
    assert outcome.state == "stuck"
    assert "могло уйти" in outcome.detail


def test_recovery_is_quiet_when_nothing_is_stranded(tmp_path: Path) -> None:
    """Требует, чтобы разбор застрявшего не выдумывал застрявшего.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")
    spool.submit(_command("fine"))
    assert spool.recover() == ()
    assert spool.pending == 1


def test_a_broken_file_goes_to_stuck_not_back_to_the_queue(tmp_path: Path) -> None:
    """Требует, чтобы непригодное задание не крутилось по кругу.

    Возвращённое в очередь, оно вернулось бы снова и снова, занимая место в
    каждой паузе.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    spool = Spool(root)
    spool.submit(_command("bad"))

    broken = next((root / "ready").iterdir())
    broken.write_text("не json вовсе", encoding="utf-8")

    assert spool.take(5) == []
    assert spool.stuck == ("bad",)
    assert spool.pending == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"chat_id": "", "text": "a", "idempotency_key": "k"},
        {"chat_id": "   ", "text": "a", "idempotency_key": "k"},
        {"chat_id": 1234, "text": "a", "idempotency_key": "k"},
        {"chat_id": "1", "text": "", "idempotency_key": "k"},
        {"chat_id": "1", "text": None, "idempotency_key": "k"},
        {"chat_id": "1", "text": "a"},
        {"chat_id": "1", "text": "a", "idempotency_key": "../уход"},
        ["не словарь"],
    ],
)
def test_an_unusable_payload_is_never_sent(tmp_path: Path, payload: object) -> None:
    """Требует, чтобы непригодное задание не превращалось в отправку.

    Приведения к строке нет нарочно: идентификатор диалога числом дал бы адрес,
    по которому мы не были.

    Аргументы:
        tmp_path (Path): временный каталог.
        payload (object): непригодное содержимое файла.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    spool = Spool(root)
    (root / "ready" / "000000000001-k.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    assert spool.take(5) == []
    assert spool.stuck == ("k",)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "с кириллицей",
        "с/косой",
        ".",
        "..",
        ".скрытый",
        ".hidden",
        "хвост-точкой.",
        "с:двоеточием",
        "x" * 121,
        "с пробелом",
    ],
)
def test_a_key_unfit_for_a_file_name_is_refused(tmp_path: Path, key: str) -> None:
    """Требует отказа на ключе, непригодном для имени файла.

    Ключ становится частью имени. Косая черта увела бы задание в чужой каталог,
    точки - на уровень выше, двоеточие не принимает Windows.

    Аргументы:
        tmp_path (Path): временный каталог.
        key (str): непригодный ключ.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")
    with pytest.raises(ValidationError):
        spool.submit(_command(key))


def test_an_overfull_queue_refuses(tmp_path: Path) -> None:
    """Требует отказа на переполнении, а не молчаливого накопления.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool", max_spooled=3)
    for step in range(3):
        assert spool.submit(_command(f"k{step}"))

    with pytest.raises(UsageError) as raised:
        spool.submit(_command("лишний".encode("ascii", "ignore").decode() or "extra"))
    assert "переполнена" in str(raised.value)


@pytest.mark.parametrize("limit", [0, -1])
def test_a_zero_limit_is_refused(tmp_path: Path, limit: int) -> None:
    """Требует, чтобы ноль не читался как «без предела».

    Аргументы:
        tmp_path (Path): временный каталог.
        limit (int): непригодный предел.

    Возвращает:
        None
    """
    with pytest.raises(ValidationError):
        Spool(tmp_path / "spool", max_spooled=limit)


def test_taking_respects_the_limit(tmp_path: Path) -> None:
    """Требует, чтобы за раз забиралось не больше просимого.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")
    for step in range(5):
        spool.submit(_command(f"k{step}"))

    assert len(spool.take(2)) == 2
    assert spool.pending == 3
    assert spool.take(0) == []


def test_the_outcome_is_missing_until_the_command_is_settled(tmp_path: Path) -> None:
    """Требует, чтобы неотработанное задание не выглядело отработанным.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")
    spool.submit(_command("waiting"))
    assert spool.outcome("waiting") is None
    assert spool.outcome("никогда не было") is None


def test_settling_removes_the_command_after_recording(tmp_path: Path) -> None:
    """Требует, чтобы исход был записан РАНЬШЕ снятия задания.

    Обратный порядок оставил бы задание, которого нет ни во взятых, ни в
    отработанных, - и повтор с тем же ключом прошёл бы как новый.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    spool = Spool(root)
    spool.submit(_command("k"))
    entry = spool.take(1)[0]
    spool.settle(entry, state="sent", detail="confirmed")

    assert not entry.path.exists()
    assert (root / "done" / "k.json").exists()
    assert not spool.submit(_command("k"))


def test_no_partial_file_is_left_behind(tmp_path: Path) -> None:
    """Требует, чтобы читатель из другого процесса не застал полузаписанное.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    root = tmp_path / "spool"
    spool = Spool(root)
    spool.submit(_command("k"))
    spool.settle(spool.take(1)[0], state="sent", detail="confirmed")

    leftovers = [one.name for one in (root / "done").iterdir() if one.suffix == ".partial"]
    assert leftovers == []


def test_two_processes_racing_on_one_name_do_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Требует, чтобы проигравший гонку не затёр выигравшего.

    Два процесса читают очередь, оба видят её пустой, оба считают номер один и
    оба берут одно имя. Проверка с последующей записью здесь не спасает:
    вклиниться успевают ровно между ними.

    Спасает исключительное создание файла - и это единственная проверка, где
    его видно. Подставляется одинаковое имя и снимается память о ключе: иначе
    до гонки дело не дошло бы, повтор отсеялся бы раньше.

    Аргументы:
        tmp_path (Path): временный каталог.
        monkeypatch (pytest.MonkeyPatch): подменяет имя и память о ключах.

    Возвращает:
        None
    """
    spool = Spool(tmp_path / "spool")

    monkeypatch.setattr(Spool, "_name_for", staticmethod(lambda key, waiting: "одно-имя.json"))
    monkeypatch.setattr(Spool, "_known", lambda self, key: False)

    assert spool.submit(_command("first", text="первый"))
    assert not spool.submit(_command("second", text="второй")), (
        "проигравший гонку объявил себя принятым: одно из двух заданий пропало бы молча"
    )

    survived = json.loads((spool.root / "ready" / "одно-имя.json").read_text(encoding="utf-8"))
    assert survived["text"] == "первый", "проигравший затёр выигравшего"
