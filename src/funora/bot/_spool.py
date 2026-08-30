"""Очередь исходящих между ПРОЦЕССАМИ: каталог вместо памяти.

ЗАЧЕМ ВТОРАЯ ОЧЕРЕДЬ, КОГДА ЕСТЬ [_outbox.py]. Та решает задачу про потоки:
класть можно откуда угодно, трогает площадку один. Эта решает задачу про
процессы, и разница не в объёме, а в том, что ломается.

Очередь в памяти живёт ровно столько, сколько живёт процесс. Телеграм-бот,
поднятый отдельной командой - обычное устройство, и у него нет ни одного способа
попросить об отправке: у него другой интерпретатор, другая память и никакого
доступа к чужой очереди.

Соблазн решить это сокетом либо базой отвергнут: каталог с файлами не требует ни
порта, ни зависимости, ни запущенного посредника, а атомарность даёт сама
файловая система. Переименование внутри тома атомарно и на Windows, и на Linux.

ЧТО ЗДЕСЬ САМОЕ ВАЖНОЕ - НЕ ОЧЕРЕДЬ, А ЗАСТРЯВШЕЕ. Задание, взятое в работу
процессом, который умер, - это сообщение с НЕИЗВЕСТНОЙ судьбой: могло уйти,
могло не уйти. Отправить его снова значит рискнуть вторым сообщением
покупателю; выбросить молча - потерять первое.

Поэтому такие задания не делают ни того, ни другого. Они переносятся в
`stuck/`, откуда их не берёт никто, и о них сообщают вслух. Решает человек, и
решает он, посмотрев переписку, - другого способа узнать здесь нет.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ..errors import UsageError, ValidationError
from ._outbox import SendCommand

__all__ = ["Spool", "SpoolEntry", "SpoolOutcome", "MAX_SPOOLED"]

_log = logging.getLogger("funora.bot.spool")

#: Сколько заданий каталог принимает, прежде чем отказывать.
#:
#: Довод тот же, что у очереди в памяти, и цена ошибки та же: наблюдение
#: разбирает по нескольку за шаг, а класть можно сколько угодно быстро. Разница
#: одна - переполнение здесь съедает не память, а место на диске, и потому
#: предел взят с запасом.
MAX_SPOOLED: Final[int] = 4096

#: Сколько знаков занимает порядковый номер в имени файла.
#:
#: Имена сортируются как строки, и без выравнивания нулями десятое задание
#: встало бы раньше второго. Порядок здесь - это порядок отправки покупателям.
_ORDER_WIDTH: Final[int] = 12

#: Что позволено в ключе идемпотентности.
#:
#: Ключ становится ЧАСТЬЮ ИМЕНИ ФАЙЛА, и это накладывает ограничение, которого у
#: очереди в памяти нет. Косая черта увела бы задание в чужой каталог, точки -
#: на уровень выше, двоеточие не принимает Windows.
#:
#: Края отдельно: ключ обязан начинаться и кончаться буквой либо цифрой. Иначе
#: проходят ключи «.» и «..» - имена, которые у файловой системы означают не
#: файл, а каталог, - и ключ с ведущей точкой, дающий скрытый файл.
_KEY: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,118}[A-Za-z0-9])?$")

#: Подкаталоги. Имя каталога и есть состояние задания.
_READY: Final[str] = "ready"
_TAKEN: Final[str] = "taken"
_DONE: Final[str] = "done"
_STUCK: Final[str] = "stuck"


@dataclass(frozen=True, slots=True)
class SpoolEntry:
    """Задание, взятое из каталога.

    Attributes:
        command (SendCommand): Просьба отправить сообщение.
        path (Path): Файл, которым задание сейчас представлено. Лежит в
            подкаталоге взятого: задание уже никому больше не достанется.
    """

    command: SendCommand
    path: Path


@dataclass(frozen=True, slots=True)
class SpoolOutcome:
    """Чем кончилось задание.

    Attributes:
        idempotency_key (str): Ключ задания.
        state (str): Одно из sent, refused, stuck.
        detail (str): Подробность: исход отправки либо имя отказа.
        at (str): Момент записи, как его пишет `datetime.isoformat`.
    """

    idempotency_key: str
    state: str
    detail: str
    at: str


def _order(value: int) -> str:
    """Собирает порядковую часть имени файла.

    Аргументы:
        value (int): Номер задания.

    Возвращает:
        str: Номер, выровненный нулями.
    """
    return str(value).zfill(_ORDER_WIDTH)


class Spool:
    """Очередь исходящих, разделяемая между процессами.

    Каталог создаётся при первом обращении. Класть в него можно из любого
    процесса; забирать обязан ровно один - тот, что ведёт наблюдение.

    Args:
        path (Path | str): Каталог очереди.
        max_spooled (int): Сколько заданий держать, прежде чем отказывать.

    Raises:
        ValidationError: Если предел непригоден.
    """

    __slots__ = ("_root", "_max")

    def __init__(self, path: Path | str, max_spooled: int = MAX_SPOOLED) -> None:
        if max_spooled < 1:
            raise ValidationError(
                f"предел очереди {max_spooled} не годится: ноль и отрицательное "
                "здесь читались бы как «не принимать ничего», а очередь без "
                "предела копит сообщения, которые уйдут с опозданием на часы"
            )
        self._root = Path(path)
        self._max = max_spooled
        for name in (_READY, _TAKEN, _DONE, _STUCK):
            (self._root / name).mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Каталог очереди.

        Возвращает:
            Path: Корень, в котором лежат подкаталоги состояний.
        """
        return self._root

    def submit(self, command: SendCommand) -> bool:
        """Кладёт задание в очередь. Звать можно из любого процесса.

        Аргументы:
            command (SendCommand): Просьба отправить сообщение.

        Возвращает:
            bool: True, если задание принято. False означает, что задание с
            таким ключом уже есть либо уже отработано, - то есть повтор.

        Raises:
            ValidationError: Если ключ непригоден для имени файла.
            UsageError: Если очередь переполнена.
        """
        key = command.idempotency_key
        if not _KEY.match(key):
            raise ValidationError(
                f"ключ идемпотентности {key!r} не годится для очереди в каталоге: "
                "он становится частью имени файла, а в имени позволены только "
                "латиница, цифры, точка, дефис и подчёркивание, не длиннее 120 "
                "знаков. Косая черта увела бы задание в чужой каталог"
            )

        if self._known(key):
            return False

        ready = self._root / _READY
        waiting = sorted(ready.iterdir())
        if len(waiting) >= self._max:
            raise UsageError(
                f"очередь исходящих переполнена: {len(waiting)} заданий ждут "
                f"отправки при пределе {self._max}. Наблюдение разбирает её по "
                "нескольку за шаг, и класть быстрее, чем она вычерпывается, "
                "значит копить сообщения, которые уйдут с опозданием на часы"
            )

        payload = {
            "chat_id": command.chat_id,
            "text": command.text,
            "idempotency_key": key,
            "declared_cold": command.declared_cold,
            "at": datetime.now(UTC).isoformat(),
        }
        target = ready / self._name_for(key, waiting)
        # Исключительное создание, а не проверка с последующей записью: между
        # проверкой и записью успевает вклиниться второй процесс, и одно из двух
        # заданий пропало бы молча.
        try:
            with open(target, "x", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        except FileExistsError:
            return False
        return True

    @staticmethod
    def _name_for(key: str, waiting: list[Path]) -> str:
        """Придумывает имя файла для нового задания.

        Номер берётся от последнего лежащего, а не от счётчика в памяти:
        процессов несколько, и у каждого свой счётчик начался бы с нуля.

        Вынесено отдельно НАРОЧНО. Два процесса, посчитавшие номер до того, как
        записал первый, получают одно имя, и защищает от этого исключительное
        создание файла. Проверить защиту, не имея права подставить одинаковое
        имя, нельзя - а непроверенная защита ничем не отличается от её
        отсутствия.

        Аргументы:
            key (str): Ключ идемпотентности.
            waiting (list[Path]): Задания, уже лежащие в очереди.

        Возвращает:
            str: Имя файла.
        """
        last = 0
        if waiting:
            head = waiting[-1].name.split("-", 1)[0]
            if head.isdigit():
                last = int(head)
        return f"{_order(last + 1)}-{key}.json"

    def _known(self, key: str) -> bool:
        """Говорит, встречался ли ключ в любом из состояний.

        Аргументы:
            key (str): Ключ идемпотентности.

        Возвращает:
            bool: True, если задание с этим ключом уже есть либо было.
        """
        if (self._root / _DONE / f"{key}.json").exists():
            return True
        for where in (_READY, _TAKEN, _STUCK):
            for path in (self._root / where).iterdir():
                if path.name.endswith(f"-{key}.json") or path.name == f"{key}.json":
                    return True
        return False

    def recover(self) -> tuple[str, ...]:
        """Разбирает задания, оставшиеся взятыми от прошлого запуска.

        ВОЗВРАЩАТЬ ИХ В ОЧЕРЕДЬ НЕЛЬЗЯ. Задание попало во взятые перед самой
        отправкой; умер процесс до неё или после - по файлу не видно. Вернуть в
        очередь значит послать покупателю второе сообщение, а второго сообщения
        не отменить.

        Выбросить молча тоже нельзя: первое могло не уйти.

        Поэтому они переносятся в `stuck/` и называются вслух. Решает человек,
        посмотрев переписку.

        Возвращает:
            tuple[str, ...]: Ключи заданий с неизвестной судьбой.
        """
        stranded: list[str] = []
        for path in sorted((self._root / _TAKEN).iterdir()):
            key = self._key_of(path)
            target = self._root / _STUCK / path.name
            os.replace(path, target)
            self._record(
                SpoolOutcome(
                    idempotency_key=key,
                    state="stuck",
                    detail="процесс не дожил до записи исхода: сообщение могло уйти",
                    at=datetime.now(UTC).isoformat(),
                )
            )
            stranded.append(key)

        if stranded:
            _log.warning(
                "заданий с неизвестной судьбой: %d. Они не будут отправлены "
                "повторно - посмотрите переписку и решите сами: %s",
                len(stranded),
                ", ".join(stranded),
            )
        return tuple(stranded)

    def take(self, limit: int) -> list[SpoolEntry]:
        """Забирает из очереди до указанного числа заданий.

        Взятие - это ПЕРЕИМЕНОВАНИЕ, а не чтение. Файл, перенесённый во взятые,
        второму разбирающему уже не достанется: переименование атомарно, и
        проигравший получит отказ файловой системы, а не половину задания.

        Аргументы:
            limit (int): Сколько заданий забрать.

        Возвращает:
            list[SpoolEntry]: Взятые задания в порядке поступления.
        """
        taken: list[SpoolEntry] = []
        for path in sorted((self._root / _READY).iterdir()):
            if len(taken) >= max(0, limit):
                break

            target = self._root / _TAKEN / path.name
            try:
                os.replace(path, target)
            except OSError:
                # Задание перехватил кто-то другой либо файл исчез. Ни то, ни
                # другое не повод останавливать разбор остальных.
                continue

            command = self._read(target)
            if command is None:
                # Непригодное задание не отправляется и не возвращается в
                # очередь: оно вернулось бы снова и снова. Уходит в застрявшие,
                # где его увидит человек.
                key = self._key_of(target)
                os.replace(target, self._root / _STUCK / target.name)
                self._record(
                    SpoolOutcome(
                        idempotency_key=key,
                        state="stuck",
                        detail="файл задания непригоден: отправлять нечего",
                        at=datetime.now(UTC).isoformat(),
                    )
                )
                _log.warning("задание %s непригодно и перенесено в застрявшие", key)
                continue

            taken.append(SpoolEntry(command=command, path=target))
        return taken

    def settle(self, entry: SpoolEntry, *, state: str, detail: str) -> None:
        """Закрывает задание, записав исход.

        Аргументы:
            entry (SpoolEntry): Задание, взятое через `take`.
            state (str): sent либо refused.
            detail (str): Подробность исхода.

        Возвращает:
            None
        """
        self._record(
            SpoolOutcome(
                idempotency_key=entry.command.idempotency_key,
                state=state,
                detail=detail,
                at=datetime.now(UTC).isoformat(),
            )
        )
        # Файл задания снимается ПОСЛЕ записи исхода. Обратный порядок оставил
        # бы задание, которого нет ни во взятых, ни в отработанных, - и повтор
        # с тем же ключом прошёл бы как новый.
        entry.path.unlink(missing_ok=True)

    def outcome(self, key: str) -> SpoolOutcome | None:
        """Читает исход задания. Звать можно из любого процесса.

        Аргументы:
            key (str): Ключ идемпотентности.

        Возвращает:
            SpoolOutcome | None: Исход либо None, если задание ещё не
            отработано.
        """
        path = self._root / _DONE / f"{key}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        return SpoolOutcome(
            idempotency_key=str(raw.get("idempotency_key") or key),
            state=str(raw.get("state") or ""),
            detail=str(raw.get("detail") or ""),
            at=str(raw.get("at") or ""),
        )

    @property
    def pending(self) -> int:
        """Сколько заданий ждёт отправки.

        Возвращает:
            int: Число файлов в очереди.
        """
        return sum(1 for _ in (self._root / _READY).iterdir())

    @property
    def stuck(self) -> tuple[str, ...]:
        """Задания с неизвестной судьбой.

        Возвращает:
            tuple[str, ...]: Ключи, о которых должен решить человек.
        """
        return tuple(self._key_of(one) for one in sorted((self._root / _STUCK).iterdir()))

    def _record(self, outcome: SpoolOutcome) -> None:
        """Записывает исход задания.

        Аргументы:
            outcome (SpoolOutcome): Что случилось с заданием.

        Возвращает:
            None
        """
        payload = {
            "idempotency_key": outcome.idempotency_key,
            "state": outcome.state,
            "detail": outcome.detail,
            "at": outcome.at,
        }
        target = self._root / _DONE / f"{outcome.idempotency_key}.json"
        # Через временное имя и переименование: читатель из другого процесса
        # иначе застал бы файл наполовину записанным и счёл бы исход
        # непригодным.
        temporary = target.with_suffix(".partial")
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, target)

    @staticmethod
    def _key_of(path: Path) -> str:
        """Достаёт ключ идемпотентности из имени файла.

        Аргументы:
            path (Path): Файл задания.

        Возвращает:
            str: Ключ.
        """
        name = path.name.removesuffix(".json")
        head, _, tail = name.partition("-")
        return tail if head.isdigit() and tail else name

    @staticmethod
    def _read(path: Path) -> SendCommand | None:
        """Читает задание из файла.

        Аргументы:
            path (Path): Файл задания.

        Возвращает:
            SendCommand | None: Задание либо None, если файл непригоден.
        """
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None

        chat_id = raw.get("chat_id")
        text = raw.get("text")
        key = raw.get("idempotency_key")
        # Все три обязательны и все три строки. Приведения к строке нет:
        # идентификатор диалога числом дал бы адрес, по которому мы не были.
        if not isinstance(chat_id, str) or not chat_id.strip():
            return None
        if not isinstance(text, str) or not text:
            return None
        if not isinstance(key, str) or not _KEY.match(key):
            return None

        cold = raw.get("declared_cold")
        return SendCommand(
            chat_id=chat_id,
            text=text,
            idempotency_key=key,
            declared_cold=cold if isinstance(cold, bool) else False,
        )
